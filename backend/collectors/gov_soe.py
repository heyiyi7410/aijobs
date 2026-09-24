# -*- coding: utf-8 -*-
"""地方国资委 / 人社局招聘公告采集器 —— 第三国企源，补国聘网 + 中国公共招聘网的密度。

为什么需要它：
  * 国聘网是全国性结构化源（主力，密集，但只覆盖在国聘发岗的央企/国企）；
  * 中国公共招聘网(mohrss) 企业库 99.5% 是民企，靠关键词只能捞出零星国企；
  * 各省/市国资委官网的「国企招聘」专栏是**官方公告**，100% 地方国企/央企，
    零民企污染，正好补前两家的地域覆盖（尤其省属/市属国企）。

难点与取舍（对应「换源不破防」）：
  * 这类站点没有结构化 API，岗位是「一篇篇招聘公告 HTML」，所以走
    「列表页抽公告链接 → 逐篇抓正文 → 解析公司/日期/性质」的路线；
  * 公告是散文，没有薪资/人数等结构化字段，这些字段留空（前端对空值已做隐藏）；
  * 不同站点模板不同，因此做成**配置驱动**：把新地区加进
    data/gov_soe_sources.json 即可，无需改代码；
  * 单位性质默认判为「国企」（地方国企）；公司名命中央企名录则升级为「央企」；
  * 任一地区抓不到都只是跳过，guopin 仍兜底密度，用户无感。

接口（已实测可用，见 data/gov_soe_sources.json）：
  列表页（HTML）→ list_link_re 抽公告链接 → 逐篇 HTML 解析
"""
import html as htmlmod
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from . import net
from .base import BaseCollector
from .guopin import CENTRAL_KEYWORDS, _is_central, DISPATCH_RE

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'gov_soe_sources.json')

# 默认配置（CONFIG_PATH 不存在时启用）。以实测可用的地区为准。
DEFAULT_SOURCES = [
    {
        "region": "广东",
        "label": "广东省国资委·国企招聘",
        "list_url": "https://gzw.gd.gov.cn/qydt/gwkgjt/",
        "list_link_re": r"qydt/gwkgjt/content/post_\d+\.html",
        "base_url": "https://gzw.gd.gov.cn",
        "city": "广东",
        "max_articles": 15,
    },
    {
        "region": "湖北",
        "label": "湖北华图·国企招聘频道(聚合)",
        "list_url": "https://hb.huatu.com/guoqizp/zhaopin/",
        "list_link_re": r"guoqizp/\d+\.html",
        "base_url": "https://hb.huatu.com",
        "city": "湖北",
        "max_articles": 15,
    },
]

# 只保留「真的在招人」的公告，过滤掉纯资讯/活动新闻
_JOB_SIGNAL = re.compile(r'招聘|招考|应聘|招贤|纳才|引进|公开遴选|公开选拔|招才|招募|招才引智')
# 招聘后期的公示 / 复审 / 成绩 / 汇总等对求职者已无投递价值，直接过滤
_NOISE_RE = re.compile(
    r'公示|拟聘用|资格复审|复审公告|笔试成绩|成绩查询|成绩公告|面试公告|'
    r'体检公告|考察公告|录用公告|招考汇总|公告汇总|打印入口|考试大纲|准考证')
# 公告标题里的「【国企招聘】XX集团」专用提取（广东国资委格式）
_TITLE_COMPANY_RE = re.compile(
    r'【国企招聘】\s*([一-龥（）()]{2,30}?(?:集团|公司|有限公司|股份有限公司))')
# 通用公司名：正文里第一个像国企主体的名称。
# 用「优先级列表」而非单一正则——关键要让「集团有限公司 / 股份有限公司」这类
# 长后缀优先于单独的「集团 / 公司」，否则非贪婪会过早停在「集团」上，
# 把「湖北交通投资集团有限公司」截成「湖北交通投资集团」。
_COMPANY_RES = [
    re.compile(r'([一-龥（）()]{2,30}?股份(?:有限)?公司)'),
    re.compile(r'([一-龥（）()]{2,30}?集团(?:有限)?公司)'),
    re.compile(r'([一-龥（）()]{2,30}?有限公司)'),
    # 事业单位 / 机关单位主体（医院、高校、科研院所、局委厅等）
    re.compile(r'([一-龥（）()]{2,30}?医院)'),
    re.compile(r'([一-龥（）()]{2,30}?事业单位)'),
    re.compile(r'([一-龥（）()]{2,30}?大学)'),
    re.compile(r'([一-龥（）()]{2,30}?学院)'),
    re.compile(r'([一-龥（）()]{2,30}?学校)'),
    re.compile(r'([一-龥（）()]{2,30}?研究院)'),
    re.compile(r'([一-龥（）()]{2,30}?研究所)'),
    re.compile(r'([一-龥（）()]{2,30}?机关)'),
    re.compile(r'([一-龥（）()]{2,30}?局)'),
    re.compile(r'([一-龥（）()]{2,30}?委员会)'),
    re.compile(r'([一-龥（）()]{2,30}?厅)'),
    re.compile(r'([一-龥（）()]{2,30}?法院)'),
    re.compile(r'([一-龥（）()]{2,30}?检察院)'),
    re.compile(r'([一-龥（）()]{2,30}?公司)'),
    re.compile(r'([一-龥（）()]{2,30}?集团)'),
    re.compile(r'([一-龥（）()]{2,30}?中心)'),
    re.compile(r'([一-龥（）()]{2,30}?中学)'),
    re.compile(r'([一-龥（）()]{2,30}?小学)'),
    re.compile(r'([一-龥（）()]{2,30}?高中)'),
]


# 公司名前的引导词正则（_strip_lead 用）。
#
# 两条配合而不是一条，是因为中文里「2026年度广东省烟草专卖局」这种前缀的
# 字数不固定：早先单条 `[0-9年月份日（【(（一二三四五六七八九十]+` 会把
# 「年度」只吃掉「年」，剩个「度」挂到公司名头上，得到「度广东省烟草专卖局」
# （2026-09-21 扩源时在中公源产出里发现）。所以先整块吃掉
# 「数字+年/年度+可选括号序号」，再兜底吃剩下的孤立量词。
_JUNK_LEAD_DATE = re.compile(
    r'^\s*(?:[0-9]{2,4}\s*年(?:度)?|[0-9]+\s*月|[0-9]+\s*日|'
    r'[（(【]\s*[一二三四五六七八九十0-9]+\s*[）)】])+\s*')
_JUNK_LEAD = re.compile(r'^[0-9\s年月份日（【(（一二三四五六七八九十]+')
# 公文/批次类引导词：「关于」「转发」「第X批」「2027届」……跨在日期和序号之间，
# 单靠上面两条吃不全，故单独列一条。
_LEAD_WORDS_RE = re.compile(
    r'^(?:关于|有关|印发|发布|转发|开展|组织|做好|现将|拟|第[一二三四五六七八九十0-9]+批(?:次)?|'
    r'[0-9]{2,4}\s*届)+')


def _strip_lead(name):
    """剥掉公司名前的日期/序号/公文引导词。

    循环剥到不动为止：真实标题常是「关于2026年度（第二批）XX公司」这种叠串，
    剥一轮只能剥掉最外层。剥完再剥，才能落到干净的主体名上。
    """
    prev = None
    while prev != name:
        prev = name
        name = _JUNK_LEAD_DATE.sub('', name)
        name = _LEAD_WORDS_RE.sub('', name)
        name = _JUNK_LEAD.sub('', name)
    return name.strip('（）()【】 ')


def _find_company(text):
    m = _TITLE_COMPANY_RE.search(text)
    if m:
        return m.group(1)
    # 先剥掉「2026年度」「（第二批）」「关于」这类引导词再匹配。
    # 不剥就匹配的后果：中文公司名正则必须开头锚定边界，而「2026年度广东省
    # 烟草专卖局」里那个孤零零的「度」会被当成名字首字，非贪婪一路吃到
    # 「度广东省烟草专卖局」（2026-09-21 扩源时在中公源产出里发现）。
    text = _strip_lead(text)
    for rx in _COMPANY_RES:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None
_DATE_RE = re.compile(
    r'(?:发布时间|发布日期|时间)[：: ]*\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})')
_PLAIN_DATE_RE = re.compile(r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})')
# 用于在散文中定位城市（缺省用地区名）
_CITY_HINTS = ['北京', '上海', '广州', '深圳', '珠海', '东莞', '佛山', '天津', '重庆',
               '杭州', '南京', '成都', '武汉', '黄石', '宜昌', '襄阳', '荆门', '荆州',
               '黄冈', '孝感', '咸宁', '恩施', '随州', '鄂州', '西安', '苏州', '青岛',
               '宁波', '厦门', '中山', '惠州', '江门', '肇庆', '清远', '汕头', '湛江']

# 省份 → 下辖城市。用户只选了某「市」时，仍应匹配其所属省份的源，
# 否则广东省源在「深圳」搜索下会被整源跳过，反而把本地国企藏起来（违背「破茧」）。
PROVINCE_CITIES = {
    # 用途：用户选了「深圳」这类城市时，把「广东」源也算命中（城市在其省内）。
    # 早先只有粤/鄂/苏三省，扩源到全国后其它省会整源被跳过——用户选「成都」
    # 时四川源直接不抓（2026-09-21 补全）。
    '北京': ['海淀', '朝阳', '东城', '西城', '丰台', '通州', '昌平', '大兴'],
    '天津': ['滨海新区', '和平', '河西', '南开', '河东', '红桥', '武清', '宝坻'],
    '上海': ['浦东新区', '黄浦', '徐汇', '长宁', '静安', '普陀', '闵行', '宝山'],
    '重庆': ['万州', '涪陵', '渝中', '江北', '沙坪坝', '九龙坡', '南岸', '永川'],
    '河北': ['石家庄', '唐山', '秦皇岛', '邯郸', '邢台', '保定', '张家口',
            '承德', '沧州', '廊坊', '衡水'],
    '山西': ['太原', '大同', '阳泉', '长治', '晋城', '朔州', '晋中', '运城',
            '忻州', '临汾', '吕梁'],
    '内蒙古': ['呼和浩特', '包头', '乌海', '赤峰', '通辽', '鄂尔多斯',
            '呼伦贝尔', '巴彦淖尔', '乌兰察布'],
    '辽宁': ['沈阳', '大连', '鞍山', '抚顺', '本溪', '丹东', '锦州', '营口',
            '阜新', '辽阳', '盘锦', '铁岭', '朝阳', '葫芦岛'],
    '吉林': ['长春', '吉林', '四平', '辽源', '通化', '白山', '松原', '白城',
            '延边'],
    '黑龙江': ['哈尔滨', '齐齐哈尔', '鸡西', '鹤岗', '双鸭山', '大庆', '伊春',
            '佳木斯', '七台河', '牡丹江', '黑河', '绥化'],
    '江苏': ['南京', '苏州', '无锡', '常州', '徐州', '南通', '扬州', '镇江',
            '泰州', '盐城', '淮安', '连云港', '宿迁'],
    '浙江': ['杭州', '宁波', '温州', '嘉兴', '湖州', '绍兴', '金华', '衢州',
            '舟山', '台州', '丽水'],
    '安徽': ['合肥', '芜湖', '蚌埠', '淮南', '马鞍山', '淮北', '铜陵', '安庆',
            '黄山', '滁州', '阜阳', '宿州', '六安', '亳州', '池州', '宣城'],
    '福建': ['福州', '厦门', '莆田', '三明', '泉州', '漳州', '南平', '龙岩',
            '宁德'],
    '江西': ['南昌', '景德镇', '萍乡', '九江', '新余', '鹰潭', '赣州', '吉安',
            '宜春', '抚州', '上饶'],
    '山东': ['济南', '青岛', '淄博', '枣庄', '东营', '烟台', '潍坊', '济宁',
            '泰安', '威海', '日照', '临沂', '德州', '聊城', '滨州', '菏泽'],
    '河南': ['郑州', '开封', '洛阳', '平顶山', '安阳', '鹤壁', '新乡', '焦作',
            '濮阳', '许昌', '漯河', '三门峡', '南阳', '商丘', '信阳', '周口',
            '驻马店', '济源'],
    '湖北': ['武汉', '黄石', '十堰', '宜昌', '襄阳', '鄂州', '荆门', '孝感',
            '荆州', '黄冈', '咸宁', '随州', '恩施', '仙桃', '天门', '潜江'],
    '湖南': ['长沙', '株洲', '湘潭', '衡阳', '邵阳', '岳阳', '常德', '张家界',
            '益阳', '郴州', '永州', '怀化', '娄底'],
    '广东': ['广州', '深圳', '珠海', '东莞', '佛山', '中山', '惠州', '江门',
            '肇庆', '清远', '汕头', '湛江', '茂名', '韶关', '梅州', '汕尾',
            '河源', '阳江', '潮州', '揭阳', '云浮'],
    '广西': ['南宁', '柳州', '桂林', '梧州', '北海', '防城港', '钦州', '贵港',
            '玉林', '百色', '贺州', '河池', '来宾', '崇左'],
    '海南': ['海口', '三亚', '三沙', '儋州', '琼海', '文昌', '万宁'],
    '四川': ['成都', '自贡', '攀枝花', '泸州', '德阳', '绵阳', '广元', '遂宁',
            '内江', '乐山', '南充', '眉山', '宜宾', '广安', '达州', '雅安',
            '巴中', '资阳', '凉山'],
    '贵州': ['贵阳', '六盘水', '遵义', '安顺', '毕节', '铜仁', '黔东南',
            '黔南', '黔西南'],
    '云南': ['昆明', '曲靖', '玉溪', '保山', '昭通', '丽江', '普洱', '临沧',
            '楚雄', '红河', '文山', '西双版纳', '大理', '德宏', '怒江', '迪庆'],
    '西藏': ['拉萨', '日喀则', '昌都', '林芝', '山南', '那曲', '阿里'],
    '陕西': ['西安', '铜川', '宝鸡', '咸阳', '渭南', '延安', '汉中', '榆林',
            '安康', '商洛'],
    '甘肃': ['兰州', '嘉峪关', '金昌', '白银', '天水', '武威', '张掖', '平凉',
            '酒泉', '庆阳', '定西', '陇南', '临夏', '甘南'],
    '青海': ['西宁', '海东', '海北', '黄南', '海南州', '果洛', '玉树', '海西'],
    '宁夏': ['银川', '石嘴山', '吴忠', '固原', '中卫'],
    '新疆': ['乌鲁木齐', '克拉玛依', '吐鲁番', '哈密', '昌吉', '阿克苏',
            '喀什', '伊犁', '塔城', '阿勒泰'],
}


def _clean(html):
    # 先剥掉 script/style/注释：华图等聚合站的 <script> 里有 article_id 等
    # JS 代码，不剥掉就会混进正文（desc 变成代码+导航噪声，邮箱提取也白搭）
    txt = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', html or '',
                 flags=re.I | re.S)
    txt = re.sub(r'<!--.*?-->', ' ', txt, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', txt)).strip()


# 公告页尾部的站点导航/推荐区块标记，正文到这里就该截断
_TAIL_RE = re.compile(
    r'网站导航|全部考试|备考助手|时政热点|相关推荐|版权所有|免责声明'
    r'|华图简介|走进华图|扫描二维码|手机访问|关键词阅读')

# 「报名方式/报名邮箱」通常写在公告最末尾——只按标题起点截断很容易把它切掉，
# 于是出现"抓到了邮箱，但用户在自己看到的正文里找不到出处"的怪状。命中即补回。
_APPLY_HINT_RE = re.compile(
    r'报名方式|报名邮箱|报名事项|投递方式|投递邮箱|简历投递|应聘方式|应聘邮箱'
    r'|报名时间|发送至|发至|联系方式')


def _article_body(html, body, title):
    """截取公告正文段落（给 desc 用）。

    聚合站整页文本 = 站头导航 + 公告正文 + 站尾导航。做法：从标题第一次
    出现的位置开始（正文总在标题后面），到尾部导航标记处截断。

    额外补一段「报名方式」：它通常写在公告最末尾，只按标题起点截 2200 字
    很容易把它切掉——那样会抓到了邮箱、但用户在自己看到的正文里找不到出处，
    没法核对这邮箱到底是报名邮箱还是监督/中介邮箱。主段+补段总长控制在
    1500 字以内（desc 的存储上限）。
    """
    t = (title or '').strip()
    start = 0
    if t and len(t) >= 6:
        pos = body.find(t[:12])
        if pos > 0:
            start = pos
    seg = body[start:start + 2200]
    # 从标题之后开始找站尾标记，免得标题里带这类词时当场就把正文截没了。
    m = _TAIL_RE.search(seg[len(t):] if t else seg)
    if m:
        # ⚠️ 原来是 seg[:start and (len(t) + m.start())]：`start` 为 0 时
        # `0 and X` 求值为 0，切片变成空串，下一行 `or body` 兜底退回
        # body[:1050] —— 站头站尾噪声全灌进 desc，这次截断等于白做。
        # 而 start==0 恰恰是常态（标题是拼出来的、或短于 6 个字、或在正文里
        # 根本找不到）。
        # 上面的 search 是在 seg[len(t):] 里做的，拿到的偏移是相对的，
        # 必须加回 len(t) 才是 seg 里的绝对位置；t 为空时就在全串里搜，直接用。
        cut = (len(t) + m.start()) if t else m.start()
        seg = seg[:cut]
    seg = (seg.strip() or body)[:1050].strip()
    # 补「报名方式」段：主段里没出现**邮箱**时，说明那段被截掉了——把它补回来，
    # 用户才能在正文里核对邮箱出处（抓到的邮箱必须看得见来源，否则只能盲信）。
    # 判据用「有没有邮箱」而不是「有没有报名二字」：公告中部常有「报名时间」
    # 之类的小标题，用词判断会误判成"已经写过了"而漏补。
    if not _EMAIL_RE.search(seg):
        m = _EMAIL_RE.search(body)
        if m:
            lo = max(0, m.start() - 100)
            h = _APPLY_HINT_RE.search(body)
            if h and 0 <= h.start() <= m.start():
                lo = min(lo, max(0, h.start() - 20))   # 顺带带上「报名方式」小标题
            hi = m.start() + 320
            lo = max(lo, hi - 420)                     # 补段总长压在 420 字内
            tail = body[lo:hi].strip()
            if tail:
                seg = (seg + ' … ' + tail) if seg else tail
    return seg


def _load_sources():
    """读取渠道的地区配置；读不到就用内置默认，绝不因配置文件拖垮搜索。"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    except Exception:                            # noqa: BLE001
        return DEFAULT_SOURCES
    return DEFAULT_SOURCES


def _abs(url, base):
    if url.startswith('http'):
        return url
    # 协议相对链接（华图部分省站写成 //gs.huatu.com/2013/0903/712284.html）。
    # 早先按「以 / 开头」处理，会拼成 https://gs.huatu.com//gs.huatu.com/...，
    # 请求必然 404 而被静默跳过——扩源时才发现（2026-09-21 修）。
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        return base.rstrip('/') + url
    return base.rstrip('/') + '/' + url.lstrip('/')


def _fetch_html(url):
    # 国资委站点多为明文/公网 HTTPS；部署机若挂了指向本地的坏代理会让请求拿不到，
    # 这里显式绕过代理直连（与 mohrss 同理）。失败返回 ''，由上层静默跳过。
    raw = net.request(url, as_json=False, timeout=15, retries=2,
                      bypass_proxy=True, cache_ttl=1800)
    return raw or ''


# 正文里公司名常被「2026年」「（一）」「应聘人员可登录」之类的引导词污染，
# 提取后把这类前缀剥掉，拿到干净的主体名。

# ---------------------------------------------------------------------------
# 深链提取：公告是"文章"，真正的投递入口（招聘系统报名页 / HR 邮箱）藏在正文里。
# 只把 apply_url 设成公告页只能让人读公告，设成里面的报名系统链接才叫"投递页"。
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r'https?://[^\s"\'<>）)，。；、]{6,150}', re.I)
# 明显不是投递页的：样式/图片脚本、聚合站自身、搜索/微信文章
_BAD_URL_RE = re.compile(
    r'\.(css|js|png|jpe?g|gif|svg|ico|woff2?)(\?|$)'
    r'|huatu\.com|baidu\.com|zhannei\.|weixin\.qq\.com|mp\.weixin'
    r'|beian|gov\.cn/.*?\bindex\b', re.I)
# 看起来像招聘/报名系统的关键词
_JOBSYS_RE = re.compile(
    r'zhaopin|job|apply|zsrc|rencai|talent|recruit|rlzy|/rc\.|hr\.'
    r'|Detail|detail|signup|register|baoming|zp\.', re.I)
# 邮箱必须限定 ASCII：Python 的 \w 会匹配中文，宽泛的 [\w.\-]+@ 会把
# 「应聘者请在X月X日前...发送至邮箱xxx@163.com」整句吞成"邮箱"，得到垃圾值。
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

# 「排除来源站自己的域名」原本用 host 的最后两段去比对，结果 gzw.gd.gov.cn 被
# 算成 gov.cn、rsc.whu.edu.cn 被算成 edu.cn——于是 hr@xxx.gov.cn / zp@xxx.edu.cn
# 这类**真实报名邮箱**被整类误杀（政府/事业单位/高校的报名邮箱恰恰大量用这些域）。
# 实际上要排除的只有一种情况：**聚合站**（华图这类转载站）页面上的
# 「站点反馈/客服」邮箱（fankui@huatu.com）。官方源用本机构域名做报名邮箱是
# 完全正常且正规的，必须保留。
_AGGREGATOR_DOMAINS = frozenset((
    'huatu.com', 'offcn.com', 'zgjsks.com', 'gaodun.com', 'chinaacc.com',
    'zhaopin.com', '51job.com', 'liepin.com', 'zhipin.com', 'lagou.com',
))

# 明显不该拿来投简历的地址：占位符 / 系统信箱 / 被误抓的资源文件名
_PLACEHOLDER_DOMAINS = frozenset((
    'example.com', 'example.org', 'example.net', 'test.com', 'test.org',
    'domain.com', 'yourdomain.com', 'yourcompany.com', 'sample.com',
    'email.com', 'mail.com', 'localhost',
))
_SYSTEM_LOCALPARTS = frozenset((
    'noreply', 'no-reply', 'donotreply', 'do-not-reply', 'postmaster',
    'webmaster', 'abuse', 'mailer-daemon',
))
_RESOURCE_EXT = frozenset((
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'css', 'js',
))

# 上下文语义：报名类词加分；监督/举报/咨询类词若无报名类词同现则弃用。
# 公告里同时挂「报名邮箱」和「监督举报邮箱」很常见，草率取第一个会投进纪检组。
_MAIL_GOOD_RE = re.compile(
    r'报名|投递|应聘|简历|申请|接收|提交|发送至|发至|联系邮箱|招聘邮箱')
_MAIL_BAD_RE = re.compile(
    r'监督|举报|投诉|纪检|反馈|咨询|客服|建议|技术支持|网站|侵权|违法和不良')


def _html_unescape(t):
    """解 HTML 实体，解两次以覆盖 `&amp;#64;` 这种双重编码（@ 常被这样藏）。"""
    t = htmlmod.unescape(t or '')
    return htmlmod.unescape(t)


def _email_text(html):
    """给邮箱提取专用的纯文本（不复用 _clean，两者取舍不同）。

    与 _clean 的两点差异都为了「抓得到、抓得准」：
      · 块级标签换成空格（防相邻文本粘连出假邮箱），**行内标签直接删掉**——
        公告常用 `<span>@</span>` 把 @ 包起来防爬，若一律换成空格，
        `hr<span>@</span>abc.com` 会变成 `hr @ abc.com` 而抓不到；
      · 剥完标签再做实体/全角归一，处理 `&#64;`、`&#x40;`、全角 ＠。
    """
    t = html or ''
    t = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', t, flags=re.I | re.S)
    t = re.sub(r'<!--.*?-->', ' ', t, flags=re.S)
    t = re.sub(
        r'</?(?:p|div|br|li|ul|ol|tr|td|th|table|h[1-6]|section|article|header'
        r'|footer|blockquote|dl|dt|dd)\b[^>]*>', ' ', t, flags=re.I)
    t = re.sub(r'<[^>]+>', '', t)
    t = _html_unescape(t)
    t = t.replace('\uff20', '@').replace('\uff0e', '.')
    return re.sub(r'\s+', ' ', t).strip()


def _looks_fake_mail(mail):
    """明显不是报名邮箱：占位符、系统信箱、被误抓的图片/资源文件名。"""
    local, _, dom = (mail or '').strip().lower().partition('@')
    if not local or not dom:
        return True
    if local in _SYSTEM_LOCALPARTS or dom in _PLACEHOLDER_DOMAINS:
        return True
    tld = dom.rsplit('.', 1)[-1]
    return tld in _RESOURCE_EXT or tld.isdigit()


def _same_site(dom, host):
    """邮箱域是否属于来源站自己（属于就该排除）。

    只针对**聚合站**：华图页面上的 fankui@huatu.com 是站点反馈邮箱，投过去等于
    投错地方。官方源（xx.gov.cn / xx.edu.cn）用本机构域名做报名邮箱是正规做法，
    一律保留——早先按 host 最后两段通配会把它们整类误杀（见 _AGGREGATOR_DOMAINS）。
    """
    if not dom or not host:
        return False
    if dom == host:
        return True
    for ag in _AGGREGATOR_DOMAINS:
        if (host == ag or host.endswith('.' + ag)) and \
                (dom == ag or dom.endswith('.' + ag)):
            return True
    return False


def _pick_apply_email(text, host):
    """从公告文本里挑「报名邮箱」，而不是草率地取第一个。

    公告里同时挂着「报名邮箱」和「监督举报邮箱」很常见，取第一个可能把简历投进
    纪检组或中介。做法：按出现位置逐个看邮箱**前面**的标签——命中报名类词优先；
    命中监督/举报/咨询类词且无报名类词同现的直接弃用。

    只往前看不往后看是关键：中文公告几乎都写成「报名邮箱：xxx」，标签在邮箱
    之前；若把邮箱后面也算进上下文，旁边那个邮箱的「报名」标签会被算到前一个
    邮箱头上——「监督举报邮箱 jb@…，报名邮箱 hr@…」里的 jb 也会被认成报名邮箱。
    全都不合格时返回 ''：宁可让用户走跳转投递，也不投错地方。
    """
    text = text or ''
    seen, spots = set(), []
    for m in _EMAIL_RE.findall(text):
        if m in seen:
            continue
        seen.add(m)
        spots.append((m, text.find(m)))
    spots.sort(key=lambda x: x[1])           # 按出现位置，先到先得
    best, best_score = '', 0
    for m, i in spots:
        dom = m.split('@')[-1].lower()
        if _same_site(dom, host) or _looks_fake_mail(m):
            continue
        head = text[max(0, i - 50):i]
        good = bool(_MAIL_GOOD_RE.search(head))
        if not good and _MAIL_BAD_RE.search(head):
            continue
        score = 2 if good else 1
        if score > best_score:
            best, best_score = m, score
            if score == 2:
                break
    return best


# --------------------------------------------------------------------------
# 电话提取：为什么必须要有这一路
#
# 邮箱是「最好的」投递方式，但覆盖率天生就低：国企/央企/事业单位习惯在公告里
# 留邮箱，民企几乎不上邮箱。实测库里 468 条 gov_soe 公告，带邮箱 139 条，
# 而**含座机的有 277 条、含手机号 55 条**——电话比邮箱多一倍，全躺在正文里
# 没被解析过。用户要投民企，靠邮箱这条路本身就是死胡同。
#
# 判据和邮箱一样严：只看号码**前面**的标签，命中「联系电话/报名电话」优先，
# 命中「监督/举报/纪检/技术维护」直接弃用。把简历打进纪委或网站维护电话，
# 比没有联系方式更糟。
# --------------------------------------------------------------------------
# 手机号 11 位（允许 138 0013 8000 / 138-0013-8000 这类分隔写法）、
# 座机 0 + 区号 2~3 位 + 号码 7~8 位。前后用 (?<!\d)/(?!\d) 卡住，
# 免得从身份证号、日期、长串编号里截出半个号码当电话。
_PHONE_RE = re.compile(
    r'(?<!\d)(1[3-9]\d[\d\s\-\u2013]{7,11}\d|0\d{2,3}[\s\-]?\d{7,8})(?!\d)')
# 号码前面 50 字内命中这些，才像「招聘联系」用的电话
_PHONE_GOOD_RE = re.compile(
    r'联系电话|报名电话|咨询电话|招聘电话|联系手机|联系方式|联系方法|联系号码'
    r'|有意.{0,8}联系|请.{0,3}联系|可.?联系|电话联系|来电咨询'
    r'|简历.{0,10}(投递|发送|发至|发送至此)')
# 命中这些就不是招人的电话：监督举报、纪检、以及网站/系统维护电话
_PHONE_BAD_RE = re.compile(
    r'监督|举报|纪检|投诉|纪委|巡察|廉政|作风建设'
    r'|技术.{0,4}(支持|服务|咨询)|网站.{0,4}维护|系统维护|运维电话'
    r'|故障|报修|客服热线|售后服务')


def _looks_fake_phone(phone):
    """占位/示例号码：全同数字、连续递增、位数不对。"""
    d = re.sub(r'\D', '', phone or '')
    if len(d) < 10 or len(d) > 12:
        return True
    if len(set(d)) <= 2:                  # 00000000000、11111111111
        return True
    return d in ('12345678901', '0123456789', '01234567890')


def _pick_apply_phone(text):
    """从公告文本里挑「招聘联系电话」，逻辑照抄 _pick_apply_email。

    同样只往前看标签、同样宁缺毋滥。额外一条：手机号优先于座机——座机常常是
    单位总机或人社局办公室，转接到招人的那一位要碰运气；手机号更可能直接是
    经办人。全都不合格就返回 ''。
    """
    text = text or ''
    seen, spots = set(), []
    for m in _PHONE_RE.findall(text):
        raw = m if isinstance(m, str) else m[0]
        digits = re.sub(r'\D', '', raw)
        if digits in seen or _looks_fake_phone(digits):
            continue
        seen.add(digits)
        spots.append((digits, text.find(raw)))
    spots.sort(key=lambda x: x[1])            # 按出现位置，先到先得
    best, best_score = '', -1
    for digits, i in spots:
        head = text[max(0, i - 50):i]
        good = bool(_PHONE_GOOD_RE.search(head))
        if not good and _PHONE_BAD_RE.search(head):
            continue
        mobile = len(digits) == 11 and digits.startswith('1')
        score = (2 if good else 0) + (1 if mobile else 0)
        if score > best_score:
            best, best_score = digits, score
            if score == 3:                    # 好标签 + 手机号，够好了
                break
    return best


def _drop_shared_phones(rows, min_companies=3):
    """清掉「一个号码挂在好几家公司头上」的假电话。

    为什么必须做这一步：聚合站（华图/中公这类）的公告页底部挂着**站点自己的**
    统一咨询电话，每篇都有。单看一篇，它前后也写着「咨询电话」，标签判据完全
    认不出来——实测回填时 02787870401 这一个号同时挂在湖北长江运营、黄石城投、
    武汉大学、湖北数字文旅、襄阳能源…十几家互不相关的单位上。真要打过去，
    接电话的是站点的客服，不是招人的人。

    判据很硬：一个 HR 的电话不可能同时属于 3 家不同公司。达到阈值就整批清空。
    """
    if not rows:
        return rows
    owners = {}
    for j in rows:
        p = (j.get('hr_phone') or '').strip()
        if not p:
            continue
        owners.setdefault(p, set()).add((j.get('company') or '').strip())
    shared = {p for p, cs in owners.items() if len(cs) >= min_companies}
    if not shared:
        return rows
    for j in rows:
        if (j.get('hr_phone') or '').strip() in shared:
            j['hr_phone'] = ''
    return rows


def _extract_apply_target(html, fallback_url):
    """从公告正文里挖真正的投递入口。

    返回 (apply_url, hr_email)：
      · apply_url 优先取「招聘/报名系统」外链（那才是能点进去报名的页），
        挖不到就退回公告页本身（至少是这条岗位的具体页，不再是门户首页）；
      · hr_email 取正文里**像报名邮箱的那一个**（见 _pick_apply_email），
        供邮件投递用；只有监督/咨询邮箱或全是占位符时返回 ''。
    """
    urls = _URL_RE.findall(html or '')
    apply_url = fallback_url
    for u in dict.fromkeys(urls):            # 保序去重
        if _BAD_URL_RE.search(u):
            continue
        if _JOBSYS_RE.search(u):
            apply_url = u
            break
    host = (urlparse(fallback_url).hostname or '').lower()
    hr_email = _pick_apply_email(_email_text(html), host)
    return apply_url, hr_email


def _parse_article(url, html, link_text='', city_from_body=True):
    """从一篇公告 HTML 解析出一条岗位记录；非招聘公告返回 None。

    city_from_body: 是否允许扫正文补城市。干净的官方源（如省国资委）正文
    就是文章本身，可放心扫；但华图等聚合站正文夹带大量「黄石/武汉」侧栏导航
    噪声，扫正文会把无关公告错标成黄石——这种源应传 False，城市只信标题，
    标题没有就回落源地区名（宁可不标也不标错）。
    """
    body = _clean(html)
    if not _JOB_SIGNAL.search(link_text or '') and not _JOB_SIGNAL.search(body):
        return None
    # 后期公示/复审/成绩/准考证等对求职者已无投递价值，标题命中即丢弃
    if _NOISE_RE.search(link_text or ''):
        return None
    # 公司名：优先列表标题（站点作者撰写，最干净），正文侧栏/推荐里的公司名
    # 是噪声，绝不用它污染信息流；标题也拿不到就退一步只扫正文前段，再没有就丢弃。
    company = _find_company(link_text) if link_text else None
    if not company:
        company = _find_company(body[:600])
    if not company:
        return None
    company = _strip_lead(company)
    if len(company) < 2:
        return None
    # 性质：以单位主体类型为主、招聘信号为辅，避开「非公务员/不纳入事业编」这类否定句误判
    # 文本信号只扫「标题 link_text」，绝不扫正文 body——华图等聚合站正文夹带大量
    # 「公务员/省考/公考」侧栏导航噪声，会把事业/国企公告误标成公务员。
    comp = company or ''
    is_corp = bool(re.search(r'公司|集团|股份|企业', comp))
    is_inst = bool(re.search(r'医院|大学|学院|学校|研究院|研究所|事业单位', comp))
    is_gov = bool(re.search(r'局|委|厅|机关|政府|法院|检察院', comp))
    title_sig = link_text or ''
    has_institution = bool(re.search(
        r'事业单位(招聘|编制|公开招|工作人|岗位|考试)|招聘事业单位|事业编制',
        title_sig))
    has_civil = bool(re.search(
        r'公务员(招录|招聘|招考|考试)|省考|国考|选调生|公开遴选|公考', title_sig))
    if is_corp:
        nature = '央企' if _is_central(comp) else '国企'
    elif is_gov or has_civil:
        nature = '公务员'
    elif is_inst or has_institution:
        nature = '事业单位'
    else:
        nature = '国企'
    # 日期（优先「发布时间」这种明确字段，其次标题/正文里出现的日期）
    d = _DATE_RE.search(html) or _PLAIN_DATE_RE.search((link_text or '') + ' ' + body)
    date = d.group(1).replace('年', '-').replace('月', '-').replace('/', '-') if d else ''
    hire = '劳务派遣' if DISPATCH_RE.search(body) else '直签'
    # 城市：优先「标题 link_text」里的城市；标题没有时，仅在 city_from_body=True
    # （干净的官方源）时才扫正文补城市。聚合站正文噪声大，由调用方关掉此开关，
    # 城市回落源地区名（宁可不标也不标错）。
    ttitle = link_text or ''
    _wh = '武汉'
    city = next((c for c in _CITY_HINTS if c in ttitle), '')
    if not city and city_from_body:
        city = next((c for c in _CITY_HINTS if c != _wh and c in body), '')
    if not city and _wh in ttitle:
        city = _wh
    # 标题：列表文字若像公告就用它，否则退化成「公司 招聘公告」
    title = link_text if (link_text and len(link_text) < 60 and _JOB_SIGNAL.search(link_text)) else (company + ' 招聘公告')
    # 深链：优先用正文里的招聘/报名系统链接（那才是真能报名的页）；
    # 挖不到就用公告原文页（这条岗位的具体页）。
    # 若两者都没设，normalize() 会兜底成门户首页，自动投只能打开首页、找不到入口。
    apply_url, hr_email = _extract_apply_target(html, url)
    # 电话单独走一遍：民企/基层岗位大量只在公告里留个手机号，没有邮箱
    hr_phone = _pick_apply_phone(_email_text(html))
    art = _article_body(html, body, title)
    return {
        'company': company,
        'title': title[:60],
        'city': city,
        'salary_text': '面议',
        'desc': art[:1500],
        'url': url,
        'apply_url': apply_url,
        'hr_email': hr_email,          # 公告里的报名邮箱，供邮件投递用
        'hr_phone': hr_phone,          # 公告里的联系电话，供电话/短信联系用
        'nature': nature,
        'hire_type': hire,
        'deadline': date,
        'published': date,
        'headcount': 0,
        'recruit_type': '',
    }


# GovSoeCollector 服务的单位性质。含「民营」的理由见 fetch() 里的注释。
_SERVED_NATURES = {'央企', '国企', '事业单位', '公务员', '民营'}


class GovSoeCollector(BaseCollector):
    name = 'gov_soe'
    label = '地方国资委/人社局·国企招聘公告'

    def fetch(self, keyword='', city='', limit=30, natures=None):
        # 服务于央企/国企/事业单位/公务员；用户只要外企等其它性质时不联网。
        # 注意集合里必须有「民营」：这些公告源里同样夹着民企岗位（国企集团下的
        # 子公司、混改公司），早年把它漏了，导致用户只勾民营时本源直接罢工、
        # 一条都出不来（2026-09-22 修）。提成模块常量是为了能单测——
        # 否则只能真跑一次联网采集才知道门禁放不放行。
        if natures and not (_SERVED_NATURES & set(natures)):
            return []
        sources = _load_sources()
        if not sources:
            return []

        # 1) 先按城市筛出**真正会抓的源**。
        #    这里必须筛完再分配配额：早先 per_region 的分母写成 len(sources)
        #    （全部源），用户选了「武汉」只剩一两个湖北源能命中，每源却仍只分到
        #    limit//全部源数 篇，白白扔掉大半公告；而配置里的 max_articles
        #    压根没被读过（2026-09-21 修）。
        picked = []
        for cfg in sources:
            region_city = cfg.get('city', '')
            # 「全国」源（中公行业子站这类）不绑定任何城市，用户选「武汉」时
            # 也必须抓——否则它们会被整源跳过，等于白扩。全国源的公告里
            # 常有武汉/湖北的央企二级单位岗位（2026-09-21 扩源时发现）。
            if city and region_city and region_city != '全国':
                in_region = (city in region_city) or (city in (cfg.get('label') or ''))
                in_province = city in PROVINCE_CITIES.get(region_city, [])
                if not (in_region or in_province):
                    continue
            picked.append(cfg)
        if not picked:
            return []

        # 2) 每源抓取量：以该源自己配的 max_articles 为准（那是这个源的抓取深度），
        #    limit 只做整体封顶——但**每源下限保 5 篇**。早先写的是
        #    min(limit, 60) // len(sources)，源从 5 个扩到 29 个之后每源只剩 1 篇，
        #    等于「扩了源反而更少」（2026-09-21 修）。
        budget = max(1, int(limit or 30))
        share = max(5, budget // len(picked))

        # 3) 各省站是不同子域，并行抓互不打扰（net 的节流是按域名串行的，
        #    同域内仍然守规矩）。串行抓 20+ 个省要把后台刷新拖到十几分钟。
        def _one(cfg):
            per = max(1, min(int(cfg.get('max_articles') or 15), share))
            try:
                return self._one_region(cfg, per, keyword) or []
            except Exception:                   # noqa: BLE001 单源隔离
                return []

        out = []
        with ThreadPoolExecutor(max_workers=min(len(picked), 8)) as ex:
            for rows in ex.map(_one, picked):
                out.extend(rows)
        # 聚合站页面底部挂着站点自己的统一咨询电话，每篇都有 —— 跨篇看才发现。
        # 放在这里做（而不是每篇内做），因为判据需要「同一个号属于几家公司」
        # 这种跨篇信息。
        out = _drop_shared_phones(out)
        return out[:budget]

    def _one_region(self, cfg, per, keyword):
        html = _fetch_html(cfg['list_url'])
        if not html:
            return []
        link_re = re.compile(cfg.get('list_link_re', r'content/post_\d+\.html'))
        base = cfg.get('base_url', '')
        pairs = []
        for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                             html, re.S | re.I):
            u, raw_txt = m.group(1), m.group(2)
            if not link_re.search(u):
                continue
            abs_u = _abs(u, base)
            if abs_u not in [p[0] for p in pairs]:
                pairs.append((abs_u, _clean(raw_txt)))
        rows = []
        for link, txt in pairs[:per]:
            art = _fetch_html(link)
            if not art:
                continue
            rec = _parse_article(link, art, txt,
                                  city_from_body=cfg.get('city_from_body', True))
            if not rec:
                continue
            if not rec.get('city'):
                rec['city'] = cfg.get('city', '')
            rec['region'] = cfg.get('region', '')
            # 关键词过滤（用户搜了具体词时，只留命中的公告）
            if keyword and keyword not in (rec['company'] + rec['title'] + rec['desc']):
                continue
            rows.append(self.normalize(rec, self.name))
            time.sleep(0.3)
        return rows
