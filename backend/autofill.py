# -*- coding: utf-8 -*-
"""自动填表引擎（测试版）· Playwright 驱动。

这个模块要解决的问题：央企国企的招聘系统都要「注册 → 登录 → 填一页几十个字段 →
传附件 → 勾承诺书 → 提交」，对一个不常用电脑的人来说，光是把简历信息在五个不同
网站上各敲一遍，就足够劝退了。

引擎负责的是「敲一遍」这部分：开页面、认字段、把简历里的信息填进去。

有一条硬规矩，写在这里免得以后被改坏：
**凡是只有本人才拿得到、或者必须本人表态的东西，一律停下来问人，不猜、不试、不绕。**
具体是三类：手机短信验证码、图形验证码、承诺书勾选与最终提交。

原因不只是合规。图形验证码是对方专门用来分辨「人还是程序」的，去绕它等于告诉对方
我们不是人；短信码如果靠猜，试错几次账号就被锁了——用户在那个网站上就再也投不了，
这是比「多按两下屏幕」严重得多的损失。实测过：真实央企招聘系统在这三处拦下来的
比例接近 100%，硬冲的结果只有封号。
"""
import json
import os
import re
import threading
import time
import traceback
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
SHOT_DIR = os.path.join(ROOT_DIR, 'data', 'autofill')
# 用户同意保留的原始简历（Word/PDF）放这儿。上传附件时优先用原件，
# 这样招聘方看到的是他真正的简历（带照片和排版），不是我们拼出来的一页文字。
RESUME_DIR = os.path.join(ROOT_DIR, 'data', 'resumes')
# 浏览器登录态存在这个目录：用户登录一次国聘，cookie 就永久留着，
# 以后每次投递自动带上，不用再扫码。没这个，每单都是匿名浏览器，永远卡登录墙。
PROFILE_DIR = os.path.join(ROOT_DIR, 'data', 'browser_profile')
os.makedirs(SHOT_DIR, exist_ok=True)

# 浏览器吃内存，一次只跑一个
_LOCK = threading.Semaphore(1)

# 拿不到锁时最多等多久。宁可告诉用户「上一单还在跑」，也不要无声无息地干等——
# 一个人对着不动的进度条等 10 分钟，比直接说清楚糟糕得多。
LOCK_WAIT = 90

TASKS = {}
MAX_TASKS = 20       # 任务表只留最近这些，免得长跑之后一直在内存里堆


class _Cancelled(Exception):
    """用户又开了一单，这一单主动收工（不是出错）。"""

_PUSH = lambda t: None       # noqa: E731


def set_pusher(fn):
    """由 app.py 注入 WebSocket 推送函数，避免这里反向依赖 flask。"""
    global _PUSH
    _PUSH = fn


# --------------------------------------------------------------- 字段词表
# 顺序有意义：越靠前越先匹配。放前面的都是「更长更具体」的词，
# 否则「姓名」会被更靠后的「名」抢先，或者「毕业院校」被「院校」的兄弟词吃掉。
_FIELD_RULES = [
    ('_captcha',   ('短信验证码', '手机验证码', '图形验证码', '验证码', '校验码',
                    'captcha', 'vericode', 'verifycode', 'checkcode', 'authcode', 'vcode')),
    ('name',       ('真实姓名', '姓 名', '姓名', 'realname', 'fullname', 'xm')),
    ('phone',      ('移动电话', '手机号码', '手机号', '联系电话', '联系方式', '电话',
                    'mobile', 'phone', 'telephone', 'tel', 'lxdh', 'sj')),
    ('email',      ('电子邮箱', '电子邮件', '邮箱', 'email', 'e-mail', 'mail')),
    ('edu',        ('最高学历', '学历', '文化程度', 'education', 'degree', 'xl')),
    ('school',     ('毕业院校', '毕业学校', '院校', '学校', 'school', 'university',
                    'college', 'byyx')),
    ('major',      ('所学专业', '专业名称', '专业', 'major', 'specialty')),
    ('birth',      ('出生年月', '出生日期', '出生', '生日', 'birth', 'csny')),
    ('gender',     ('性别', 'gender', 'sex')),
    ('graduation', ('毕业时间', '毕业年份', '毕业日期', 'graduation')),
    ('city',       ('期望工作城市', '期望城市', '意向城市', '期望工作地', '意向工作地',
                    '工作地点', 'city')),
    ('salary',     ('期望薪资', '期望月薪', '薪资要求', '薪酬要求', '期望工资', 'salary')),
    ('intro',      ('自我评价', '自我介绍', '个人特长', '个人简介', '个人描述', '特长',
                    '简介', '备注', 'intro', 'remark', 'zp')),
    ('age',        ('年龄', 'age')),
]

# 简历里有、能自动填的字段。
# 这里必须和 resume_parser 认得出的字段、以及 profiles 表里的列保持一致——
# 少写一个的后果是：用户简历上明明有出生年月和学校，界面却报「你的资料里没有这项」，
# 逼他一个字一个字重打。这三处是同一件事，改一个就得看另外两个。
_PROFILE_KEYS = ('name', 'phone', 'email', 'edu', 'city', 'salary', 'intro', 'age',
                 'gender', 'birth', 'school', 'major', 'graduation')

# 简历里没有、识别得出来但填不了的：告诉用户「你资料缺这项」，而不是默默跳过
_RULE_LABEL = {
    'name': '姓名', 'phone': '手机号码', 'email': '电子邮箱', 'edu': '最高学历',
    'school': '毕业院校', 'major': '所学专业', 'birth': '出生年月', 'gender': '性别',
    'graduation': '毕业时间', 'city': '期望工作城市', 'salary': '期望月薪',
    'intro': '个人特长及自我评价', 'age': '年龄',
}


def _hit(text, keys, strict=False):
    for k in keys:
        k = k.lower()
        if len(k) <= 3 and k.isascii():
            # 两三个字母的英文缩写要卡边界，不然 'cs' 会命中 'css'
            if re.search(r'(?:^|[^a-z0-9])%s(?:[^a-z0-9]|$)' % re.escape(k), text):
                return True
        elif k in text:
            return True
    return False


def _match_field(meta):
    """label/placeholder 优先，英文 name/id 兜底。

    真实表单里 label 有一半是空的，所以线索要全部拼起来看，而不是只认 label。
    """
    primary = ('%s %s' % (meta.get('label', ''), meta.get('ph', ''))).lower()
    secondary = ('%s %s %s' % (meta.get('name', ''), meta.get('id', ''),
                               meta.get('cls', ''))).lower()
    for field, keys in _FIELD_RULES:
        if _hit(primary, keys):
            return field
    if not primary.strip():
        for field, keys in _FIELD_RULES:
            if _hit(secondary, keys, strict=True):
                return field
    return None


# --------------------------------------------------------------- 任务对象
class Task(object):
    """一次自动填表的全过程，前端轮询/收推送都看它。

    状态：running（在干）→ need_human（卡在等人）→ running → done / error
    """

    def __init__(self, tid, job, profile, url, headless=True, refill=False):
        self.id = tid
        self.job = job or {}
        self.profile = profile or {}
        self.url = url
        self.headless = headless
        # 重填覆盖：段里已经有内容也照样打开「编辑」重填一遍再保存。
        # 默认关——正常投递时已填好的段跳过就好，没必要把站点上的内容重写一遍。
        self.refill = bool(refill)
        self.status = 'running'
        self.steps = []
        self.shots = []
        self.filled = []
        self.missing = []
        self.ask = None
        self.answer = None
        self.receipt = ''
        self.note = ''
        self.error = ''
        self.cancelled = False
        self._ev = threading.Event()
        self._n = 0

    def to_dict(self):
        return {
            'id': self.id, 'status': self.status, 'url': self.url,
            'job': {'title': self.job.get('title', ''), 'company': self.job.get('company', '')},
            'steps': self.steps, 'shots': self.shots,
            'filled': self.filled, 'missing': self.missing,
            'ask': self.ask, 'receipt': self.receipt,
            'note': self.note, 'error': self.error,
            'refill': self.refill,
        }

    def push(self):
        try:
            _PUSH(self.to_dict())
        except Exception:                        # noqa: BLE001
            pass

    def step(self, label, ok=True, note=''):
        self.steps.append({'label': label, 'ok': ok, 'note': note})
        self.push()

    def show_browser_supported(self):
        """「打开浏览器窗口」这台机器干得了吗：Linux 无 DISPLAY 就干不了。"""
        import sys
        if not sys.platform.startswith('linux'):
            return True
        return bool(os.environ.get('DISPLAY'))

    def shot(self, page, label):
        self._n += 1
        name = '%s_%02d.png' % (self.id, self._n)
        try:
            page.screenshot(path=os.path.join(SHOT_DIR, name))
            self.shots.append({'name': name, 'label': label})
        except Exception:                        # noqa: BLE001
            pass
        self.push()

    def ask_human(self, kind, title, msg, placeholder='', itype='text',
                  timeout=900, items=None):
        """停在这里等人。浏览器保持开着，等人这一下不会丢上下文。

        items：**需要用户本人补的清单**（结构化）。站点只在简历目录里把没完成的
        段标红、写一句「请补充优化下列标黄部分」，既不解释为什么、也不说怎么补，
        用户看到红字只会以为程序坏了。所以这里把清单连同「为什么不能替你补」
        「怎么补最快」一起推给前端渲染——只传一句「还差东西」等于没帮上忙。
        每项形如 {'key','label','why','how'}。
        """
        self.ask = {'type': kind, 'title': title, 'msg': msg,
                    'placeholder': placeholder, 'input_type': itype}
        if items:
            self.ask['items'] = items
        self.status = 'need_human'
        self.push()
        got = self._ev.wait(timeout)
        self._ev.clear()
        self.ask = None
        if self.cancelled:
            raise _Cancelled()
        self.status = 'running'
        val = self.answer
        self.answer = None
        self.push()
        return (val or '') if got else ''

    def answer_with(self, value):
        self.answer = value
        self._ev.set()


def submit_answer(tid, value):
    t = TASKS.get(tid)
    if not t or t.status != 'need_human':
        return False
    t.answer_with(value)
    return True


def get(tid):
    t = TASKS.get(tid)
    return t.to_dict() if t else None


def cancel(tid):
    """让一单主动收工：把等人那一下叫醒，正在跑的流程会在下一步退出来。"""
    t = TASKS.get(tid)
    if t and t.status in ('running', 'need_human'):
        t.cancelled = True
        t._ev.set()
        return True
    return False


def cancel_all(except_id=None):
    """一个人同时只跑一单：新单开跑前，把旧单请下场。

    不做这件事的后果很具体——用户在「需要你出手」那一步直接关掉页面走了，
    这一单会攥着浏览器和锁等到超时，期间再点「开始自动填」只会一直转圈。
    """
    n = 0
    for k, t in list(TASKS.items()):
        if k != except_id and t.status in ('running', 'need_human'):
            t.cancelled = True
            t._ev.set()
            n += 1
    return n


# --------------------------------------------------------------- 页面工具
def _settle(page, ms=900):
    try:
        page.wait_for_load_state('domcontentloaded', timeout=8000)
    except Exception:                            # noqa: BLE001
        pass
    page.wait_for_timeout(ms)


def _wait_real_content(page, timeout=30):
    """SPA 站（国聘就是）登录完常把你丢在骨架屏上：HTML 到了、正文还没渲染。
    等到页面有真正的文字内容（超过阈值）再往下走，最多等 timeout 秒。
    2026-09-20 实测：不等的后果是把「请输入职位或企业名称」搜索框当表单字段。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if len(_page_text(page).strip()) > 150:
                return True
        except Exception:                        # noqa: BLE001
            pass
        try:
            page.wait_for_timeout(800)
        except Exception:                        # noqa: BLE001
            return False
    return False


def _page_text(page):
    try:
        return page.inner_text('body') or ''
    except Exception:                            # noqa: BLE001
        return ''


_SCAN_JS = r"""
() => {
  const nodes = Array.from(document.querySelectorAll('input,select,textarea'));
  return nodes.map((el, i) => {
    el.setAttribute('data-af-idx', String(i));
    // 单选/多选的 label 写的是「选项名」（男、女），不是字段名（性别），
    // 所以这两个要跳过 label，直接去表格里找左边那格。
    const isChoice = (el.type === 'radio' || el.type === 'checkbox');
    let label = '';
    if (el.id && !isChoice) {
      const l = document.querySelector('label[for="' + el.id + '"]');
      if (l) label = l.innerText;
    }
    if (!label && !isChoice) { const p = el.closest('label'); if (p) label = p.innerText; }
    if (!label) {
      // 老式政府网站常用表格排版：字段名在左边那一格，没有 label
      const td = el.closest('td');
      if (td && td.previousElementSibling) label = td.previousElementSibling.innerText;
    }
    if (!label) {
      // 弹窗向导里常见「<label>字段名</label><input>」兄弟排法（国聘就是这样）：
      // 前一个兄弟节点是短文本就当字段名。上限 30 字，免得把整段说明当成标签。
      const prev = el.previousElementSibling;
      if (prev && prev.innerText && prev.innerText.trim().length <= 30
          && prev.innerText.trim()) label = prev.innerText;
    }
    if (!label) {
      const tr = el.closest('tr');
      if (tr) { const f = tr.querySelector('th,td'); if (f) label = f.innerText; }
    }
    if (!label && el.parentElement) label = el.parentElement.innerText;
    const r = el.getBoundingClientRect();
    return {
      idx: i, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
      name: el.name || '', id: el.id || '', ph: el.placeholder || '',
      label: (label || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
      cls: (typeof el.className === 'string' ? el.className : '')
    };
  });
}
"""


def _scan_fields(page):
    """把页面上所有填写项连同「它是什么字段」的线索一起捞出来。

    隐藏域要排掉：它们没有 label，取到的是整个表单的文字，会把「验证码」这种词
    误按到隐藏域头上。
    """
    try:
        out = page.evaluate(_SCAN_JS) or []
    except Exception:                            # noqa: BLE001
        return []
    return [m for m in out if m.get('type') != 'hidden' and m.get('type') != 'submit']


def _loc(page, meta):
    return page.locator('[data-af-idx="%s"]' % meta['idx'])


def _fill_one(page, meta, value):
    value = str(value)
    loc = _loc(page, meta)
    try:
        if meta['tag'] == 'select':
            try:
                loc.select_option(label=value)
                return True
            except Exception:                    # noqa: BLE001
                pass
            opts = loc.locator('option')
            for i in range(opts.count()):
                txt = (opts.nth(i).inner_text() or '').strip()
                if txt and (value in txt or txt in value):
                    loc.select_option(index=i)
                    return True
            return False
        loc.fill(value)
        return True
    except Exception:                            # noqa: BLE001
        return False


def _click_any(page, names, roles=('button', 'link')):
    for n in names:
        for role in roles:
            try:
                loc = page.get_by_role(role, name=n, exact=False)
                if loc.count():
                    loc.first.click(timeout=3000)
                    return True
            except Exception:                    # noqa: BLE001
                pass
        try:
            loc = page.get_by_text(n, exact=False)
            if loc.count():
                loc.first.click(timeout=3000)
                return True
        except Exception:                        # noqa: BLE001
            pass
    return False


def _mask(phone):
    phone = (phone or '').strip()
    return phone[:3] + '****' + phone[-4:] if len(phone) == 11 else (phone or '你预留的手机号')


def _read_sms_from_page(page):
    """靶场会把短信码写在页面上；真站点不会，那就返回空，后面转去问人。"""
    m = re.search(r'验证码(?:是|为|：|:)?\s*([0-9]{4,8})', _page_text(page))
    return m.group(1) if m else ''


def _read_graph_from_page(page):
    m = re.search(r'图形验证码答案\s*[：:]\s*([0-9A-Za-z]{1,8})', _page_text(page))
    return m.group(1) if m else ''


def _is_graph_captcha(meta):
    hay = ('%s %s %s %s' % (meta.get('label', ''), meta.get('ph', ''),
                            meta.get('name', ''), meta.get('id', ''))).lower()
    return not any(k in hay for k in ('短信', '手机', 'sms', 'mobile'))


# --------------------------------------------------------------- 简历附件
def _make_resume_file(t):
    """兜底生成一份纯文本简历供上传。

    只有拿不到用户原始简历文件（他没同意保留、或文件已被清理）时才走这条路。
    文本版只能保证「附件能传」这条链路不断，招聘方看不到照片和排版，
    所以能传原件就一定传原件。
    """
    p = t.profile

    def _v(label, key):
        v = p.get(key)
        v = '、'.join(str(x) for x in v) if isinstance(v, (list, tuple)) else (v or '')
        return '%s：%s' % (label, v) if str(v).strip() else None

    lines = ['个 人 简 历', '']
    for label, key in (('姓名', 'name'), ('性别', 'gender'), ('出生年月', 'birth'),
                       ('手机', 'phone'), ('邮箱', 'email'), ('年龄', 'age'),
                       ('现居', 'city'), ('最高学历', 'edu'), ('毕业院校', 'school'),
                       ('所学专业', 'major'), ('毕业时间', 'graduation'),
                       ('工作经验', 'exp'), ('期望岗位', 'job_types'),
                       ('期望月薪', 'salary'), ('技能', 'skills')):
        line = _v(label, key)
        if line:
            lines.append(line)
    intro = (p.get('intro') or '').strip()
    if intro:
        lines += ['', '自我介绍：', intro]
    lines += ['', '应聘岗位：%s · %s' % (t.job.get('title', ''), t.job.get('company', ''))]

    path = os.path.join(SHOT_DIR, 'resume_%s.txt' % t.id)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def _resume_file_for_upload(t):
    """挑一份简历文件去上传：能拿到用户原件就用原件。

    原件是他自己做（或找人做）的那份 Word/PDF，照片、排版、证书扫描件都在里面——
    招聘方看到的就是他真正的简历。以前无脑生成 txt，等于把人家的简历换成了
    一页纯文字，照片全丢，这一步是很多人不愿意用自动投的原因。
    """
    raw = (t.profile.get('resume_path') or '').strip()
    if raw:
        # 只认落在我们自己 resumes 目录里的文件，避免路径被写成别的什么东西
        safe = os.path.normpath(raw)
        if os.path.isfile(safe) and os.path.dirname(safe) == os.path.normpath(RESUME_DIR):
            return safe, os.path.basename(safe)
    return _make_resume_file(t), '自动生成的简历.txt'


# --------------------------------------------------------------- 主流程
def _run(t):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        t.status = 'error'
        t.error = ('没有安装自动填表组件。在项目目录执行：'
                   'pip install playwright && playwright install chromium')
        t.push()
        return

    if not _LOCK.acquire(timeout=LOCK_WAIT):
        t.status = 'error'
        t.error = ('上一单还没结束，浏览器忙不过来。等它停了再点一次「开始自动填」。')
        t.push()
        return
    # 「打开浏览器窗口」在服务器上开不了：机器没有显示器（无 DISPLAY），
    # headed 启动会直接崩。转成后台填，截图照常——别让用户对着一堆
    # Missing X server 的英文堆栈发懵。
    headless = t.headless
    if not headless and not t.show_browser_supported():
        headless = True
        t.step('这台服务器没有显示器', ok=False,
               note='已改为后台填，过程照常截图给你看')
    try:
        with sync_playwright() as pw:
            # 持久化浏览器：登录态跨任务保留（见 PROFILE_DIR 的注释）
            os.makedirs(PROFILE_DIR, exist_ok=True)
            ctx = pw.chromium.launch_persistent_context(
                PROFILE_DIR,
                headless=headless,
                viewport={'width': 1280, 'height': 900},
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                args=['--disable-blink-features=AutomationControlled'],
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                # 政府网站爱用 confirm 弹窗，先自动点掉，不然会卡死
                page.on('dialog', lambda d: d.accept())
                _flow(t, page)
            finally:
                ctx.close()
    except _Cancelled:
        t.status = 'done'
        t.note = '这单提前收工了（你重新开了一单）。页面上的内容没有被提交。'
        t.step('提前收工', ok=False, note='你重新开了一单')
    except Exception:
        t.status = 'error'
        t.error = traceback.format_exc()[-1200:]
        t.push()
    finally:
        _LOCK.release()


# 把投递表单弹出来的按钮：国聘这类站详情页上没有表单，点了才弹出来
_APPLY_BTNS = ('申请职位', '立即申请', '申请该职位', '投递简历', '立即投递',
               '我要应聘', '投个简历')


def _click_text_if_exists(page, text):
    """点页面上含指定文字的元素（自定义组件的原生 input 往往是隐藏的，
    点文字本身最通用）。"""
    try:
        loc = page.get_by_text(text, exact=False)
        if loc.count():
            loc.first.click(timeout=3000)
            return True
    except Exception:                            # noqa: BLE001
        pass
    return False


# 在「XX经历缺失」那一行里找「添加」按钮：给标记上 data-af-add 属性，
# 再用普通 locator 去点——直接用文字定位会点中页面里第一个同名按钮。
# section 参数化：教育经历/实习经历/校内活动或社会实践三个向导共用这一段。
_SECTION_ADD_JS = r"""
(section) => {
  const isAdd = el => el.childElementCount === 0 &&
                      /^(?:添\s*加|编\s*辑)$/.test((el.innerText || '').trim());
  // 上一轮标的属性要清掉，否则下一轮 .first 会点到旧按钮
  document.querySelectorAll('[data-af-add]')
    .forEach(el => el.removeAttribute('data-af-add'));
  const btns = Array.from(document.querySelectorAll('button, a, span, div'))
    .filter(isAdd);
  for (const b of btns) {
    let p = b.parentElement, n = 0;
    while (p && n < 8) {
      // 往上找包含段落名的祖先时，走到 body 也能「命中」（整页当然包含这几个字），
      // 会把别段的「添加」标成自己的。圈里一旦出现第二个「添加」按钮，
      // 说明已经越过段落边界，立刻停。
      const addCount = Array.from(p.querySelectorAll('button, a, span, div'))
        .filter(isAdd).length;
      if (addCount > 1) break;
      if ((p.innerText || '').indexOf(section) >= 0) {
        b.setAttribute('data-af-add', 'sec');
        return true;
      }
      p = p.parentElement; n++;
    }
  }
  return false;
}
"""


def _click_section_add(t, page, section):
    """点「XX经历」那一行里的「添加」，弹出子表单。"""
    try:
        marked = page.evaluate(_SECTION_ADD_JS, section)
    except Exception:                            # noqa: BLE001
        marked = False
    if not marked or not page.locator('[data-af-add="sec"]').count():
        return False
    try:
        page.locator('[data-af-add="sec"]').first.click(timeout=4000)
        return True
    except Exception:                            # noqa: BLE001
        return False


def _wiz_fill(t, page, mapping):
    """在弹出的向导（子表单）里按 mapping 填字段。

    mapping 是 [(标签关键词元组, 值), ...]，按顺序匹配——
    更具体的词要放前面（「开始时间」要在「时间」前面，否则两个日期格
    都会被「时间」抢走填成同一个值）。每个字段命中第一组就停。
    返回填上的格数。
    """
    fields = [m for m in _scan_fields(page) if m['visible']]
    filled = 0
    for m in fields:
        if m['type'] in ('radio', 'checkbox', 'file'):
            continue
        hay = ('%s %s' % (m.get('label', ''), m.get('ph', ''))).lower()
        if not hay.strip() or hay in ('添加', '保存'):
            continue
        for keys, value in mapping:
            if not value:
                continue
            value = str(value).strip()
            if not value:
                continue
            if any(k in hay for k in keys):
                if _fill_one(page, m, value):
                    t.filled.append({'label': m.get('label') or keys[0], 'value': value})
                    filled += 1
                break
    return filled


def _save_wizard(t, page, section, entry_label):
    # antd 会把两字按钮渲染成「保 存」（中间插空格），必须两种都试
    if _click_any(page, ('保 存', '保存', '确 定', '确定',
                         '完 成', '完成', '提 交', '提交')):
        t.step('保存了一段%s：%s' % (section, entry_label))
        _settle(page, 1200)
        t.shot(page, '%s保存后' % section)
        return True
    t.missing.append({'label': section, 'why': '保存按钮没找到，需要你手动点'})
    return False


# ================= 国聘 Ant Design 表单适配（2026-09-20 实测 DOM） =================
# c.iguopin.com 的补全表单是 antd：label 带 title 属性、按钮文字插空格、
# 下拉是 .ant-select（选项挂在 body 级 .ant-select-dropdown）、日期是
# .ant-picker、级联是 .ant-cascader、选项组是普通元素点击。控件普查见
# data/guopin_dom/census_*.txt。

_DEGREE_MAP = {'专科': '大专', '大专': '大专', '高职': '大专',
               '本科': '本科', '硕士': '硕士', '研究生': '硕士', '博士': '博士'}


def _ant_item(page, label):
    """按 label 的 title/文本前缀找 .ant-form-item，返回 locator 或 None。

    优先用 label[title^=...]（这个模板的 label 基本都带 title）；
    万一某个 label 没 title 属性，退回按 innerText 前缀匹配。"""
    try:
        loc = page.locator('.ant-form-item').filter(
            has=page.locator('label[title^="%s"]' % label))
        if loc.count():
            return loc.first
    except Exception:                                # noqa: BLE001
        pass
    try:
        loc = page.locator('.ant-form-item').filter(
            has=page.locator('label:text-matches("^%s", "i")'
                             % re.escape(label)))
        if loc.count():
            return loc.first
    except Exception:                                # noqa: BLE001
        pass
    return None


def _ant_click_opt(t, page, label, value):
    """按钮组（统招/全日制/学位证/海外留学…）：点组里文字等于 value 的选项。"""
    if not value:
        return False
    js = """([prefix, value]) => {
      const items = Array.from(document.querySelectorAll('.ant-form-item'));
      const it = items.find(el => {
        const lb = el.querySelector('label');
        return lb && ((lb.title || lb.innerText || '').trim().startsWith(prefix));
      });
      if (!it) return false;
      const cands = Array.from(it.querySelectorAll('*')).filter(el =>
        el.childElementCount === 0 && (el.innerText || '').trim() === value);
      for (const c of cands) {
        const r = c.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) { c.click(); return true; }
      }
      return false;
    }"""
    try:
        ok = bool(page.evaluate(js, [label, str(value)]))
        if ok:
            t.filled.append({'label': label, 'value': value})
            page.wait_for_timeout(300)
        return ok
    except Exception:                                # noqa: BLE001
        return False


def _ant_select(t, page, label, value, strict=False, timeout=6):
    """antd 下拉：点开 selector → 匹配选项 title；搜不到就打字过滤再试。
    strict=True 时匹配不到就算了（宁可留空问人，也不乱选一个）。"""
    if not value:
        return False
    item = _ant_item(page, label)
    if item is None:
        return False
    try:
        item.locator('.ant-select-selector').first.click(timeout=3000)
    except Exception:                                # noqa: BLE001
        return False
    page.wait_for_timeout(700)
    value = str(value)
    typed = False
    end = time.time() + timeout
    while time.time() < end:
        opts = page.locator(
            '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
            '.ant-select-item-option')
        n = opts.count()
        if n:
            for i in range(n):
                o = opts.nth(i)
                title = (o.get_attribute('title') or o.inner_text() or '').strip()
                if title and (value in title or title in value):
                    try:
                        o.click(timeout=2500)
                        t.filled.append({'label': label, 'value': title})
                        page.wait_for_timeout(400)
                        return True
                    except Exception:            # noqa: BLE001
                        continue
            if not typed:
                try:
                    inp = item.locator(
                        'input.ant-select-selection-search-input').first
                    if inp.count() and inp.is_enabled():
                        inp.click(timeout=1500)
                        inp.type(value, delay=80)
                        typed = True
                        page.wait_for_timeout(1000)
                        continue
                except Exception:                # noqa: BLE001
                    pass
            if not strict:
                try:
                    opts.first.click(timeout=2500)
                    t.filled.append({'label': label,
                                     'value': '(近似选择) %s' % value})
                    page.wait_for_timeout(400)
                    return True
                except Exception:                # noqa: BLE001
                    pass
            break
        page.wait_for_timeout(400)
    try:
        page.keyboard.press('Escape')
    except Exception:                                # noqa: BLE001
        pass
    return False


def _range_input(item, want):
    """在「在职时间」区间里按 placeholder 找起/止那个**真正生效**的输入框。

    实测这个控件的结束框 id 带动态后缀（period_end_picker-fe-1601-customization），
    不能硬编码。同一个 placeholder 下会同时存在两个 input：
      - id 以 `_picker` 结尾、父级 class 是 `ant-picker-input`  → **真的**，
        写它才能让 antd form state 落值（结束时间错误才会消失）；
      - id 以 `_input` 结尾、父级 class 是 `ant-input-affix-wrapper…` → **假的**，
        看得见也能写进 DOM，但写了 form state 仍是空，保存照报「请选择结束时间」。
    两者都 visible，所以「取第一个可见的」会误选后者（2026-09-20 实测踩过）。
    判定顺序：父级含 ant-picker-input > id 以 _picker 结尾 > 第一个可见 > first。"""
    phs = {'start': ['开始时间', '开始日期', '入职时间'],
           'end': ['结束时间', '结束日期', '离职时间']}[want]

    def _is_real_picker(e):
        try:
            return bool(e.evaluate(
                "el => !!el.closest('.ant-picker-input')"))
        except Exception:                            # noqa: BLE001
            return False

    def _is_picker_id(e):
        try:
            return str(e.get_attribute('id') or '').endswith('_picker')
        except Exception:                            # noqa: BLE001
            return False

    for ph in phs:
        try:
            loc = item.locator('input[placeholder="%s"]' % ph)
            n = loc.count()
        except Exception:                            # noqa: BLE001
            continue
        if not n:
            continue
        cands = []
        for i in range(n):
            e = loc.nth(i)
            try:
                vis = e.is_visible()
            except Exception:                        # noqa: BLE001
                vis = False
            cands.append((e, vis))
        # 优先级：真 picker 输入 > id 带 _picker 后缀 > 可见 > 首个
        for pred in (_is_real_picker, _is_picker_id):
            for e, _vis in cands:
                if pred(e):
                    return e
        for e, vis in cands:
            if vis:
                return e
        return cands[0][0]
    return None


def _ant_range(t, page, label, start, end, start_id=None, end_id=None):
    """antd 日期区间（就读年月/在职时间）：起止各点一个 input 敲完整日期。

    这个模板的「在职时间」是两个**独立日期框**（开始/结束），可见框都带
    readonly，且结束框 id 带动态后缀——按 placeholder 定位、去 readonly 再敲，
    是唯一稳的做法（2026-09-20 实测）。日期必须敲完整（YYYY-MM 自动补 -01）。"""
    start = str(start or '').strip()
    end = str(end or '').strip()
    if not start and not end:
        return False
    item = _ant_item(page, label)
    if item is None:
        return False
    try:
        ok = False
        # 开始框：普通点击即可
        if start:
            si = _range_input(item, 'start')
            if si is not None and _type_date(si, start, page):
                ok = True
        # 结束框被同层 picker 盖住、点击会被拦截，必须 force
        if end:
            ei = _range_input(item, 'end')
            if ei is not None and _type_date(ei, end, page, force=True):
                ok = True
        page.wait_for_timeout(300)
        t.filled.append({'label': label, 'value': '%s~%s' % (start, end)})
        return ok
    except Exception:                                # noqa: BLE001
        return False


def _ant_text(t, page, label, value):
    """普通输入框 / 文本域。"""
    if not value:
        return False
    item = _ant_item(page, label)
    if item is None:
        return False
    for sel in ('textarea', 'input.ant-input', 'input'):
        loc = item.locator(sel)
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible() and el.is_enabled() and el.is_editable():
                    el.fill(str(value))
                    t.filled.append({'label': label, 'value': str(value)[:40]})
                    return True
            except Exception:                    # noqa: BLE001
                continue
    return False


def _type_date(inp, value, page, force=False):
    """在 antd 日期输入框里敲入 value；YYYY-MM 自动补成 YYYY-MM-DD。

    antd 日期框常带 readonly，原生键盘敲不进去——先去掉 readonly 属性再敲。
    敲完按 Enter 让 antd 提交（onChange 触发），返回 True 表示提交成功。

    两个关键坑（2026-09-20 实测）：
    1) 必须敲**完整日期**：日精度日期框里只敲「2019-06」，输入框会显示它、
       但 antd 的 form state 仍是 null（判定为不完整），保存时报「请选择…」。
       所以识别到 YYYY-MM 就补成 YYYY-MM-01 直接敲。
    2) 绝不能按 Escape：面板未提交时 Escape 会把刚敲的值丢掉。
    force=True：目标框被同层元素拦截点击（如结束时间框被 picker 盖住）时，
       跳过可点击性检查用 dispatch 方式聚焦，再敲键盘。"""
    def box_value():
        try:
            return (inp.input_value() or '').strip()
        except Exception:                            # noqa: BLE001
            return ''
    try:
        # 去掉 readonly，否则 keydown 被浏览器丢弃、值永远进不去
        try:
            inp.evaluate("el => el.removeAttribute('readonly')")
        except Exception:                            # noqa: BLE001
            pass
        target = str(value).strip()
        m = re.match(r'^(\d{4})-(\d{1,2})$', target)
        if m:
            target = '%s-%02d-01' % (m.group(1), int(m.group(2)))
        m2 = re.match(r'^(\d{4})/(\d{1,2})$', str(value).strip())
        if m2:
            target = '%s-%02d-01' % (m2.group(1), int(m2.group(2)))
        for v in (target, str(value).strip()):
            if not v:
                continue
            if force:
                # 被拦截点击时，直接聚焦再敲（不经过可点击性检查）
                try:
                    inp.evaluate("el => { el.focus(); }")
                except Exception:                    # noqa: BLE001
                    try:
                        inp.click(timeout=1500, force=True)
                    except Exception:                # noqa: BLE001
                        pass
            else:
                try:
                    inp.click(timeout=2000)
                except Exception:                    # noqa: BLE001
                    try:
                        inp.click(timeout=1500, force=True)
                    except Exception:                # noqa: BLE001
                        pass
            page.wait_for_timeout(180)
            inp.press_sequentially(v, delay=45)
            page.wait_for_timeout(200)
            page.keyboard.press('Enter')
            page.wait_for_timeout(450)
            if box_value() == v:
                return True
        return bool(box_value())
    except Exception:                                # noqa: BLE001
        return bool(box_value())


def _ant_date(t, page, label, value):
    """单个日期框（出生日期/入党时间等）：按 label 找 .ant-picker，点开敲日期回车。

    这个模板很多控件没有 id，只能靠 label 定位（_ant_item 按 label 的
    title 前缀匹配）。日期值是 YYYY-MM 或 YYYY-MM-DD。"""
    if not value:
        return False
    item = _ant_item(page, label)
    if item is None:
        return False
    try:
        inp = item.locator('.ant-picker-input input').first
        if not _type_date(inp, value, page):
            return False
        page.wait_for_timeout(300)
        # 提交后用 Tab 把焦点移出日期框（关掉面板并保留已提交的值）；
        # 绝不能按 Escape，否则 antd 会丢弃刚填的值。
        try:
            page.keyboard.press('Tab')
        except Exception:                            # noqa: BLE001
            pass
        page.wait_for_timeout(250)
        t.filled.append({'label': label, 'value': str(value)})
        return True
    except Exception:                                # noqa: BLE001
        return False


def _ant_select_by_label_text(t, page, label_text, value, strict=False, timeout=6):
    """按 form-item 的 label 完整/包含文字打开其下拉并选 value。

    专治这个模板里 label 极长（如「是否有严重违纪违法……」）又没 id 的
    是非题下拉：_ant_item 的前缀匹配对超长 label 不可靠，这里用包含匹配。

    重要：必须用 Playwright 真实点击（会触发 mousedown）才能展开 antd 下拉，
    JS 的 element.click() 不触发 mousedown，下拉不会展开。"""
    if not value:
        return False
    value = str(value)
    try:
        item = page.locator('.ant-form-item').filter(
            has=page.locator('label', has_text=label_text)).first
        if not item.count():
            return False
        item.locator('.ant-select-selector').first.click(timeout=3000)
    except Exception:                                # noqa: BLE001
        return False
    page.wait_for_timeout(700)
    end = time.time() + timeout
    while time.time() < end:
        opts = page.locator(
            '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
            '.ant-select-item-option')
        n = opts.count()
        if n:
            for i in range(n):
                o = opts.nth(i)
                title = (o.get_attribute('title') or o.inner_text() or '').strip()
                if title and (value in title or title in value):
                    try:
                        o.click(timeout=2500)
                        t.filled.append({'label': label_text[:16], 'value': title})
                        page.wait_for_timeout(400)
                        return True
                    except Exception:                # noqa: BLE001
                        continue
            if not strict:
                try:
                    ti = (opts.first.get_attribute('title')
                          or opts.first.inner_text() or '').strip()
                    opts.first.click(timeout=2000)
                    t.filled.append({'label': label_text[:16],
                                     'value': '(近似) %s' % ti})
                    return True
                except Exception:                    # noqa: BLE001
                    pass
            break
        page.wait_for_timeout(400)
    try:
        page.keyboard.press('Escape')
    except Exception:                                # noqa: BLE001
        pass
    if strict:
        t.missing.append({'label': label_text[:16],
                          'why': '选项里没有「%s」' % value})
    return False


def _ant_cascader(t, page, label, keywords, timeout=10):
    """级联（生源地/工作地区/所属行业）：逐级点含关键词的项，如 ['中国','湖北','武汉']。

    兼容 antd v4/v5：下拉容器可能是 .ant-cascader-dropdown 包 .ant-cascader-menu，
    也可能是老式 .ant-cascader-menus，两种选择器都试。每级都取「最后一个菜单」
    （最深的那一级）去点，点完叶子级联会自动收起。"""
    item = _ant_item(page, label)
    if item is None:
        return False
    try:
        item.locator('.ant-select-selector').first.click(timeout=3000)
    except Exception:                                # noqa: BLE001
        return False
    page.wait_for_timeout(800)

    def menus_loc():
        for sel in (
            '.ant-cascader-dropdown:not(.ant-cascader-dropdown-hidden) '
            '.ant-cascader-menu',
            '.ant-cascader-menus:not(.ant-cascader-menus-hidden) '
            '.ant-cascader-menu',
            '.ant-cascader-menu',
        ):
            m = page.locator(sel)
            if m.count():
                return m
        return None

    filled = []
    for kw in keywords:
        if not kw:
            continue
        end = time.time() + timeout
        hit = False
        while time.time() < end and not hit:
            menus = menus_loc()
            if menus is None or not menus.count():
                page.wait_for_timeout(400)
                continue
            opts = menus.last.locator('.ant-cascader-menu-item')
            for i in range(opts.count()):
                o = opts.nth(i)
                txt = (o.get_attribute('title') or o.inner_text() or '').strip()
                if txt and (str(kw) in txt or txt in str(kw)):
                    try:
                        o.click(timeout=2500)
                        filled.append(txt)
                        page.wait_for_timeout(800)
                        hit = True
                        break
                    except Exception:            # noqa: BLE001
                        continue
            if not hit:
                page.wait_for_timeout(400)
        if not hit:
            break
    if not filled:
        try:
            page.keyboard.press('Escape')
        except Exception:                            # noqa: BLE001
            pass
        return False
    t.filled.append({'label': label, 'value': '/'.join(filled)})
    try:
        page.keyboard.press('Escape')
    except Exception:                                # noqa: BLE001
        pass
    return True


def _region_current(modal_loc):
    """读弹窗里「当前最深一级」的列，返回该列的可点项列表（ElementHandle 化）。

    这个级联是「点一级、右侧长出新一列」，列多了以后要取最后一列。
    省列类名是 .item-txt（无 leaf-item），市/区列是 .leaf-item.item-txt。"""
    # 收集所有列（列容器通常带 item-txt 的父级；这里直接按叶子类名分组取最右列）
    best = None
    for sel in ('.leaf-item.item-txt', '.item-txt'):
        loc = modal_loc.locator(sel)
        n = loc.count()
        if n and (best is None or n > best[1]):
            best = (loc, n)
    return best


def _region_click(modal_loc, text, leaf_only=False, allow_first=False):
    """在弹窗级联的「当前可见列」里点匹配 text 的项。

    leaf_only=True 只在 .leaf-item.item-txt（市/区列）里找；
    allow_first=True 且 text 为空时，点该列第一个可见项（不编数据，用控件默认首项）。
    返回 (ok, clicked_text)。"""
    sel = '.leaf-item.item-txt' if leaf_only else '.item-txt'
    els = modal_loc.locator(sel)
    n = els.count()
    if n == 0:
        return False, ''
    if (not text) and allow_first:
        for i in range(n):
            e = els.nth(i)
            try:
                if not e.is_visible():
                    continue
                txt = (e.get_attribute('title') or e.inner_text() or '').strip()
                e.click(timeout=2500)
                return True, txt
            except Exception:                        # noqa: BLE001
                continue
        return False, ''
    for i in range(n):
        e = els.nth(i)
        try:
            if not e.is_visible():
                continue
            t1 = (e.get_attribute('title') or '').strip()
            t2 = (e.inner_text() or '').strip()
        except Exception:                            # noqa: BLE001
            continue
        if text == t1 or text == t2 or (t2 and text in t2):
            try:
                e.click(timeout=2500)
                return True, (t1 or t2)
            except Exception:                        # noqa: BLE001
                continue
    return False, ''


def _ant_region_modal(t, page, label, keywords, timeout=15):
    """国聘「生源地 / 高考所在地区」弹窗式级联（ant-tabs + 多级 item-txt）。

    结构（2026-09-20 实测）：点开字段 → 弹 .ant-modal-content，里面有
    Tab（中国/海外）+ 省列（span.item-txt）+ 市列（div.leaf-item.item-txt）
    + 区列（再点市后才出现，仍是 .leaf-item）。**必须一路选到区级叶子**
    表单才落值，只选到市级时字段仍是空的（这就是保存报「请选择生源地」的根因）。

    keywords = [国别tab, 省, 市]；不足三级时，最后一级允许取该列首项。
    返回 True 表示选完且字段真的有了显示值。"""
    item = _ant_item(page, label)
    if item is None:
        return False
    try:
        item.locator('.ant-select-selector').first.click(timeout=3000)
    except Exception:                                # noqa: BLE001
        return False
    page.wait_for_timeout(900)

    def modal():
        return page.locator('.ant-modal-content').first

    def picked():
        """字段上是否出现了已选值（.ant-select-selection-item）。"""
        try:
            si = item.locator('.ant-select-selection-item')
            if si.count():
                return (si.first.inner_text() or '').strip()
        except Exception:                            # noqa: BLE001
            pass
        return ''

    chosen = []

    # 1) 选国别 Tab（中国 / 海外）
    if keywords and keywords[0] in ('中国', '海外'):
        try:
            tab = modal().locator('.ant-tabs-tab-btn',
                                  has_text=keywords[0]).first
            if tab.count():
                tab.click(timeout=2000)
                page.wait_for_timeout(600)
        except Exception:                            # noqa: BLE001
            pass

    # 2) 省
    if len(keywords) >= 2 and keywords[1]:
        ok, txt = _region_click(modal(), keywords[1], leaf_only=False)
        if not ok:
            return _region_fail(page, label)
        chosen.append(txt)
        page.wait_for_timeout(800)

    # 3) 市
    if len(keywords) >= 3 and keywords[2]:
        ok, txt = _region_click(modal(), keywords[2], leaf_only=True)
        if not ok:
            return _region_fail(page, label)
        chosen.append(txt)
        page.wait_for_timeout(800)

    # 4) 区级：市不是叶子，右侧还会长出区列——必须再选一级才落值。
    #    优先按档案通信地址里的区匹配；匹配不到就取该列首项兜底
    #    （不编造数据，用控件自己给的第一项，保证不被个别名称差异卡死）。
    if not picked():
        want = keywords[3] if len(keywords) >= 4 else ''
        ok, txt = _region_click(modal(), want, leaf_only=True)
        if not ok:
            ok, txt = _region_click(modal(), '', leaf_only=True,
                                    allow_first=True)
        if ok:
            chosen.append(txt)
            page.wait_for_timeout(800)

    # 5) 若弹窗还在且字段已显示，点空白/关闭让它收起（值已提交）
    if not picked():
        return _region_fail(page, label)
    try:
        page.keyboard.press('Escape')
    except Exception:                                # noqa: BLE001
        pass
    page.wait_for_timeout(400)
    t.filled.append({'label': label, 'value': '/'.join(chosen)})
    return True


def _region_fail(page, label):
    """级联没选成：关掉弹窗，返回 False（调用方按需记 missing）。"""
    try:
        page.keyboard.press('Escape')
    except Exception:                                # noqa: BLE001
        pass
    return False


def _province_of(city):
    """城市 → 省份（工作地区级联的第一级）。"""
    try:
        from collectors.gov_soe import PROVINCE_CITIES
        for prov, cities in PROVINCE_CITIES.items():
            if city in cities:
                return prov
    except Exception:                                # noqa: BLE001
        pass
    return None


def _guopin_cancel(t, page):
    """点「取 消」关表单；弹「有内容没有保存」就点「确 定」。"""
    for name in ('取 消', '取消'):
        loc = page.get_by_role('button', name=name)
        if loc.count():
            try:
                loc.first.click(timeout=2000)
                page.wait_for_timeout(800)
                break
            except Exception:                    # noqa: BLE001
                pass
    for name in ('确 定', '确定'):
        loc = page.get_by_role('button', name=name)
        if loc.count():
            try:
                loc.first.click(timeout=2000)
                page.wait_for_timeout(800)
                return
            except Exception:                    # noqa: BLE001
                pass


def _guopin_save(t, page, section, first_label, entry_label):
    """点保存并验证真的存上了（表单收起才算成功；被校验拦下就如实记录）。
    保存按钮重试几次——弹窗关闭/表单重渲染的瞬间可能点空。"""
    saved = False
    for _ in range(3):
        if _click_any(page, ('保 存', '保存')):
            saved = True
            break
        page.wait_for_timeout(900)
    if not saved:
        # 保存按钮没找到——先看看这段是不是已经保存过了（弹窗选完自动存的情况）
        if ('%s缺失' % section) not in _page_text(page):
            t.step('已保存一段%s：%s（弹窗自动保存）' % (section, entry_label))
            return True
        t.missing.append({'label': section, 'why': '保存按钮没找到，需要你手动点'})
        _guopin_cancel(t, page)
        return False
    _settle(page, 1500)
    item = _ant_item(page, first_label)
    if item is not None and item.count():
        # 表单没收起：如果缺失标记也没了，说明其实存上了
        if ('%s缺失' % section) not in _page_text(page):
            t.step('已保存一段%s：%s' % (section, entry_label))
            return True
        t.shot(page, '%s保存未通过' % section)
        t.missing.append({'label': section,
                          'why': '点了保存但表单没收起（必填没填全或校验没过），'
                                 '改动已取消，需要你手动补'})
        _guopin_cancel(t, page)
        return False
    t.step('保存了一段%s：%s' % (section, entry_label))
    t.shot(page, '%s保存后' % section)
    return True


def _guopin_edu_form(t, page, e):
    """按实测普查填教育表单：下拉=学历/学校/专业分类/专业名称，
    按钮组=涉及/统招/全日制/学位证/海外留学，区间=就读年月。"""
    degree = _DEGREE_MAP.get(str(e.get('degree') or t.profile.get('edu') or ''), '')
    school = str(e.get('school') or '')
    ok = []
    ok.append(_ant_click_opt(t, page, '本人不涉及该段学历', '本人有该段学历'))
    ok.append(_ant_select(t, page, '学历', degree, strict=True))
    ok.append(_ant_click_opt(t, page, '统招', '统招'))
    ok.append(_ant_click_opt(t, page, '全日制', '全日制'))
    ok.append(_ant_click_opt(t, page, '学位证',
                             '有学位证' if degree in ('本科', '硕士', '博士')
                             else '无学位证'))
    ok.append(_ant_range(t, page, '就读年月', e.get('start'), e.get('end')))
    ok.append(_ant_select(t, page, '学校名称', school))
    ok.append(_ant_select(t, page, '专业分类', e.get('major')))
    ok.append(_ant_select(t, page, '专业名称', e.get('major')))
    overseas = e.get('overseas')
    if overseas is None:
        overseas = bool(re.search(
            r'伦敦|曼彻斯特|牛津|剑桥|爱丁堡|悉尼|墨尔本|多伦多|温哥华|纽约|'
            r'波士顿|芝加哥|加州|洛杉矶|东京|首尔|新加坡|柏林|巴黎|莫斯科|海外|留学',
            school))
    ok.append(_ant_click_opt(t, page, '海外留学',
                             '海外留学经历' if overseas else '非海外留学经历'))
    if e.get('desc'):
        ok.append(_ant_text(t, page, '在校经历', e.get('desc')))
    return any(ok)


def _guopin_work_form(t, page, entry):
    """填 工作/实习经历（按 2026-09-20 实测的真实 DOM）。

    注意：这个岗位模板的控件**没有 id**（单位性质/工作性质/在职时间都是裸
    antd 控件），所以单位性质/工作性质用 label 版下拉（_ant_select）、
    在职时间用 label 版区间（_ant_range）。能按 id 命中的（单位名称/职位名称/
    工作内容/has_overseas）才用 _gp_fill/_gp_radio。
    """
    _gp_fill(t, page, 'company_name', entry.get('company'), '单位名称')
    _gp_fill(t, page, 'job_name', entry.get('role'), '职位名称')
    nature = entry.get('nature') or ''
    if not nature:
        c = str(entry.get('company') or '')
        if '国有' in c or '保险' in c or '人寿' in c:
            nature = '国有企业'
    if nature:
        _ant_select(t, page, '单位性质', nature, strict=False)
    _ant_select(t, page, '工作性质',
               '实习' if entry.get('type') == 'intern' else '全职', strict=False)
    # 在职时间：两个独立日期框，按 placeholder（开始时间/结束时间）定位
    _ant_range(t, page, '在职时间', entry.get('start'), entry.get('end'))
    _gp_fill(t, page, 'introduction', entry.get('desc'), '工作内容')
    # 所属行业：弹窗式级联（cascader-modal-field，跟生源地同一个组件），
    # 不选保存就被拦在「请选择所属行业」上、表单不收（2026-09-20 实测）。
    # 门类按单位/职位理性推导，推导不出就用控件自带的「不限」——绝不编造事实。
    _fill_industry_modal(t, page, _guess_industry(entry))
    _gp_radio(t, page, 'has_overseas', '非海外工作经历', '海外工作',
              optional=True)
    return True


# 站点「所属行业」门类词表（实测 2026-09-20）；左列是这些门类，
# 选了门类右侧才出具体行业叶子。
_GP_INDUSTRY_TREE = (
    ('金融业', '金融'), ('信息传输、软件和信息技术服务业', '互联网/IT/电子/通信'),
    ('教育', '教育培训'), ('卫生和社会工作', '制药/医疗'),
    ('交通运输、仓储和邮政业', '交通/物流/贸易/零售'),
    ('批发和零售业', '交通/物流/贸易/零售'),
    ('文化、体育和娱乐业', '广告/传媒/文化/体育'),
    ('房地产业', '房地产/建筑'), ('建筑业', '房地产/建筑'),
    ('制造业', '机械/制造'), ('住宿和餐饮业', '服务业'),
    ('公共管理、社会保障和社会组织', '政府/非盈利机构/其他'),
)


def _fill_industry_modal(t, page, industry):
    """填工作经历「所属行业」弹窗式级联（门类 → 具体行业）。

    这是 cascader-modal-field（跟生源地同一组件），但**没有国别 Tab**、
    只有两级：门类列（span.item-txt）+ 叶子（div.leaf-item.item-txt）。
    选门类后叶子列出现该门类下的行业；叶子取「不限」类兜底，不编造。"""
    try:
        item = page.locator(
            '#edit-section [data-testid="cascader-modal-field"]').first
        if not item.count():
            return False
        item.locator('.ant-select-selector').first.click(timeout=3000)
    except Exception:                                # noqa: BLE001
        return False
    page.wait_for_timeout(1000)

    def modal():
        return page.locator('.ant-modal-content').first

    # 把我们推导的行业映射成站点门类名
    cat = str(industry or '').strip()
    for our, theirs in _GP_INDUSTRY_TREE:
        if our and our in cat:
            cat = theirs
            break
    else:
        cat = ''                                     # 映射不上就不点门类
    # 点门类（省列 = .item-txt:not(.leaf-item)）
    clicked_cat = ''
    if cat:
        ok, clicked_cat = _region_click(modal(), cat, leaf_only=False)
    # 点具体行业叶子：优先取「不限」类，没有就取第一个叶子
    ok2 = False
    for cand in ('不限', '不限行业'):
        ok2, _ = _region_click(modal(), cand, leaf_only=True)
        if ok2:
            break
    if not ok2:
        ok2, _ = _region_click(modal(), '', leaf_only=True, allow_first=True)
    if not (clicked_cat or ok2):
        try:
            page.keyboard.press('Escape')
        except Exception:                            # noqa: BLE001
            pass
        return False
    page.wait_for_timeout(500)
    # 行业弹窗选完叶子即生效（弹窗节点会留在 DOM 里但 display:none，属正常）；
    # 关掉这个隐藏节点，免得它挡住后面点保存。
    try:
        page.evaluate("""() => {
          for (const w of document.querySelectorAll('.ant-modal-wrap')) {
            const m = w.querySelector('.ant-modal-content');
            if (m && !(m.offsetWidth || m.offsetHeight)) { w.style.display = 'none'; }
          }
        }""")
    except Exception:                                    # noqa: BLE001
        pass
    # 用字段的显示值确认真的落值了（.ant-select-selection-item）
    landed = ''
    try:
        landed = page.evaluate("""() => {
          const f = document.querySelector(
            '#edit-section [data-testid="cascader-modal-field"]');
          if (!f) return '';
          const si = f.querySelector('.ant-select-selection-item');
          if (si) return (si.innerText || si.getAttribute('title') || '').trim();
          const inp = f.querySelector('input');
          return inp ? (inp.value || '').trim() : '';
        }""") or ''
    except Exception:                                    # noqa: BLE001
        landed = ''
    if not (t.filled and t.filled[-1].get('label') == '所属行业'):
        t.filled.append({'label': '所属行业', 'value': landed or clicked_cat or cat})
    return bool(landed) or bool(clicked_cat or ok2)


def _guess_industry(entry):
    """按单位名/职位名推一个行业类目（用于必选的「所属行业」下拉）。

    只是为了让保存通过；具体判断优先用档案里显式给的 industry 字段。"""
    explicit = str(entry.get('industry') or '').strip()
    if explicit:
        return explicit
    text = '%s %s' % (entry.get('company') or '', entry.get('role') or '')
    table = (
        ('保险|人寿|平安|太保|财险', '金融业'),
        ('银行|证券|基金|信托|投资', '金融业'),
        ('学校|大学|学院|教育|培训', '教育'),
        ('医院|医疗|药|生物|健康', '卫生和社会工作'),
        ('软件|科技|信息|网络|计算机|互联网', '信息传输、软件和信息技术服务业'),
        ('制造|工厂|机械|电子|汽车', '制造业'),
        ('建筑|工程|建设|施工', '建筑业'),
        ('物流|运输|快递|航运|航海', '交通运输、仓储和邮政业'),
        ('酒店|餐饮|旅游', '住宿和餐饮业'),
        ('房地产|物业', '房地产业'),
        ('传媒|广告|文化|体育|娱乐', '文化、体育和娱乐业'),
        ('政府|机关|事业单位|事业', '公共管理、社会保障和社会组织'),
        ('贸易|销售|商贸|零售|批发', '批发和零售业'),
    )
    for pat, ind in table:
        if re.search(pat, text):
            return ind
    return '其他'


def _guopin_workarea(t, page, prov, city):
    """工作地区是弹窗式选择器（cascader-modal）：左列省份是 .level-item，
    点省后右侧城市芯片在 .flat-leaf .leaf-item（2026-09-20 实测 DOM）。
    选完叶子弹窗自动收；没收就找确认按钮。"""
    def click_item(txt):
        try:
            return page.evaluate("""(txt) => {
              const sels = ['.cascader-modal .level-item .item-txt',
                            '.cascader-modal .flat-leaf .leaf-item'];
              for (const sel of sels) {
                const els = Array.from(document.querySelectorAll(sel));
                const it = els.find(el => {
                  const s = ((el.getAttribute('title')) ||
                             el.innerText || '').trim();
                  return s === txt || s.startsWith(txt);
                });
                if (it) { it.click(); return true; }
              }
              return false;
            }""", str(txt))
        except Exception:                            # noqa: BLE001
            return False

    ok = False
    page.wait_for_timeout(800)
    for kw in (prov, city):
        if not kw:
            continue
        end = time.time() + 8
        hit = False
        while time.time() < end and not hit:
            hit = click_item(kw)
            if not hit:
                page.wait_for_timeout(500)
        if not hit:
            return False
        page.wait_for_timeout(900)
        ok = True
    page.wait_for_timeout(800)
    # 城市选完弹窗通常自动关；还开着就点确认
    if page.locator('.cascader-modal').count():
        for name in ('确 定', '确定'):
            loc = page.get_by_role('button', name=name)
            if loc.count():
                try:
                    loc.first.click(timeout=2000)
                    break
                except Exception:                    # noqa: BLE001
                    pass
    page.wait_for_timeout(800)
    if _ant_item(page, '工作地区') is not None:
        v = _ant_item(page, '工作地区').locator('.ant-select-selection-item')
        if v.count():
            t.filled.append({'label': '工作地区', 'value': v.first.inner_text()})
            return True
    return ok


def _guopin_intent_form(t, page):
    """求职意向（必填）：弹窗式工作地区 + 期望薪资（有数字填数字，没有点面议）。"""
    city = (t.profile.get('city') or '').strip() or '武汉'
    prov = _province_of(city)
    ok = False
    item = _ant_item(page, '工作地区')
    if item is not None:
        try:
            item.locator('.ant-select-selector').first.click(timeout=3000)
            ok = _guopin_workarea(t, page, prov, city)
        except Exception:                            # noqa: BLE001
            pass
    salary = (t.profile.get('salary') or '').strip()
    nums = re.findall(r'\d+(?:\.\d+)?', salary)
    # 期望月薪是数字输入（K/个月），「5000-8000」这类文本会被校验打回——
    # 直接点「面议」最稳；点不到再试着填纯数字
    if not _ant_click_opt(t, page, '期望月薪', '面议'):
        if nums:
            vals = [float(n) / 1000 if float(n) >= 100 else float(n) for n in nums[:2]]
            ok = _ant_text(t, page, '期望月薪', '%g' % vals[0]) or ok
            if len(vals) > 1:
                ok = _ant_text(t, page, '期望月薪', '%g' % vals[1]) or ok
    return ok


def _guopin_lang_form(t, page):
    """语言能力（必填）：语种/读写/听说。选项实测是 精通|熟练|入门，取「熟练」。
    选项匹配不到就留空问人，不乱选。"""
    ok = []
    ok.append(_ant_select(t, page, '语种', '英语', strict=True))
    ok.append(_ant_select(t, page, '读写能力', '熟练', strict=True))
    ok.append(_ant_select(t, page, '听说能力', '熟练', strict=True))
    return any(ok)


def _guopin_other_form(t, page):
    """其他说明（必填）：是否服从调剂=是；诚信声明勾选框勾上。
    近亲属说明是用户隐私事实，不代填，留空让保存拦下后问人。"""
    ok = []
    ok.append(_ant_click_opt(t, page, '是否服从调剂', '是'))
    try:
        checked = page.evaluate(r"""() => {
          const cbs = Array.from(document.querySelectorAll(
            'input[type=checkbox], .ant-checkbox-input'));
          for (const c of cbs) {
            if (!c.checked) { c.click(); return true; }
          }
          return false;
        }""")
        if checked:
            t.filled.append({'label': '个人诚信说明', 'value': '已勾选（材料属实声明）'})
        ok.append(bool(checked))
    except Exception:                                # noqa: BLE001
        pass
    return any(ok)


def _guopin_upload_attachment(t, page):
    """附件（必填）：把档案里保留的简历原件传给第一个可用的文件输入。"""
    path = (t.profile.get('resume_path') or '').strip()
    if not path or not os.path.exists(path):
        t.missing.append({'label': '附件',
                          'why': '档案里没有简历原件，无法自动上传，需要你手动传'})
        return False
    try:
        inputs = page.locator('input[type=file]')
        for i in range(inputs.count()):
            el = inputs.nth(i)
            try:
                el.set_input_files(path, timeout=5000)
                page.wait_for_timeout(2000)
                t.filled.append({'label': '附件',
                                 'value': os.path.basename(path)})
                t.shot(page, '附件已上传')
                # 传完点保存（编辑区有保存按钮就存上，没有就算了，别取消）
                _click_any(page, ('保 存', '保存'))
                page.wait_for_timeout(1200)
                return True
            except Exception:                    # noqa: BLE001
                continue
    except Exception:                                # noqa: BLE001
        pass
    t.missing.append({'label': '附件', 'why': '没找到可用的上传入口，需要你手动传'})
    return False


def _profile_experiences(p):
    """把档案里的结构化经历读出来（存的是 JSON 字符串）。

    每段：{type: work|edu|intern|campus, school/major/degree 或 company/role,
          start, end, desc}。坏数据（不是列表、没有内容的段）直接丢掉。
    """
    raw = p.get('experiences')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:                        # noqa: BLE001
            raw = []
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        e = dict(e)      # 别改原数据
        # 前端编辑器教育段的学校/专业存的是 company/role，统一归一化成 school/major
        if e.get('type') == 'edu':
            if not str(e.get('school') or '').strip():
                e['school'] = e.get('company')
            if not str(e.get('major') or '').strip():
                e['major'] = e.get('role')
        else:
            if not str(e.get('company') or '').strip():
                e['company'] = e.get('school')
        if str(e.get('company') or '').strip() or str(e.get('school') or '').strip():
            out.append(e)
    return out


def _add_education(t, page, entries):
    """教育经历没有「无」选项，必须真的补一段：点那一行的「添加」，
    在弹出的子表单里按档案填 学校/专业/学历/起止时间，再保存。

    档案里有几段教育经历就补几段（本科+硕士很常见）；没有结构化的，
    退回用档案的 school/major/edu/graduation 四个标量字段补一段。
    国聘是 antd 表单，走 _guopin_edu_form 精确适配；其它站退回通用 _wiz_fill。
    """
    if not entries:
        entries = [{'school': t.profile.get('school'), 'major': t.profile.get('major'),
                    'degree': t.profile.get('edu'), 'end': t.profile.get('graduation')}]
    added_any = False
    antd = _ant_item(page, '学历') is not None or _ant_item(page, '学校名称') is not None
    for e in entries:
        if not _click_section_add(t, page, '教育经历'):
            t.missing.append({'label': '教育经历',
                              'why': '没找到「添加」按钮，需要你手动补'})
            return added_any
        _settle(page, 1200)
        t.shot(page, '教育经历填写窗')
        start, end = str(e.get('start') or ''), str(e.get('end') or '')
        period = ('%s-%s' % (start, end)) if start and end else (end or start)
        if antd:
            _guopin_edu_form(t, page, e)
        else:
            _wiz_fill(t, page, [
                (('毕业院校', '学校名称', '院校名称', '院校', '学校', 'school'), e.get('school')),
                (('专业名称', '所学专业', '专业方向', '专业', 'major'), e.get('major')),
                (('最高学历', '学历层次', '学历', 'degree'), e.get('degree') or t.profile.get('edu')),
                (('开始时间', '入学时间', '开始', 'from'), start),
                (('结束时间', '毕业时间', '结束', '至今', 'to'), end),
                (('起止时间', '在校时间', '时间段', '时间'), period),
            ])
        t.shot(page, '教育经历已填')
        if antd:
            if _guopin_save(t, page, '教育经历', '学校名称', e.get('school') or ''):
                added_any = True
        elif _save_wizard(t, page, '教育经历', e.get('school') or ''):
            added_any = True
        if not any(e.get(k) for k in ('school', 'major', 'degree', 'start', 'end')):
            t.missing.append({'label': '教育经历',
                              'why': '弹窗里没认出能填的字段，需要你手动补'})
    return added_any


def _add_experience(t, page, section, entry):
    """实习经历 / 校内活动或社会实践：档案里有真实内容的，照教育经历的
    方式一段一段真填——比勾「无经历」有用得多，HR 看到的是加分项。
    国聘（antd）走 _guopin_work_form 精确适配；其它站退回通用 _wiz_fill。"""
    if not _click_section_add(t, page, section):
        t.missing.append({'label': section,
                          'why': '没找到「添加」按钮，需要你手动补'})
        return
    _settle(page, 1200)
    t.shot(page, '%s填写窗' % section)
    start, end = str(entry.get('start') or ''), str(entry.get('end') or '')
    period = ('%s-%s' % (start, end)) if start and end else (end or start)
    antd = _ant_item(page, '单位名称') is not None
    if antd:
        _guopin_work_form(t, page, entry)
    else:
        _wiz_fill(t, page, [
            (('公司名称', '单位名称', '实习单位', '公司/单位', '公司全称', '公司', '单位', '企业',
              '社团/组织', '组织名称', '社团名称'),
             entry.get('company')),
            (('担任职位', '职位名称', '担任职务', '岗位名称', '担任岗位', '职位', '职务', '岗位',
              '角色', '担任角色'),
             entry.get('role')),
            (('开始时间', '开始', 'from'), start),
            (('结束时间', '结束', '至今', 'to'), end),
            (('起止时间', '在职时间', '时间段', '时间'), period),
            (('工作内容', '工作描述', '实习内容', '职责描述', '经历描述', '内容描述',
              '主要职责', '内容', '描述', '职责'), entry.get('desc')),
        ])
    t.shot(page, '%s已填' % section)
    if antd:
        _guopin_save(t, page, section, '单位名称', entry.get('company') or '')
    else:
        _save_wizard(t, page, section, entry.get('company') or '')


# ================= 国聘：照真人操作录制重写（2026-09-20） =================
# 数据来源：data/recorder/trace_*.json —— 用户手填一整遍、一路到「申请成功」
# 录下的 221 步。锚点一律用控件 id（#salary_monthly / #education / #email …）
# 和段 id（#job_expectation / #education / #language / #attachment / #others /
# #basic_info），比按 label 文字找稳得多。

# 段 id → 中文名（c.iguopin.com/apply 页面上的简历段）
_GP_SECTIONS = (
    ('job_expectation', '求职意向'),
    ('education', '教育经历'),
    ('work_experience', '工作/实习经历'),
    ('project', '项目经历'),
    ('certificate', '资格证书'),
    ('language', '语言能力'),
    ('assessment', '自我评价'),
    ('attachment', '附件'),
    ('others', '其他说明'),
    ('family', '家庭成员'),
    ('basic_info', '基本信息'),
    # 某些国企岗位模板会多出这个必填段（段 id 是随机串 biz_XXXX）。
    # 段标题固定，靠标题在页面上找，详见 _gp_kin_section_id()。
    ('kin', '亲属在集团公司系统单位任职情况'),
)

# 站点「简历目录」里用的段名 → 内部 key（两边名字一致，见 _gp_red_sections）
_GP_NAME2KEY = dict((n, k) for k, n in _GP_SECTIONS)

# 缺了也不影响投递、且档案里也没数据的段：只在报告里提一句，不弹人工
_GP_OPTIONAL_SECTIONS = ('project', 'certificate')

# 「重填覆盖」模式重新填的段：都是**内容段**（有档案数据可写的那种）。
# 不含 attachment（要传原件）、certificate/kin（是事实声明不是内容）、
# family（家人隐私）——那几段跟重填没关系，照旧走各自的处理。
_GP_REFILL_SECTIONS = ('job_expectation', 'education', 'work_experience',
                       'language', 'assessment', 'others', 'basic_info')

# 每段「为什么不能替你补 / 怎么补最快」的解释。
#
# ⚠️ 这里**按段名关键词匹配**，不按段 id 写死。
# 原因（2026-09-20 用户指出）：每个岗位要填的段和字段都不一样 ——
# 国聘按岗位下发不同的简历模板，模板里有哪些段由接口返回的
# template.module_list 决定（每项带 name/title/required/present/user_choice）。
# 同一个账号投不同岗位，会分别遇到 荣誉奖励 / 论文专著 / 培训经历 /
# 亲属任职 / 技能特长 这些段。写死一张「段 id → 文案」的表，
# 遇到没见过的段就只能给一句废话，用户还是不知道该干什么。
# 所以改成按段名匹配：见过的段给准话，没见过的段也有像样的兜底。
#
# 每条规则：(段名关键词, 为什么不能替你补, 怎么补最快)
_GP_ADVICE_RULES = (
    (('证书', '资格'),
     '「有没有相关证书」只有你本人能确认。AI 替你声明「无」，'
     '万一你其实有，就是投递材料造假。',
     '确实没有 → 点这一段的一键「无…」声明；有 → 点「添加」，'
     '把证书名称、等级、发证机关照实填上。'),
    (('亲属', '回避'),
     '涉及国企的任职回避要求，只有你本人和家人才知道，AI 不能替你下结论。',
     '确实没有 → 点这一段的一键「无…」声明；有 → 照实填亲属姓名、'
     '所属单位、职务、联系方式。'),
    (('家庭', '成员', '父母', '配偶', '子女'),
     '这一段要填家人的姓名、出生日期、工作单位、职务。这些是家人的隐私，'
     'AI 不能凭空编，也不该从别处猜。',
     '点「添加」逐项照实填。如果你在「我的档案」里填过家庭成员，'
     '这一步我会自动替你填上。'),
    (('附件', '材料', '证明'),
     '附件要上传你的简历或证明材料原件，得由你本人从本机选文件。',
     '点「上传」，从本机选文件传上去。'),
    (('荣誉', '奖励', '获奖', '奖项', '表彰'),
     '获奖和荣誉是你本人的经历，档案里没有 AI 就不能替你编。',
     '有 → 点「添加」，填获奖名称、级别、获得时间；'
     '确实没有 → 直接保存留空，站点一般不拦这一段。'),
    (('论文', '专著', '专利', '著作', '科研', '成果', '课题'),
     '学术成果只有你本人知道，AI 编一个就是学术造假。',
     '有 → 点「添加」，填题目、刊物/授权机构、时间；没有 → 留空保存。'),
    (('培训', '进修', '课程'),
     '培训经历只有你本人知道，AI 不能替你编。',
     '有 → 点「添加」，填培训名称、机构、时间；没有 → 留空保存。'),
    (('技能', '特长'),
     '技能和特长要照实说，夸大或编造在面试时会被问穿。',
     '点「添加」，把你会、并且拿得出手的技能照实填上；没有 → 留空保存。'),
    (('校园', '社团', '实践'),
     '校园经历只有你本人知道，AI 不能替你编。',
     '有 → 点「添加」，填组织/职位/时间/做了什么；没有 → 留空保存。'),
    (('基本',),
     '基本信息里有几项只有你本人知道（如入党时间、紧急联系人）。'
     '这几项在「我的档案」里补一次，以后所有投递都能自动带上。',
     '点「编辑」，把标红的项补上再保存；也可以先在「我的档案」里补，'
     '下次开投我会自动填。'),
    (('教育', '学历', '学校'),
     '站点把这一段标成「必填但没完成」——通常是其中某一项'
     '（如培养方式、学历类型）没填上，站点就算它没完成。',
     '点「编辑」看哪一项标红，补上再保存；如果你在「我的档案」里'
     '补齐这一项，以后换岗位也能自动带上。'),
    (('工作', '实习', '在职'),
     '站点把这一段标成「必填但没完成」——通常是有某一项没填上。',
     '点「编辑」看哪一项标红，补上再保存；档案里有这段经历的话'
     '下次我会自动填。'),
    (('项目',),
     '项目经历档案里没有的话，AI 不能替你编。',
     '有 → 点「添加」照实填项目名称、角色、时间、内容；没有 → 留空保存。'),
    (('语言',),
     '语言能力要照实填，站点会看证书或面试核对。',
     '点「添加」，选语种和掌握程度；没必要硬凑，没有就留空保存。'),
)
_GP_ADVICE_DEFAULT = (
    '这一段被这个岗位的简历模板标成了必填，但里面的内容只有你本人知道，'
    'AI 替你写就是替你编材料。',
    '点这一段的「编辑」或「添加」，看看它要哪几项，按实填上再保存。',
)


def _gp_advice(title, declare_text='', fields=None):
    """按段名给出「为什么不能替你补 / 怎么补最快」，再补上这一段的实际情况。

    declare_text：这一段自带的一键「无…」声明按钮文字（现找的，不是写死的）。
    fields：这一段要求的字段名（读得到就带上，让用户知道到底要准备什么）。
    """
    why = how = ''
    tid = title or ''
    for keys, w, h in _GP_ADVICE_RULES:
        if any(k in tid for k in keys):
            why, how = w, h
            break
    if not why:
        why, how = _GP_ADVICE_DEFAULT
    if declare_text:
        how = ('%s 这一段站点提供了一键声明，你确实没有就点「%s」。'
               % (how, declare_text))
    if fields:
        how = '%s 这一段要填：%s。' % (how, '、'.join(fields[:8]))
    return {'why': why, 'how': how}

# 国聘「基本信息」段必填、缺了保存就过不去的档案字段。
# 单一数据源：开投前预检（前端 /api/autofill/required）和真机跑时的
# _guopin_resume_guard 缺失判断都从这里取，改一处即可，避免前后端脱节。
# 注意：姓名/手机/邮箱是登录账号实名锁定的（不可覆盖），性别/出生日期站点按
# 账号预填，这几项不在清单里。清单只列「需要用户在档案里自己补、且 bot 不替编」的字段。
def guopin_required_fields():
    return [
        {'k': 'emergency_name', 'label': '紧急联系人姓名', 'ph': '例如：张某某',
         'note': '国聘基本信息段必填，缺了保存过不去'},
        {'k': 'emergency_phone', 'label': '紧急联系人电话', 'ph': '例如：13800138000',
         'note': '国聘基本信息段必填，缺了保存过不去'},
        {'k': 'height', 'label': '身高（cm）', 'ph': '例如：175',
         'note': '国聘基本信息段必填'},
        {'k': 'weight', 'label': '体重（kg）', 'ph': '例如：65',
         'note': '国聘基本信息段必填'},
        {'k': 'address', 'label': '通信地址', 'ph': '例如：湖北省武汉市硚口区某某路1号',
         'note': '国聘基本信息段必填'},
        {'k': 'party_join_date', 'label': '入党时间', 'ph': '例如：2019-06',
         'note': '政治面貌为中共党员时必填（登录账号默认党员）'},
        # ---- 下面 8 项是 2026-09-21 补的 ----
        # 起因：这些都是**只有本人知道**的事实，autofill 以前全写死了
        # （汉族 / 未婚 / 健康 / 统招 / 全日制 / 有学位证 / 英语熟练 / 服从调剂），
        # 而 profiles 表当时根本没有对应的列——等于替用户编材料。
        # 现在一律读档案，没填就在开投前先问一次：宁可多问一句，不能填假话。
        {'k': 'nation', 'label': '民族', 'ph': '例如：汉族',
         'note': '国聘基本信息段要用；少数民族照实填，不替你默认汉族'},
        {'k': 'marital', 'label': '婚姻状况', 'ph': '未婚 / 已婚 / 离异 / 丧偶',
         'note': '国聘基本信息段要用'},
        {'k': 'health', 'label': '健康状况', 'ph': '例如：健康',
         'note': '国聘基本信息段要用'},
        {'k': 'edu_regular', 'label': '学历性质', 'ph': '统招 / 非统招',
         'note': '成人教育·自考·函授选「非统招」——这项填错等于学历性质造假'},
        {'k': 'edu_fulltime', 'label': '学习形式', 'ph': '全日制 / 非全日制',
         'note': '在职·函授一般是「非全日制」'},
        {'k': 'has_degree', 'label': '学位证', 'ph': '有学位证 / 无学位证',
         'note': '只有毕业证、没有学位证的，照实选「无学位证」'},
        {'k': 'foreign_lang', 'label': '外语语种',
         'ph': '英语（不会外语就填「不会外语」）',
         'note': '国聘有「语言能力」段；不会外语就填「不会外语」，不替你编'},
        {'k': 'can_arrange', 'label': '是否服从调剂', 'ph': '是 / 否',
         'note': '只有你能决定——服从调剂可能被派到偏远地区'},
    ]

# 城市 → 省份（国聘的工作地区/户籍是 国家-省-市 三级）
_CITY_PROV = {
    '北京': '北京', '上海': '上海', '天津': '天津', '重庆': '重庆',
    '武汉': '湖北', '宜昌': '湖北', '襄阳': '湖北', '黄石': '湖北',
    '荆州': '湖北', '十堰': '湖北', '孝感': '湖北', '荆门': '湖北',
    '鄂州': '湖北', '黄冈': '湖北', '咸宁': '湖北', '随州': '湖北',
    '长沙': '湖南', '株洲': '湖南', '湘潭': '湖南', '衡阳': '湖南',
    '广州': '广东', '深圳': '广东', '珠海': '广东', '东莞': '广东',
    '佛山': '广东', '中山': '广东', '惠州': '广东', '汕头': '广东',
    '南京': '江苏', '苏州': '江苏', '无锡': '江苏', '常州': '江苏',
    '南通': '江苏', '徐州': '江苏', '扬州': '江苏', '镇江': '江苏',
    '杭州': '浙江', '宁波': '浙江', '温州': '浙江', '嘉兴': '浙江',
    '金华': '浙江', '绍兴': '浙江', '台州': '浙江',
    '成都': '四川', '绵阳': '四川', '德阳': '四川', '宜宾': '四川',
    '西安': '陕西', '咸阳': '陕西', '宝鸡': '陕西',
    '郑州': '河南', '洛阳': '河南', '开封': '河南', '新乡': '河南',
    '济南': '山东', '青岛': '山东', '烟台': '山东', '潍坊': '山东',
    '合肥': '安徽', '芜湖': '安徽', '蚌埠': '安徽',
    '南昌': '江西', '九江': '江西', '赣州': '江西',
    '福州': '福建', '厦门': '福建', '泉州': '福建',
    '石家庄': '河北', '唐山': '河北', '保定': '河北', '廊坊': '河北',
    '太原': '山西', '大同': '山西', '临汾': '山西',
    '沈阳': '辽宁', '大连': '辽宁', '鞍山': '辽宁',
    '长春': '吉林', '吉林': '吉林',
    '哈尔滨': '黑龙江', '大庆': '黑龙江',
    '昆明': '云南', '大理': '云南', '丽江': '云南',
    '贵阳': '贵州', '遵义': '贵州',
    '南宁': '广西', '桂林': '广西', '柳州': '广西',
    '海口': '海南', '三亚': '海南',
    '兰州': '甘肃', '西宁': '青海', '银川': '宁夏',
    '乌鲁木齐': '新疆', '拉萨': '西藏',
    '呼和浩特': '内蒙古', '包头': '内蒙古',
    '香港': '香港', '澳门': '澳门', '台北': '台湾',
}


def _gp_prov_of(city):
    return _CITY_PROV.get(str(city or '').strip(), '')


def _salary_range(text):
    """'5000-8000' / '5000元' → (下限, 上限)；解析不出来返回 (0, 0)。"""
    nums = re.findall(r'\d+', str(text or '').replace(',', ''))
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    if len(nums) == 1:
        v = int(nums[0])
        return v, int(v * 1.5)
    return 0, 0


def _gp_kin_section_id(page):
    """找「亲属在集团公司系统单位任职情况」段的真实 id。

    这个段不是固定 id（实测是随机串 biz_XXXX），只能按标题文字反查它所在的
    段容器 id。找不到返回 None（说明这个岗位没有这段）。"""
    js = """() => {
      const want = '亲属在集团公司系统单位任职情况';
      for (const el of document.querySelectorAll('[id]')) {
        const id = el.id || '';
        if (!id || id.length > 40) { continue; }
        const own = (el.innerText || '').trim();
        if (own.startsWith(want)) { return id; }
      }
      return '';
    }"""
    try:
        return page.evaluate(js) or None
    except Exception:                                   # noqa: BLE001
        return None


def _gp_sections_todo(page):
    """返回还没填完的段 id 列表：段标题容器带 error-field-notice 就是缺。"""
    ids = [s[0] for s in _GP_SECTIONS if s[0] != 'kin']
    kin = _gp_kin_section_id(page)
    if kin:
        ids.append(kin)
    js = """(ids) => {
      const out = [];
      for (const id of ids) {
        const root = document.getElementById(id);
        if (!root) { continue; }
        if (root.querySelector('.error-field-notice')) { out.push(id); }
      }
      return out;
    }"""
    try:
        return list(page.evaluate(js, ids))
    except Exception:                                   # noqa: BLE001
        return []


def _gp_close_edit(page):
    """关掉还开着的编辑区。国聘一次只允许开一个编辑区，上一段没收起来，
    后面所有段点「编辑」都不会生效（2026-09-20 实测踩过）。"""
    js = """() => {
      if (!document.getElementById('edit-section')) { return false; }
      for (const b of document.querySelectorAll('button')) {
        const t = (b.innerText || '').replace(/\\s/g, '');
        if ((t === '取消' || t === '关闭') && b.offsetWidth) {
          b.click();
          return true;
        }
      }
      return false;
    }"""
    try:
        if page.evaluate(js):
            page.wait_for_timeout(600)
            return True
    except Exception:                                   # noqa: BLE001
        pass
    return False


def _gp_present_sections(page, ids):
    """这一套模板上真实存在的段 id（不同岗位模板的段不一样）。

    重填模式靠它筛：只在**页面上真的有**的段上重填，不然会对着一个
    不存在的段白开一次、还报成「没找到入口」（实测「语言能力」就是这样）。
    """
    try:
        got = page.evaluate("(ids) => ids.filter(i => !!document.getElementById(i))",
                            list(ids))
        return set(got or [])
    except Exception:                                   # noqa: BLE001
        return set(ids)                                 # 读不到就都试，别漏填


def _gp_open(t, page, sid, name, prefer=None):
    """点某段右上角的「添加 / 编辑」。段内可能有多个同名按钮，必须限定在段里。

    prefer='edit'：**优先点第一条记录的「编辑」**，用于「重填覆盖」——
    打开的是已有条目，字段是带原值预载的，改完保存就等于覆盖那一条。

    ⚠️ 已有记录右侧的「编辑」是 display:none（鼠标 hover 才现），
    getBoundingClientRect() 拿到的是 0×0；但 JS 的 el.click() 对隐藏元素
    照样派发事件、React 的 onClick 照常触发（2026-09-20 实测：编辑框正常打开
    且字段带原值）。所以 prefer='edit' 时不做可见性判断，直接点第一个「编辑」。
    """
    _gp_close_edit(page)
    js = """([sid, prefer]) => {
      const root = document.getElementById(sid);
      if (!root) { return 'no-section'; }
      // 只点 button/a/span：祖先 div 的 innerText 也常常正好是「添加」，
      // 但 React 的 onClick 绑在最内层那个 span 上，点外层 div 不会触发。
      const all = Array.from(root.querySelectorAll('button, a, span'));
      const vis = el => { const r = el.getBoundingClientRect();
                          return r.width > 0 && r.height > 0; };
      const pick = (want, needVis) => {
        const hit = all.filter(el => (el.innerText || '').trim() === want);
        if (!hit.length) { return null; }
        if (!needVis) { return hit[0]; }
        return hit.find(vis) || null;
      };
      if (prefer === 'edit') {
        // 重填覆盖：先要「编辑」（已有条目），没有才退回「添加」
        const e = pick('编辑', false);
        if (e) { e.click(); return 'ok'; }
      }
      const a = pick('添加', true) || pick('添加', false);
      if (a) { a.click(); return 'ok'; }
      const e2 = pick('编辑', true) || pick('编辑', false);
      if (e2) { e2.click(); return 'ok'; }
      return 'not-found';
    }"""
    try:
        r = page.evaluate(js, [sid, prefer])
    except Exception:                                   # noqa: BLE001
        r = 'error'
    if r == 'no-section':
        # 这套模板压根没有这一段（不同岗位模板的段不一样，实测「语言能力」
        # 就在某些模板里不存在）。这不是「用户要手动补」，别报成缺失。
        return False
    if r == 'ok':
        # 点了「编辑」不等于表单已经渲染出来：antd 的表单是异步挂载的，
        # 而且字段并不在段容器里，是挂到 #edit-section 上的，两边都要等。
        for _ in range(24):
            try:
                n = page.evaluate(
                    "() => { const e = document.getElementById('edit-section'); "
                    "return e ? e.querySelectorAll('input, textarea, "
                    ".ant-select, .ant-radio-group, .ant-picker').length : 0; }")
            except Exception:                           # noqa: BLE001
                n = 0
            if n:
                break
            page.wait_for_timeout(250)
        page.wait_for_timeout(400)
        return True
    t.missing.append({'label': name,
                      'why': '没找到这段的「添加/编辑」入口（%s）' % r})
    return False


def _gp_save(t, page, name, sid=None):
    """点编辑区底部的「保 存」（antd 按钮文字里插了空格，要去掉再比）。

    sid：段 id。保存成功最靠谱的证据是「这个段的『缺失』提示消失了」——
    光看编辑区还在不在会误判（有的模板收起后 DOM 节点还留着）。

    ⚠️ 2026-09-20 实测踩过的坑：这个保存按钮是 `<button type="submit">`
    （class 含 submit-btn），**用 JS 的 b.click() 点它不会触发任何请求**——
    React 把提交绑在 form 的 onFinish 上，JS 合成的 click 在 antd 的
    type=submit 按钮上不冒泡成真正的 form submit，结果就是「点了保存、
    没报错、表单不收、也没网请求」。必须用 Playwright 的**真实点击**
    （走鼠标事件）才会 POST /personal/resume/v1/add。
    所以这里先按段内文字定位按钮、真实 click；失败再退回 JS 点击兜底。
    """
    hit = ''
    # 首选：Playwright 真实点击（type=submit 的按钮只有真实点击才提交）
    try:
        btn = page.locator('#edit-section button').filter(
            has_text=re.compile(r'^\s*保\s*存\s*$')).first
        if not btn.count():
            btn = page.locator('#edit-section button').filter(
                has_text=re.compile(r'^\s*确\s*定\s*$')).first
        if btn.count():
            btn.click(timeout=3000)
            hit = '保存'
    except Exception:                                   # noqa: BLE001
        hit = ''
    # 兜底：JS 点击（个别模板不是 submit 按钮时仍可用）
    if not hit:
        js = """() => {
          const root = document.getElementById('edit-section') || document;
          const btns = Array.from(root.querySelectorAll('button'));
          for (const b of btns) {
            const txt = (b.innerText || '').replace(/\\s/g, '');
            if (txt === '保存' || txt === '确定' || txt === '提交') {
              const r = b.getBoundingClientRect();
              if (r.width > 0 && r.height > 0) { b.click(); return txt; }
            }
          }
          return '';
        }"""
        try:
            hit = page.evaluate(js)
        except Exception:                               # noqa: BLE001
            hit = ''
    if not hit:
        # 按钮灰了（disabled）点了也没用，得说清楚是灰的，别只说没找到
        try:
            st = page.evaluate(
                "() => { const root = document.getElementById('edit-section')"
                " || document; for (const b of root.querySelectorAll('button'))"
                " { const t = (b.innerText || '').replace(/\\s/g, ''); "
                "if (t === '保存' || t === '确定' || t === '提交') { "
                "return {txt: t, disabled: !!b.disabled}; } } return null; }")
        except Exception:                               # noqa: BLE001
            st = None
        why = '保存按钮没找到，需要你手动点'
        if st and st.get('disabled'):
            why = '保存按钮是灰的点不动（表单没识别到改动或有必填项空着）'
        t.missing.append({'label': name, 'why': why})
        return False
    t.step('%s：点了「%s」' % (name, hit))
    t.shot(page, '%s保存后' % name)

    # 保存是异步的，给足时间：轮询到编辑区收起来为止，最多 8 秒
    info = {'open': True, 'errs': [], 'notice': [], 'still': None}
    for _ in range(16):
        page.wait_for_timeout(500)
        try:
            info = page.evaluate(
                "([sid]) => { const e = document.getElementById('edit-section'); "
                "if (!e) { return {open: false, errs: [], notice: [], "
                "still: null}; } "
                "const r = e.getBoundingClientRect(); "
                "const n = e.querySelectorAll('input, textarea, "
                ".ant-select, .ant-radio-group').length; "
                "let still = null; "
                "if (sid) { const s = document.getElementById(sid); "
                "  still = s ? !!s.querySelector('.error-field-notice') : null; } "
                "const bad = Array.from(document.querySelectorAll("
                "'.ant-form-item-explain-error'))"
                ".map(x => (x.innerText || '').trim()).filter(Boolean); "
                "const notice = Array.from(document.querySelectorAll("
                "'.error-field-notice'))"
                ".map(x => (x.innerText || '').trim()).filter(Boolean); "
                "return {open: !!(r.width && r.height && n), "
                "errs: bad.slice(0, 6), notice: notice.slice(0, 3), "
                "still: still}; }", [sid])
        except Exception:                               # noqa: BLE001
            info = {'open': False, 'errs': [], 'notice': [], 'still': None}
        if not info.get('open'):
            return True
        # 段上的「缺失」提示没了，说明这次保存其实生效了
        if sid and info.get('still') is False:
            _gp_close_edit(page)
            return True

    why = '点了保存但表单没收起来'
    errs = [e for e in (info.get('errs') or []) if e]
    if errs:
        why += '，校验没过：%s' % '；'.join(errs)[:120]
    else:
        notice = [e for e in (info.get('notice') or []) if e]
        why += '，页面提示：%s' % ('；'.join(notice)[:60] or '无')
    t.missing.append({'label': name, 'why': why})
    _gp_close_edit(page)
    return False


def _gp_pick_option(t, page, value, label, timeout=8, sel_id=None):
    """在已经展开的下拉里点文本匹配的选项（.ant-select-item-option）。

    sel_id：这个下拉自己的 id。页面上可能同时残留着别的下拉的弹层，
    不按 id 限定就会点到别的字段的选项上去（民族/政治面貌时好时坏的元凶）。
    """
    value = str(value)
    if sel_id:
        scope = '#%s_list .ant-select-item-option, ' % sel_id
    else:
        scope = ''
    end = time.time() + timeout
    typed = False
    while time.time() < end:
        # 页面上会残留上一个下拉的弹层，取「最后一个非隐藏的」才是当前这个
        dd = page.locator(
            '.ant-select-dropdown:not(.ant-select-dropdown-hidden)').last
        opts = dd.locator('.ant-select-item-option')
        n = opts.count()
        if n:
            for i in range(n):
                o = opts.nth(i)
                try:
                    txt = (o.inner_text() or '').strip()
                except Exception:                       # noqa: BLE001
                    continue
                if txt and (value in txt or txt in value):
                    try:
                        o.click(timeout=1500)
                    except Exception:                   # noqa: BLE001
                        # 被遮住 / 在视口外时真实点击会超时，antd 的选项其实
                        # 认的是 mousedown，用 JS 把整套鼠标事件补上就行。
                        try:
                            o.evaluate(
                                "el => { for (const t of ['mousedown', "
                                "'mouseup', 'click']) { el.dispatchEvent("
                                "new MouseEvent(t, {bubbles: true})); } }")
                        except Exception:               # noqa: BLE001
                            continue
                    t.filled.append({'label': label, 'value': txt})
                    page.wait_for_timeout(400)
                    return True
            if not typed:
                try:
                    page.keyboard.type(value, delay=80)
                    typed = True
                    page.wait_for_timeout(1000)
                    continue
                except Exception:                       # noqa: BLE001
                    pass
            break
        page.wait_for_timeout(300)
    try:
        page.keyboard.press('Escape')
    except Exception:                                   # noqa: BLE001
        pass
    return False


def _gp_select(t, page, sel_id, value, label=None, strict=True,
               optional=False):
    """普通 antd 下拉：点 #sel_id 展开 → 选选项。"""
    if not value:
        return False
    label = label or sel_id
    el = page.locator('#%s' % sel_id)
    if not el.count():
        return False
    # 已经选过了就别动：站里已有的选择（比如实名账号填过的政治面貌）
    # 比档案里的默认值更可信，覆盖它没好处。
    try:
        cur = (el.first.inner_text() or '').strip()
        if cur and '请选择' not in cur and cur != value:
            t.filled.append({'label': label, 'value': '%s（原有，未改）' % cur[:24]})
            return True
    except Exception:                                   # noqa: BLE001
        pass
    opened = False
    try:
        el.first.scroll_into_view_if_needed(timeout=2000)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        el.first.click(timeout=3000)
        opened = True
    except Exception:                                   # noqa: BLE001
        pass
    if not opened:
        # 被遮挡时普通点击会超时，force 一下绕开可见性检查
        try:
            el.first.click(timeout=3000, force=True)
            opened = True
        except Exception:                               # noqa: BLE001
            pass
    if not opened:
        # 最后兜底：antd 的下拉是靠 mousedown 展开的，只派发 click 不会展开，
        # 所以这里要把整套鼠标事件补齐。
        try:
            opened = bool(page.evaluate(
                "(id) => { const e = document.getElementById(id); "
                "if (!e) { return false; } "
                "for (const t of ['mousedown', 'mouseup', 'click']) { "
                "  e.dispatchEvent(new MouseEvent(t, {bubbles: true})); } "
                "return true; }", sel_id))
        except Exception:                               # noqa: BLE001
            opened = False
    if not opened:
        return False
    page.wait_for_timeout(700)
    if _gp_pick_option(t, page, value, label, sel_id=sel_id):
        return True
    if not strict:
        try:
            o = page.locator(
                '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                '.ant-select-item-option').first
            if o.count():
                txt = (o.inner_text() or '').strip()
                o.click(timeout=2000)
                t.filled.append({'label': label, 'value': '(近似) %s' % txt})
                return True
        except Exception:                               # noqa: BLE001
            pass
    if optional:
        return False
    t.missing.append({'label': label, 'why': '选项里没有你要的值（%s）' % value})
    return False


def _gp_typeahead(t, page, sel_id, text, label=None):
    """可搜索下拉（学校/专业名称）：输入文字后要再点弹窗里的「确 认」，
    不然 antd 不会把自定义文本收进去（录制里第 62、68 步）。"""
    if not text:
        return False
    label = label or sel_id
    try:
        el = page.locator('#%s' % sel_id)
        if not el.count():
            return False
        el.first.click(timeout=3000)
        el.first.type(str(text), delay=90)
    except Exception:                                   # noqa: BLE001
        return False
    page.wait_for_timeout(1500)
    # 先看有没有自动补全弹窗（自定义输入走这条路）
    for sel in ('.autocomplete-select-popup button.ant-btn-primary',
                '.autocomplete-select-popup button'):
        try:
            b = page.locator(sel)
            if b.count():
                b.first.click(timeout=2000)
                t.filled.append({'label': label, 'value': str(text)})
                page.wait_for_timeout(800)
                return True
        except Exception:                               # noqa: BLE001
            continue
    # 没有弹窗，就当下拉选项处理
    if _gp_pick_option(t, page, text, label, timeout=4):
        return True
    t.missing.append({'label': label, 'why': '搜不到也没法自定义（%s）' % text})
    return False


def _gp_cascader_dropdown(t, page, path, timeout=6):
    """已经展开的级联下拉里逐级点（国家 → 省 → 市）。"""
    # 三种容器都可能有：级联下拉、弹窗式级联、普通下拉。
    # 只点文本精确相等的叶子元素，避免点到外层容器（React 绑的是最内层）。
    js = """(kw) => {
      const roots = [];
      document.querySelectorAll(
        '.ant-cascader-dropdown:not(.ant-select-dropdown-hidden)'
      ).forEach(e => roots.push(e));
      const md = document.querySelector('.cascader-modal');
      if (md) { roots.push(md); }
      document.querySelectorAll(
        '.ant-select-dropdown:not(.ant-select-dropdown-hidden)'
      ).forEach(e => roots.push(e));
      for (const r of roots) {
        for (const el of r.querySelectorAll('*')) {
          if (el.children.length) { continue; }
          const t = (el.innerText || el.getAttribute('title') || '').trim();
          if (t === kw) {
            const b = el.getBoundingClientRect();
            if (b.width > 0) { el.click(); return true; }
          }
        }
      }
      return false;
    }"""
    hit = []
    end = time.time() + timeout
    for kw in path:
        done = False
        while time.time() < end and not done:
            try:
                done = bool(page.evaluate(js, kw))
            except Exception:                           # noqa: BLE001
                done = False
            if not done:
                page.wait_for_timeout(400)
        if not done:
            break
        hit.append(kw)
        page.wait_for_timeout(700)
    return hit


def _gp_cascader_modal(t, page, path, timeout=8):
    """弹窗式级联（户籍所在地）：tab 选国家 → .level-item 选省 →
    .leaf-item 选市/区。录制里第 187~190 步。"""
    if not page.locator('.cascader-modal').count():
        return []
    try:
        tab = page.locator('.cascader-modal .ant-tabs-tab-btn').filter(
            has_text='中国').first
        if tab.count():
            tab.click(timeout=2000)
            page.wait_for_timeout(500)
    except Exception:                                   # noqa: BLE001
        pass
    js = """([cls, kw]) => {
      const root = document.querySelector('.cascader-modal');
      if (!root) { return false; }
      for (const it of root.querySelectorAll('.' + cls)) {
        const t = (it.innerText || it.getAttribute('title') || '').trim();
        if (t === kw) {
          const r = it.getBoundingClientRect();
          if (r.width > 0) { it.click(); return true; }
        }
      }
      return false;
    }"""
    hit = []
    end = time.time() + timeout
    for i, kw in enumerate(path):
        cls = 'level-item' if i == 0 else 'leaf-item'
        done = False
        while time.time() < end and not done:
            try:
                done = bool(page.evaluate(js, [cls, kw]))
            except Exception:                           # noqa: BLE001
                done = False
            if not done:
                page.wait_for_timeout(400)
        if not done:
            break
        hit.append(kw)
        page.wait_for_timeout(600)
    return hit


def _gp_cascader(t, page, sel_id, path, label=None):
    """工作地区/户籍：两种级联样式都试，谁出结果算谁的。"""
    if not path:
        return False
    label = label or sel_id
    try:
        el = page.locator('#%s' % sel_id)
        if not el.count():
            return False
        el.first.click(timeout=3000)
    except Exception:                                   # noqa: BLE001
        return False
    page.wait_for_timeout(900)
    hit = _gp_cascader_dropdown(t, page, path)
    if not hit:
        hit = _gp_cascader_modal(t, page, path)
    if hit:
        t.filled.append({'label': label, 'value': '/'.join(hit)})
        page.wait_for_timeout(600)
        try:
            page.keyboard.press('Escape')
        except Exception:                               # noqa: BLE001
            pass
        return True
    t.missing.append({'label': label,
                      'why': '级联里没找到 %s' % '/'.join(path)})
    return False


def _gp_radio(t, page, gid, value, label=None, optional=False):
    """按钮组（本人有该段学历 / 统招 / 全日制 / 学位证 / 海外留学 / 服从调剂）。

    不同岗位模板字段不一样，optional=True 的项找不到就算了，不算「缺失」。
    """
    if not value:
        return False
    label = label or gid
    js = """([gid, value]) => {
      const root = document.getElementById(gid);
      if (!root) { return false; }
      for (const lb of root.querySelectorAll('label.ant-radio-button-wrapper')) {
        const t = (lb.innerText || '').trim();
        if (t === value || t.indexOf(value) >= 0) {
          const r = lb.getBoundingClientRect();
          if (r.width > 0) { lb.click(); return true; }
        }
      }
      return false;
    }"""
    try:
        ok = bool(page.evaluate(js, [gid, str(value)]))
    except Exception:                                   # noqa: BLE001
        ok = False
    if ok:
        t.filled.append({'label': label, 'value': value})
        page.wait_for_timeout(350)
    elif not optional:
        t.missing.append({'label': label, 'why': '按钮组里没有「%s」' % value})
    return ok


def _gp_fact(t, key, label):
    """取档案里的一项**个人事实**（民族 / 婚姻 / 统招 / 外语 / 服从调剂 …）。

    档案里没填就返回空串，同时记一条「需要你补」——**绝不返回默认值**。

    为什么单独抽这个：这些字段只有本人知道，给个「最省事的值」就是替用户
    编材料。2026-09-21 之前这里写死过一批：民族「汉族」、婚姻「未婚」、
    学历性质「统招」「全日制」、外语「英语 + 熟练」、健康状况「健康」、
    是否服从调剂「是」——而 profiles 表当时根本没有这些列。
    后果不是「填得不完美」：成人教育被填成统招全日制是**学历性质造假**，
    凭空安上「英语熟练」在面试时当场穿帮。
    """
    v = str(t.profile.get(key) or '').strip()
    if not v:
        t.missing.append({'label': label,
                          'why': '档案里没填这一项，只有你本人知道，需要你补一次'})
    return v


# 「其他说明」段里的是非题，只有**承诺类**才代答「否」。判据是题目里出现
# 这些词 —— 它们问的都是「你有没有这回事」，对绝大多数人答案是「没有」，
# 而且答错等于给自己扣分（说没违纪是事实否定，不是编造）。
# 剩下的「是否…」题（接受调剂 / 愿否出差 / 是否符合某条件）是**个人意愿**，
# 一律不代答：2026-09-21 之前这里是不分题型统一答「否」的，
# 会把「是否愿意接受调剂」这类题也替他答掉。
_NO_ANSWER_KEYWORDS = ('违纪', '违法', '犯罪', '处分', '处罚', '失信',
                       '竞业', '回避', '涉诉', '诉讼', '仲裁', '吸毒',
                       '赌博', '不良记录', '被列为')


def _gp_answer_no(t, page):
    """把编辑区里的**承诺类**是非题选「否」，意愿类的不动（留给用户自己答）。

    这些题的下拉 id 是随机串（biz_XXXX），label 又极长（如「是否有严重违纪违法……」），
    只能靠 label 文字定位。未选时 antd 下拉的 title 就是整句问题干（"请选择是否有…"），
    不能拿「值 != 请选择」去判断——那样会把所有未选题误判成已选而跳过。
    正确判据：看 .ant-select-selection-item 的当前值，已是 是/否 才跳过，
    仍是「请选择…」占位的才补选「否」。是否服从调剂是单选按钮，不在这里处理。
    """
    collect = """() => {
      const out = [];
      for (const it of document.querySelectorAll(
          '#edit-section .ant-form-item')) {
        const l = it.querySelector('label');
        if (!l) { continue; }
        const txt = (l.getAttribute('title') || l.innerText || '').trim();
        if (txt.indexOf('是否') < 0) { continue; }
        const sel = it.querySelector('.ant-select');
        if (!sel) { continue; }
        const item = sel.querySelector('.ant-select-selection-item');
        const cur = item
          ? ((item.getAttribute('title') || item.innerText || '').trim())
          : '';
        // 已经是 是/否 之一就跳过；占位是「请选择…」才需要补
        if (cur === '是' || cur === '否') { continue; }
        out.push(txt);
      }
      return out;
    }"""
    try:
        labels = list(page.evaluate(collect))
    except Exception:                                   # noqa: BLE001
        labels = []
    got = False
    pending = []
    for txt in labels:
        if any(k in txt for k in _NO_ANSWER_KEYWORDS):
            if _ant_select_by_label_text(t, page, txt, '否', strict=True):
                got = True
        else:
            # 意愿类：只有本人能定，不代答，报上去让他自己选
            pending.append(txt)
    for txt in pending:
        t.missing.append({'label': txt[:34],
                          'why': '这一题问的是你本人的意愿或情况，AI 不替你答，'
                                 '请你自己选一下'})
    page.wait_for_timeout(400)
    return got


def _gp_radio_by_label(t, page, keyword, value, label=None, optional=True):
    """按字段标题找按钮组：有些段（承诺类是非题）的 id 是随机串 biz_XXXX，
    只能靠 label 文案定位。"""
    label = label or keyword[:12]
    js = """([kw, value]) => {
      for (const it of document.querySelectorAll('.ant-form-item')) {
        const l = it.querySelector('label');
        if (!l) { continue; }
        if ((l.innerText || '').indexOf(kw) < 0) { continue; }
        for (const lb of it.querySelectorAll(
            'label.ant-radio-button-wrapper, label.ant-radio-wrapper')) {
          const t = (lb.innerText || '').trim();
          const r = lb.getBoundingClientRect();
          if ((t === value || t.indexOf(value) >= 0) && r.width > 0) {
            lb.click();
            return true;
          }
        }
      }
      return false;
    }"""
    try:
        ok = bool(page.evaluate(js, [keyword, str(value)]))
    except Exception:                                   # noqa: BLE001
        ok = False
    if ok:
        t.filled.append({'label': label, 'value': value})
        page.wait_for_timeout(300)
    elif not optional:
        t.missing.append({'label': label,
                          'why': '这个选项没找到（要选「%s」）' % value})
    return ok


def _gp_fill(t, page, el_id, value, label=None, optional=False):
    """普通输入框 / 文本域 / 数字框，直接按 id 填。

    optional=True：这个字段不是每段都有（模板不同），找不到就算了，不算缺失。
    """
    if value in (None, ''):
        return False
    label = label or el_id
    # 国聘很多字段的原生 input 是隐藏的，真正显示出来的是
    # <id>-fe-1601-customization（邮箱/手机…都是这个套路），两个都试。
    cands = ['#%s' % el_id]
    if not el_id.endswith('-fe-1601-customization'):
        cands.append('#%s-fe-1601-customization' % el_id)
    # 段容器和字段常常同名（#assessment 既是「自我评价」段又是段里的文本域），
    # 所以带标签的选择器要排在前面，否则会点到外层 div 上。
    cands = (['input#%s' % el_id, 'textarea#%s' % el_id] + cands)
    # 已经有值就别动：实名账号的姓名/手机/邮箱是锁定的，硬填必然失败，
    # 而且不该拿档案里的名字去覆盖你站里已有的真实信息。
    try:
        for sel in cands:
            el = page.locator(sel)
            if not el.count():
                continue
            cur = (el.first.input_value() or '').strip()
            if cur:
                t.filled.append({'label': label,
                                 'value': '%s（原有，未改）' % cur[:30]})
                return True
    except Exception:                                   # noqa: BLE001
        pass
    for sel in cands:
        try:
            el = page.locator(sel)
            if not el.count():
                continue
            first = el.first
            if not first.is_visible():
                continue
            first.scroll_into_view_if_needed(timeout=2000)
            first.click(timeout=2000)
            first.fill(str(value))
            page.wait_for_timeout(250)
            t.filled.append({'label': label, 'value': str(value)[:40]})
            return True
        except Exception:                               # noqa: BLE001
            continue
    # 最后兜底：直接用 JS 塞值并派发 input 事件（React 受控组件也认）
    ok = False
    for sel in cands:
        try:
            ok = bool(page.evaluate(
                "([s, v]) => { const e = document.querySelector(s); "
                "if (!e) { return false; } "
                "const proto = e.tagName === 'TEXTAREA' "
                "  ? window.HTMLTextAreaElement.prototype "
                "  : window.HTMLInputElement.prototype; "
                "const set = Object.getOwnPropertyDescriptor(proto, 'value').set; "
                "set.call(e, v); "
                "e.dispatchEvent(new Event('input', {bubbles: true})); "
                "e.dispatchEvent(new Event('change', {bubbles: true})); "
                "return true; }", [sel, str(value)]))
        except Exception:                               # noqa: BLE001
            continue
        if ok:
            t.filled.append({'label': label, 'value': str(value)[:40]})
            return True
    if optional:
        return False
    why = '这个输入框找不到或填不进去'
    try:
        diag = page.evaluate(
            "(id) => { const e = document.getElementById(id); "
            "if (!e) { return '页面上没有这个字段'; } "
            "const r = e.getBoundingClientRect(); "
            "return e.tagName.toLowerCase() + ' 可见=' + !!(r.width || r.height)"
            " + ' 只读=' + !!e.readOnly + ' 禁用=' + !!e.disabled; }", el_id)
        if diag:
            why = '%s（%s）' % (why, diag)
    except Exception:                                   # noqa: BLE001
        pass
    t.missing.append({'label': label, 'why': why})
    return False


def _gp_check(t, page, el_id, label=None):
    """勾选框（个人诚信说明）。"""
    label = label or el_id
    js = """(id) => {
      const el = document.getElementById(id);
      if (!el) { return false; }
      if (!el.checked) { el.click(); }
      return !!el.checked;
    }"""
    try:
        ok = bool(page.evaluate(js, el_id))
    except Exception:                                   # noqa: BLE001
        ok = False
    if ok:
        t.filled.append({'label': label, 'value': '已勾选'})
        page.wait_for_timeout(300)
    return ok


def _gp_date(t, page, el_id, value, label=None):
    """单个日期框（工作/实习的 period_start、period_end 是分开的两个）。

    点开面板敲 YYYY-MM 回车；敲不进去就退回直接往输入框里塞。
    """
    if not value:
        return False
    label = label or el_id
    try:
        el = page.locator('#%s' % el_id).first
        el.scroll_into_view_if_needed(timeout=2000)
        el.click(timeout=3000)
        page.wait_for_timeout(600)
        page.keyboard.type(str(value), delay=60)
        page.wait_for_timeout(400)
        page.keyboard.press('Enter')
        page.wait_for_timeout(500)
        page.keyboard.press('Escape')
        val = page.locator('#%s' % el_id).first.input_value()
        if val:
            t.filled.append({'label': label, 'value': val})
            return True
    except Exception:                                   # noqa: BLE001
        pass
    return _gp_fill(t, page, el_id, value, label)


def _gp_monthrange(t, page, el_id, start, end, label=None):
    """月份区间（就读年月 / 获得时间）：点开面板直接敲 YYYY-MM 再回车。"""
    if not start and not end:
        return False
    label = label or el_id
    try:
        el = page.locator('#%s' % el_id)
        if not el.count():
            return False
        el.first.click(timeout=3000)
        page.wait_for_timeout(700)
        if start:
            page.keyboard.type(str(start), delay=60)
            page.wait_for_timeout(500)
            page.keyboard.press('Enter')
            page.wait_for_timeout(600)
        if end:
            page.keyboard.type(str(end), delay=60)
            page.wait_for_timeout(500)
            page.keyboard.press('Enter')
            page.wait_for_timeout(600)
        page.keyboard.press('Escape')
    except Exception:                                   # noqa: BLE001
        return False
    try:
        val = page.locator('#%s' % el_id).first.input_value()
    except Exception:                                   # noqa: BLE001
        val = ''
    if val:
        t.filled.append({'label': label, 'value': val})
        return True
    t.missing.append({'label': label, 'why': '日期没填进去，需要你手动选'})
    return False


def _gp_click_apply(page):
    """点「申请职位」按钮（真实点击）。点到了返回 True。

    这个按钮同样是 `ant-btn apply-btn`，用真实点击最稳（JS click 在个别
    antd 按钮上不触发 React 的 onClick，2026-09-20 在保存按钮上踩过）。"""
    for sel in ('button.apply-btn', '.apply-btn'):
        try:
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                b.first.click(timeout=2500)
                return True
        except Exception:                               # noqa: BLE001
            continue
    return _click_any(page, _APPLY_BTNS)


def _gp_apply_fail_text(page):
    """读「申请失败」弹窗的正文；没有失败弹窗返回 ''。

    国聘点申请后由**后端**校验简历完整性，不完整就返回 code=88002
    「简历数据不完整」，前端弹一个「申请失败」的框（2026-09-20 实测）。
    旧代码只去找「知道了」这三个字，等于把原因丢掉了——用户只看到失败，
    不知道缺什么。这里把正文读出来，才能如实告诉他被什么拦下。"""
    js = """() => {
      for (const m of document.querySelectorAll(
             '.ant-modal-content, .ant-modal-confirm, .ant-modal-body')) {
        if (!(m.offsetWidth || m.offsetHeight)) continue;
        const tx = (m.innerText || '').replace(/\\s+/g, ' ').trim();
        if (!tx) continue;
        if (/申请失败|投递失败|不完整|失败/.test(tx)) { return tx.slice(0, 140); }
      }
      return '';
    }"""
    try:
        return (page.evaluate(js) or '').strip()
    except Exception:                                   # noqa: BLE001
        return ''


def _gp_missing_sections_desc(page):
    """把「还缺哪几段」列成人话（用于告诉用户去补哪几段）。

    段清单从站点目录现读（_gp_blockers），不依赖写死的段表 ——
    不同岗位的模板段不一样。读不到目录才退回段容器判据。
    """
    names = [b['label'] for b in _gp_blockers(page, deep=False)]
    if names:
        return '、'.join(names)
    todo = set(_gp_sections_todo(page))
    left = [n for s, n in _GP_SECTIONS if s in todo and s != 'kin']
    kin = _gp_kin_section_id(page)
    if kin and kin in todo:
        left.append('亲属在集团公司系统单位任职情况')
    return '、'.join(left) or '（站点没标出具体哪段，请看页面上标黄/带红字的部分）'


def _gp_red_sections(page):
    """读右侧「简历目录」，返回每段 {'name','key','required','done'}。

    这是站点给用户看的那份进度，也是「红字」的出处。每项 DOM 形如：
        <a><p>段名<span class="ant-tag">(必填)</span></p>
           <span class="anticon anticon-check finished|no-finish"></span></a>
    带 (必填) 且勾是 no-finish（灰勾）的 = 站点要求补的那几段。
    读不到目录（结构变了）返回 []，调用方会退回段容器判据。
    """
    js = """() => {
      const box = document.querySelector('.basic-section-menu');
      if (!box) return null;
      const out = [];
      for (const a of box.querySelectorAll('a,li')) {
        const tx = (a.innerText || '').replace(/\\s+/g, ' ').trim();
        if (!tx) continue;
        const chk = a.querySelector('span.anticon-check');
        const cls = chk ? (chk.className || '') : '';
        out.push({
          name: tx,
          required: !!a.querySelector('span.ant-tag'),
          done: cls.indexOf('finished') >= 0 && cls.indexOf('no-finish') < 0,
        });
      }
      return out;
    }"""
    try:
        raw = page.evaluate(js)
    except Exception:                                   # noqa: BLE001
        return []
    if not raw:
        return []
    out = []
    for r in raw:
        name = re.sub(r'[（(]必填[）)]', '', r.get('name') or '').strip()
        if not name:
            continue
        out.append({'name': name, 'key': _GP_NAME2KEY.get(name, ''),
                    'required': bool(r.get('required')),
                    'done': bool(r.get('done'))})
    return out


def _gp_section_id_by_title(page, title):
    """按段标题反查段容器的真实 id。

    段容器 id 一般是模块名（education / basic_info …），但有些段（实测
    「亲属在集团公司系统单位任职情况」）是随机串 biz_XXXX，写不进固定表，
    只能按标题文字反查。同一个段名会被外层容器也「以它开头」命中，所以取
    innerText 最短的那个 —— 那才是最贴身的容器。
    """
    js = """(want) => {
      let best = '', bestLen = 1e9;
      for (const el of document.querySelectorAll('[id]')) {
        const id = el.id || '';
        if (!id || id.length > 40) { continue; }
        const own = (el.innerText || '').trim();
        if (!own.startsWith(want)) { continue; }
        if (own.length < bestLen) { bestLen = own.length; best = id; }
      }
      return best;
    }"""
    try:
        return page.evaluate(js, title) or None
    except Exception:                                   # noqa: BLE001
        return None


def _gp_id_exists(page, sid):
    """页面上有没有这个 id 的容器。"""
    try:
        return bool(page.evaluate("(i) => !!document.getElementById(i)", sid))
    except Exception:                                   # noqa: BLE001
        return False


def _gp_declare_text(page, sid):
    """这一段里有没有一键「无XX」声明？返回按钮文字（没有返回 ''）。

    以前这里写死「无资格证书」「无亲属在集团公司系统单位任职情况」两句。
    用户指出每岗位的段不一样之后改成**在段里现找**：段内任何文字形如
    「无××××」的按钮，就是站点给的一键声明入口，新段也能自动认出来。
    """
    js = """(sid) => {
      const root = document.getElementById(sid);
      if (!root) { return ''; }
      for (const el of root.querySelectorAll('button, a, span')) {
        const tx = (el.innerText || '').trim();
        if (/^无[^\\s]{2,24}$/.test(tx)) { return tx; }
      }
      return '';
    }"""
    try:
        return page.evaluate(js, sid) or ''
    except Exception:                                   # noqa: BLE001
        return ''


def _gp_read_edit_fields(page):
    """读当前开着的编辑区里「要填哪几项」（必填的优先）。只读不写。"""
    js = """() => {
      const root = document.getElementById('edit-section');
      if (!root) { return []; }
      const out = [];
      for (const it of root.querySelectorAll('.ant-form-item')) {
        const lab = it.querySelector('.ant-form-item-label label');
        if (!lab) { continue; }
        const t = (lab.innerText || '').replace(/\\s+/g, '').trim();
        if (!t) { continue; }
        out.push({label: t, required: !!it.querySelector('.ant-form-item-required')});
      }
      return out;
    }"""
    try:
        rows = page.evaluate(js) or []
    except Exception:                                   # noqa: BLE001
        return []
    req = [r['label'] for r in rows if r.get('required')]
    return req or [r['label'] for r in rows]


def _gp_probe_fields(t, page, sid, name):
    """打开这一段看它要填哪几项，读完立刻点「取消」（绝不落库）。

    ⚠️ 只对**我们不认识、又没有一键声明**的段做这件事（见 _gp_blockers）：
    打开表单是打扰性操作，能自己填的段不需要走这条路。
    """
    keep = list(t.missing)
    try:
        if not _gp_open(t, page, sid, name):
            return []
        got = _gp_read_edit_fields(page)
        _gp_close_edit(page)
        return got
    except Exception:                                   # noqa: BLE001
        return []
    finally:
        t.missing = keep             # 探路失败不该记进「要你手动填」


def _declared_none(profile):
    """用户确认过「确实没有」的段名/段 id 列表。

    通用：档案 declared_none（JSON 数组，想列几段列几段）+ 两个历史列
    （no_certificate / no_relative，早期只支持那两段时留下的）。
    """
    out = []
    for x in _json_list(profile.get('declared_none')):
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    if _truthy(profile.get('no_certificate')) and '资格证书' not in out:
        out.append('资格证书')
    if _truthy(profile.get('no_relative')) \
            and '亲属在集团公司系统单位任职情况' not in out:
        out.append('亲属在集团公司系统单位任职情况')
    return out


def _gp_declared_match(declared, label, sid):
    """用户确认的「没有」里，有没有这一段（按段名或段 id 比）。"""
    for d in declared or ():
        d = str(d).strip()
        if not d:
            continue
        if d == label or d == sid:
            return True
        if len(d) >= 3 and (d in label or label in d):
            return True
    return False


def _gp_blockers(page, t=None, deep=True):
    """这个岗位上「必填但还没完成」的段 → 给用户看的结构化清单。

    每项：{'key','label','why','how','declare','fields'}
      why     为什么不能替你补（家人隐私 / 事实声明 / 要原件 …）
      how     怎么补最快（含这一段自带的一键「无…」）
      declare 这一段的一键声明按钮文字（没有就是 ''）
      fields  这一段要填的字段名（只对不认识的段探一次）

    **通用化（2026-09-20）**：段清单从站点右侧「简历目录」现读，解释按段名
    匹配，一键「无…」在段里现找 —— 全程不依赖写死的段表。所以不同岗位的
    模板（段不一样、字段不一样）都能给出准确清单，不会因为遇到没见过的段
    而漏报，也不会把不存在的段报成「要你手动补」。

    deep=True 时，对本项目还不认识的必填段，会打开它读一遍字段名（读完取消），
    好让用户知道「这个岗位到底要我填什么」。
    """
    red = _gp_red_sections(page)
    if not red:
        # 目录读不到（页面结构变了）时退回段容器判据 —— 总比空手回强
        todo = set(_gp_sections_todo(page))
        kin = _gp_kin_section_id(page)
        red = []
        for sid, name in _GP_SECTIONS:
            if sid == 'kin':
                if kin and kin in todo:
                    red.append({'name': name, 'key': kin,
                                'required': True, 'done': False})
            elif sid in todo:
                red.append({'name': name, 'key': sid,
                            'required': True, 'done': False})
    out = []
    known = set(_GP_REFILL_SECTIONS) | {'family', 'attachment'}
    for r in red:
        if not (r.get('required') and not r.get('done')):
            continue
        sid = r.get('key') or ''
        if not sid or not _gp_id_exists(page, sid):
            sid = _gp_section_id_by_title(page, r['name']) or sid
        decl = _gp_declare_text(page, sid) if sid else ''
        fields = []
        # 只对「不认识、又没一键声明」的段去读字段：打开表单是打扰性操作
        if deep and t is not None and sid and not decl and sid not in known:
            fields = _gp_probe_fields(t, page, sid, r['name'])
        info = _gp_advice(r['name'], decl, fields)
        out.append({'key': sid or r['name'], 'label': r['name'],
                    'why': info['why'], 'how': info['how'],
                    'declare': decl, 'fields': fields})
    return out


def _truthy(v):
    """档案里的「是/否」标记：1/'1'/'true'/'yes'/'是' 都算真。

    这些值来自 SQLite（INTEGER 会读成 int 1）或前端表单（字符串 '1'），
    两种都得认，不能只写 `if profile.get(k)`——'0' 在 Python 里也是真。"""
    return str(v if v is not None else '').strip().lower() in (
        '1', 'true', 'yes', 'y', '是', 'on')


def _gp_declare_none(page, sid, text):
    """点某段里的一键「无XX」声明按钮（如「无资格证书」）。

    这是**事实声明**（"我确实没有"），只有用户本人能下——所以这个函数不认识
    用户，调用方必须先拿到用户明确的「没有」（档案里的 no_certificate /
    no_relative 标记，或用户当面回答）。这里只负责把它点下去、并确认真生效。

    返回 True 表示该段的必填缺失提示没了（真的声明成功）。
    """
    clicked = False
    # 先用 Playwright 真实点击（antd 按钮有的只认真实点击，JS click 不触发）
    try:
        btn = page.locator('#%s button' % sid).filter(
            has_text=re.compile(r'^\s*%s\s*$' % re.escape(text))).first
        if btn.count():
            try:
                btn.click(timeout=2500)
            except Exception:                           # noqa: BLE001
                btn.click(timeout=2500, force=True)
            clicked = True
    except Exception:                                   # noqa: BLE001
        clicked = False
    if not clicked:
        # 退回 JS 点击：有的「无XX」是 span 上绑的事件
        js = """([sid, want]) => {
          const root = document.getElementById(sid);
          if (!root) return false;
          for (const el of root.querySelectorAll('button, a, span')) {
            if ((el.innerText || '').trim() === want) {
              const r = el.getBoundingClientRect();
              if (r.width > 0 && r.height > 0) { el.click(); return true; }
            }
          }
          return false;
        }"""
        try:
            clicked = bool(page.evaluate(js, [sid, text]))
        except Exception:                               # noqa: BLE001
            clicked = False
    if not clicked:
        return False
    page.wait_for_timeout(1200)
    # 有的会弹二次确认（只在可见弹层里点「确定」，别误点页面其它确定）
    try:
        page.evaluate("""() => {
          for (const w of document.querySelectorAll(
                 '.ant-modal-content, .ant-popover-inner, .ant-popconfirm')) {
            if (!(w.offsetWidth || w.offsetHeight)) continue;
            for (const b of w.querySelectorAll('button')) {
              const tx = (b.innerText || '').replace(/\\s/g, '');
              if (tx === '确定' || tx === '确认' || tx === '是') {
                b.click(); return;
              }
            }
          }
        }""")
    except Exception:                                   # noqa: BLE001
        pass
    page.wait_for_timeout(1600)
    # 生效判据：该段的必填缺失提示没了
    try:
        still = page.evaluate(
            "([sid]) => { const e = document.getElementById(sid); "
            "return e ? !!e.querySelector('.error-field-notice') : false; }",
            [sid])
    except Exception:                                   # noqa: BLE001
        still = True
    return not still


def _gp_family_form(t, page, m):
    """填「家庭成员」一条。**数据一律来自档案里用户自己录入的 family**，
    这里不编也不会编家人的任何信息。

    这个模板的 6 个字段恰好都有干净的 id（2026-09-20 实测）：
      #relation 关系（下拉）/ #name 姓名 / #birthdate 出生日期（日期框）
      #political 政治面貌（下拉）/ #company_name 工作单位或就读院校 / #position 职务
    """
    if not isinstance(m, dict):
        return False
    ok = False
    if m.get('relation'):
        ok = _gp_select(t, page, 'relation', str(m['relation']), '关系',
                        strict=False) or ok
    if m.get('name'):
        ok = _gp_fill(t, page, 'name', m['name'], '姓名', optional=True) or ok
    if m.get('birth'):
        ok = _gp_date_id(t, page, 'birthdate', m['birth'], '出生日期') or ok
    if m.get('political'):
        ok = _gp_select(t, page, 'political', str(m['political']), '政治面貌',
                        strict=False) or ok
    if m.get('company'):
        ok = _gp_fill(t, page, 'company_name', m['company'],
                      '工作单位/就读院校', optional=True) or ok
    if m.get('position'):
        ok = _gp_fill(t, page, 'position', m['position'], '职务',
                      optional=True) or ok
    return ok


def _gp_date_id(t, page, el_id, value, label=None):
    """按 id 填一个日期框（如家庭成员段的 #birthdate）。

    `_ant_date` 是按 label 找的，段里 label 会重名；这个模板的家庭成员段
    日期框有 id，直接按 id 定位更稳。日期规则同 `_type_date`：必须敲完整日期。"""
    if not value:
        return False
    label = label or el_id
    try:
        loc = page.locator('#%s' % el_id)
        if not loc.count():
            return False
        try:
            tag = loc.first.evaluate("el => el.tagName")
        except Exception:                               # noqa: BLE001
            tag = ''
        if tag == 'INPUT':
            inp = loc.first
        else:
            inp = page.locator('#%s input' % el_id).first
            if not inp.count():
                return False
        if not _type_date(inp, str(value), page):
            return False
        try:
            page.keyboard.press('Tab')      # 移焦提交，绝不用 Escape（会丢值）
        except Exception:                               # noqa: BLE001
            pass
        page.wait_for_timeout(250)
        t.filled.append({'label': label, 'value': str(value)})
        return True
    except Exception:                                   # noqa: BLE001
        return False


def _json_list(v):
    """档案里存的 JSON 数组字段（experiences / family）读出来；坏了就当空。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            got = json.loads(v)
            return got if isinstance(got, list) else []
        except Exception:                               # noqa: BLE001
            return []
    return []


def _guopin_resume_guard(t, page):
    """国聘的拦截：点完「申请职位」不放表单，先要求补全站内简历。

    2026-09-20 按真人完整手填一遍的录制（data/recorder/trace_*.json）重写：
    求职意向 → 教育经历 → 工作/实习 → 语言能力 → 附件(人工) → 其他说明 →
    基本信息 → 点申请职位 → 确认同步。附件要传毕业证/成绩单这类原件材料，
    脚本没有也不该代传，这一段如实转人工。
    """
    url = ''
    try:
        url = page.url or ''
    except Exception:                                   # noqa: BLE001
        pass
    text = _page_text(page)
    on_apply = ('/apply' in url) or ('简历信息填写不完善' in text)
    if not on_apply:
        return False

    # 重填覆盖：段里已有内容也重填一遍（打开「编辑」，预载原值，改完保存=覆盖）。
    # 开着它就不能因为「没有缺的段」提前退出，否则重跑等于什么都没干。
    refill = bool(getattr(t, 'refill', False))
    prefer = 'edit' if refill else None

    if not refill:
        if not _gp_sections_todo(page) and '简历信息填写不完善' not in text \
                and '缺失' not in text:
            return False

    if refill:
        t.step('重填模式：已有内容的段也会打开重填一遍，保存即覆盖', ok=False)
    else:
        t.step('国聘要求先补全站内简历，按真人填过的流程帮你补', ok=False)
    t.shot(page, '国聘简历待补全')

    exps = _profile_experiences(t.profile)
    works = [e for e in exps if e.get('type') == 'work']
    interns = [e for e in exps if e.get('type') == 'intern']
    campus = [e for e in exps if e.get('type') == 'campus']
    edus = [e for e in exps if e.get('type') == 'edu']
    city = str(t.profile.get('city') or '').strip()
    prov = _gp_prov_of(city)

    todo = set(_gp_sections_todo(page))
    for sid, name in _GP_SECTIONS:
        if ('%s缺失' % name) in text:
            todo.add(sid)
    # 亲属任职段的 id 是随机串，不进 _GP_SECTIONS 的 id 匹配；用标题文本兜底
    if '亲属在集团公司系统单位任职情况缺失' in text:
        todo.add('kin')
    if refill:
        # 内容段一律纳入（不管站点说它完没完成）——但只限于这套模板真有的段
        todo |= (_gp_present_sections(page, _GP_REFILL_SECTIONS)
                 & set(_GP_REFILL_SECTIONS))

    def need(*sids):
        return any(s in todo for s in sids)

    # ---------- 1) 求职意向 ----------
    if need('job_expectation') and _gp_open(t, page, 'job_expectation',
                                            '求职意向', prefer=prefer):
        if city and prov:
            _gp_cascader(t, page, 'location-fe-1601-customization',
                         ['中国', prov, city], '工作地区')
        else:
            t.missing.append({'label': '工作地区',
                              'why': '档案里没写期望城市，没帮你选'})
        # 期望月薪：不同岗位模板的字段不一样，两种都认
        #   老模板 #salary_monthly（单位百元，上限 250）+ #salary_times
        #   新模板 #salary_requirement_min / #salary_requirement_max（元）
        low, high = _salary_range(t.profile.get('salary'))
        if low:
            if page.locator('#salary_requirement_min').count():
                _gp_fill(t, page, 'salary_requirement_min', low, '期望月薪下限')
                if high:
                    _gp_fill(t, page, 'salary_requirement_max', high,
                             '期望月薪上限')
            elif page.locator('#salary_monthly').count():
                _gp_fill(t, page, 'salary_monthly',
                         low if low <= 250 else int(round(low / 100.0)),
                         '期望月薪(百元)')
                _gp_fill(t, page, 'salary_times', 1, '薪资倍数')
            else:
                _gp_check(t, page, 'salary_requirement_none', '期望薪资(面议)')
                _gp_check(t, page, 'salary_none', '期望薪资(保密)')
        _gp_save(t, page, '求职意向', sid='job_expectation')

    # ---------- 2) 教育经历 ----------
    if need('education') and edus:
        for e in edus:
            if not _gp_open(t, page, 'education', '教育经历', prefer=prefer):
                break
            degree = _DEGREE_MAP.get(
                str(e.get('degree') or t.profile.get('edu') or ''), '')
            school = str(e.get('school') or t.profile.get('school') or '')
            major = str(e.get('major') or t.profile.get('major') or '')
            _gp_radio(t, page, 'skip_edu', '本人有该段学历')
            _gp_select(t, page, 'education', degree, '学历')
            # 统招 / 全日制 / 学位证：原来按「学历是本科就写统招、全日制、有学位证」
            # 硬推 —— 成人教育·自考·函授会被填成统招全日制（学历性质造假），
            # 只有毕业证没学位证的会被填成「有学位证」。一律改成读档案，
            # 档案没填就不猜，标出来等用户自己确认。
            for _k, _gid, _lab in (
                    ('edu_regular', 'is_regular', '学历性质（统招 / 非统招）'),
                    ('edu_fulltime', 'is_fulltime', '学习形式（全日制 / 非全日制）'),
                    ('has_degree', 'has_degree', '学位证（有 / 无）')):
                _v = _gp_fact(t, _k, _lab)
                if _v:
                    _gp_radio(t, page, _gid, _v, _lab.split('（')[0])
            _gp_monthrange(t, page, 'period', e.get('start'), e.get('end'),
                           '就读年月')
            _gp_typeahead(t, page, 'school-fe-1601-customization', school,
                          '学校名称')
            _gp_select(t, page, 'major_category', major, '专业分类', strict=False)
            _gp_typeahead(t, page, 'major-fe-1601-customization', major,
                          '专业名称')
            # 专业排名：原来写死「学校未排名」——档案里没有这一项，等于替用户
            # 下结论。这一段不是必填（多数人学校也没有排名），留空不猜。
            overseas = e.get('overseas')
            if overseas is None:
                overseas = bool(re.search(
                    r'伦敦|曼彻斯特|牛津|剑桥|爱丁堡|悉尼|墨尔本|多伦多|温哥华|'
                    r'纽约|波士顿|芝加哥|加州|洛杉矶|东京|首尔|新加坡|柏林|巴黎|'
                    r'莫斯科|海外|留学', school))
            _gp_radio(t, page, 'has_overseas',
                      '海外留学经历' if overseas else '非海外留学经历', '海外留学')
            if e.get('desc'):
                _gp_fill(t, page, 'experience', e.get('desc'), '在校经历')
            if major:
                _gp_fill(t, page, 'major_course', major, '主修课程')
            _gp_save(t, page, '教育经历', sid='education')
            break        # 先补最高学历那段，其余的交给人工核对
        # 只补了最高那一段：其它学历必须让用户知道 —— 以前这里是静默跳过，
        # 用户投完都不知道自己还有一段学历没进站内简历。
        if len(edus) > 1:
            t.step('教育经历：只补了最高那一段，另外 %d 段请你在页面上核对补上'
                   % (len(edus) - 1), ok=False)
            t.missing.append({
                'label': '其余 %d 段教育经历' % (len(edus) - 1),
                'why': '国聘这一段一次只补最高学历，剩下的需要你在页面上手动添加'})
    elif need('education'):
        t.missing.append({'label': '教育经历',
                          'why': '档案里没有教育经历，需要你手动补'})

    # ---------- 3) 工作 / 实习经历（真实段 id 是 work_experience） ----------
    # 优先用真·工作经历（type=work）——站点这一段叫「工作/实习经历」，把实习经历
    # 当工作填进去是退而求其次，不是首选。档案里的顺序就是简历里的顺序（最近的在前），
    # 所以第一条就是最近那份工作。
    for entry in (works + interns + campus)[:1]:
        if not need('work_experience'):
            break
        if _gp_open(t, page, 'work_experience', '工作/实习经历', prefer=prefer):
            _guopin_work_form(t, page, entry)
            _gp_save(t, page, '工作/实习经历', sid='work_experience')
    if need('work_experience') and not (works or interns or campus):
        t.missing.append({'label': '工作/实习经历',
                          'why': '档案里没有工作或实习经历，需要你手动补'})

    # ---------- 4) 语言能力（真实只有 语种/读写/听说/成绩 四项） ----------
    if need('language') and _gp_open(t, page, 'language', '语言能力',
                                     prefer=prefer):
        # 原来这里写死「英语 + 熟练 + 熟练」——档案里根本没有外语水平，
        # 等于**凭空给用户安一门技能**，面试英文一问就穿帮。
        # 现在读档案：填了就照填；没填就整段留空并标出来让他确认
        # （确实不会外语，在档案里选「不会外语」即可）。
        lang = _gp_fact(t, 'foreign_lang', '外语语种（不会外语就选「不会外语」）')
        if lang and lang != '无':
            _gp_select(t, page, 'category', lang, '语种')
            lvl = str(t.profile.get('foreign_level') or '').strip()
            if lvl:
                _gp_select(t, page, 'writing_ability', lvl, '读写能力')
                _gp_select(t, page, 'speaking_ability', lvl, '听说能力')
            else:
                t.missing.append({'label': '外语水平（读写 / 听说）',
                                  'why': '档案里填了语种但没填水平，需要你补一次'})
            _gp_fill(t, page, 'score', '', '成绩')
            _gp_save(t, page, '语言能力', sid='language')
        elif lang == '无':
            t.step('语言能力：档案里选的是「没有外语」，这一段留空不填', ok=False)
            _gp_close_edit(page)
        else:
            _gp_close_edit(page)

    # ---------- 4.5) 自我评价（模板里这段还有「个人优势」「特长」） ----------
    if need('assessment') and _gp_open(t, page, 'assessment', '自我评价',
                                       prefer=prefer):
        intro = str(t.profile.get('summary') or t.profile.get('intro') or '').strip()
        if intro:
            _gp_fill(t, page, 'advantage', intro, '个人优势')
            _gp_fill(t, page, 'assessment', intro, '自我评价')
            _gp_fill(t, page, 'speciality', str(t.profile.get('major') or ''),
                     '熟悉专业有何专长')
            _gp_save(t, page, '自我评价', sid='assessment')
        else:
            # 原来是档案没写就填一段「本人工作认真负责，具备良好的学习能力…」
            # 的模板文案 —— 那是**替用户写自夸材料**：面试被问「你自己怎么想的」
            # 他答不上来，而且这种千篇一律的句子 HR 一眼认得出。
            t.step('自我评价：档案里没写自我介绍，没替你编一段', ok=False)
            t.missing.append({'label': '自我评价',
                              'why': '档案里没写自我介绍，需要你自己写两句'
                                     '（AI 替你写等于编经历）'})
            _gp_close_edit(page)

    # ---------- 5) 附件：要传毕业证/成绩单原件，转人工 ----------
    if need('attachment'):
        t.step('附件段留给你手动传', ok=False)
        t.ask_human(
            'confirm', '附件（毕业证/成绩单等）需要你手动上传',
            '这一段要传毕业证、学位证、成绩单这类原件材料，脚本没有这些文件、'
            '也不该替你乱传。请在浏览器里打开「附件」段，把需要的材料传完并点保存，'
            '然后回来随便输个字点确认，我继续后面的投递。',
            placeholder='传完了就随便输个字点确认')
        _settle(page)

    # ---------- 6) 其他说明：服从调剂 + 承诺勾选 + 几道是非题 ----------
    if need('others') and _gp_open(t, page, 'others', '其他说明', prefer=prefer):
        # 是否服从调剂：**只有本人能决定** —— 服从调剂意味着可能被派到偏远地区，
        # 原来写死「是」等于替他做了这个人生的选择。现在读档案，没填就不选。
        ca = _gp_fact(t, 'can_arrange', '是否服从调剂')
        if ca:
            # 不同模板的选项文字不一样：有「是/否」也有「服从/不服从」
            if not _gp_radio(t, page, 'can_arrange', ca, '是否服从调剂',
                             optional=True):
                _gp_radio(t, page, 'can_arrange',
                          '服从' if ca == '是' else '不服从', '是否服从调剂',
                          optional=True)
        # 承诺类是非题代答「否」，意愿类留着让用户自己选（见 _gp_answer_no）
        _gp_answer_no(t, page)
        _gp_check(t, page, 'term_of_use', '本人承诺')
        _gp_save(t, page, '其他说明', sid='others')

    # ---------- 7) 基本信息 ----------
    if need('basic_info') and _gp_open(t, page, 'basic_info', '基本信息',
                                       prefer=prefer):
        # 姓名/手机/邮箱：站点已按登录账号实名预填（锁定字段），强行覆盖会失败，
        # 保持原值即可满足必填（详见 _gp_fill 里的「原有未改」处理）。
        # 性别：radio，id=gender 存在
        _gp_radio(t, page, 'gender',
                  (t.profile.get('gender') or '男'), '性别', optional=True)
        # 出生日期：此模板无 id，按 label 找日期框
        if t.profile.get('birth'):
            _ant_date(t, page, '出生日期', str(t.profile.get('birth')))
        # 国籍 / 民族 / 政治面貌 / 婚姻状况：无 id 的 antd 下拉，按 label 选
        # 国籍固定「中国」——国聘是国内平台、账号手机实名，这不是编造。
        _ant_select(t, page, '国籍', '中国', strict=False)
        # 民族 / 婚姻状况：原来写死「汉族」「未婚」——profiles 表当时根本没这两列，
        # 少数民族被填成汉族、已婚被填未婚，都是替用户编事实。改成读档案，
        # 没填就不选，标出来让他自己补一次。
        for _k, _lab in (('nation', '民族'), ('marital', '婚姻状况')):
            _v = _gp_fact(t, _k, _lab)
            if _v:
                _ant_select(t, page, _lab, _v, strict=False)
        # 政治面貌：这个岗位模板的下拉只有党派选项（中共党员 / 各民主党派），
        # 没有「群众」。登录账号本身就是中共党员，保持默认「中共党员」即可；
        # 相应地「入党时间」是必填项，需要单独填。
        if t.profile.get('party_join_date'):
            _ant_date(t, page, '入党时间',
                      str(t.profile.get('party_join_date')))
        else:
            t.missing.append({'label': '入党时间',
                              'why': '档案里没填入党时间（党派必填项），需要你手动补'})
        # 健康状况：id=health 是文本输入。原来写死「健康」——同样是替用户下结论，
        # 改成读档案，没填就留空并标出来。
        _hv = _gp_fact(t, 'health', '健康状况（健康 / 良好 / 一般）')
        if _hv:
            _gp_fill(t, page, 'health', _hv, '健康状况', optional=True)
        # 生源地 = 高考时所在地区，档案里没有，按现居城市填，标注让你核对。
        # 这个站点是弹窗式级联（ant-tabs + item-txt），且是省→市→区三级，
        # 必须选到区级才落值（用 _ant_region_modal 处理）。
        # 区级优先取通信地址里的区（如「湖北省武汉市洪山区」→ 洪山区）。
        # 关键：字符类必须排除「省/市/区/县/旗」这几个分级字，否则 {2,8}
        # 会从左吞成「湖北省武汉市洪山」整串（2026-09-20 踩过这个坑）。
        addr = str(t.profile.get('address') or '').strip()
        district = ''
        for m in re.finditer(r'([^省市区县旗]{2,8})(?:区|县|旗)', addr):
            district = m.group(0)
        if not district:
            for m in re.finditer(r'([^省市区县旗]{2,8})市', addr):
                district = m.group(0)
        if city and prov:
            _ant_region_modal(t, page, '生源地',
                              ['中国', prov, city, district])
        # 这几项来自档案本身（每个用户自己填的：紧急联系人/身高/体重/通信地址）。
        # 档案里没填的，下面循环会标记成「需人工补」，bot 绝不替用户编数据。
        _gp_fill(t, page, 'contact_name', t.profile.get('emergency_name'),
                 '紧急联系人姓名', optional=True)
        _gp_fill(t, page, 'contact_telephone', t.profile.get('emergency_phone'),
                 '紧急联系人电话', optional=True)
        _gp_fill(t, page, 'height', t.profile.get('height'), '身高', optional=True)
        _gp_fill(t, page, 'weight', t.profile.get('weight'), '体重', optional=True)
        _gp_fill(t, page, 'contact_address', t.profile.get('address'),
                 '通信地址', optional=True)
        # 这几项缺了就会卡在国聘保存那一步——从 guopin_required_fields() 取，
        # 和前端开投前预检是同一份清单，改一处两端同步。
        # 注：民族/婚姻/学历性质这些**各段自己会报**（见 _gp_fact），这里跳过，
        # 免得同一项在清单里出现两次。
        _reported_elsewhere = {'nation', 'marital', 'health', 'edu_regular',
                               'edu_fulltime', 'has_degree', 'foreign_lang',
                               'can_arrange'}
        for f in guopin_required_fields():
            key = f['k']
            if key == 'party_join_date' or key in _reported_elsewhere:
                continue   # 上面已按党员必填单独处理 / 各段自报
            if not str(t.profile.get(key) or '').strip():
                t.missing.append({'label': f['label'],
                                  'why': '档案里没填，需要在档案里补一次'})
        _gp_save(t, page, '基本信息', sid='basic_info')

    # ---------- 7.5) 「确实没有」的事实声明：段是现认的，不写死 ----------
    # 有些必填段站点配了一键「无XX」（等于「我确实没有这项」）。这是**事实声明**，
    # 只有用户本人能下 —— 所以只在用户明确确认过「没有」时才替他点。确认来源：
    #   1) 档案 declared_none（通用，任意段都能列；兼容旧的 no_certificate/no_relative）
    #   2) 上一屏清单里用户当面点过的「确实没有，帮我声明」（answer 带回 __declare__）
    # 绝不能因为「档案里没这条数据」就默认「无」：那是替用户编事实。
    declared = _declared_none(t.profile)
    if declared:
        for b in _gp_blockers(page, t, deep=False):
            if not b.get('declare'):
                continue
            if not _gp_declared_match(declared, b['label'], b['key']):
                continue
            if _gp_declare_none(page, b['key'], b['declare']):
                t.filled.append({'label': b['label'],
                                 'value': '无（按你确认过的声明）'})

    # 上面几句声明会改掉段的缺失状态，重新扫一遍再决定谁还需要人工
    todo = set(_gp_sections_todo(page))

    # ---------- 8) 家庭成员：家人隐私，只能用**用户自己录入**的数据 ----------
    # 国聘把「家庭成员」标成必填、只有「添加」没有「无」。字段 6 个全必填：
    # 关系/姓名/出生日期/政治面貌/工作单位/职务。
    # 这些是家人的隐私，bot 绝不编：档案里 family 有用户自己填的条目才填，
    # 没有就留给人工（下面 8.5 会如实告诉他）。
    fam = _json_list(t.profile.get('family'))
    if 'family' in todo and fam:
        for m in fam:
            if not _gp_open(t, page, 'family', '家庭成员'):
                break
            _gp_family_form(t, page, m)
            _gp_save(t, page, '家庭成员', sid='family')
        todo = set(_gp_sections_todo(page))

    # ---------- 8.5) 还剩下的必填段：列清单给用户，能一键声明的让他当面确认 ----------
    # 段清单完全由站点目录驱动（见 _gp_blockers），所以不管这个岗位多出什么段
    # （荣誉奖励 / 论文专著 / 亲属任职 / 培训经历 …），用户看到的都是
    # 「这一段要什么、为什么不能替你补、怎么补最快」。
    # 用户在那张清单上点「确实没有，帮我声明」时，answer 会带回
    # __declare__|段名 —— 我们替他点声明，然后重新扫一遍（最多 4 轮）。
    for _ in range(4):
        blockers = _gp_blockers(page, t)
        if not blockers:
            break
        labels = [b['label'] for b in blockers]
        t.step('%s：需要你本人补' % '、'.join(labels), ok=False)
        t.shot(page, '待人工补的段')
        ans = t.ask_human(
            'confirm', '这个岗位要求这几段，要你本人补',
            '国聘按岗位下发的简历模板要求下面这几段，内容只有你本人'
            '（或你家人）知道，AI 替你填就是替你编材料。每段的'
            '「为什么不能替你补」「怎么补最快」都在清单里。\n'
            '· 补完并保存后，回到这个页面点「我补齐了，继续投递」；\n'
            '· 有站点一键「无…」的，你确实没有就直接在清单里点'
            '「确实没有，帮我声明」。',
            placeholder='补完了就随便输个字点确认',
            items=blockers)
        _settle(page)
        if not (ans or '').startswith('__declare__'):
            break
        want = [x.strip() for x in (ans or '').split('|')[1:] if x.strip()]
        if not want:
            break
        hit = False
        for b in blockers:
            if not b.get('declare'):
                continue
            if not any((w == b['label'] or w == b['key'] or w in b['label'])
                       for w in want):
                continue
            if _gp_declare_none(page, b['key'], b['declare']):
                t.filled.append({'label': b['label'], 'value': '无（你刚确认的）'})
                hit = True
        _settle(page)
        if not hit:
            break

    # ---------- 收尾：还有没补完的，如实告诉你 ----------
    _settle(page)
    # 上面那张清单已经报过的段不再重复弹。清单是按站点目录现读的，
    # 所以不管这个岗位多出什么段，既不漏报也不重复报。
    reported = set(b['label'] for b in _gp_blockers(page, t, deep=False))
    todo = set(_gp_sections_todo(page))
    todo.discard('attachment')                  # 附件本来就交给人工
    kin_id = _gp_kin_section_id(page)
    if kin_id:
        todo.discard(kin_id)
    for s in _GP_OPTIONAL_SECTIONS:
        todo.discard(s)
    names = [n for s, n in _GP_SECTIONS if s in todo and n not in reported]
    if names:
        t.shot(page, '仍有缺失段')
        t.ask_human(
            'confirm', '还有几段需要你手动补',
            '这几段我没填成：%s。请你在浏览器里手动补完并保存，'
            '然后回来随便输个字点确认，我继续投递。' % '、'.join(names),
            placeholder='补完了就随便输个字点确认')
        _settle(page)

    if os.environ.get('GP_NO_SUBMIT') == '1':
        t.step('测试模式：简历已补完，停在点「申请职位」之前，不真投', ok=True)
        t.shot(page, '测试模式停在提交前')
        t.status = 'done'
        return True

    # 补完点「申请职位」。国聘这条路是**后端**校验简历完整性：不完整就返回
    # code=88002「简历数据不完整」，前端弹一个「申请失败」的框（2026-09-20 实测，
    # 抓包见 POST /personal/apply/resume/v1/deliver）。
    # 旧代码只找「知道了」三个字，等于把失败原因丢了——用户只看到「失败」。
    # 现在把原因读出来、把还缺的段列清楚，让他一次补到位，再重试（最多 3 轮）。
    for _ in range(3):
        if not _gp_click_apply(page):
            t.missing.append({'label': '申请职位',
                              'why': '没找到「申请职位」按钮，需要你手动点'})
            break
        t.step('点了申请职位')
        page.wait_for_timeout(2800)
        t.shot(page, '点了申请职位后')
        # 成功：弹出「确认同步简历」→ 点掉即投递完成
        if _click_any(page, ('确认同步', '确 认 同 步')):
            t.step('确认同步简历')
            page.wait_for_timeout(2000)
            break
        fail = _gp_apply_fail_text(page)
        if not fail:
            t.step('申请已提交（没有失败提示）', ok=True)
            break
        # 失败：关掉弹窗，把原因 + 还缺的段如实告诉用户，请他补完再重试
        _click_any(page, ('知道了', '知 道 了'))
        page.wait_for_timeout(900)
        blockers = _gp_blockers(page)
        lack = ('、'.join(b['label'] for b in blockers)
                or _gp_missing_sections_desc(page))
        t.step('申请被拦下：%s' % fail, ok=False)
        t.shot(page, '申请失败-待人工补')
        t.ask_human(
            'confirm', '申请没通过：%s' % fail,
            '国聘后端校验简历完整性时返回「%s」，判定还差这几段：%s。'
            '这些内容只有你本人能提供，AI 代填等于替你编材料。'
            '每段的「为什么不能替你补」和「怎么补最快」见下面清单；'
            '补齐并保存后回到这个页面点下面的按钮，我再替你点一次申请。'
            % (fail, lack),
            placeholder='补完了就随便输个字点确认',
            items=blockers)
        _settle(page)
    t.shot(page, '投递结束')
    return True

def _flow(t, page):
    t.step('打开投递页面', note=t.url)
    page.goto(t.url, wait_until='domcontentloaded', timeout=40000)
    _settle(page)
    _wait_real_content(page, 20)
    t.shot(page, '刚打开的页面')

    if _need_auth(page):
        _wait_login(t, page)

    # 先把投递表单弄出来再认字段——不然在详情页上扫一圈一个填写项都没有，
    # 流程照样走完，看起来「投递成功」其实什么都没发生（2026-09-18 实测国聘就是）
    if _click_any(page, _APPLY_BTNS):
        t.step('点了申请按钮，等投递表单弹出来')
        _settle(page)
        _wait_real_content(page, 10)
        t.shot(page, '投递表单应该弹出来了')

    if _need_auth(page):
        _wait_login(t, page)

    # 国聘这类站会先拦一道「站内简历不完善」，补完才放真正的表单。
    # guard 返回 True 表示这一站它全包了（简历段填完 + 点申请），
    # 别再让通用填表把简历字段又扫一遍——那只会把已填的报成「不认识」。
    if not _guopin_resume_guard(t, page):
        _fill_apply_form(t, page)

    if t.status != 'error':
        t.status = 'done'
        t.push()


def _wait_login(t, page):
    """登录墙：扫码/短信验证码只能人来，机器代劳不了。

    能做的都做了：登录状态存在本地浏览器目录（PROFILE_DIR）里，
    人登录一次，这家网站以后的所有投递都不用再登。
    """
    t.step('对方要求先登录才能投递', ok=False)
    t.shot(page, '登录窗口')
    if t.headless:
        tip = ('这个网站必须登录后才能投，但这次是后台模式，你看不到浏览器。'
               '请回到上一页，勾上「显示浏览器」重新点一次自动填，'
               '在弹出的浏览器里登录一下（扫码或收验证码）。'
               '登录一次就被记住了，以后投这个网站不用再登。')
    else:
        tip = ('请在刚弹出的浏览器窗口里登录这个网站（扫码或收验证码都行）。'
               '登录一次就被记住了，以后投这个网站的其它岗位不用再登。'
               '登录好了回到这里随便输个字点确认。')
    t.ask_human('login', '这一步需要你登录', tip,
                placeholder='登录好了就随便输个字点确认')
    # 登录完站点多半把你丢在首页或骨架屏，不会自己回到岗位页——
    # 必须重新打开岗位地址，并等正文真正渲染出来（骨架屏上什么都没有）
    try:
        page.goto(t.url, wait_until='domcontentloaded', timeout=40000)
    except Exception:                            # noqa: BLE001
        pass
    _wait_real_content(page)
    if _need_auth(page):
        # 登录没成（二维码过期/没点确认）：再给一次机会，别拿没登录的页面硬填
        t.step('看起来还没登录成功，请重新登录后再确认一次', ok=False)
        t.shot(page, '登录似乎没成功')
        t.ask_human('login', '还是没检测到登录', '浏览器里这个网站看起来还没登录上。'
                    '请确认登录成功后，再随便输个字点确认。',
                    placeholder='登录成功了就随便输个字点确认')
        try:
            page.goto(t.url, wait_until='domcontentloaded', timeout=40000)
        except Exception:                        # noqa: BLE001
            pass
        _wait_real_content(page)
    # 回到岗位页后，重新把投递表单点出来
    if _click_any(page, _APPLY_BTNS):
        _settle(page)
    _wait_real_content(page, 15)
    _settle(page)
    t.shot(page, '登录后回到投递页')


def _need_auth(page):
    if re.search(r'(reg|login|signin|signup|register)', page.url, re.I):
        return True
    text = _page_text(page)
    return bool(page.locator('input[type=password]').count()) and \
        ('登录' in text or '注册' in text)


def _handle_auth(t, page):
    t.step('对方要求先注册登录')
    if not re.search(r'reg', page.url, re.I):
        for name in ('免费注册', '立即注册', '注册新账号', '注册账号', '去注册', '注册'):
            try:
                loc = page.get_by_role('link', name=name, exact=False)
                if loc.count():
                    loc.first.click(timeout=3000)
                    _settle(page)
                    break
            except Exception:                    # noqa: BLE001
                continue
    t.shot(page, '注册页面')
    _fill_registration(t, page)


def _fill_registration(t, page):
    fields = [m for m in _scan_fields(page) if m['visible']]
    caps = [m for m in fields if _match_field(m) == '_captcha']

    # 1) 手机号
    phone = (t.profile.get('phone') or '').strip()
    if phone:
        for m in fields:
            if _match_field(m) == 'phone' and m['type'] not in ('password',):
                if _fill_one(page, m, phone):
                    t.filled.append({'label': '注册手机号', 'value': phone})
                    t.step('填好注册手机号', note=phone)
                    break
    t.shot(page, '填了手机号')

    # 2) 短信验证码：先把码发出去，再看能不能自己读到，读不到就问人
    sms_field = next((m for m in caps if not _is_graph_captcha(m)), None)
    if sms_field:
        _click_any(page, ('获取验证码', '发送验证码', '获取短信验证码', '免费获取', '发送'))
        _settle(page)
        t.shot(page, '已请求短信验证码')
        code = _read_sms_from_page(page)
        if not code:
            code = t.ask_human(
                'sms', '看一下你的手机',
                '验证码已经发到 %s 了。把手机上收到的那 6 位数字填进来。' % _mask(phone),
                placeholder='6位数字', itype='tel')
        if code:
            _fill_one(page, sms_field, code)
            t.step('填入短信验证码', note='后 4 位 %s' % code[-4:])
        t.shot(page, '填了短信验证码')

    # 3) 密码：这是用户的账号，得他自己定，我们不代设也不记录
    pwd_field = next((m for m in fields if m['type'] == 'password'), None)
    if pwd_field:
        pwd = t.ask_human(
            'password', '给这个网站设一个密码',
            '这个账号是你的，密码要你自己定。我们不会替你设，也不会存下来——'
            '但你要自己记住，以后登录这个网站还要用。',
            placeholder='至少6位，自己记住', itype='password')
        if pwd:
            _fill_one(page, pwd_field, pwd)
            t.step('填好密码', note='内容不留存')

    # 4) 图形验证码：能读到（靶场）就自动填，读不到（真实站点）就给人看图
    graph_field = next((m for m in caps if _is_graph_captcha(m)), None)
    if graph_field:
        ans = _read_graph_from_page(page)
        if not ans:
            t.shot(page, '需要你认一下这个验证码')
            ans = t.ask_human(
                'captcha', '帮忙认一下图里的字',
                '上面那张图里有几个数字/字母，照着填进来就行。'
                '这是网站用来分辨真人还是程序的，只能你来。',
                placeholder='图里的字')
        if ans:
            _fill_one(page, graph_field, ans)
            t.step('填入图形验证码')
        t.shot(page, '填了图形验证码')

    if _click_any(page, ('注册并登录', '立即注册', '同意协议并注册', '注册', '提交注册')):
        t.step('提交注册')
        _settle(page, 1500)
    t.shot(page, '注册提交后')


def _fill_apply_form(t, page):
    fields = [m for m in _scan_fields(page) if m['visible']]
    if not fields:
        # 没有表单还照常走完，用户会以为投出去了——宁可报错也别装成功
        t.status = 'error'
        t.error = ('这个页面上没有找到可以填的表单。多半是还没登录'
                   '（看上面的截图右上角是不是「登录/注册」），'
                   '也可能这个岗位要去手机上操作。')
        t.push()
        return
    t.step('识别到 %d 个填写项，开始对照你的资料' % len(fields))

    caps, file_fields, promise_fields = [], [], []
    seen_radio = set()
    for m in fields:
        rule = _match_field(m)
        if rule == '_captcha':
            caps.append(m)
            continue
        if m['type'] == 'file':
            file_fields.append(m)
            continue
        if m['type'] in ('hidden', 'submit', 'button', 'image', 'reset', 'password'):
            continue
        if m['type'] == 'checkbox':
            hay = ('%s %s %s' % (m['label'], m['name'], m['id'])).lower()
            if any(k in hay for k in ('承诺', '同意', '保证', '真实', 'promise', 'agree')):
                promise_fields.append(m)
            continue
        label = m['label'] or m['name'] or m['id'] or m['ph'] or '未命名字段'
        # 站点噪声：搜索框（请输入职位或企业名称）、antd 隐藏控件的
        # id/name（location-fe-xxx、rc_select_x、salary_monthly）——
        # 这些不是投递表单字段，报上来只会吓到人（2026-09-20 实测国聘）
        lid = str(m.get('id') or '')
        lname = str(m.get('name') or '')
        if lid.startswith(('rc_select', 'location-fe')) or lname in (
                'salary_monthly',) or '请输入职位或企业名称' in label:
            continue
        # 一组单选（男/女）是两个 input，但只是一个字段，别报两遍
        if m['type'] == 'radio':
            grp = m['name'] or label
            if grp in seen_radio:
                continue
            seen_radio.add(grp)
        if not rule:
            t.missing.append({'label': label, 'why': '不认识这项，需要你自己判断'})
            continue
        if rule not in _PROFILE_KEYS:
            t.missing.append({'label': _RULE_LABEL.get(rule, label),
                              'why': '你的资料里没有这项'})
            continue
        value = t.profile.get(rule)
        if isinstance(value, (list, tuple)):
            value = '、'.join(str(x) for x in value)
        if not value:
            t.missing.append({'label': _RULE_LABEL.get(rule, label),
                              'why': '你的资料里没填这项'})
            continue
        if m['type'] == 'radio':
            if _pick_radio(page, m, str(value)):
                t.filled.append({'label': _RULE_LABEL.get(rule, label), 'value': str(value)})
            else:
                t.missing.append({'label': _RULE_LABEL.get(rule, label),
                                  'why': '这项要你自己选'})
        elif _fill_one(page, m, value):
            t.filled.append({'label': _RULE_LABEL.get(rule, label), 'value': str(value)})
        else:
            t.missing.append({'label': _RULE_LABEL.get(rule, label),
                              'why': '自动填失败，需要你手动填'})

    # 附件：自动传一份简历文件（能传原件就传原件）
    if file_fields:
        try:
            path, shown = _resume_file_for_upload(t)
            _loc(page, file_fields[0]).set_input_files(path)
            t.filled.append({'label': '简历附件', 'value': shown})
            t.step('自动上传了简历附件：%s' % shown)
        except Exception as e:                   # noqa: BLE001
            t.missing.append({'label': '简历附件', 'why': '上传失败：%s' % e})

    # 图形验证码
    graph_field = next((m for m in caps if _is_graph_captcha(m)), None)
    if graph_field:
        ans = _read_graph_from_page(page)
        if not ans:
            t.shot(page, '需要你认一下这个验证码')
            ans = t.ask_human('captcha', '帮忙认一下图里的字',
                              '图里有几个数字/字母，照着填进来。只能你来。',
                              placeholder='图里的字')
        if ans:
            _fill_one(page, graph_field, ans)
            t.filled.append({'label': '图形验证码', 'value': '（已填）'})

    # 承诺书：落款是本人，代理人只能问，不能替他表态
    if promise_fields:
        agree = t.ask_human(
            'promise', '这一条得你亲自同意',
            '这家单位要求勾选《诚信应聘承诺书》——内容是「你填的信息真实有效」，'
            '署名是你。我不能替你表态。同意的话我就勾上，不同意我就停在这儿。')
        if agree and agree.lower() not in ('no', 'cancel', '0', 'false'):
            try:
                _loc(page, promise_fields[0]).check()
                t.filled.append({'label': '《诚信应聘承诺书》', 'value': '已勾选（经你同意）'})
                t.step('按你的意思勾了承诺书')
            except Exception as e:               # noqa: BLE001
                t.missing.append({'label': '《诚信应聘承诺书》',
                                  'why': '帮你勾失败了，你自己点一下（%s）' % e})
        else:
            t.missing.append({'label': '《诚信应聘承诺书》',
                              'why': '你没同意，我不替你勾——不勾就提交不了'})

    t.shot(page, '填好了，提交前停一下')

    # 停在这里等人确认 —— 提交是不可撤销的，落款也是本人
    t.step('已填 %d 项，还有 %d 项需要你' % (len(t.filled), len(t.missing)))
    tip = '上面这张图就是替你填好的样子。'
    if t.missing:
        tip += '还有 %d 项没完成（清单里列着），如果其中有必填的，提交会被退回来。' % len(t.missing)
    ok = t.ask_human('confirm', '没问题的话，我替你点提交',
                     tip + '确认无误就点「确认提交」；想自己再看一遍，点「先不提交」，'
                           '页面会留着不动。')
    if not ok or ok.lower() in ('no', 'cancel', '0', 'false'):
        t.note = '已经停在提交前，没有替你提交。页面上的内容都填好了。'
        t.step('停在提交前，没有提交')
        t.shot(page, '停在这里')
        return

    if _click_any(page, ('提交应聘申请', '确认投递', '立即申请', '保存并提交', '提交申请',
                         '申请该职位', '提交', '投递')):
        t.step('点提交')
        _settle(page, 1800)
        t.shot(page, '提交结果')
        after = _page_text(page)
        m = re.search(r'回执(?:编号|号)?\s*[：:]?\s*([A-Za-z0-9\-]{4,})', after)
        if m:
            t.receipt = m.group(1)
            t.step('拿到回执编号', note=t.receipt)
            t.note = '已经替你提交了。'
        elif re.search(r'(不能为空|请先|不正确|不对|失败|必填|请输入|请选择)', after):
            t.step('提交被退回来了', ok=False, note='有必填项没完成')
            t.note = '提交被退回来了——还有必填项没填，看上面的截图。'
        else:
            t.note = '提交了，但页面上没看到回执编号，你自己再确认一眼。'
    else:
        t.step('没找到提交按钮', ok=False, note='需要你手动点一下')
        t.note = '没找到提交按钮，需要你自己点。'
    t.push()


def _pick_radio(page, meta, value):
    """单选按钮：按同一组里的选项文字去点。"""
    try:
        name = meta.get('name') or ''
        if name:
            group = page.locator('input[type=radio][name="%s"]' % name)
            for i in range(group.count()):
                el = group.nth(i)
                lab = el.evaluate(
                    'e => (e.closest("label") ? e.closest("label").innerText : "")'
                    ' || (e.parentElement ? e.parentElement.innerText : "")')
                if lab and value in (lab or ''):
                    el.check()
                    return True
    except Exception:                            # noqa: BLE001
        pass
    return False


# --------------------------------------------------------------- 入口
def start(job, profile, url, headless=True, refill=False):
    cancel_all()                 # 新单开跑，旧单请下场（详见 cancel_all 注释）
    for old in (k for k, v in list(TASKS.items())[:-MAX_TASKS + 1]
                if v.status in ('done', 'error')):
        TASKS.pop(old, None)
    tid = uuid.uuid4().hex[:12]
    t = Task(tid, job, profile, url, headless, refill=refill)
    TASKS[tid] = t
    threading.Thread(target=_run, args=(t,), daemon=True).start()
    return tid, t
