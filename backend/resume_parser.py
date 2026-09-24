# -*- coding: utf-8 -*-
"""简历解析：把用户传上来的简历读成文本，再挑出我们能用的字段。

为什么要做这个：目标用户不常用电脑，让他在第 1 步手打姓名、手机、邮箱、学历、技能……
是一道很高的门槛。但大部分人手里其实已经有现成的简历（Word 或 PDF），
所以第一步的入口应该是「有简历就传上来」，而不是「从头打字」。

做法上守住两条，写在这里免得以后被改坏：

1. **只填识别得出来的。** 识别不到就留空、让用户自己补，绝不猜。
   手机号和邮箱填错的后果是面试通知发不到人，比让用户多打两个字严重得多。
2. **区分可信度。** 手机号、邮箱有固定格式，认出来就是对的（high）；
   姓名靠排版猜，会认错（low）——标 low 的字段界面上必须提示「核对一下」。

支持 PDF / docx / txt。docx 用标准库拆包（它就是 zip + XML，不需要第三方库）；
PDF 用 pypdf（纯 Python，requirements.txt 有）。老式 .doc 和照片版读不了，
这种情况给一句人话告诉他怎么办，而不是抛个看不懂的错。
"""
import io
import re
import zipfile
from functools import lru_cache

# 能读的扩展名
SUPPORTED = ('.pdf', '.docx', '.txt', '.md')

# 传输前先在界面上拦一道，后端也再挡一次
MAX_BYTES = 5 * 1024 * 1024

# 认得出但会认错的字段（界面上要提示核对）
FIELD_LABEL = {
    'name': '姓名', 'phone': '手机号', 'email': '邮箱', 'age': '年龄',
    'edu': '学历', 'exp': '工作年限', 'city': '期望城市',
    'skills': '会做的事', 'intro': '自我介绍',
    'gender': '性别', 'birth': '出生年月', 'school': '毕业院校',
    'major': '所学专业', 'graduation': '毕业时间',
    # 工作经历是唯一一个「值不是标量」的字段：它是一串条目（公司/职位/起止/职责），
    # 简历里写得最满、而投国聘时最缺的就是它（2026-09-20 补）。
    'experiences': '工作经历',
}

# 城市兜底表 —— 真正用的是前端传过来的那份（frontend/src/options.js 的 CITIES），
# 这里只在没传的时候兜底，省得以后改了一边忘了另一边。
DEFAULT_CITIES = ('北京', '上海', '广州', '深圳', '杭州', '成都', '武汉', '西安',
                  '长沙', '苏州', '合肥', '厦门', '重庆', '天津', '南京', '郑州',
                  '青岛', '宁波', '东莞', '佛山', '无锡', '福州', '济南', '大连',
                  '昆明', '南昌', '南宁', '贵阳', '哈尔滨', '沈阳', '温州', '常州')


# --------------------------------------------------------------- 取纯文本
def _decode(data):
    """按常见中文编码依次试，别让乱码把后面的字段识别全废掉。"""
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'utf-16'):
        try:
            t = data.decode(enc)
            if t.count('\ufffd') <= len(t) * 0.01:      # 替换符太多说明猜错了
                return t
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('gb18030', errors='replace')


def _docx_text(data):
    """docx 就是 zip + XML，标准库直接拆，不引第三方库。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            xml = z.read('word/document.xml')
        except KeyError:
            return ''
    # 命名空间用通配，省得把一整串 URL 写死在匹配里
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    tag_t, tag_p = '}t', '}p'
    lines = []
    for p in root.iter():
        if not p.tag.endswith(tag_p):
            continue
        seg = ''.join(x.text or '' for x in p.iter() if x.tag.endswith(tag_t))
        lines.append(seg)
    return '\n'.join(lines)


def _pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, ('服务器上还缺一个读 PDF 的小组件。'
                      '让技术人员执行 pip install pypdf 就行；'
                      '或者你把简历用 Word 另存为 .docx 再传。')
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt('')                    # 有些 PDF 只设了空密码
            except Exception:                         # noqa: BLE001
                return None, '这份 PDF 加了密码，打不开。请传没有密码的版本。'
        pages = []
        for pg in list(reader.pages)[:20]:             # 简历不会有 20 页，防跑飞
            pages.append(pg.extract_text() or '')
        return '\n'.join(pages), ''
    except Exception as e:                            # noqa: BLE001
        return None, '这份 PDF 打不开（%s）。可以试试另存为 .docx 再传。' % str(e)[:60]


def extract_text(filename, data):
    """返回 (纯文本, 错误说明)。读不了时文本为 None、说明是人话。"""
    name = (filename or '').lower()
    ext = name[name.rfind('.'):] if '.' in name else ''

    if ext in ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.heic'):
        return None, ('照片版简历暂时读不了——照片上的字要专门的技术才能认。'
                      '如果原来有 Word 或 PDF 版本，传那份更准。')
    if ext == '.doc':
        return None, ('这种老式 Word（.doc）读不了。用 Word 打开它，'
                      '「另存为」选「Word 文档(.docx)」，再传上来就行。')
    if ext not in SUPPORTED:
        return None, ('只认 PDF、Word(.docx) 和纯文本(.txt) 三种。'
                      '现在传的是 %s。' % (ext or '没有扩展名的文件'))

    if ext == '.pdf':
        return _pdf_text(data)
    if ext == '.docx':
        try:
            text = _docx_text(data)
        except Exception as e:                        # noqa: BLE001
            return None, '这个 Word 文件打不开（%s），可能没传完整。' % str(e)[:60]
        return text, ''
    return _decode(data), ''


# --------------------------------------------------------------- 提字段
# 全角数字/冒号先归一，不然「１３８」这种正规排版反而认不出来
_FULLWIDTH = str.maketrans('０１２３４５６７８９：', '0123456789:')


def _norm(text):
    t = (text or '').translate(_FULLWIDTH).replace('\u00a0', ' ')
    t = re.sub(r'[ \t\u3000]+', ' ', t)
    return t


_EDU_RANK = [
    ('小学', ('小学',)),
    ('初中', ('初中',)),
    ('高中中专', ('高中', '中专', '职高', '技校', '中技', '职专', '中等职业')),
    ('大专', ('大专', '专科', '高职', '高专', '大学专科')),
    ('本科及以上', ('本科', '学士', '硕士', '研究生', '博士', 'mba', 'MBA')),
]

_SKILL_KEYS = (
    ('有电工证', ('电工证', '低压电工', '高压电工', '电工进网', '强电')),
    ('有焊工证', ('焊工证', '电焊', '焊接', '气保焊')),
    ('有叉车证', ('叉车',)),
    ('会开车', ('驾驶证', '驾照', '会开车', '开车', 'c1', 'C1', 'b2', 'B2', '驾驶')),
    ('会骑电动车', ('电动车', '摩托车', '骑手')),
    ('会用电脑', ('会用电脑', '计算机操作', '懂电脑')),
    ('会办公软件', ('办公软件', 'excel', 'EXCEL', 'Excel', 'word', 'Word', 'wps', 'WPS', 'office')),
    ('会做饭', ('厨师', '做饭', '后厨', '配菜', '面点')),
    ('会收银', ('收银',)),
    ('会维修', ('维修', '检修', '修理', '保养')),
    ('体力好', ('搬运', '装卸', '体力', '重体力')),
    ('能上夜班', ('夜班', '夜间')),
    ('能接受倒班', ('倒班', '三班', '轮班')),
    ('普通话好', ('普通话',)),
    ('会英语', ('英语', '四级', '六级', 'cet', 'CET')),
    ('有护士证', ('护士证', '护理', '护师', '养老护理')),
    ('有保安证', ('保安证',)),
    ('有安全员证', ('安全员', '安全生产管理')),
)

_CITY_LABELS = ('期望城市', '意向城市', '期望工作城市', '期望工作地', '意向工作地',
                '期望地区', '目标城市', '求职意向', '现居', '所在城市', '所在地',
                # 住址/户籍也算：蓝领岗位基本就近找，简历上没写期望城市时，
                # 拿住址当默认值比空着让人从三十多个城市里挑要省事。
                '家庭住址', '住址', '户籍所在地', '户籍')

_INTRO_LABELS = ('自我评价', '自我介绍', '个人评价', '自我描述', '个人简介',
                 '个人优势', '自我介绍及特长', '个人特长')


def _current_year():
    import time
    return int(time.strftime('%Y'))


# 把分隔符去掉，1 3 8 - 0013 8000 这种排版也能认。
# +86 这种国际区号也一并认——很多人（尤其海外留学回来的）就在简历里这么写，
# 只按纯 11 位找会漏掉，最后变成「简历上有电话，系统说没有」。
def _find_phone(t):
    flat = re.sub(r'[\s\-‑–—()（）]', '', t)
    m = re.search(r'(?<!\d)(?:\+?86)?(1[3-9]\d{9})(?!\d)', flat)
    return m.group(1) if m else ''


def _find_email(t):
    m = re.search(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', t)
    return m.group(0) if m else ''


def _find_name(t):
    """先找「姓名：张三」这种带标签的（可信）；找不到再猜第一行（要人核对）。"""
    m = re.search(r'姓\s*名\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})(?![\u4e00-\u9fa5])', t)
    if m:
        return m.group(1), 'high'
    for line in t.split('\n')[:6]:
        s = line.strip()
        if not (2 <= len(s) <= 4):
            continue
        if not re.fullmatch(r'[\u4e00-\u9fa5]+', s):
            continue
        if any(k in s for k in ('简历', '求职', '应聘', '个人', '基本', '信息',
                                '工作', '经历', '教育', '技能', '评价', '男', '女')):
            continue
        return s, 'low'
    return '', ''


def _find_age(t):
    m = re.search(r'年\s*龄\s*[:：]?\s*(\d{1,2})', t) or re.search(r'(\d{1,2})\s*岁', t)
    if m:
        age = int(m.group(1))
        if 16 <= age <= 70:
            return str(age)
    # 「出生年月：1999.05」这种，用出生年推年龄
    m = re.search(r'出\s*生(?:年月|日期|时间)?\s*[:：]?\s*((?:19|20)\d{2})', t)
    if m:
        age = _current_year() - int(m.group(1))
        if 16 <= age <= 70:
            return str(age)
    return ''


def _find_edu(t):
    """一份简历会写好几段学历（本科、高中…），取最高的那个当现状。"""
    low = t.lower()
    best = -1
    for i, (label, keys) in enumerate(_EDU_RANK):
        if any(k.lower() in low for k in keys):
            best = max(best, i)
    return _EDU_RANK[best][0] if best >= 0 else ''


_CN_NUM = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

# 年限既能写「5 年」也能写「五年」。
# ⚠️ 必须带数字边界 (?<!\d)(?!\d)：不带的话「2020年工作经验」「2015至2020年的工作经验」
#    会从四位年份中间截出 "20"，被当成 20 年经验填进表单。年限是从适岗与否到
#    排序权重的依据，编一个错的值比留空更糟 —— 而 exp 不在 low 清单里，
#    界面不会提示用户核对，等于悄悄替他造了个假数据。
_N = r'(?<!\d)(\d{1,2}|[一二两三四五六七八九十])(?!\d)'

# 旧写法（未加边界），留着给回归用例对照：test_50 盯的就是这个
_LEGACY_N = r'(\d{1,2}|[一二两三四五六七八九十])'


def _years(g):
    return int(g) if g.isdigit() else _CN_NUM.get(g, 0)


def _exp_years(t):
    """从文字里找出「干了几年」。找不到返回 -1（不是 0）。"""
    # 认可的说法只有明写「几年」这一种。工作经历里的起止年份（2019-2023）
    # 和学制（三年制大专）都不算 —— 前者按最早年份算会虚报年限，
    # 后者根本不是工作年限。宁可让用户自己填，也不替他猜（README 第 1 步「只读」原则）。
    for pat in (_N + r'\s*年(?:以上)?(?:的)?(?:工作)?经[验历]',   # 5年经验 / 两年工作经验
                r'工作经[验历]\s*[:：]?\s*' + _N + r'\s*年',      # 工作经历：5年
                r'工龄\s*[:：]?\s*' + _N + r'\s*年'):             # 工龄 8 年
        m = re.search(pat, t)
        if m:
            return _years(m.group(1))
    # 口语写法：「做电工 5 年」「干这行三年」「从事电工工作 4 年」。
    # 中间隔的那几个字**不许是数字** —— 否则「做电工 2019 年到 2024 年」会被读成 19 年。
    m = re.search(r'(?:做|干|从事|从业|工作|当了)[^，。；、,;\n\d]{0,8}?'
                  + _N + r'\s*年(?!制|一贯)', t)
    if m:
        return _years(m.group(1))
    return -1


def _find_exp(t):
    if any(k in t for k in ('应届', '无工作经验', '没工作经验', '无经验')):
        return '没经验'
    n = _exp_years(t)
    if n < 0:
        # 不按「工作经历的起止年份」去推年限：试过，会算错——比如
        # 「2021.07-2023.06 某某公司」其实是 2 年，按最早那年起算就变成 5 年以上了。
        # 宁可不填让用户点一下，也不能给他一个错的年限（会直接影响岗位排序）。
        return ''
    if n < 1:
        return '1年以内'
    if n < 3:
        return '1-3年'
    if n < 5:
        return '3-5年'
    return '5年以上'


def _find_city(t, cities):
    """城市容易认错：『北京大学』里有『北京』，但那是学校不是期望城市。
    所以只在带标签的附近找，找不到就留空让用户自己选。"""
    for label in _CITY_LABELS:
        for m in re.finditer(re.escape(label), t):
            seg = t[m.end():m.end() + 24]
            for c in cities:
                if c and c in seg:
                    return c
    return ''


def _find_skills(t):
    low = t.lower()
    return [label for label, keys in _SKILL_KEYS
            if any(k.lower() in low for k in keys)]


# 「教你怎么写简历」的说明文字，和真的自我介绍是两种东西。
# 真的在写自我介绍的人不会说这些词 —— 命中就说明我们抓到的其实是说明书，
# 这时候宁可留空让用户自己写，也绝不能把说明书塞进他的简历里。
_INTRO_META = ('网申', '简历', '模板', '美观', '文本框', '上传', '通过率',
               '务必', '纯真实', '填写', '范例', '如下图', '如上图',
               '如图所示', '须知', '注意事项', '怎么写', '如何写', '示例')

# 标签后面紧跟这些词 → 那是说明文档的小标题（「自我评价怎么写」），不是正文
_INTRO_HEAD = re.compile(r'\s*(?:怎么写|如何写|该写|要写|应该怎么写|示例|范例|模板)')

# 「怎么写简历」的说明／须知文档里满是这些词。真简历偶尔也会出现「简历」两个字，
# 所以要求命中 3 个以上才算 —— 宁可少提醒，也不能把正常简历说成说明书。
_DOC_SMELL = ('简历', '网申', '模板', '说明', '须知', '注意事项', '填写', '上传',
              '通过率', '老师', '务必', '照片信息', '审核材料', '范例')
_SMELL_MIN = 3


def _find_intro(t):
    for label in _INTRO_LABELS:
        for m in re.finditer(re.escape(label), t):
            seg = t[m.end():]
            if _INTRO_HEAD.match(seg):
                continue
            seg = re.sub(r'^[\s:：\-—]*', '', seg)
            # 取到下一个常见小标题为止，不然会把整份简历的后半截都吞进来
            seg = re.split(r'\n\s*(?:工作经历|教育经历|项目经历|技能证书|证书|获奖|'
                           r'自我评价|自我介绍|联系方式|个人信息)\s*[:：]?\s*\n', seg)[0]
            # PDF 双栏排版的左栏（时间那一列）会整列插到自我介绍后面，
            # 实测「个人优势」段末尾就是这么混进「2023.01-至今 2020.04-2022.12」的，
            # 读起来像在说自己的工作年限。时间行单独剔掉。
            seg = '\n'.join(l for l in seg.split('\n')
                            if not _WORK_RANGE_RE.fullmatch(l.strip()))
            seg = re.sub(r'\s*\n\s*', ' ', seg).strip()[:300]
            if len(seg) < 6:
                continue
            if any(k in seg for k in _INTRO_META):
                continue                    # 认出来是说明书 → 换下一个候选，都不行就留空
            return seg
    return ''


# --------------------------------------------------------------- 基础信息
# 招聘表单最爱问、而简历里其实都写着的那几项：性别、出生年月、学校、专业、毕业时间。
# 之前没解析这几项，自动填表就只能空着让人补——对不常用电脑的人来说，
# 「明明简历上有，还要我一个字一个字打」最劝退。所以这里补上。
#
# 依然守住「只填识别得出来的」：这几项全都要求**带标签**（性别：男 / 出生年月：1999.05），
# 不从正文里猜。猜错学校、猜错出生年月的代价比让用户看一眼严重得多。
_SEX_RE = re.compile(r'性\s*别\s*[:：]?\s*(男|女)')

# 出生年月：1999.05 / 1999年5月 / 1999-05-12 / 1999/05
_BIRTH_RE = re.compile(
    r'(?:出\s*生(?:年月|日期|时间)?|生\s*日)\s*[:：]?\s*'
    r'((?:19|20)\d{2})\s*[年\-\./]?\s*(\d{1,2})?')

# --------------------------------------------------------------- 教育经历
# 真正的简历绝大多数**不写**「毕业院校：XXX」这种填空式，而是教育表格一行带过：
#     2020.09-2024.06    武汉交通职业学院（公办）    航海技术    专科
# 所以学校和专业必须从这一行里认。光靠「学校」「专业」两个词去正文里裸搜会出事——
# 实测一份真简历里，「专业」抓到的是「专业特色：获BCS」，「学校」抓到的是「代表学校参赛
# 多次获得省级」。从那以后：先解析教育行，行里没有才退回标签，且标签必须带冒号。
_EDU_HEAD_RE = re.compile(r'教育(?:背景|经历)|学历(?:背景|经历)?|学习经历|在校经历|教育情况')

# 教育段到这儿就结束了。要求是**独占一行的短标题**（「获奖情况」），
# 不能是「荣誉奖项：伦敦商业金融学院…」这种带正文的行——按关键词硬截会把
# 第二所学校直接截没（实测过：只剩东伦敦大学，武汉交通职业学院丢了）。
_EDU_STOP_WORDS = ('实习', '实践', '工作', '项目', '获奖', '荣誉', '技能',
                   '证书', '培训', '实训', '校园', '校内', '社会经历',
                   '求职意向', '自我评价', '自我介绍')

# 很多人把「网申注意事项」附在简历后面，那里面也有一份教育经历，不能拿它当正文
_EDU_DOC_RE = re.compile(r'(?:网申|填写注意事项|注意板块|投递提醒|邮件主题)')

_DATE_RE = re.compile(r'(?:19|20)\d{2}\s*[.\-/年]\s*\d{1,2}\s*[月日]?')

# 学校名：以这些词收尾，前后都带括号注释的（「东伦敦大学（伦敦第6）」）只取主体
_SCHOOL_RE = re.compile(
    r'([\u4e00-\u9fa5A-Za-z0-9·\-\.]{2,20}'
    r'(?:大学|学院|学校|中学|职校|技校|中等专业学校|职业技术学院|职业学院|'
    r'技师学院|师范学校|附属中学|研究院|分校|校区))')

# 学历等级（从高到低），用来在好几段教育经历里挑最高的那条
_ROW_EDU = (('博士', ('博士', 'PHD', 'Phd', 'phd')),
            ('硕士', ('硕士', '研究生', 'MBA', 'mba', 'Mba', 'master', 'Master')),
            ('本科', ('本科', '学士', '双学位')),
            ('大专', ('大专', '专科', '高职', '高专')),
            ('高中中专', ('高中', '中专', '职高', '技校', '中技', '职专')))

_EDU_ORDER = [k for k, _ in _ROW_EDU]


def _row_level(s):
    """这行是什么学历？认不出来返回 -1。"""
    best = -1
    for i, (_, keys) in enumerate(_ROW_EDU):
        if any(k in s for k in keys):
            best = max(best, len(_EDU_ORDER) - 1 - i)   # 博士分最高
    return best


def _edu_rows(t):
    """拆出教育段落里的每一条经历：{'school','major','level','end'}。"""
    heads = [m.start() for m in _EDU_HEAD_RE.finditer(t)]
    rows = []
    for head in heads:
        seg = t[head:head + 3000]
        m = _EDU_DOC_RE.search(seg)
        if m:
            seg = seg[:m.start()]
        got = []
        for line in seg.split('\n'):
            line = line.strip()
            if not line:
                continue
            if len(line) <= 16 and line.startswith(_EDU_STOP_WORDS):
                break                           # 下一个大标题，教育段结束
            if len(line) > 220:
                continue
            # 「情况说明：2025年2月至7月，于对外经济贸易大学青岛研究院…」
            # 这种是上一个学校的补充说明，不是一条经历——行首带冒号的一律跳过
            if re.match(r'^.{0,8}[:：]', line):
                continue
            if not _DATE_RE.search(line):
                continue                        # 没有起止时间的不是教育经历行
            # 列分隔靠**单空格**切：原文里那一长串空格在归一化时已经被压成一个了
            # （PDF 提取出来的更是如此），所以不能按「两个以上空格」分列。
            # 一行通常就三样东西：学校、专业、学历，认得出这三样就够了。
            toks = [x for x in re.split(r'\s+', _DATE_RE.sub(' ', line, count=2))
                    if x and x not in ('-', '–', '—', '~', '至')]
            school, si = '', -1
            for i, c in enumerate(toks):
                sm = _SCHOOL_RE.search(c)
                if sm:
                    # 前缀里可能粘着「于/在/毕业于」这类字（正文里提到学校时常见），去掉
                    school = re.sub(r'^(?:毕业于|就读于|就读|毕业|于|在|来自)',
                                    '', sm.group(1))
                    si = i
                    break
            if not school:
                continue
            # 专业：学校后面剩下的词里，去掉学历词、再去掉像学校名的，剩下的就是它。
            # 带标点的（「至2026年1月，完成LQA」）说明取偏了，宁可空着也别填错。
            major = ''
            for c in toks[si + 1:]:
                if _row_level(c) >= 0 and len(c) <= 6:
                    continue                    # 学历列（有的表是 学校|学历|专业）
                if _SCHOOL_RE.search(c):
                    continue
                if len(c) > 24 or re.search(r'[，。；：,;、]', c):
                    continue
                major = c
                break
            end = ''
            ms = list(_DATE_RE.finditer(line))
            if len(ms) >= 2:
                end = _ym(ms[1].group(0))       # 后面那个是毕业时间
            elif ms:
                end = _ym(ms[0].group(0))
            got.append({'school': school, 'major': major,
                        'level': _row_level(line), 'end': end})
        if got:
            # 「教育背景」这个标题在 Word 里常被重复输出好几遍，
            # 取第一个真能拆出经历的那段就够了（后面那段往往是网申说明里的副本）
            rows = got
            break
    return rows


def _ym(s):
    """'2024.06' / '2024年6月' → '2024-06'。"""
    m = re.search(r'((?:19|20)\d{2})\s*[.\-/年]\s*(\d{1,2})', s or '')
    if not m:
        return ''
    mon = int(m.group(2))
    if 1 <= mon <= 12:
        return '%s-%02d' % (m.group(1), mon)
    return m.group(1)


@lru_cache(maxsize=16)
def _top_edu(t):
    """学历最高的那条教育经历（同级取结束时间最晚的）。"""
    rows = _edu_rows(t)
    if not rows:
        return None
    return sorted(rows, key=lambda r: (r['level'], r['end']))[-1]


# 带标签取一段文字：标签后面紧跟内容，遇到换行/逗号/竖线就停（简历常用这些分隔）。
# 注意：**必须有冒号**。以前冒号是可选的，结果「专业特色：获BCS」被当成
# 「专业 → 特色：获BCS」，专业栏直接填错。
_SCHOOL_LABELS = ('毕业院校及专业', '毕业院校', '毕业学校', '就读院校',
                  '所在院校', '院校名称', '学校名称', '院校', '学校')
_MAJOR_LABELS = ('所学专业', '专业名称', '主修专业', '专业方向', '专业')
_GRAD_LABELS = ('毕业时间', '毕业年份', '毕业日期')

# 取值时允许的字符集：遇到这些之外的东西就当这项结束了
_TAIL = r'[^\n，。；,;、|｜（）()]{0,30}'

# 这些词一出现，就说明这栏不是专业本身（「主修课程：…」讲的是学了哪些课）
_MAJOR_NOISE = ('课程', '特色', '描述', '技能', '介绍', '排名', '方向：', '怎么写')


def _labeled(label_keys, t):
    """在带标签的写法里取值：「毕业院校：武汉大学」→ 武汉大学。

    只认标签 + 冒号后面紧跟的那一小段，且不许跨行——跨行取到的往往是下一段经历的标题。
    """
    for label in label_keys:
        m = re.search(re.escape(label) + r'\s*[:：]\s*(' + _TAIL + r')', t)
        if not m:
            continue
        v = m.group(1).strip()
        v = re.sub(r'[\s:：\-—]+$', '', v)
        if v:
            return v
    return ''


def _find_gender(t):
    m = _SEX_RE.search(t)
    return m.group(1) if m else ''


def _find_birth(t):
    """只输出 YYYY-MM（招聘表基本都只问到月）。只有年份就只给年份。"""
    m = _BIRTH_RE.search(t)
    if not m:
        return ''
    year, mon = m.group(1), m.group(2)
    if not (1930 <= int(year) <= _current_year()):
        return ''
    if mon:
        mon = int(mon)
        if 1 <= mon <= 12:
            return '%s-%02d' % (year, mon)
    return year


def _find_school(t):
    # 1) 教育表格行（真简历的主流写法）
    top = _top_edu(t)
    if top and top['school']:
        return top['school']
    # 2) 标签写法，且值里得真的有学校名，不然就是正文里的「代表学校参赛」
    v = _labeled(_SCHOOL_LABELS, t)
    if v:
        sm = _SCHOOL_RE.search(v)
        if sm:
            return sm.group(1)
    return ''


def _find_major(t):
    top = _top_edu(t)
    if top and top['major']:
        return top['major']
    v = _labeled(_MAJOR_LABELS, t)
    if not v:
        return ''
    if any(k in v for k in _MAJOR_NOISE):
        return ''                       # 「主修课程：…」不是专业，宁可空着让人填
    if _SCHOOL_RE.search(v):
        return ''                       # 取到下一栏的学校名了，说明这栏其实没写专业
    return v[:24]


def _find_graduation(t):
    for label in _GRAD_LABELS:
        m = re.search(re.escape(label) + r'\s*[:：]?\s*((?:19|20)\d{2})'
                      r'\s*[年\-\./]?\s*(\d{1,2})?', t)
        if not m:
            continue
        year, mon = m.group(1), m.group(2)
        if not (1950 <= int(year) <= _current_year() + 10):
            continue
        if mon and 1 <= int(mon) <= 12:
            return '%s-%02d' % (year, int(mon))
        return year
    # 没写「毕业时间：」就用最高学历那条的结束时间（在读就是预计毕业）
    top = _top_edu(t)
    return (top or {}).get('end', '')


# --------------------------------------------------------------- 工作经历
# 为什么单独做这一段：国聘投递前要补全它的站内简历，「工作/实习经历」是必填段。
# 而档案里原来只有实习/校内活动两类（前端经历编辑器也只给了这两个入口），
# 一个工作多年的人传完简历，档案里依旧一条工作经历都没有 —— 自动投到这一步只能
# 停下来说「档案里没有工作或实习经历，需要你手动补」。可简历里明明写得清清楚楚：
# 公司、职位、起止年月、做了什么。这些字我们都读到了，只是没有人要。
#
# 难点是 PDF 抽出来的**行序不可靠**。双栏排版里左侧那列时间会被整列先吐出来：
# 实测那份简历的三条时间「2023.01-至今 / 2020.04-2022.12 / 2019.06-2020.04」
# 全跑到了「工作经历」标题**前面**，公司名反而在后面一行行跟着。所以不能按
# 「上一行是时间」就近配对。做法是两边各自按出现顺序抽 —— 公司一行一条、时间
# 一条 —— 然后**只有数量相等时才按顺序两两配对**；数量不等就只给公司和职位、
# 时间留空让用户自己补。宁可少填，也不能把 2018 年那段配到 2020 年那家公司头上。
_WORK_HEAD_RE = re.compile(
    r'(?m)^[ \t]*(?:工作经[历验]|工作履历|职业经[历验]|工作背景)\s*[:：]?\s*$')

# 工作段到这儿结束（下一个大标题）。跟教育段一个规矩：要求**独占一行**的短标题，
# 不能是「工作经历：…」这种带正文的行，否则按关键词硬截会把后半段事迹一起切掉。
_WORK_STOP_RE = re.compile(
    r'(?m)^[ \t]*(?:教育(?:经历|背景|情况)|学历(?:背景)?|学习经历|'
    r'实习(?:经历|实践)?|实践经历|项目(?:经历|经验)|获奖(?:情况|经历)?|'
    r'荣誉(?:奖项|证书)?|技能(?:证书|特长)?|证书|培训经历|自我评价|自我介绍|'
    r'个人优势|个人信息|基本信息|联系方式|求职意向|校园经历|校内活动)\s*[:：]?\s*$')

# 一行是不是「某公司 + 职位」。机构名收尾词认到了才认，避免把描述句当公司名：
# 反面例子「负责公司产品的立项」—— 若只按「含公司二字」就会把「负责公司」当公司。
# 所以还要求机构名不少于 5 个字（「负责公司」4 个字，挡住）。
_ORG_NAME_RE = re.compile(
    r'^([\u4e00-\u9fa5A-Za-z0-9（）()·\-\.]{5,31}?'
    r'(?:有限公司|有限责任公司|股份有限公司|公司|集团|工厂|学校|大学|学院|医院|'
    r'银行|事务所|研究院|研究所|工作室|中心|超市|商店|酒店|餐厅|门店|分行|支行))'
    r'[\s|｜,，\-—:：]*(.{0,24})$')

# 学历词：教育行常紧挨在「教育经历」标题上面，落在工作段范围里，靠这个挡掉
_WORK_NOISE = ('本科', '硕士', '专科', '大专', '博士', '高中', '中专', '学士',
               '研究生', 'MBA', 'mba')

# 起止年月：2023.01-至今 / 2020.04-2022.12 / 2019年6月—2020年4月。
# 要求带月份 —— 教育行那种「2007-2011」自然不匹配，省得把上学时间当工作时间。
# 分隔符写成「至 或 一个符号」而不是 `[至]{1,2}`：后者会把「-至今」里的「至」
# 当分隔符吃掉，结束时间只剩一个「今」字（2026-09-20 实测踩过）。
_WORK_RANGE_RE = re.compile(
    r'((?:19|20)\d{2})\s*[.\-/年]\s*(\d{1,2})\s*月?\s*(?:至|[-—~～到])\s*'
    r'((?:19|20)\d{2}\s*[.\-/年]\s*(\d{1,2})\s*月?|至今|现在|今|目前)')

# 教育行（就压在「教育经历」标题上面那一行）的特征：既有学历词、又带学校名或
# 「2007-2011」这种年份区间。它会落进工作段的正文里，被当成上一段的工作内容。
_WORK_EDU_LINE = re.compile(r'(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}\s*$')


def _work_ym(year, mon):
    """2023 / 1 → 2023-01。月份读不出来或不合法的，就只给年份。"""
    year = str(year).strip()
    try:
        m = int(mon)
    except (TypeError, ValueError):
        return year
    return '%s-%02d' % (year, m) if 1 <= m <= 12 else year


def _find_work_experiences(t):
    """抽出工作经历条目：[{type:'work', company, role, start, end, desc}]。

    只在**明确的「工作经历」标题**下面找。找不到标题就返回空，绝不在全篇乱抓 ——
    简历里「实习/实践」「校内活动」段同样有单位名，那些是另一码事，混进来就把
    用户的实习经历说成了正式工作。
    """
    head = _WORK_HEAD_RE.search(t)
    if not head:
        return []
    body = t[head.end():]
    stop = _WORK_STOP_RE.search(body)
    if stop:
        body = body[:stop.start()]

    rows = []
    for line in body.split('\n'):
        s = line.strip()
        if not s or _WORK_RANGE_RE.fullmatch(s):
            continue                      # 空行、纯时间行（左栏那一列）
        noise = any(k in s for k in _WORK_NOISE)
        if noise and (_SCHOOL_RE.search(s) or _WORK_EDU_LINE.search(s)):
            continue                      # 教育行，不是工作内容（也绝不当公司名）
        m = (_ORG_NAME_RE.match(s)
             if len(s) <= 60 and not noise else None)
        if m:
            role = (m.group(2) or '').strip(' -—|｜,，:：')
            if len(role) > 16:
                role = ''                 # 这么长的是描述句，不是职位名
            rows.append({'company': m.group(1), 'role': role, 'desc': []})
        elif rows:
            rows[-1]['desc'].append(s)    # 公司行下面的都算这一段的工作内容
    if not rows:
        return []

    # 时间在全篇里按出现顺序抽：左栏那一列可能整体跑到标题前面，只在段内取会漏
    times = []
    for m in _WORK_RANGE_RE.finditer(t):
        start = _work_ym(m.group(1), m.group(2))
        end_raw = m.group(3)
        # 「至今/现在」不填结束时间：要不要写、写哪个月，只有用户自己知道
        end = _work_ym(end_raw[:4], m.group(4)) if end_raw[:4].isdigit() else ''
        times.append((start, end))

    # 数量对得上才配 —— 对不上就只给公司/职位，时间留空交给人
    pairs = times if len(times) == len(rows) else []
    out = []
    for i, r in enumerate(rows[:5]):
        desc = re.sub(r'\s+', ' ', ' '.join(r['desc'])).strip()
        desc = re.sub(r'^(?:内容|业绩|工作内容|工作职责|职责|主要业绩)\s*[:：]\s*', '', desc)
        e = {'type': 'work', 'company': r['company'], 'role': r['role'],
             'desc': desc[:300]}
        e['start'], e['end'] = pairs[i] if pairs else ('', '')
        out.append(e)
    return out


def parse_fields(text, cities=None):
    """从纯文本里挑出能用的字段。

    返回 {'fields': {字段: 值}, 'low': [需要用户核对的字段]}
    识别不到的字段直接不出现在 fields 里 —— 让界面提示用户自己补，不猜。
    """
    cities = DEFAULT_CITIES if cities is None else cities
    t = _norm(text)
    if not t.strip():
        return {'fields': {}, 'low': []}

    fields, low = {}, []

    name, conf = _find_name(t)
    if name:
        fields['name'] = name
        if conf == 'low':
            low.append('name')

    for key, fn in (('phone', _find_phone), ('email', _find_email),
                    ('age', _find_age), ('edu', _find_edu), ('exp', _find_exp),
                    ('intro', _find_intro),
                    # 招聘表必问的几项：性别、出生年月、学校、专业、毕业时间
                    ('gender', _find_gender), ('birth', _find_birth),
                    ('school', _find_school), ('major', _find_major),
                    ('graduation', _find_graduation)):
        v = fn(t)
        if v:
            fields[key] = v

    v = _find_city(t, cities)
    if v:
        fields['city'] = v

    v = _find_skills(t)
    if v:
        fields['skills'] = v

    # 工作经历：投国聘时最缺的一项。前面那些标量字段凑不出「公司/职位/起止年月」，
    # 少了它，自动投到「工作/实习经历」段就只能停下来转人工。
    work = _find_work_experiences(t)
    if work:
        fields['experiences'] = work
        # 公司名和起止年份是照着排版认出来的（PDF 抽出来的行序还不一定规整），
        # 标成「要核对」：对不上时用户扫一眼就能改，比投出去才发现错强。
        low.append('experiences')

    # 手机号和邮箱是简历里几乎必有的两样，而且格式固定、认得准。
    # 两样都没有基本可以断定这文件不是简历——学校发的填写说明、模板、证书、合同都长这样。
    # 这时候照填出来的字段大概率是错的，得先提醒用户，别让他稀里糊涂就下一步了。
    warn = ''
    if 'phone' not in fields and 'email' not in fields:
        warn = ('这份文件里没找到手机号和邮箱，它可能不是简历'
                '（比如学校发的填写说明、模板或证书扫描件）。')
    elif sum(1 for k in _DOC_SMELL if k in t) >= _SMELL_MIN and len(fields) <= 3:
        # 有电话也可能不是简历：就业指导文档里常附咨询电话。
        # 这类文档满篇是「简历/网申/模板/填写说明」，真简历不会这么说话。
        # 但很多人把「网申注意事项」原样附在简历后面，那份说明会把整份文件拖得像文档
        # ——所以只在**几乎什么都没认出来**时才提醒，认出东西了就闭嘴。
        warn = ('这份文件读起来像是「怎么写简历」的说明或须知，'
                '不太像简历本身。')
    return {'fields': fields, 'low': low, 'warn': warn}
