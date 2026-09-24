# -*- coding: utf-8 -*-
"""岗位采集入口：多源聚合 —— 这是「打破信息茧房」的核心第二层。

设计要点：
1. 多源并行。任何一家反爬/改版/挂掉，其它源照常出结果，用户无感。
2. 真实源优先。内置演示库只在「所有真实源都没数据」时兜底，
   绝不与真实岗位混在一起，避免误导用户。
3. 状态可观测。collect_jobs_with_report() 返回每个源的健康情况，
   前端/运维一眼能看出哪条路断了。
4. 单源故障隔离。任何一个源抛异常都只是跳过，不影响整体。
"""
import json
import os
import time
import concurrent.futures as _cf

from .api import HttpCollector, JsonFileCollector
from .foreign import ForeignCollector
from .gov_soe import GovSoeCollector, PROVINCE_CITIES
from .guopin import GuopinCollector
# 就业在线：国密加密接口。加密方案已破解并验证（默认公钥 D + SM4/SM2/SM3，
# 见 jobonline.py 文件头），按省分片可抓全量。它同时留在 LIVE_COLLECTORS 里，
# 所以线上搜索也能实时出这个源的结果。
from .jobonline import JobOnlineCollector
from .mohrss import MohrssCollector
from .sample import NATURES, SampleCollector

__all__ = ['collect_jobs', 'collect_jobs_with_report', 'NATURES', 'SOURCES']

# data/sources.json 可以在不改动代码的情况下单独关掉某个渠道，
# 形如 {"guopin": false}。文件不存在或写坏了就按代码里的 enabled 走。
_OVERRIDE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'sources.json')

# 单位性质默认勾选。必须含「民营」：民企岗位本就只占库里 3%，若默认再把它
# 排除掉，用户连勾都没地方勾，等于替他做了「只看体制内」的决定。
DEFAULT_NATURES = ['央企', '国企', '外企', '事业单位', '公务员', '民营']

# 前端「不限城市」这类选项，统一按“不限制城市”处理
NO_CITY = {'', '不限城市', '不限', '全国', '不限地区'}

# 指定城市时，如果结果少于这个数，就自动放宽到全国再补一批
# ——宁可让用户看到外地的机会，也不要给他一个空白页
MIN_CITY_HITS = 5

# 岗位城市里出现这些词 = 全国性招聘，不是某个具体城市
NATIONAL_WORDS = ('全国', '全国范围', '全国各地', '不限城市', '不限地区')

# 真实数据源：按优先级排列
LIVE_COLLECTORS = [GuopinCollector(), MohrssCollector(), GovSoeCollector(),
                   ForeignCollector(), JobOnlineCollector(),
                   JsonFileCollector(), HttpCollector()]
# 兜底源：只有真实源全军覆没时才启用
FALLBACK_COLLECTORS = [SampleCollector()]

SOURCES = LIVE_COLLECTORS + FALLBACK_COLLECTORS


def _overrides():
    """读取渠道开关覆盖配置。读不到就当没有，绝不让配置文件拖垮搜索。"""
    try:
        with open(_OVERRIDE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:                            # noqa: BLE001
        return {}


def _enabled(collector, overrides=None):
    """渠道是否启用：代码里的 enabled 为准，data/sources.json 可以覆盖。"""
    ov = _overrides() if overrides is None else overrides
    return bool(ov.get(collector.name, collector.enabled))


def _active(group, overrides=None):
    return [c for c in group if _enabled(c, overrides)]


def _norm_city(c):
    c = (c or '').strip()
    return '' if c in NO_CITY else c


def _in_city(job, city):
    """这条岗位是不是用户选的那个城市。

    放宽到全国那一轮的结果里也夹着本市岗位，得把它们挑出来分别对待：
    是本市的不标「外地」。城市字段有的是「武汉市」有的是「武汉」，所以两头都试。
    """
    if not city:
        return False
    c = (job.get('city') or '').strip()
    return bool(c) and (city in c or c in city)


def _city_scope(job, city):
    """这条岗位的城市，跟用户选的城市是什么关系。

    返回 local（本市）/ province（本省）/ national（全国招聘）/ out（外地）。

    只做字符串互包含时，「全国」「湖北」会被一律判成外地 —— 而这两类公告里
    恰恰常含本市岗位（2026-09-23 实测：选武汉抓 40 条，「全国」23 条、「湖北」
    4 条，真正写「武汉」的只有 1 条）。本市可选岗位只剩 1 条，又反过来触发
    「城市放宽」补一大批外地岗进来，用户看到的就是满屏外地。所以这里分档：
    「全国 / 本省」算不算数，交给用户那个开关决定，不再替他一刀切。
    """
    if not city:
        return 'local'          # 用户选了「不限城市」，没有外地这一说
    c = (job.get('city') or '').strip()
    if not c:
        return 'out'
    if city in c or c in city:
        return 'local'
    if c in NATIONAL_WORDS:
        return 'national'
    # 省级公告（「湖北」）或本省其他城市：对选「武汉」的人来说是本省
    if city in PROVINCE_CITIES.get(c, []):
        return 'province'
    return 'out'


def _run(collector, keyword, city, limit, natures):
    """跑一个采集器，返回 (岗位列表, 耗时秒, 错误信息)。永不抛异常。"""
    t0 = time.time()
    try:
        rows = collector.fetch(keyword=keyword, city=city,
                               limit=limit, natures=natures)
        return list(rows or []), round(time.time() - t0, 2), ''
    except Exception as e:                      # noqa: BLE001 单源隔离
        return [], round(time.time() - t0, 2), '%s: %s' % (type(e).__name__, e)


def _run_group(group, keyword, city, limit, natures):
    """并行跑一组采集器：任何一个慢/挂都不阻塞其它源（对应「换源不破防」）。"""
    results = {}
    if not group:
        return results
    with _cf.ThreadPoolExecutor(max_workers=min(len(group), 8)) as ex:
        futs = {ex.submit(_run, c, keyword, city, limit, natures): c
                for c in group}
        for fut in _cf.as_completed(futs):
            c = futs[fut]
            results[c.name] = fut.result()
    return results


def collect_jobs_with_report(keyword='', city='', limit=200, natures=None,
                             exclude=None, include_wide=True):
    """聚合所有渠道，并返回每个渠道的健康报告。

    exclude: 已投递过的岗位标识集合（见 models.list_applied_keys），
             命中的岗位直接不再出现——投过的人不该再被推同一个坑。
    include_wide: 用户勾了「连全国招聘和本省其他城市一起看」时为 True（默认）。
             False = 只要本市，全国/本省/外地一概标成 nearby 由前端收起来，
             并且不再跑「放宽到全国」那一轮补位。
    """
    city = _norm_city(city)
    want = DEFAULT_NATURES if natures is None else list(natures)
    exclude = set(exclude or ())
    overrides = _overrides()
    live = _active(LIVE_COLLECTORS, overrides)
    fallback = _active(FALLBACK_COLLECTORS, overrides)

    seen = set()
    jobs = []
    # name -> {'ok': 是否真的拿到过数据, 'count': 最终入选条数, 'cost': 耗时}
    stat = {c.name: {'ok': False, 'count': 0, 'cost': 0.0, 'error': '',
                     'enabled': _enabled(c, overrides)}
            for c in LIVE_COLLECTORS + FALLBACK_COLLECTORS}

    def _hit(j):
        """这个岗位是不是已经投过了。完整键和「公司|职位|城市」都算。"""
        if not exclude:
            return False
        if j.get('key') in exclude:
            return True
        return '%s|%s|%s' % (j.get('company') or '', j.get('title') or '',
                             j.get('city') or '') in exclude

    def absorb(collector, rows, classify=True):
        """把一个渠道的结果并入，同时累计该渠道的入选条数。

        classify=False 用于演示库兜底那一轮：真实源全军覆没时它已经是最后
        一点东西了，再按城市把它标成「外地」收起来，用户就只剩空白页。

        每一条都按 _city_scope 分档。以前只有「放宽到全国」那一轮才标 nearby，
        按城市抓的那一轮抓到什么就原样显示 —— 于是黄石、襄阳、惠州这些
        非本市岗位被当成武汉本地岗推给用户（2026-09-23 实测：选武汉抓回来的
        40 条里真正的武汉岗只有 1 条）。现在两轮都过这一道。
        """
        got = 0
        for j in rows or []:
            if want and (j.get('nature') or '民营') not in want:
                continue
            k = j.get('key') or (j.get('company', '') + j.get('title', '') + j.get('city', ''))
            if k in seen:
                continue
            seen.add(k)
            if _hit(j):
                continue
            j = dict(j)
            if classify:
                scope = _city_scope(j, city)
                # 「全国 / 本省」算不算数由用户开关决定；外地一律算外面。
                outside = (scope == 'out'
                           or (scope in ('province', 'national') and not include_wide))
                if outside:
                    j['nearby'] = True
                if scope != 'local':
                    j['city_scope'] = scope
            jobs.append(j)
            got += 1
        st = stat[collector.name]
        st['count'] += got
        return got

    # 第一轮：按用户选的城市抓（多源并行，互不阻塞 —— 慢源不拖垮整体）
    _first = _run_group(live, keyword, city, limit, want)
    for c in live:
        rows, cost, err = _first.get(c.name, ([], 0.0, ''))
        st = stat[c.name]
        st['cost'] += cost
        st['error'] = err
        if not err and rows:
            st['ok'] = True
        absorb(c, rows)

    # 城市放宽：本地机会太少时补一批全国的，标 nearby 让前端提示「不在本市」。
    # 宁可让用户看到外地的机会，也不要给他一个空白页。
    # 用户勾掉了「连全国招聘和本省一起看」时不补 —— 他明确说了只要本市，
    # 给他一堆外地岗就是违背他刚做的选择。
    # 判据用「本市条数」而不是总条数：否则外地岗自己就把名额凑够了，
    # 本市其实一条没有也照样不补。
    relaxed = False
    local_hits = len([x for x in jobs if not x.get('nearby')])
    if city and include_wide and local_hits < MIN_CITY_HITS:
        relaxed = True
        _second = _run_group(live, keyword, '', limit, want)
        for c in live:
            rows, cost, err = _second.get(c.name, ([], 0.0, ''))
            stat[c.name]['cost'] += cost
            if not err and rows:
                stat[c.name]['ok'] = True
            absorb(c, rows)

    # 真实源全军覆没才启用演示库，且明确标注，不跟真实岗位混淆
    used_fallback = False
    if not jobs:
        used_fallback = True
        for c in fallback:
            rows, cost, err = _run(c, keyword, city, limit, want)
            stat[c.name].update(cost=cost, error=err)
            if not err and rows:
                stat[c.name]['ok'] = True
            absorb(c, rows, classify=False)

    report = []
    for kind, group in (('live', LIVE_COLLECTORS), ('fallback', FALLBACK_COLLECTORS)):
        for c in group:
            st = stat[c.name]
            report.append({'name': c.name, 'label': c.label, 'kind': kind,
                           'ok': st['ok'], 'count': st['count'],
                           'cost': round(st['cost'], 2), 'error': st['error'],
                           'enabled': st['enabled']})

    return {
        'jobs': jobs[:limit],
        'sources': report,
        'fallback': used_fallback,
        'relaxed': relaxed,
        'total': len(jobs),
        'natures': want,
        'city': city,
        'keyword': keyword,
    }


def collect_jobs(keyword='', city='', limit=200, natures=None, exclude=None):
    """只要岗位列表时用这个（内部接口/测试用）。"""
    return collect_jobs_with_report(keyword, city, limit, natures,
                                    exclude)['jobs']
