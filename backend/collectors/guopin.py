# -*- coding: utf-8 -*-
"""国聘网采集器（iguopin.com，国务院国资委主办的央企国企招聘平台）。

为什么拿它当主力源：
  * 它的搜索接口完全开放，不需要登录、不需要 cookie、没有签名；
  * 返回体里自带 company_info.nature_cn（国企/外商独资/…），天然带单位性质；
  * 实测关键词「电力」100 条里 80 条是国企，密度远高于商业招聘网站。

接口（通过抓包自家页面得到）：
  POST https://gp-api.iguopin.com/api/jobs/v1/recom-job
  body {"search": {"page":1,"page_size":20,"keyword":"电力",
                   "company_nature":["11AzDak"]}}
  注意：company_nature 必须是数组，传字符串会报错。
  城市参数在服务端不生效，只能本地按地区名过滤。
"""
import re
import time

from . import net
from .base import BaseCollector

API = 'https://gp-api.iguopin.com/api/jobs/v1/recom-job'
DETAIL = 'https://www.iguopin.com/job/detail?id=%s'
HOME = 'https://www.iguopin.com'

# 单位性质字典编码（取自 /api/base/category/v1/by-alias?alias=company_nature）
NATURE_CODES = {
    '国企': '11AzDak',
    '外商独资': '113yihzr',
    '港澳台投资': '116w7VTv',
    '中外合资': '1178xpmL',
    '民营企业': '1145DorR',
    '事业单位': '11X9v75',
}

# 内部性质 → 需要向接口索取的编码。央企在国聘网同样标成"国企"，
# 拿到本地再用央企集团关键词二次识别，所以两者共用同一个码。
CODES_FOR_NATURE = {
    '央企': ['11AzDak'],
    '国企': ['11AzDak'],
    '外企': ['113yihzr', '116w7VTv'],
    '合资': ['1178xpmL'],
    '民营': ['1145DorR'],
    '事业单位': ['11X9v75'],
}

# 默认只抓这几类：国企（含央企）+ 外资/港澳台 + 合资
DEFAULT_CODES = ['11AzDak', '113yihzr', '116w7VTv', '1178xpmL']


def _codes_for(natures):
    """natures 为 None/空时按「不限」处理，把所有性质的编码都带上。"""
    if not natures:
        return sorted(set(NATURE_CODES.values()))
    codes = []
    for n in natures:
        codes.extend(CODES_FOR_NATURE.get(n, []))
    return sorted(set(codes)) or DEFAULT_CODES

NATURE_CN_MAP = {
    '国企': '国企',
    '外商独资': '外企',
    '港澳台投资': '外企',
    '中外合资': '合资',
    '民营企业': '民营',
    # 站点字典里「事业单位」是独立性质（有自己的码 11X9v75），这里必须原样保留：
    # 早先把它并进「国企」，结果是双向错——勾国企时会混进事业编岗位，
    # 勾事业单位时又搜不到它们（2026-09-21 修）。
    '事业单位': '事业单位',
    '上市公司': '民营',
    '股份制企业': '民营',
    '其他': '民营',
}

# 国资委监管的央企集团名录（基于 SASAC 央企名单整理，覆盖全部 ~98 家）。
# 接口只把央企和地方国企都标成「国企」，分不出层级，所以这里用集团核心名做
# 子串命中识别——子公司名里通常带着集团名（如「国家电投集团山东能源发展有限公司」
# 命中「国家电投」）。新增一家央企集团，往这里加它的核心名即可。
# 注：2021 年后新组建的央企（矿产资源、稀土、电气装备、安能、国新等）已补齐。
CENTRAL_KEYWORDS = [
    # 军工 / 航天 / 核
    '中核', '中国核工业', '中国航天科技', '中国航天科工', '中国航空工业',
    '中国船舶', '中国兵器工业', '中国兵器装备', '中国电子科技', '中国航发',
    '中国航空发动机', '中国融通', '中国工程物理研究院',
    # 能源
    '中国石油', '中国石化', '中国海油', '中国海洋石油', '国家电网', '南方电网',
    '中国华能', '中国大唐', '中国华电', '国家电投', '国家电力投资', '中国三峡',
    '中国长江三峡', '国家能源', '中国广核', '中国核电', '中国矿产资源集团',
    '中国稀土集团', '中国中煤', '中国煤炭科工', '中国化学工程', '中国黄金',
    '中国盐业', '中国有色', '矿冶科技', '有研科技', '中国钢研', '鞍钢',
    '中国宝武', '中铝', '中国铝业', '中国五矿', '中国建材', '中国节能',
    '中国国新', '中国安能',
    # 通信 / 电子 / 信息
    '中国电信', '中国联通', '中国移动', '中国星网', '中国卫星网络', '中国电子',
    '中国普天', '中国华录', '中国铁塔', '中国信通', '中国信息通信',
    # 装备制造 / 交通
    '中国一汽', '中国第一汽车', '东风汽车', '中国一重', '中国机械工业', '国机',
    '哈电', '哈尔滨电气', '东方电气',     '中国中车', '中国中铁', '中国铁建',
    '中国铁道建筑', '中国铁路通信信号', '中国交通建设', '中交', '中国建筑',
    '中国远洋海运', '中国航空集团',
    '中国东方航空', '中国南方航空', '中国商飞', '中国商用飞机', '中国民航信息',
    '中国航油', '中国航空油料', '中国航材', '中国航空器材', '中国物流',
    '南水北调', '中国检验认证', '中国电力建设', '中能建', '中国能源建设', '中电建',
    # 农业 / 粮食 / 医药
    '中粮', '中储粮', '中国储备粮', '中国农业发展', '中国农发', '中国林业',
    '中国医药集团', '国药',
    # 投资 / 贸易 / 综合
    '国投', '国家开发投资', '招商局', '华润', '中国中化', '中国通用技术',
    '中国诚通', '中国保利', '中国旅游', '中国旅游集团', '新兴际华',
]

# 正文里出现这些词 → 多半是劳务派遣，不是跟单位直签
DISPATCH_RE = re.compile(r'劳务(派遣|外包|派遗)|派遣(制|用工)|第三方(用工|派遣)'
                         r'|外包(员工|用工)|与.{0,6}劳务公司(签订|签约)')


def _is_central(name):
    return any(k in name for k in CENTRAL_KEYWORDS)


def _nature_of(company_name, nature_cn):
    if _is_central(company_name):
        return '央企'
    return NATURE_CN_MAP.get(nature_cn or '', '民营')


def _salary(job):
    lo, hi = job.get('min_wage') or 0, job.get('max_wage') or 0
    unit = job.get('wage_unit_cn') or '元/月'
    if job.get('is_negotiable') or (not lo and not hi):
        return '面议'
    if lo and hi and lo != hi:
        return '%d-%d%s' % (lo, hi, unit)
    return '%d%s' % (lo or hi, unit)


def _today():
    return time.strftime('%Y-%m-%d')


def _in_apply_window(apply_start, deadline, today=None):
    """报名窗口没到/已过的岗推给用户也是白推——点进去就是
    「该职位尚未开始报名」（2026-09-18 用户实测踩过：报名 9-29 才开始）。
    宁可少推几条，也别让人白高兴一场。起止时间缺一个就放行，别误杀。"""
    today = today or _today()
    if apply_start and apply_start > today:
        return False
    if deadline and deadline < today:
        return False
    return True


def _city_of(job):
    dl = job.get('district_list') or []
    if not dl:
        return '', ''
    area = (dl[0].get('area_cn') or '').strip()
    # '北京-海淀区' / '中国-北京-北京市-海淀区' / '钦州'
    parts = [p for p in area.split('-') if p and p != '中国']
    if not parts:
        return '', ''
    city = parts[0].replace('市', '')
    district = parts[1] if len(parts) > 1 else ''
    return city, district


# 服务端把 page_size 硬顶在 20，传再大也只给 20 条
PAGE_SIZE = 20
MAX_PAGE = 10                      # 最多翻 10 页 = 200 条


class GuopinCollector(BaseCollector):
    name = 'guopin'
    label = '国聘网（国资委央企国企招聘平台）'

    def fetch(self, keyword='', city='', limit=30, natures=None):
        need = max(1, min(int(limit or 30), MAX_PAGE * PAGE_SIZE))
        pages = min(MAX_PAGE, -(-need // PAGE_SIZE))     # 向上取整
        out = []
        for page in range(1, pages + 1):
            if len(out) >= limit:
                break
            rows = self._one_page(keyword, page, city, natures)
            if not rows:                                 # 空页才是真的到底了
                break
            out.extend(rows)
        return out[:limit]

    def _one_page(self, keyword, page, city, natures):
        search = {
            'page': page,
            'page_size': PAGE_SIZE,
            'keyword': keyword or '',
            'company_nature': _codes_for(natures),
        }
        payload = {'search': search,
                   'recom': {'update_time': True, 'company_nature': True,
                             'hot_job': True}}
        data = net.request(API, data=payload,
                           headers={'Referer': HOME + '/job', 'Origin': HOME},
                           timeout=20, retries=2,
                           cache_ttl=1800)          # 同一查询缓存 30 分钟
        if not data or data.get('code') not in (200, '200'):
            return []
        rows = ((data.get('data') or {}).get('list')) or []
        out = []
        skipped = 0
        for j in rows:
            item = self._convert(j)
            if not item:
                continue
            if natures and item['nature'] not in natures:
                continue
            if city and city not in item['city']:
                continue
            if not _in_apply_window(item['apply_start'], item['deadline']):
                skipped += 1
                continue
            out.append(item)
        self.skipped_not_open = skipped
        return out

    def _convert(self, j):
        title = (j.get('job_name') or '').strip()
        company = (j.get('company_name') or '').strip()
        if not title or not company:
            return None
        city, district = _city_of(j)
        info = j.get('company_info') or {}
        nature = _nature_of(company, info.get('nature_cn'))
        desc = re.sub(r'\s+', ' ', j.get('contents') or '')[:1200]
        hire = '劳务派遣' if DISPATCH_RE.search(desc) else '直签'
        tags = [t for t in [j.get('category_cn'), j.get('recruitment_type_cn'),
                            info.get('industry_cn')] if t]
        # 招聘人数（amount）：接口给 0 表示未公开，不显示
        headcount = j.get('amount') or 0
        recruit_type = j.get('recruitment_type_cn') or ''   # 校园招聘 / 社会招聘
        jid = j.get('job_id') or ''
        return self.normalize({
            'title': title,
            'company': company,
            'city': city,
            'district': district,
            'salary_text': _salary(j),
            'exp': j.get('experience_cn') or '经验不限',
            'edu': j.get('education_cn') or '不限',
            'tags': tags,
            'desc': desc,
            'nature': nature,
            'hire_type': hire,
            'url': DETAIL % jid if jid else HOME,
            'apply_url': DETAIL % jid if jid else HOME,
            'hr_email': '',
            'deadline': (j.get('end_time') or '')[:10],
            'apply_start': (j.get('start_time') or '')[:10],
            'published': (j.get('refresh_time') or '')[:10],
            'headcount': int(headcount) if headcount else 0,
            'recruit_type': recruit_type,
        }, self.name)
