# -*- coding: utf-8 -*-
"""外企官网直招采集器。

为什么要有这一条路：
  51job / 猎聘 / BOSS 全部强制登录或 WAF 拦死，硬啃要么成本极高要么违规。
  但外企**自己的官网招聘页是给求职者看的公开信息**，本来就希望你找到它——
  不需要登录、不需要 cookie、没有签名。更关键的是：官网直招 = 直签，
  天然不会混进劳务派遣，这正是我们最想给用户的结果。

已接入：
  亚马逊中国 amazon.jobs —— 实测 269 条在招岗位，接口全开放，
  返回体自带投递链接（url_next_step）和发布日期。

新增一家外企 = 照抄一个 _fetch_xxx()，在 fetch() 的循环里加一行即可，
不用动别的地方。
"""
import re
import urllib.parse

from . import net
from .base import BaseCollector

# 亚马逊中国：公开 JSON，无鉴权。注意 query 参数在服务端不生效，
# 只能全量拉取后本地过滤关键词。
AMZ_API = 'https://www.amazon.jobs/en/search.json'
AMZ_HOME = 'https://www.amazon.jobs'
AMZ_PAGE = 100                      # 每页最多 100 条
AMZ_MAX_PAGE = 3                    # 最多 3 页，够覆盖中国区在招量

# 亚马逊返回的城市是英文，求职者填的是中文，需要映射后才能匹配
CITY_EN2CN = {
    'beijing': '北京', 'shanghai': '上海', 'guangzhou': '广州',
    'shenzhen': '深圳', 'chengdu': '成都', 'hangzhou': '杭州',
    'dalian': '大连', 'suzhou': '苏州', 'xian': '西安', "xi'an": '西安',
    'tianjin': '天津', 'wuhan': '武汉', 'nanjing': '南京',
    'chongqing': '重庆', 'qingdao': '青岛', 'shenyang': '沈阳',
    'xiamen': '厦门', 'ningbo': '宁波', 'changsha': '长沙',
    'zhengzhou': '郑州', 'jinan': '济南', 'hefei': '合肥',
    'foshan': '佛山', 'dongguan': '东莞', 'wuxi': '无锡',
    'shijiazhuang': '石家庄', 'harbin': '哈尔滨', 'changchun': '长春',
    'kunming': '昆明', 'nanning': '南宁', 'fuzhou': '福州',
    'hong kong': '香港', 'taipei': '台北',
}

# 出现这些词基本是外包/派遣，外企官网虽然少见，但转包岗位还是标记一下
DISPATCH_RE = re.compile(r'劳务(派遣|外包)|派遣(制|用工)|第三方(用工|派遣)'
                         r'|外包(员工|用工)|dispatch(ed)?\s+worker')


def _cn_city(raw):
    """英文城市名转中文，转不出来就原样返回（中文岗位名里可能自带）。"""
    s = (raw or '').strip()
    if not s:
        return ''
    return CITY_EN2CN.get(s.lower(), s)


def _match_city(want, job_city_cn, job_city_raw):
    """城市匹配。求职者填「成都」，岗位写 Chengdu，两边都要能对上。"""
    if not want:
        return True
    want = want.replace('市', '').strip()
    if not want:
        return True
    cands = [job_city_cn or '', job_city_raw or '']
    for c in cands:
        c = (c or '').replace('市', '').strip()
        if c and (want in c or c in want):
            return True
    return False


def _posted(posted_date):
    """'September 16, 2026' → '2026-09-16'。转不出来就空着。"""
    m = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5,
        'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10,
        'november': 11, 'december': 12,
    }
    s = (posted_date or '').strip().lower()
    parts = s.replace(',', ' ').split()
    try:
        month, day, year = parts[0], parts[1], parts[2]
        return '%s-%02d-%02d' % (year, m[month], int(day))
    except Exception:                            # noqa: BLE001
        return ''


class ForeignCollector(BaseCollector):
    """外企官网直招。目前接了亚马逊中国，后面按同一模式加。"""

    name = 'foreign'
    label = '外企官网直招（亚马逊中国等）'

    # 只在用户勾了外企/合资时才去请求，别白花一次网络开销
    WANTED = ('外企', '合资')

    def fetch(self, keyword='', city='', limit=30, natures=None):
        if natures and not any(n in natures for n in self.WANTED):
            return []
        rows = []
        for one in (self._fetch_amazon(limit),):
            rows.extend(one or [])
        out = []
        for item in rows:
            if city and not _match_city(city, item['city'], item.get('city_raw')):
                continue
            if keyword and not self._hit_keyword(item, keyword):
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _hit_keyword(item, keyword):
        kw = (keyword or '').strip()
        if not kw:
            return True
        # 中文关键词按整词匹配；英文按大小写不敏感子串匹配
        hay = '%s %s %s' % (item.get('title', ''), item.get('company', ''),
                            item.get('desc', ''))
        if re.search(r'[\u4e00-\u9fff]', kw):
            return kw in hay
        return kw.lower() in hay.lower()

    def _fetch_amazon(self, limit=30):
        out = []
        for page in range(AMZ_MAX_PAGE):
            if len(out) >= limit:
                break
            rows = self._amazon_page(page * AMZ_PAGE)
            if not rows:
                break                            # 空页才是真的到底了
            out.extend(rows)
        return out

    def _amazon_page(self, offset):
        qs = urllib.parse.urlencode({
            'result_limit': AMZ_PAGE,
            'offset': offset,
            'sort': 'recent',
            'normalized_country_code[]': 'CHN',
        })
        data = net.request('%s?%s' % (AMZ_API, qs),
                           headers={'Referer': AMZ_HOME + '/en/',
                                    'Accept': 'application/json'},
                           timeout=25, retries=2,
                           cache_ttl=1800)        # 同一查询缓存 30 分钟
        if not data:
            return []
        out = []
        for j in data.get('jobs') or []:
            item = self._convert_amazon(j)
            if item:
                out.append(item)
        return out

    def _convert_amazon(self, j):
        title = (j.get('title') or '').strip()
        company = (j.get('company_name') or '').strip()
        if not title or not company:
            return None
        city_raw = (j.get('city') or '').strip()
        city = _cn_city(city_raw)
        desc = re.sub(r'<[^>]+>', ' ', j.get('description_short') or '')
        desc = re.sub(r'\s+', ' ', desc).strip()[:1200]
        path = (j.get('job_path') or '').strip()
        detail = (AMZ_HOME + path) if path.startswith('/') else AMZ_HOME
        apply_url = (j.get('url_next_step') or '').strip() or detail
        tags = [t for t in [j.get('job_category'), j.get('business_category'),
                            j.get('job_schedule_type')] if t]
        hire = '劳务派遣' if DISPATCH_RE.search(desc) else '直签'
        return self.normalize({
            'title': title,
            'company': company,
            'city': city,
            'city_raw': city_raw,
            'district': '',
            'salary_text': '面议',            # 亚马逊中国岗位不公开薪资
            'exp': '经验不限',
            'edu': '不限',
            'tags': tags,
            'desc': desc,
            'nature': '外企',                 # 外企官网直招，性质确定
            'hire_type': hire,
            'url': detail,
            'apply_url': apply_url,           # 官网自己的投递页，可直接点进去
            'hr_email': '',
            'published': _posted(j.get('posted_date')),
        }, self.name)
