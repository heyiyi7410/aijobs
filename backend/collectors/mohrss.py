# -*- coding: utf-8 -*-
"""中国公共招聘网采集器（job.mohrss.gov.cn，人社部官方聚合站）。

为什么拿它当第二国企源：
  * 它是人社部官方的「全国招聘信息聚合站」，覆盖海量地方国企 / 央企岗位，
    很多地方国资委下属企业只在这上面发，国聘网反而没有 —— 正好补上「茧房」的另一半。
  * 列表接口完全开放、明文 HTTP、无需登录 / cookie / 签名。

接口（抓包 + 实测得到）：
  GET/POST http://job.mohrss.gov.cn/cjobs/jobinfolist/listJobinfolist
      ?pageNo=N&orderType=score&aab019=10&textfield=<关键词>
  数据不在 JSON 里，而是藏在 HTML 的 <input id="findjoblist" value="..."> 中
  （value 是 HTML 转义过的 JSON 字符串），正则抠出来 html.unescape 后 json.loads 即可。
  每页 20 条。

两个关键坑（对应「换源不破防」第二层）：
  1) 服务端只对 aab019 提供**顶层大类**（10=企业 / 30=机关 / 50=事业单位），
     「11=国有企业」之类的细分类服务端不支持，返回 0 条；而且它的搜索表单
     **没有公司名字段**，也没有单位性质字段，只有工种 / 地区 / 性质 / 薪资。
     → 所以「只捞国企」既不能靠服务端过滤，也不能读官方性质，只能靠公司名识别。
  2) 但服务端支持全文检索参数 `textfield`（匹配标题/描述），用它带用户的关键词，
     能精准拿到相关岗位（比盲扫全量企业库强太多）。

═══ 2026-09-21 重大修正：这个源其实是民企主池子，我们一直把它关着 ═══

实测（probe_mohrss_pool.py，翻 8 页 160 条去重后）：
  · 有合法电话 118 条（74%），有联系人姓名 160 条（100%）
  · 性质分布：民营 155（97%）/ 国企 4 / 央企 1
  · 也就是说**每页 20 条里我们收下 1~2 条、丢掉 18 条**

原因就在下面 `_nature_of()` 的老实现：识别不出央企/国企就返回 None，
调用方直接丢弃。当年写这条的理由是「宁可漏掉，也不把民企谎标成国企污染
央企/国企信息流」——那个底线现在依然成立，但代价是把整个民企池子关在门外，
而民企恰恰是这个站的大头（aab019=10 是服务端「企业」大类，里面 97% 是民企）。

修正后的判定：央企/国企/外企关键词命中就按命中走，剩下的一律标**民营**。
误标方向是「不知名国企 → 民营」，只会让用户少看一条，绝不会让用户以为自己
在投国企 —— 和原来那条底线（不许把差的吹成好的）方向相反，可以接受。

═══ 第二个修正：电话不用抓详情页，列表报文里就有 ═══

列表 JSON 自带三个联系字段：
  aae004 联系人姓名（如「夏老师」「周小姐」）  aae005 联系电话  aae006 单位地址
所以电话是**零额外请求**拿到的（原来是每条都抓一次详情页，还只为了拿邮箱，
命中率却很低——详情页多半写「邮箱：暂无介绍」）。现在详情页只在邮箱预算内抓。

═══ 第三个修正：城市字段缺地级市，必须用地址补 ═══

报文里 area_ 是省、aab302 是区，**没有地级市**，而且源数据有错配
（「北京碧水物业管理」的 area_ 竟然是「内蒙古自治区」）。用户搜「武汉」时
按省/区匹配会全部落空。acb202 是企业自己填的完整地址（含「武汉市洪山区…」），
比 area_ 可靠，城市匹配必须带上它。
═══ 第四个修正：地区过滤参数是**大写 AREA**，且必须传行政区划代码 ═══

表单里藏着 `AREA`（=aab301，行政区划代码）和 `AREA_name` 两个隐藏字段。
之前传小写的 `area` 服务端直接忽略，于是只能拿全国混杂结果 —— 用户搜
「武汉」一条都出不来（实测 0 条）。换成 AREA=420100 之后：
    武汉 → 2225 条岗位，20/20 条带电话（岚图汽车、施耐德电气制造（武汉）、
    武汉海康威视、武汉天源环保…）
这才是这个源对本地求职者的真正价值，前面全是白跑。

═══ 第五个修正：这里也有「聚合站统一热线」 ═══

武汉结果里 `17307102651` 同时出现在岚图汽车、东海敏实、奋进智能、施耐德、
海康威视、吉兴汽车、莱恩输变电等 7 家毫不相干的公司 —— 跟 gov_soe 那边踩过
的是同一个坑：那是第三方招工中介的统一号，不是任何一家的 HR。
所以这里也必须跑 `_drop_shared_phones()`，否则用户打过去全是中介。
"""
import re
import time
import html as _html
import json as _json
import urllib.parse

from . import net
from .base import BaseCollector
from .guopin import CENTRAL_KEYWORDS          # 复用国聘网的央企集团名录（~98 家）
from .gov_soe import _extract_apply_target    # 复用公告页的邮箱/报名入口提取
from .gov_soe import _drop_shared_phones      # 复用「同一个号挂 N 家公司 = 中介热线」判据

# 列表 / 详情
LIST_URL = 'http://job.mohrss.gov.cn/cjobs/jobinfolist/listJobinfolist'
DETAIL = 'http://job.mohrss.gov.cn/cjobs/jobinfolist/cb21/showgw?id=%s'
HOME = 'http://job.mohrss.gov.cn'
REF = HOME + '/cjobs/jobinfolist/listJobinfolist'

# 企业层（其它大类里没有央企国企）
AAB019 = '10'
PAGE_SIZE = 20
# 电话现在从列表免费拿，翻页成本大幅下降，从 2 页扩到 6 页（120 条候选）。
# 再往上翻边际收益低：同一批企业会反复刷屏（实测「首都信息发展」一页出现 4 次）。
MAX_PAGE = 6
# 邮箱要抓详情页（每条一次请求），实测多半是「暂无介绍」，所以限量。
# 只给「已经有电话」之外的岗位补邮箱，预算用完就不再请求。
EMAIL_BUDGET = 12
# 翻页间的退避：密集请求会被瞬时限流，先睡一下再退避重试
PAGE_GAP = 0.6
RETRY_GAP = 1.2

# ── 行政区划代码（GB/T 2260）：地区过滤只认这个，不认城市名 ──────────────
# 服务端 AREA 参数吃的是 aab301 代码。传城市名没有用，传错则被忽略回退到全国。
# 这张表覆盖省会/直辖市/计划单列市 + 湖北全部地级市（用户在湖北求职）；
# 表里没有的城市会退到「省级代码 + 本地文本过滤」，功能不丢，只是没那么精准。
CITY_CODES = {
    '北京': '110000', '天津': '120000', '上海': '310000', '重庆': '500000',
    '石家庄': '130100', '唐山': '130200', '保定': '130600',
    '太原': '140100', '大同': '140200',
    '呼和浩特': '150100', '包头': '150200',
    '沈阳': '210100', '大连': '210200', '鞍山': '210300',
    '长春': '220100', '吉林': '220200',
    '哈尔滨': '230100', '大庆': '230600',
    '南京': '320100', '无锡': '320200', '徐州': '320300', '常州': '320400',
    '苏州': '320500', '南通': '320600', '扬州': '321000',
    '杭州': '330100', '宁波': '330200', '温州': '330300', '嘉兴': '330400',
    '金华': '330700',
    '合肥': '340100', '芜湖': '340200',
    '福州': '350100', '厦门': '350200', '泉州': '350500',
    '南昌': '360100', '赣州': '360700', '九江': '360400',
    '济南': '370100', '青岛': '370200', '烟台': '370600', '潍坊': '370700',
    '郑州': '410100', '洛阳': '410300', '新乡': '410700',
    # 湖北全域（用户主要求职地，全部列出）
    '武汉': '420100', '黄石': '420200', '十堰': '420300', '宜昌': '420500',
    '襄阳': '420600', '鄂州': '420700', '荆门': '420800', '孝感': '420900',
    '荆州': '421000', '黄冈': '421100', '咸宁': '421200', '随州': '421300',
    '恩施': '422800', '仙桃': '429004', '潜江': '429005', '天门': '429006',
    '长沙': '430100', '株洲': '430200', '湘潭': '430300', '衡阳': '430400',
    '广州': '440100', '深圳': '440300', '珠海': '440400', '佛山': '440600',
    '东莞': '441900', '中山': '442000', '惠州': '441300',
    '南宁': '450100', '柳州': '450200', '桂林': '450300',
    '海口': '460100', '三亚': '460200',
    '成都': '510100', '绵阳': '510700', '德阳': '510600',
    '贵阳': '520100', '遵义': '520300',
    '昆明': '530100', '曲靖': '530300',
    '拉萨': '540100',
    '西安': '610100', '咸阳': '610400', '宝鸡': '610300',
    '兰州': '620100', '西宁': '630100', '银川': '640100',
    '乌鲁木齐': '650100', '喀什': '653100',
}

# 省级代码：城市表里查不到时用它兜底（拿全省结果再本地按城市名筛）
PROVINCE_CODES = {
    '北京': '110000', '天津': '120000', '上海': '310000', '重庆': '500000',
    '河北': '130000', '山西': '140000', '内蒙古': '150000', '辽宁': '210000',
    '吉林': '220000', '黑龙江': '230000', '江苏': '320000', '浙江': '330000',
    '安徽': '340000', '福建': '350000', '江西': '360000', '山东': '370000',
    '河南': '410000', '湖北': '420000', '湖南': '430000', '广东': '440000',
    '广西': '450000', '海南': '460000', '四川': '510000', '贵州': '520000',
    '云南': '530000', '西藏': '540000', '陕西': '610000', '甘肃': '620000',
    '青海': '630000', '宁夏': '640000', '新疆': '650000',
}

# 城市 → 所属省（用于城市代码表未命中时找省级代码）。
# 跟 models._province_cities 同源，避免两处各写一套。
_CITY_TO_PROVINCE = None


def _area_code(city):
    """城市名 → AREA 参数值。返回 (代码, 是否精确到城市)。

    精确到城市时可以让服务端直接筛干净；只能到省级时，调用方还得再用
    城市名在本地过一遍（省级结果里混着兄弟城市）。
    """
    if not city:
        return '', False
    c = (city or '').replace('市', '').replace('省', '').replace(
        '自治区', '').strip()
    if not c:
        return '', False
    if c in CITY_CODES:
        return CITY_CODES[c], True
    # 手写表没收录的地级市 → 回落到全国表（343 个，从就业在线城市字典生成）。
    # 命中就能让服务端精确筛，不必退到「全省 + 本地文本过滤」。
    try:
        from .city_codes import CITY_CODES_FULL
        if c in CITY_CODES_FULL:
            return str(CITY_CODES_FULL[c]), True
    except Exception:                              # noqa: BLE001
        pass
    if c in PROVINCE_CODES:
        return PROVINCE_CODES[c], False
    # 城市表没收录的小城市：不猜代码（猜错会被服务端忽略、悄悄回退成全国，
    # 那比不传还危险），交给调用方用城市名在本地过一遍。
    return '', False

# 本地国企（地方国资）强信号词。只收「基本不可能是民企」的词，
# 避免出现「集团 / 股份 / 有限公司」这类通用词把民企误判成国企。
STATE_KEYWORDS = [
    # 国资字样（最强信号）
    '国资委', '国有资产', '国有独资', '国有控股', '国有出资', '国有资本',
    '国有资产管理',
    # 城投 / 城建
    '城投', '城建投资', '城市建设投资', '城市建投',
    # 交通 / 港航
    '交投', '交通投资', '港务', '港航', '轨道交通', '地铁', '公交', '市政',
    # 公用事业
    '水务', '自来水', '排水', '污水处理', '供热', '供暖', '燃气',
    # 粮农 / 盐业 / 供销
    '粮油', '粮食', '盐业', '供销', '农垦', '储备粮',
    # 文旅 / 金融国资
    '文旅', '旅投', '旅游投资', '文化投资', '金控', '金融控股',
    # 机场 / 安居
    '机场', '安居', '保障房', '公租房',
    # 农林水
    '水利', '灌区', '水库', '林场', '苗圃', '农机', '种业', '水产',
]

# 正文里出现这些词 → 多半是劳务派遣，不是跟单位直签
DISPATCH_RE = re.compile(r'劳务(派遣|外包|派遗)|派遣(制|用工)|第三方(用工|派遣)'
                         r'|外包(员工|用工)|与.{0,6}劳务公司(签订|签约)')

# 公司名里带这些词 → 人力资源/劳务中介，不是真实用工单位。
# 民企池子里这类占比不低（实测「共青城启辰人力资源管理有限公司红安分公司」
# 一页刷了 4 条），用户最烦的就是满怀希望打过去发现是中介，必须标出来。
AGENCY_RE = re.compile(r'人力资源|劳务派遣|劳务服务|人才服务|职业介绍|'
                       r'企业管理服务|服务外包|外包服务|劳务分包|人力服务')

# 外企：名字里带外资字样。识别不出就按民营走，不单独设「合资」
FOREIGN_RE = re.compile(r'外资|外商|外企|独资\(外国|合资|\(中国\)|中国有限公司')

# 列表页里 findjoblist 这个隐藏 input 存了转义后的 JSON
_FINDBOX_RE = re.compile(r'id="findjoblist"[^>]*value="([^"]*)"')

# 电话：座机 0xxx-xxxxxxx / 手机 1xx-xxxx-xxxx，允许中间有空格或短横线
_TEL_RE = re.compile(r'(?<!\d)(1[3-9]\d[\d\s\-–]{7,11}\d|0\d{2,3}[\s\-]?\d{7,8})(?!\d)')


def _is_central(name):
    return any(k in name for k in CENTRAL_KEYWORDS)


def _is_state(name):
    return any(k in name for k in STATE_KEYWORDS)


def _nature_of(company):
    """公司名 → 央企 / 国企 / 外企 / 民营（不再返回 None 丢弃）。

    历史：这里原来识别不出就返回 None，调用方直接丢弃，理由是「绝不把民企
    谎标成国企」。那条底线保留，但「丢弃」这个动作把占 97% 的民企一起丢了。
    现在识别不出就是民营 —— aab019=10 已经是服务端「企业」大类，里面不可能
    有事业单位/机关，标民营不会把体制内岗位污染成体制外。
    """
    if _is_central(company):
        return '央企'
    if _is_state(company):
        return '国企'
    if FOREIGN_RE.search(company):
        return '外企'
    return '民营'


def _norm_phone(value):
    """报文里的电话 → 干净号码；不是号码就返回 ''。

    实测脏值有两种：占位符（0000000 / 12345678）和带分机的座机
    （010-88511155-5849，取主干即可）。重复数字和过短的一律判为无效。
    """
    v = (value or '').strip()
    if not v:
        return ''
    m = _TEL_RE.search(v)
    if not m:
        return ''
    p = re.sub(r'[\s\-–]', '', m.group(0))
    if len(p) < 7 or len(set(p)) <= 2:      # 7 位以下 / 全是重复数字
        return ''
    return p


def _match_keyword(item, keyword):
    kw = (keyword or '').strip()
    if not kw:
        return True
    hay = '%s %s %s' % (item.get('title', ''), item.get('company', ''),
                         item.get('desc', ''))
    if re.search(r'[\u4e00-\u9fff]', kw):
        return kw in hay
    return kw.lower() in hay.lower()


def _match_city(want, city, district, address=''):
    """城市匹配。第三个字段 address 是必须的，不是可选优化。

    这个源的 area_ 只有省、aab302 只有区，中间那层地级市是缺的，
    而且源数据还会错配（实测「北京碧水物业管理」的 area_ 是「内蒙古自治区」）。
    只有企业自己填的完整地址 acb202 里才有「武汉市洪山区…」这种带地级市的字符串，
    不加它，用户搜任何地级市都会全部落空。
    """
    if not want:
        return True
    w = want.replace('市', '').replace('省', '').replace('自治区', '').strip()
    if not w:
        return True
    for cand in (city or '', district or '', address or ''):
        c = (cand or '').replace('市', '').replace('省', '').replace(
            '自治区', '').replace('县', '').replace('区', '').strip()
        if c and (w in c or c in w):
            return True
    return False


def _salary(lo, hi):
    lo, hi = int(lo or 0), int(hi or 0)
    if lo and hi and lo != hi:
        return '%d-%d元/月' % (lo, hi)
    if hi:
        return '%d元/月' % hi
    if lo:
        return '%d元/月' % lo
    return '面议'


class MohrssCollector(BaseCollector):
    """中国公共招聘网：人社部官方聚合站，作为国聘网之外的第二国企源。"""

    name = 'mohrss'
    label = '中国公共招聘网（人社部官方聚合站·民企/央企国企）'

    # 放开民营：这个源 97% 是民企，只在用户勾了央企/国企时才去请求的老规则
    # 等于把最大那块池子锁死。用户没勾任何性质时（默认全选）当然也跑。
    WANTED = ('央企', '国企', '外企', '民营')

    def fetch(self, keyword='', city='', limit=30, natures=None):
        if natures and not any(n in natures for n in self.WANTED):
            return []
        kw = (keyword or '').strip()
        # 城市名 → 行政区划代码（服务端 AREA 参数只吃代码，传城市名会被忽略）。
        # 代码精确到市时服务端就筛干净了，不需要再拿城市名当检索词；
        # 代码只能到省、或城市表没收录时，才用城市名兜底做全文检索。
        area_code, precise = _area_code(city)
        search = kw
        if not search and not precise:
            search = (city or '').strip()
        out = []
        empty_streak = 0
        # 源里同一家公司的同名岗位会反复刷屏（实测「首都信息发展」一页 4 条、
        # 「启辰人力」4 条），按岗位 id 去重，否则翻再多页也只是重复。
        seen = set()
        for page in range(1, MAX_PAGE + 1):
            if len(out) >= limit:
                break
            rows = self._one_page(page, search, area_code, city)
            if not rows:
                # 可能是真到底，也可能是被瞬时限流（返回 0 假象）。
                # 退避后重试一次；仍空则记一次空页。连续两页空才认定「到底」
                # 而停下，这样既能跳过单页瞬时限流、又不至于盲扫到最后一页。
                time.sleep(RETRY_GAP)
                rows = self._one_page(page, search, area_code, city)
                if not rows:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                    if page < MAX_PAGE:
                        time.sleep(PAGE_GAP)
                    continue
            empty_streak = 0
            for r in rows:
                # 只有拿到岗位 id 才去重：id 缺失时若也按 None 去重，
                # 会把整页压成一条（掉这个坑时实测 4 条只剩 1 条）。
                rid = r.get('acb200')
                if rid:
                    if rid in seen:
                        continue
                    seen.add(rid)
                out.append(r)
        # 同一个中介统一热线挂在多家公司头上 → 清成空（否则用户打过去全是中介）。
        # 放在补邮箱之前，省得给这些假电话岗位浪费详情页请求。
        out = _drop_shared_phones(out)
        # 本地城市过滤：AREA 精确到市时服务端已筛干净，这里不再二次过滤
        # （否则个别 address 字段缺失的真·武汉岗位会被误杀）；只能到省级 /
        # 无代码（靠 textfield 兜底）时才靠地址再筛一遍。
        filtered = []
        for item in out:
            if city and not precise and not _match_city(
                    city, item['city'], item['district'],
                    item.get('_address', '')):
                continue
            filtered.append(item)
            if len(filtered) >= limit:
                break
        # 邮箱要抓详情页（每条一次请求，实测多半是「邮箱：暂无介绍」），
        # 所以只给前 EMAIL_BUDGET 条补，预算用完就不再请求。
        # 电话已经从列表免费拿到了，补不到邮箱的岗位依然可电话联系。
        for item in filtered[:EMAIL_BUDGET]:
            item['hr_email'] = self._detail_email(item)
        return filtered[:limit]

    def _detail_email(self, item):
        """抓详情页补一个报名邮箱；抓不到/异常都返回 ''（不挡岗位）。"""
        jid = item.get('_jid') or ''
        if not jid:
            return ''
        url = DETAIL % jid
        try:
            dhtml = net.request(url,
                                headers={'Referer': REF, 'Accept': 'text/html,*/*'},
                                timeout=10, retries=1, as_json=False,
                                bypass_proxy=True, cache_ttl=3600)
            if not dhtml:
                return ''
            _, mail = _extract_apply_target(dhtml, url)
            return mail or ''
        except Exception:                      # noqa: BLE001 邮箱是加分项，失败不影响岗位
            return ''

    def _one_page(self, page, keyword, area_code='', area_name=''):
        params = {'pageNo': page, 'orderType': 'score', 'aab019': AAB019}
        if keyword:
            params['textfield'] = keyword          # 全文检索：标题/描述都算
        if area_code:
            params['AREA'] = area_code             # 行政区划代码：服务端按地区筛
            params['AREA_name'] = area_name or ''  # 表单里这对字段一起传，缺了会被忽略
        url = '%s?%s' % (LIST_URL, urllib.parse.urlencode(params))
        raw = net.request(url, headers={'Referer': REF, 'Accept': 'text/html,*/*'},
                          timeout=12, retries=1, as_json=False,
                          bypass_proxy=True,        # 明文 HTTP，绕开可能指向本地坏代理的 http_proxy
                          cache_ttl=1800)           # 30 分钟缓存
        if not raw:
            return []
        m = _FINDBOX_RE.search(raw)
        if not m:
            return []
        try:
            data = _json.loads(_html.unescape(m.group(1)))
        except Exception:                          # noqa: BLE001 脏数据直接跳过
            return []
        if not isinstance(data, list):
            return []
        out = []
        for j in data:
            item = self._convert(j)
            if item:
                out.append(item)
        return out

    def _convert(self, j):
        title = (j.get('aca112') or '').strip()
        company = (j.get('aab004') or '').strip()
        if not title or not company:
            return None
        nature = _nature_of(company)          # 央企/国企/外企/民营，不再返回 None
        city = (j.get('area_') or '').strip()           # 省（如 黑龙江省）
        district = (j.get('aab302') or '').strip()      # 县/区（如 宝清县）
        # 企业自己填的完整地址，含地级市，城市匹配靠它（area_ 只有省，还会错配）
        address = (j.get('acb202') or '').strip()
        desc = re.sub(r'\s+', ' ', j.get('acb22a') or '')[:1200]
        # 中介/派遣要标出来：公司名带「人力资源/劳务」或正文写派遣制。
        # 民企池子里这类不少，用户打过去发现是中介最浪费时间。
        hire = '劳务派遣' if (DISPATCH_RE.search(desc)
                             or AGENCY_RE.search(company)) else '直签'
        jid = j.get('acb200') or ''
        tags = [t for t in [j.get('aca111_'), j.get('org_')] if t]
        # 电话：列表报文 aae005 直接给，零额外请求（原来要抓详情页，还拿不到）。
        # aae004 是联系人姓名，写进 desc 开头，用户打电话知道找谁。
        hr_phone = _norm_phone(j.get('aae005'))
        contact = (j.get('aae004') or '').strip()
        if contact and len(contact) <= 10 and not contact.isdigit():
            desc = '联系人：%s。%s' % (contact, desc) if hr_phone else desc
        # 详情页只在邮箱预算内抓（见 fetch），这里只留 id
        apply_url = DETAIL % jid if jid else HOME
        return self.normalize({
            'title': title,
            'company': company,
            'city': city,
            'district': district,
            'salary_text': _salary(j.get('acb241'), j.get('acb242')),
            'exp': '经验不限',
            'edu': '不限',
            'tags': tags,
            'desc': desc,
            'nature': nature,
            'hire_type': hire,
            'url': apply_url,
            'apply_url': apply_url,
            'hr_email': '',             # 由 fetch 按预算补
            'hr_phone': hr_phone,       # 列表自带，民企最主要的联系通道
            '_jid': jid,                # 内部字段，抓邮箱用，入库前不保留
            '_address': address,        # 内部字段，城市匹配用
            'deadline': (j.get('s_aae398') or '')[:10],
            'published': (j.get('s_aae395') or j.get('s_ctime') or '')[:10],
            # 公共招聘网列表报文不含招聘人数 / 校招社招，留空由前端隐藏
            'headcount': 0,
            'recruit_type': '',
        }, self.name)
