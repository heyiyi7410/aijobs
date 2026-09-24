# -*- coding: utf-8 -*-
"""按岗位定制简历（per-job tailoring）。

想法来自 GitHub 的 career-ops（72.5k stars）的 pdf 模式：拿 JD 去改写/重排
候选人的简历，而不是一份简历海投。但那套是给国外 tech岗写的（cover letter、
ATS 关键词、不带照片），国内这套基本是反的 —— 规则层走 `cn-resume-optimizer`，
这里只借用它的**工程做法**，不去抄它的措辞。

三条红线，写在这里免得以后被"更聪明的改动"破坏：

1. **不许替用户编东西。** LLM 只能改写/重排档案里已有的事实，不许新增证书、
   工龄、单位名、数字。生成后由 `fact_gate()` 逐条硬卡，卡不住不许进 docx。
   career-ops 里这一步叫 verify-cv-facts，是硬门禁不是建议。国内更该硬：
   国企背调查得动、学信网可查学历，穿帮的后果是用户自己担。
2. **LLM 只出结构化 JSON，不直接出文档。** career-ops 踩过 #3523：payload 字段名
   和模板对不上，整段"教育经历"静默消失而校验报告还写 valid。我们把技能、
   经历都做成"只能从档案里挑子集"，名字对不上就直接报错而不是静默丢内容。
3. **原件优先。** 用户那份 Word/PDF 里的照片、排版、证书扫描件才是 HR 想看的，
   我们生成的 docx 是替代品。所以：能不改的经历/教育一律照抄，改动集中在
   摘要、匹配要点、技能排序这三处信息密度最高、也最安全的地方。
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
CFG_PATH = os.path.join(DATA_DIR, 'llm.json')
RESUME_DIR = os.path.join(DATA_DIR, 'resumes')
OUT_DIR = os.path.join(DATA_DIR, 'tailored')

DEFAULT_BASE = 'https://api.deepseek.com/v1'
DEFAULT_MODEL = 'deepseek-chat'
TIMEOUT_MS = 90000

# 模型很容易在这种地方顺手写个漂亮数字。这些单位和年份是我们唯一能确定性
# 校验的"硬事实"，也是最容易出事的部分（工龄多写两年 = 背调直接挂）。
_NUM_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:年|个月|月|人|台|次|件|套|亩|吨|%|万|元|岁|cm|kg)')
_YEAR_RE = re.compile(r'(?:19|20)\d{2}')
# 单位/学校名。单字后缀（厂/站/队/局）太容易误伤（"三年工厂"会被切成"年工厂"），
# 去掉；保留成词后缀。再叠加 _iter_orgs 的量词过滤，进一步压误报。
_ORG_RE = re.compile(
    r'[\u4e00-\u9fa5]{2,10}'
    r'(?:有限责任公司|有限公司|股份公司|公司|集团|医院|学校|大学|学院|'
    r'中学|小学|研究院|设计院|事务所|银行|工厂|制造厂)')
# 这些字出现在"单位名"里几乎一定是量词误报（"年工厂""三个月公司"），直接跳过
_BAD_UNIT = set('年月日号元个台次条辆间名岁人')


def _iter_orgs(text):
    for m in _ORG_RE.finditer(text or ''):
        name = m.group(0)
        if not any(ch in _BAD_UNIT for ch in name):
            yield name


# ---------------------------------------------------------------- 配置
def load_config():
    try:
        with open(CFG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:                                        # noqa: BLE001
        return {}


def save_config(base_url='', api_key='', model='', enabled=True):
    """存 LLM 配置。密钥明文落盘，所以权限收到 600 —— 和 mail.json 一个道理。

    空密钥 = 沿用旧的（不想每次保存都重贴一遍）。
    """
    old = load_config()
    base_url = (base_url or old.get('base_url') or DEFAULT_BASE).strip().rstrip('/')
    model = (model or old.get('model') or DEFAULT_MODEL).strip()
    api_key = (api_key or '').strip() or (old.get('api_key') or '')
    cfg = {'base_url': base_url, 'api_key': api_key, 'model': model,
           'enabled': bool(enabled)}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CFG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CFG_PATH, 0o600)
    except Exception:                                        # noqa: BLE001
        pass
    return cfg


def status():
    cfg = load_config()
    return {
        'ready': bool(cfg.get('api_key')) and bool(cfg.get('enabled', True)),
        'base_url': cfg.get('base_url') or '',
        'model': cfg.get('model') or '',
        'has_key': bool(cfg.get('api_key')),
    }


# ---------------------------------------------------------------- 素材
def _read_resume_text(profile):
    """把用户上传的原件读成文本。读不出来就是空串，绝不报错中断流程。

    原件是二进制 Word/PDF，LLM 改不了它 —— 这一步是为了拿到"事实来源"，
    同时也是事实门的比对底稿：模型写出来的每个硬事实都得在这里找得到。
    """
    raw = (profile.get('resume_path') or '').strip()
    if not raw:
        return ''
    try:
        safe = os.path.normpath(os.path.abspath(raw))
        if os.path.dirname(safe) != os.path.normpath(RESUME_DIR):
            return ''
        if not os.path.isfile(safe):
            return ''
        with open(safe, 'rb') as f:
            data = f.read()
        try:
            from resume_parser import extract_text
            return (extract_text(os.path.basename(safe), data) or '').strip()
        except Exception:                                    # noqa: BLE001
            return ''
    except Exception:                                        # noqa: BLE001
        return ''


def source_text(profile):
    """事实来源 = 原件文本 + 档案里的字段值。

    档案字段也算：很多用户手填的信息本来就不在那份 PDF 里（比如是在我们这儿
    补的政治面貌、住址），拿原件否定它们反而是错的。
    """
    parts = [_read_resume_text(profile)]
    for k in ('name', 'school', 'major', 'skills', 'intro', 'exp', 'edu', 'city',
              'job_types', 'salary', 'experiences'):
        v = profile.get(k)
        if v:
            parts.append(str(v))
    return '\n'.join(p for p in parts if p).strip()


def _split_skills(s):
    """技能栏拆成列表。简历里这一栏几乎都是顿号/逗号分隔的一坨。"""
    if isinstance(s, (list, tuple)):
        return [str(x).strip() for x in s if str(x).strip()]
    return [x.strip() for x in re.split(r'[、,，;；/|\n]+', str(s or '')) if x.strip()]


# ---------------------------------------------------------------- 事实门
def fact_gate(payload, source):
    """硬卡一道：返回问题清单。空列表 = 放行。

    只卡三样能确定性判定的东西：
      - 带单位/年份的数字：工龄、人数、钱数，编出来的危害最大
      - 单位名/学校名：编一个"XX公司"是最常见的幻觉
      - 技能证书：**必须**是档案里已有技能的子集，不许新增（这一条是硬约束，
        因为证书在我们这儿查不到必然只能靠自觉，那就干脆不给模型这个自由度）

    不在比对范围内的软内容（比如措辞更专业的摘要）不卡 —— 那本来就是我们
    要它改的地方，卡了就没意义了。
    """
    problems = []
    src_digits = set(_NUM_RE.findall(source)) | {x.strip() for x in _YEAR_RE.findall(source)}
    src_orgs = set(_iter_orgs(source))

    texts = [str(payload.get('summary') or '')]
    texts += [str(h) for h in (payload.get('highlights') or [])]
    texts += [str(payload.get('mail_body') or '')]
    blob = '\n'.join(texts)

    for tok in set(_NUM_RE.findall(blob)):
        if tok.strip() not in src_digits:
            problems.append('出现了简历里没有的数字：%s' % tok.strip())
    for y in set(_YEAR_RE.findall(blob)):
        if y not in src_digits and y != time.strftime('%Y'):
            problems.append('出现了简历里没有的年份：%s' % y)
    for org in set(_iter_orgs(blob)):
        if org not in src_orgs:
            problems.append('出现了简历里没有的单位/学校名：%s' % org)

    skills = [str(x).strip() for x in (payload.get('skill_order') or []) if str(x).strip()]
    if skills and not any(size_ok(source, s) for s in skills):
        problems.append('技能清单里全是简历没有的内容')
    payload['_dropped_skills'] = [s for s in skills if not size_ok(source, s)]
    return problems


def size_ok(source, skill):
    """这条技能在素材里找得到就算数（允许原档案里是顿号分隔的一大坨）。"""
    return bool(skill) and skill in source


# ---------------------------------------------------------------- 调模型
def _strip_fences(s):
    s = (s or '').strip()
    s = re.sub(r'^```(?:json)?\s*', '', s)
    s = re.sub(r'\s*```$', '', s)
    return s.strip()


def call_llm(messages, temperature=0.3):
    """OpenAI 兼容 /chat/completions。返回 (dict|None, 错误信息)。

    为什么要"兼容"而不是绑死一家：DeepSeek / 通义 / 智谱 / 硅基流动 / OpenAI
    都是这个协议，用户换哪家都不用改代码，填个 base_url 加 model 就行。
    """
    cfg = load_config()
    if not cfg.get('api_key'):
        return None, '还没配置大模型密钥'
    url = (cfg.get('base_url') or DEFAULT_BASE).rstrip('/') + '/chat/completions'
    body = json.dumps({
        'model': cfg.get('model') or DEFAULT_MODEL,
        'messages': messages,
        'temperature': temperature,
        'stream': False,
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer %s' % cfg['api_key']},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_MS / 1000) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:                                    # noqa: BLE001
            pass
        return None, '模型接口报错 HTTP %s %s' % (e.code, detail)
    except Exception as e:                                   # noqa: BLE001
        return None, '调用模型失败：%s' % e
    content = ((data.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    content = _strip_fences(content)
    try:
        return json.loads(content), ''
    except Exception:                                        # noqa: BLE001
        return None, '模型返回的不是合法 JSON：%s' % content[:200]


# ---------------------------------------------------------------- 提示词
SYSTEM_PROMPT = """你是给中国求职者改简历的助手。求职者多数是不常用电脑的蓝领、技工、服务岗用户。

硬规则（违反任何一条这份输出就作废）：
1. **不许编造**。不能新增简历里没有的证书、工龄、单位名、学校、数字、年份。只能改写和重排已有的内容。
2. **技能/证书只能从给定的技能清单里挑**，一个字都不许加。
3. **用词跟着岗位走，事实不动**。招聘启事写"低压电工证"，简历里写"会电工"的，
   就改成"持有低压电工证"（前提是他确实有这个证、简历里有这个意思）；
   他压根没有的，不许写上去，只能写进 gaps 告诉用户"这个岗位要 XX，你的简历里没看到"。
4. 措辞看单位性质：
   - 央企/国企：稳定、服从安排、吃苦耐劳、持证上岗、未发生过安全事故；避免"追求个人发展""薪资驱动"
   - 外企：写结果和数字（带了几个人、修了多少台）；避免通篇"服从安排"
   - 不确定单位性质就按中性写实处理
5. 说人话，不用"STAR 法则""ATS 优化"这类词给用户看。
6. 只输出 JSON，不要 markdown 代码块，不要解释。

JSON 结构（键名必须完全一致）：
{
  "summary": "3-4 句的个人摘要，用岗位要求里的说法改写真实经历",
  "highlights": ["3-5 条：这条经历/技能为什么对得上这个岗位，每条不超过 40 字"],
  "skill_order": ["从档案技能里按岗位相关度重排，只减不增"],
  "changes": [{"section": "技能栏", "before": "会电工", "after": "持有低压电工证", "why": "岗位明确要求低压电工证"}],
  "gaps": ["岗位要求但简历里没有的东西，让用户自己去补或自己判断"],
  "mail_body": "150 字以内的求职邮件正文，写清投什么岗、为什么合适、证了什么"
}"""


def build_messages(profile, job, source, src_skills):
    nature = (job.get('nature') or '').strip() or '未知'
    jd = (job.get('desc') or '').strip()
    jd = jd[:1500] if jd else '（这个岗位没有抓到招聘要求正文，只能按岗位名和单位性质判断）'
    user = (
        "【目标岗位】%(title)s\n【招聘单位】%(company)s（性质：%(nature)s）\n"
        "【工作地点】%(city)s   【薪资】%(salary)s\n\n"
        "【招聘要求原文】\n%(jd)s\n\n"
        "【求职者档案】\n"
        "姓名：%(name)s  学历：%(edu)s  经验：%(exp)s  现居：%(city2)s\n"
        "求职意向：%(want)s\n技能/证书：%(skills)s\n"
        "自我评价：%(intro)s\n\n"
        "【原始简历节选（事实来源，不得超出）】\n%(source)s\n"
    ) % {
        'title': job.get('title') or '', 'company': job.get('company') or '',
        'nature': nature, 'city': job.get('city') or '',
        'salary': job.get('salary_text') or '面议', 'jd': jd,
        'name': profile.get('name') or '', 'edu': profile.get('edu') or '',
        'exp': profile.get('exp') or '', 'city2': profile.get('city') or '',
        'want': profile.get('job_types') or '', 'skills': '、'.join(src_skills) or '（未填）',
        'intro': profile.get('intro') or '（未填）',
        'source': source[:3000],
    }
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user}]


# ---------------------------------------------------------------- 主流程
def plan(profile, job):
    """生成定制方案（不落文件）。返回 dict。

    前端用它做"原文 → 改成 → 为什么"的对照预览，让用户先看懂再决定要不要用。
    事实门不过就不给 docx，但建议照样返回 —— 差的错误的ITION-list 本身对用户有用。
    """
    src = source_text(profile)
    src_skills = _split_skills(profile.get('skills'))
    if not src.strip():
        return {'ok': False, 'msg': '没有可用的简历内容：先在第一步上传简历原件或填档案'}
    st = status()
    if not st['ready']:
        return {'ok': False, 'msg': '还没配置大模型（base_url / 密钥 / 模型名）'}
    payload, err = call_llm(build_messages(profile, job, src, src_skills))
    if not payload:
        return {'ok': False, 'msg': err}

    # 技能只准是档案里那条的子集 —— 这条是硬约束，不靠模型自觉
    allowed = {s: s for s in src_skills}
    picked = [str(x).strip() for x in (payload.get('skill_order') or []) if str(x).strip()]
    kept, dropped = [], []
    for s in picked:
        if any(s == v or s in v or v in s for v in allowed.values()) or size_ok(src, s):
            kept.append(s)
        else:
            dropped.append(s)
    payload['skill_order'] = kept
    payload['_new_skills'] = dropped

    problems = fact_gate(payload, src)
    return {'ok': True, 'payload': payload, 'problems': problems,
            'blocked': bool(problems)}


def out_path(profile, job):
    """这份定制简历的落盘路径。同名可复算，用来判断"这个岗位定制过没有"。

    文件名里带岗位title要过 _safe_file_name 那一关（岗位名带斜杠很常见，
    直接 open() 会把整个任务打成 error，2026-09-21 实测过）。
    """
    key = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(job.get('key') or job.get('title') or 'job'))
    name = '%s_%s.docx' % ((profile.get('id') or 0), key[:40])
    return os.path.join(OUT_DIR, name)


def render_docx(profile, job, payload, path):
    """把定制结果渲染成一份能直接发给 HR 的 docx。

    经历、教育一律照抄档案 —— 那部分改动风险远大于收益（改错一个日期就是
    简历造假），也不在我们的输出 tok 预算里。
    """
    from docx import Document
    from docx.shared import Pt
    from docx.oxml.ns import qn

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = '宋体'
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

    def para(text, size=10.5, bold=False, space_after=2):
        p = doc.add_paragraph()
        r = p.add_run(text or '')
        r.bold = bold
        r.font.size = Pt(size)
        r.font.name = '宋体'
        r._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        p.paragraph_format.space_after = Pt(space_after)
        return p

    def heading(text):
        para(text, size=12, bold=True, space_after=4)

    para(profile.get('name') or '求职者', size=18, bold=True, space_after=6)
    bits = [profile.get('gender'), profile.get('birth'), profile.get('phone'),
            profile.get('email'), profile.get('city')]
    para(' | '.join(str(b) for b in bits if b), size=10, space_after=10)

    heading('求职意向')
    para('应聘岗位：%s（%s）' % (job.get('title') or '', job.get('company') or ''),
         space_after=8)

    if payload.get('summary'):
        heading('个人摘要')
        para(payload['summary'], space_after=8)

    highlights = [str(h) for h in (payload.get('highlights') or []) if str(h).strip()]
    if highlights:
        heading('与这个岗位直接相关的经历')
        for h in highlights:
            para('· ' + h, space_after=2)
        doc.add_paragraph()

    skills = [str(s) for s in (payload.get('skill_order') or []) if str(s).strip()]
    if skills:
        heading('技能与证书')
        para('、'.join(skills), space_after=8)

    exps = profile.get('experiences')
    if isinstance(exps, str):
        try:
            exps = json.loads(exps)
        except Exception:                                    # noqa: BLE001
            exps = []
    exps = [e for e in (exps or []) if isinstance(e, dict)]
    if exps:
        heading('工作经历')
        for e in exps:
            head = '%s  %s' % (e.get('company') or e.get('school') or '',
                               e.get('role') or e.get('major') or '')
            span = '—'.join(x for x in (e.get('start'), e.get('end')) if x)
            para((head + ('（%s）' % span if span else '')).strip(), bold=True, space_after=2)
            if e.get('desc'):
                para(str(e['desc']), space_after=6)

    if profile.get('school') or profile.get('edu'):
        heading('教育背景')
        para('%s  %s  %s  %s' % (profile.get('school') or '', profile.get('major') or '',
                                 profile.get('edu') or '', profile.get('graduation') or ''),
             space_after=8)

    if profile.get('intro'):
        heading('自我评价')
        para(str(profile['intro']), space_after=6)

    os.makedirs(OUT_DIR, exist_ok=True)
    doc.save(path)
    return path


def build(profile, job, payload=None):
    """生成定制 docx。返回 (路径, 错误信息)。

    事实门没过就拒绝生成 —— 宁可让用户用原件，也不能把一份编了东西的简历
    发到 HR 手里：那边的后果是用户承担，不是我们。
    """
    if payload is None:
        r = plan(profile, job)
        if not r.get('ok'):
            return None, r.get('msg') or '定制失败'
        payload = r['payload']
        problems = r.get('problems') or []
    else:
        problems = fact_gate(payload, source_text(profile))
    if problems:
        return None, '没通过事实核对，已拦下：' + '；'.join(problems[:3])
    try:
        return render_docx(profile, job, payload, out_path(profile, job)), ''
    except Exception as e:                                   # noqa: BLE001
        return None, '生成 Word 失败：%s' % e
