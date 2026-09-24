# -*- coding: utf-8 -*-
"""简历投递助手 · 后端服务（Flask + WebSocket 实时进度）。"""
import os
import re
import sys
import time
import uuid
import threading
import traceback
from concurrent import futures

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_socketio import SocketIO
from flask_cors import CORS

import models
import mailer
import autofill
import tailor
import resume_parser
from ai_matcher import match_jobs, INTENT_SYNONYMS
from collectors import (collect_jobs, collect_jobs_with_report, NATURES,
                        DEFAULT_NATURES, _city_scope)
from collectors.base import DEFAULT_APPLY_URL

DIST_DIR = os.path.join(ROOT_DIR, 'frontend', 'dist')
DATA_DIR = os.path.join(ROOT_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
models.init_db(os.path.join(DATA_DIR, 'app.db'))

# ------------------------- 岗位缓存（服务端持久化） -------------------------
# 岗位抓取结果落 SQLite（models.upsert_jobs），搜索改成「读库优先」：
# 命中缓存毫秒级返回；库里没有（首次/冷门城市）才走原现爬链路并回写库。
# 后台线程每 30 分钟把全国重爬一遍，让库一直是热的。
# 测试置 WEBAPP_JOBS_CACHE=0 可整层关掉（run_tests.py 已置），保证测试确定性。
_JOBS_CACHE_ON = os.environ.get('WEBAPP_JOBS_CACHE', '1') != '0'
# 只关「这台机器自己去抓」，不关读库：部署机在境外，中公等行业站对境外 IP
# 直接 403（2026-09-21 实测），让它自己爬等于每半小时白等一轮，还把库里
# 本地同步过来的更全的数据压住。置 WEBAPP_LIVE_COLLECT=0 后本机只做展示，
# 岗位由本地采集后同步过来（见 sync_jobs.py）。
_LIVE_COLLECT_ON = os.environ.get('WEBAPP_LIVE_COLLECT', '1') != '0'
_JOBS_TTL_DAYS = 30          # 库里岗位的最长有效期（公告类来源只有发布日，按抓取时间算）
_REFRESH_INTERVAL = 30 * 60  # 后台刷新周期：30 分钟
# 后台刷新抓全量性质（不止默认的全体制内），任何性质的搜索才能都命中缓存
_ALL_NATURES = list(NATURES)


def _cache_source(count):
    """读库命中时给前端的来源报告（与现爬路径的 sources 同构）。"""
    return {'name': 'jobs_cache', 'label': '岗位缓存（服务端）', 'kind': 'cache',
            'ok': True, 'count': count, 'cost': 0.0, 'error': '', 'enabled': True}


# 冷门城市补抓：缓存命中但本地结果太少时，本次请求仍秒回，
# 同时在后台定向补抓这个城市写回库，让下次搜索更满。
# 防失控三件套：进行中去重 + 同城冷却 + daemon 线程（失败只打日志）。
_CITY_CRAWL_LOCK = threading.Lock()
_CITY_CRAWL_INFLIGHT = set()
_CITY_CRAWL_LAST = {}          # (city, keyword) -> 上次补抓完成时间
_CITY_CRAWL_COOLDOWN = 600     # 同一城市 10 分钟内不重复补抓


def _kick_city_crawl(city, keyword=''):
    """后台补抓某城市的岗位并 upsert 进 jobs 表。绝不阻塞调用方。"""
    if not _JOBS_CACHE_ON or not _LIVE_COLLECT_ON or not (city or '').strip():
        return
    key = (city.strip(), (keyword or '').strip())
    with _CITY_CRAWL_LOCK:
        if key in _CITY_CRAWL_INFLIGHT:
            return
        if time.time() - _CITY_CRAWL_LAST.get(key, 0) < _CITY_CRAWL_COOLDOWN:
            return
        _CITY_CRAWL_INFLIGHT.add(key)

    def _work():
        try:
            rep = collect_jobs_with_report(keyword=key[1], city=key[0],
                                           limit=200, natures=_ALL_NATURES)
            n = models.upsert_jobs(rep['jobs'])
            print('[jobs-cache] city-crawl %s|%s upserted=%d'
                  % (key[0] or '-', key[1] or '-', n), flush=True)
        except Exception as e:                  # noqa: BLE001
            print('[jobs-cache] city-crawl %s error: %s' % (key, e), flush=True)
        finally:
            with _CITY_CRAWL_LOCK:
                _CITY_CRAWL_INFLIGHT.discard(key)
                _CITY_CRAWL_LAST[key] = time.time()

    threading.Thread(target=_work, daemon=True, name='jobs-city-crawl').start()


def _background_refresh():
    """后台线程：定期把全国岗位爬一遍写进 jobs 表，让搜索读库秒回。

    全国搜一遍（city=''）拿到的岗位都带着真实城市字段，一次就铺满所有城市，
    不用按城市挨个爬。刷新失败只打日志，绝不影响线上服务。
    """
    time.sleep(5)  # 等 Flask 起来再爬，别抢启动
    while True:
        try:
            # 1200 而不是单次搜索的 200：后台抓是给整个库铺货，多存些
            # 冷门城市才有命中；搜索本身仍然只取自己 limit 上限内的条数。
            # 也不能太小：公告源扩到 20+ 个之后，limit//源数 会反过来压住
            # 每个源的抓取深度（每源配额 = min(源自己配的 max_articles,
            # limit//源数)），1200 才能让各省源按各自配置抓满（2026-09-21）。
            jobs = collect_jobs(keyword='', city='', limit=1200, natures=_ALL_NATURES)
            n = models.upsert_jobs(jobs)
            pruned = models.prune_jobs(_JOBS_TTL_DAYS)
            print('[jobs-cache] upserted=%d pruned=%d in_db=%d'
                  % (n, pruned, models.count_jobs()), flush=True)
        except Exception as e:                  # noqa: BLE001
            print('[jobs-cache] refresh error: %s' % e, flush=True)
        time.sleep(_REFRESH_INTERVAL)


if _JOBS_CACHE_ON and _LIVE_COLLECT_ON:
    threading.Thread(target=_background_refresh, daemon=True,
                     name='jobs-cache-refresh').start()

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading', logger=False, engineio_logger=False)

# 代发时两封之间的间隔（秒）。太快会被判垃圾邮件。
SEND_INTERVAL = int(os.environ.get('SEND_INTERVAL') or 20)

# 投递任务：task_id -> 任务状态
TASKS = {}


def _push(task_id):
    """把任务进度推给所有打开着的页面。"""
    try:
        socketio.emit('progress', TASKS[task_id])
        socketio.sleep(0)
    except Exception:
        pass


def _push_autofill(t):
    """自动填表的进度推送（走另一个事件名，别和邮件代发串了）。"""
    try:
        socketio.emit('autofill', t)
        socketio.sleep(0)
    except Exception:
        pass


autofill.set_pusher(_push_autofill)


# ------------------------- 页面与静态资源 -------------------------
@app.route('/')
def index():
    idx = os.path.join(DIST_DIR, 'index.html')
    if not os.path.exists(idx):
        return ('<meta charset="utf-8"><h2>前端页面还没生成</h2>'
                '<p>请在 frontend 目录执行 <code>npm install &amp;&amp; npm run build</code>，'
                '然后重新启动。</p>'), 503
    return send_file(idx)


@app.route('/assets/<path:filename>')
def assets(filename):
    return send_from_directory(os.path.join(DIST_DIR, 'assets'), filename)


@app.route('/favicon.ico')
def favicon():
    return '', 204


# ------------------------- 接口 -------------------------
@app.route('/api/meta')
def meta():
    """前端用的选项字典（城市、岗位类型、单位性质……）。"""
    return jsonify(ok=True, natures=NATURES)


@app.route('/api/profile', methods=['POST'])
def api_save_profile():
    data = request.get_json(force=True, silent=True) or {}
    if not (data.get('name') or '').strip():
        return jsonify(ok=False, msg='请填写姓名'), 400
    pid = models.save_profile(data)
    return jsonify(ok=True, profile_id=pid)


def _save_resume_file(filename, raw):
    """用户同意保留的简历原件，存到 data/resumes，自动投上传附件时直接用它。

    存的是他手里那份 Word/PDF 本身：照片、排版、证书扫描件都在里面，
    招聘方看到的就是他真正的简历。以前自动投上传的是我们拼出来的一页纯文本，
    等于把人家的简历换了个样——这一步修的就是那个问题。
    存不成功无所谓，退回生成文本版，不影响解析和投递。
    """
    try:
        os.makedirs(autofill.RESUME_DIR, exist_ok=True)
        safe = re.sub(r'[^\w.\-]', '_', filename or 'resume')[-60:] or 'resume'
        dest = os.path.join(autofill.RESUME_DIR, '%d_%s' % (int(time.time() * 1000), safe))
        with open(dest, 'wb') as f:
            f.write(raw)
        return dest
    except Exception as e:                       # noqa: BLE001
        app.logger.warning('保存简历原件失败：%s', e)
        return ''


@app.route('/api/resume/parse', methods=['POST'])
def api_resume_parse():
    """读用户传上来的简历，把能认的字段认出来。

    默认不留文件副本（用户手里那份简历不会被我们存一份）；
    只有他明确同意「留着这份，自动投时直接用」时才保存原件——
    因为自动投要上传带照片和排版的真简历，不存就只能用文字版顶上。
    """
    data = request.get_json(force=True, silent=True) or {}
    filename = (data.get('filename') or '').strip()
    b64 = data.get('data_b64') or ''
    if not filename or not b64:
        return jsonify(ok=False, msg='没有收到文件，再传一次试试'), 400

    import base64
    import binascii
    b64 = b64.split(',', 1)[-1]              # 前端可能带 data:application/pdf;base64, 前缀
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        return jsonify(ok=False, msg='文件好像没传完整，再传一次试试'), 400
    if len(raw) > resume_parser.MAX_BYTES:
        return jsonify(ok=False,
                       msg='文件太大了（超过 %d MB）。' % (resume_parser.MAX_BYTES // 1024 // 1024)
                           + '可以只留前面一两页再传。'), 200
    if not raw:
        return jsonify(ok=False, msg='文件是空的，再传一次试试'), 200

    text, err = resume_parser.extract_text(filename, raw)
    if err:
        return jsonify(ok=False, msg=err), 200

    # 用户同意留一份才存。留了，自动投上传的就是这份原件（带照片和排版）。
    resume_path = _save_resume_file(filename, raw) if data.get('keep') else ''

    cities = [str(c).strip() for c in (data.get('cities') or [])
              if str(c).strip() and str(c).strip() != '不限城市']
    if not cities:
        cities = list(resume_parser.DEFAULT_CITIES)

    r = resume_parser.parse_fields(text or '', cities)
    fields = r['fields']
    warn = r.get('warn') or ''
    if not fields:
        return jsonify(ok=False, warn=bool(warn), got=[], low=[], fields={},
                       resume_path=resume_path,
                       msg=(warn + '这份文件里的字也没能认出什么来。'
                                   '如果它是扫描件或者图片转的 PDF，里面的字其实是图片，读不出文字。'
                                   '下面手动填一下也很快。') if warn else
                           ('这份文件里的字没能认出来。'
                            '如果它是扫描件或者图片转的 PDF，里面的字其实是图片，读不出文字。'
                            '下面手动填一下也很快。')), 200

    got = []
    for k, v in fields.items():
        if k == 'experiences':
            # 工作经历是条目列表，界面上已经在「你的经历」编辑器里一条条列出来了。
            # 这里只报个条数——照原样塞进这行摘要会变成「[object Object]、[object…」
            v = '%d 段' % len(v) if isinstance(v, list) else ''
        got.append({'key': k, 'label': resume_parser.FIELD_LABEL.get(k, k), 'value': v})
    msg = '从简历里认出了 %d 项，你看看对不对。' % len(got)
    if warn:
        msg = warn + '下面先按认出来的填了 %d 项，你核对一下。' % len(got)
    return jsonify(ok=True, warn=bool(warn), filename=filename, fields=fields,
                   got=got, low=r['low'], resume_path=resume_path, msg=msg)


@app.route('/api/jobs/search', methods=['POST'])
def api_search():
    data = request.get_json(force=True, silent=True) or {}
    kw = (data.get('keyword') or '').strip()
    city = (data.get('city') or '').strip()
    natures = data.get('natures')  # None -> 默认全体制内（央企/国企/外企/事业单位/公务员）；[] -> 不限
    # limit 可能是任意入参，直接 int() 转不动就 500 掉整个接口
    limit = _as_int(data.get('limit'), 200) or 200
    limit = max(1, min(limit, 2000))      # 别让一次请求把源站薅秃
    want = DEFAULT_NATURES if natures is None else list(natures)
    # 投过的岗位不再重复出现，避免同一个人被反复推同一个坑
    exclude = models.list_applied_keys(data.get('profile_id'))

    # 第一优先：读服务端岗位缓存。后台每 30 分钟刷新，绝大多数搜索毫秒级返回；
    # 库里没有（首次/冷门城市）才现爬兜底，并把结果回写库，下次就快了。
    if _JOBS_CACHE_ON:
        try:
            cached = models.query_jobs(city=city, natures=want,
                                       keywords=[kw] if kw else None, limit=limit)
        except Exception:                       # noqa: BLE001 读库失败就走现爬
            cached = []
        if cached:
            jobs, dropped = _filter_apply_window(cached)
            jobs = [j for j in jobs if not _excl_key_in(j, exclude)]
            # 本地结果太少：先照常秒回，再让后台补抓这个城市，下次搜索更满
            if city and len(jobs) < 5:
                _kick_city_crawl(city, kw)
            meta = {'sources': [_cache_source(len(jobs))], 'relaxed': False,
                    'fallback': False, 'total': len(cached), 'city': city,
                    'natures': want}
            return jsonify(ok=True, count=len(jobs), jobs=jobs,
                           sources=meta['sources'], relaxed=False,
                           fallback=False, total=meta['total'],
                           dropped_closed=dropped, meta=meta)

    # 兜底：现爬（原有链路），结果回写岗位缓存
    rep = collect_jobs_with_report(keyword=kw, city=city, limit=limit,
                                   natures=natures, exclude=exclude)
    if _JOBS_CACHE_ON:
        try:
            models.upsert_jobs(rep['jobs'])
        except Exception:                       # noqa: BLE001 写库失败不影响返回
            pass
    jobs, dropped = _filter_apply_window(rep['jobs'])
    return jsonify(ok=True, count=len(jobs), jobs=jobs,
                   sources=rep['sources'], relaxed=rep['relaxed'],
                   fallback=rep['fallback'], total=rep['total'],
                   dropped_closed=dropped)


# ------------------------- 报名窗口过滤 -------------------------
# 国企招聘都有「报名开始/报名截止」，窗口外的岗位推给用户就是白推：
# 点进去要么「该职位尚未开始报名」，要么公告早就截止了
# （2026-09-18 实测：搜武汉 18 个岗里 11 个 7 月就截止了）。两道闸一起把：
# 国聘在采集器里按结构化字段筛，公告类来源在这里按解析出的日期统一筛。
_DATE_NORM_RE = re.compile(r'(\d{4})\s*[年\-\./]\s*(\d{1,2})(?:\s*[月\-\./]\s*(\d{1,2}))?')


def _norm_date(s):
    """把公告里五花八门的日期归一成 YYYY-MM-DD。认不出/只到月份返回短格式或空。"""
    m = _DATE_NORM_RE.search((s or '').strip())
    if not m:
        return ''
    y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
    if not 2000 <= y <= 2100 or not 1 <= mo <= 12:
        return ''
    if d is not None and 1 <= int(d) <= 31:
        return '%04d-%02d-%02d' % (y, mo, int(d))
    return '%04d-%02d' % (y, mo)


def _filter_apply_window(jobs):
    """剔除没开放报名/已截止的岗位，返回 (留下的, 剔了几条)。

    只有日期精确到「日」才够格做判断——公告只写「2026-07」的宁可放行，
    别把人家还有机会的岗位误杀掉。

    注意：gov_soe 这类「公告抓取」来源只能解析出「发布日」，解析不到真实的
    「报名截止日」；用发布日当截止日会把大量仍在招人的公告（如湖北国企
    7 月发布的公告）误杀。因此对 gov_soe 来源跳过窗口过滤。
    """
    today = time.strftime('%Y-%m-%d')
    kept, dropped = [], 0
    for j in jobs:
        if j.get('source') == 'gov_soe':
            kept.append(j)
            continue
        start = _norm_date(j.get('apply_start') or '')
        end = _norm_date(j.get('deadline') or '')
        if len(start) == 10 and start > today:
            dropped += 1
            continue
        if len(end) == 10 and end < today:
            dropped += 1
            continue
        kept.append(j)
    return kept, dropped


_COMPANY_SUFFIX = ('股份有限公司', '有限责任公司', '有限公司', '分公司',
                   '集团公司', '集团', '公司', '股份', '总厂', '工厂')


def _norm_company(s):
    """单位名归一化，好让「武钢」匹配到「武汉钢铁（集团）公司」。

    只做三件事：去括号内容、去空白、去一层公司后缀。别再复杂化——
    求职者输入的就是单位名里的那几个字，互相包含基本就够用了。
    """
    s = re.sub(r'[（(].*?[)）]', '', (s or '').strip())
    s = re.sub(r'\s+', '', s)
    for suf in _COMPANY_SUFFIX:
        if len(s) > len(suf) and s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s


def _company_hit(company, query):
    """单位名是否对得上：归一化后互相包含即算命中（"武钢" ↔ "武汉钢铁"）。"""
    a = _norm_company(company)
    b = _norm_company(query)
    if not a or not b:
        return False
    return b in a or a in b


def _company_loose(company, query):
    """简称兜底：「中建三局」要能搜到「中国建筑第三工程局」。

    中国人搜单位几乎都打简称，而简称往往不是全名的子串。这里按字序做
    子序列匹配（简称里的每个字按顺序出现在全名里）。只在严格包含没命中
    时才用它，宁可多带几条让用户自己辨认，也别让人搜不到。
    """
    a = _norm_company(company)
    b = _norm_company(query)
    if not a or not b:
        return False
    i = 0
    for ch in b:
        i = a.find(ch, i)
        if i < 0:
            return False
        i += 1
    return True


@app.route('/api/jobs/company', methods=['POST'])
def api_company_search():
    """按单位名搜岗位。

    很多人是奔着某一家单位去的（「我就想进武钢」「我们那儿邮政在招人」），
    让他能直接敲单位名字搜，而不是只能在一堆通用岗位里翻。

    搜法：先拿单位名当关键词抓一轮；命中太少就补一轮不限关键词的宽搜，
    两轮结果都按单位名过滤。带城市搜不到时再放宽到全国——认准一家单位时，
    城市往往没那么重要，但放宽前先保证本市结果优先。
    """
    data = request.get_json(force=True, silent=True) or {}
    company = (data.get('company') or '').strip()
    if not company:
        return jsonify(ok=False, msg='请填写单位名字'), 400

    profile = data.get('profile') or {}
    city = (data.get('city') or profile.get('city') or '').strip()
    natures = data.get('natures') or profile.get('nature') or None
    pid = data.get('profile_id') or profile.get('profile_id') or profile.get('id')
    exclude = models.list_applied_keys(pid) if pid else set()

    pool, seen = [], set()

    def _take(rep):
        """把这一轮抓回来的岗位去重后放进候选池。"""
        for j in rep['jobs']:
            k = j.get('key')
            if k in seen:
                continue
            seen.add(k)
            pool.append(j)

    def _pick():
        """从候选池里挑出这家单位的岗位：先严格匹配，太少再放宽到简称。"""
        strict = [j for j in pool if _company_hit(j.get('company'), company)]
        if len(strict) >= 3:
            return strict
        out, keys = list(strict), {j.get('key') for j in strict}
        for j in pool:
            if j.get('key') in keys:
                continue
            if _company_loose(j.get('company'), company):
                out.append(j)
        return out

    # 1) 单位名当关键词搜一轮
    _take(collect_jobs_with_report(keyword=company, city=city, natures=natures,
                                   exclude=exclude))
    # 2) 命中太少：补一轮不限关键词的宽搜（岗位标题里没单位名的情况）
    if len(_pick()) < 5:
        _take(collect_jobs_with_report(keyword='', city=city, natures=natures,
                                       exclude=exclude))
    # 3) 还是太少：放宽到全国。认准一家单位时，城市常常可以商量
    if city and len(_pick()) < 3:
        _take(collect_jobs_with_report(keyword='', city='', natures=natures,
                                       exclude=exclude))

    # 放宽到全国拿回来的岗位必须标成「外地」，否则求职者会以为都在自己城市
    if city:
        for j in pool:
            jc = (j.get('city') or '').strip()
            if jc and city not in jc and jc not in city:
                j['nearby'] = True

    hits = _pick()
    matched, dropped = _filter_apply_window(match_jobs(profile, hits))
    return jsonify(ok=True, company=company, city=city,
                   count=len(matched), jobs=matched,
                   relaxed_city=bool(city and len(hits) < 3),
                   dropped_closed=dropped)


@app.route('/api/sources')
def api_sources():
    """各个职位渠道的健康状况，方便一眼看出哪条路断了。"""
    from collectors import LIVE_COLLECTORS, FALLBACK_COLLECTORS
    out = []
    for kind, group in (('live', LIVE_COLLECTORS), ('fallback', FALLBACK_COLLECTORS)):
        for c in group:
            out.append({'name': c.name, 'label': c.label, 'kind': kind,
                        'enabled': _source_enabled(c)})
    return jsonify(ok=True, sources=out)


def _source_enabled(c):
    """渠道开关：data/sources.json 可以单独关掉某个源，不用改代码。"""
    from collectors import _enabled
    try:
        return _enabled(c)
    except Exception:                            # noqa: BLE001
        return True


# 同一份意向短时间内重复搜（返回上一步改个条件再回来很常见），
# 不该让人再等一轮 10 秒的抓取。
_SEARCH_CACHE = {}
_SEARCH_TTL = 600          # 10 分钟：再长就可能给到已截止的旧岗位

# 手动抓取（用户在职位列表拉到底、下拉触发）的节流：同一份意向一分钟内
# 只真抓一次，防止有人狂下拉把源站打疼。节流期间自动退回读缓存。
_FORCE_LAST = {}
_MANUAL_COOLDOWN = 60


def _excl_key(j):
    return '%s|%s|%s' % (j.get('company') or '', j.get('title') or '',
                         j.get('city') or '')


def _excl_key_in(j, exclude):
    """这个岗位是不是已经投过了：完整键和「公司|职位|城市」两种口径都算
    （与聚合层 _hit 同口径，读缓存路径用它代替采集器内部的过滤）。"""
    if not exclude:
        return False
    return j.get('key') in exclude or _excl_key(j) in exclude


def _collect_for_profile(profile, force=False, wide=True):
    """按求职者填的意向去抓岗位。

    关键点：求职者勾的是「技术工人」「工厂普工」这种大类，并不是招聘网站上的
    真实搜索词。直接拿大类名去搜会什么都搜不到（「技术工人」在岗位标题里根本
    不存在），于是整页空白——看起来就像「系统把低分的都跳过了」。

    所以这里：
      1) 把「意向大类」展开成真正能搜到的词（电工 / 焊工 / 维修…）；
      2) 用户自己写的技能（电工证 / 会开车）本来就是真词，直接拿去搜；
      3) 严格搜完岗位还是太少，就补一轮「不限关键词、只按城市+单位性质」的宽搜，
         绝不因为关键词对不上就把岗位全跳掉。
    岗位抓回来之后由 ai_matcher 统一打分、按匹配度排序，但不删任何一条——
    分低的也照样展示，让求职者自己挑。

    wide: 用户勾了「连全国招聘和本省其他城市一起看」（默认勾）。关掉就
          只要本市 —— 全国 / 本省 / 外地一律标 nearby 收起来，也不再补全国那一轮。
    """
    city = (profile.get('city') or '').strip()
    # 档案里的字段名单数 nature（profiles 表列名也是它，前端 store 同理）；
    # 内部/历史调用有写复数 natures 的，两个都认。只认复数会永远读不到，
    # 于是退回 DEFAULT_NATURES（含事业单位、公务员）——用户明明只勾了
    # 「央企+国企」，却照样搜出事业单位（2026-09-21 实测：武汉混进 1 条武大岗位）。
    natures = profile.get('natures') or profile.get('nature')
    kw = (profile.get('keyword') or '').strip()

    # 把「意向大类」展开成可搜的真实词
    words = []
    if kw:
        words.append(kw)
    for t in (profile.get('jobTypes') or []):
        t = (t or '').strip()
        if not t:
            continue
        syns = INTENT_SYNONYMS.get(t)
        if syns:
            words.extend(syns)          # 电工、焊工、维修……这些才是岗位里真出现的词
        else:
            words.append(t)
    # 技能本身就是真词（电工证 / 会开车 / 有叉车证…），直接加进去
    for s in (profile.get('skills') or []):
        s = (s or '').strip()
        if s:
            words.append(s)
    # 去重并限量，别把对方站点打疼
    _seen = set()
    words = [w for w in words if not (w in _seen or _seen.add(w))][:5]

    # 投过的岗位不再重复出现
    pid = profile.get('id') or profile.get('profile_id')
    exclude = models.list_applied_keys(pid) if pid else set()

    # 命中缓存就直接返回（已投过的仍然要剔掉，那是个人的、不能缓存）
    # 「只要本市 / 连全国和本省」是两批不同的结果，必须分开缓存 ——
    # 否则用户勾掉开关再搜，拿回来的还是上次那批带外地岗的。
    ck = (city, tuple(sorted(natures or [])), tuple(words), bool(wide))

    # force=True：用户在列表底部手动下拉触发的抓取。跳过一切缓存直接现爬，
    # 抓回来回写库；但一分钟内同一份意向只真抓一次（防狂下拉打疼源站）。
    throttled = False
    if force:
        last = _FORCE_LAST.get(ck, 0)
        if time.time() - last < _MANUAL_COOLDOWN:
            force = False                       # 刚抓过：这次退回读缓存
            throttled = True
        else:
            _FORCE_LAST[ck] = time.time()

    if not force:
        hit = _SEARCH_CACHE.get(ck)
        if hit and time.time() - hit[0] < _SEARCH_TTL:
            fresh = [j for j in hit[1]
                     if j.get('key') not in exclude and _excl_key(j) not in exclude]
            out = dict(hit[2])
            if throttled:
                out['manual'] = 'throttled'     # 前端据此提示「刚抓过，稍后再拉」
            return fresh, out

    # 第二优先：读服务端岗位缓存（SQLite）。后台每 30 分钟刷新一次，
    # 命中就毫秒级返回；库里没有才走下面的现爬链路。
    # 这里镜像现爬链路的三轮逻辑（关键词轮 → 不限关键词宽搜轮 → 放宽到全国
    # 并标 nearby），否则「维修工」这类标题匹配不到的意向每次都落到现爬，
    # 缓存等于白建。
    if _JOBS_CACHE_ON and not force:
        want = DEFAULT_NATURES if natures is None else list(natures)

        def _db(**q):
            try:
                return models.query_jobs(natures=want, limit=200, **q)
            except Exception:                   # noqa: BLE001 读库失败就走现爬
                return []

        rows = _db(city=city, keywords=words) if words else []
        relaxed = False
        if len(rows) < 8:                       # 关键词命中太少，补一轮宽搜（同现爬 MIN_KEYWORD_HITS）
            got = {j.get('key') for j in rows}
            for j in _db(city=city):
                if j.get('key') not in got:
                    got.add(j.get('key'))
                    rows.append(j)
        # 缓存出来的岗位没走过 collect_jobs 的分档，这里补上：
        # 外地（以及用户不要的全国/本省）标 nearby，前端默认收起来。
        def _tagged(j):
            sc = _city_scope(j, city)
            if sc == 'local':
                return j
            j = dict(j)
            if sc == 'out' or (sc in ('province', 'national') and not wide):
                j['nearby'] = True
            j['city_scope'] = sc
            return j

        rows = [_tagged(j) for j in rows]
        # 本市太少才放宽到全国（同现爬 MIN_CITY_HITS）；用户勾掉开关时不补。
        # 判据用本市条数，别让外地岗自己把名额凑够。
        if city and wide and len([j for j in rows if not j.get('nearby')]) < 5:
            relaxed = True
            got = {j.get('key') for j in rows}
            for j in _db(city=''):
                if j.get('key') in got:
                    continue
                got.add(j.get('key'))
                rows.append(_tagged(j))
        if rows:
            # 本市岗太少：先照常秒回（外地岗靠 nearby 标记兜底展示），
            # 同时让后台补抓这个城市，下次本市就有得挑了
            if city and len([j for j in rows if not j.get('nearby')]) < 5:
                _kick_city_crawl(city, words[0] if words else '')
            # 缓存里存的是「未按个人投递记录过滤」的全量，剔除投过的动作放在
            # 读取时做——不然 A 投过的岗位会从缓存里消失，B 也看不到了
            agg = {'jobs': rows, 'sources': [_cache_source(len(rows))],
                   'relaxed': relaxed, 'fallback': False, 'total': len(rows),
                   'city': city, 'natures': want}
            if throttled:
                agg['manual'] = 'throttled'
            if len(_SEARCH_CACHE) > 30:
                _SEARCH_CACHE.clear()
            _SEARCH_CACHE[ck] = (time.time(), list(rows), dict(agg))
            fresh = [j for j in rows if not _excl_key_in(j, exclude)]
            return fresh, dict(agg)

    agg = {'jobs': [], 'sources': [], 'relaxed': False,
           'fallback': False, 'total': 0}
    seen = set()
    MIN_KEYWORD_HITS = 8     # 关键词搜到的岗位少于这个数，就补一轮宽搜，别让用户面对空页

    def _merge(rep):
        for j in rep['jobs']:
            k = j.get('key')
            if k in seen:
                continue
            seen.add(k)
            agg['jobs'].append(j)
        if not agg['sources']:
            agg['sources'] = rep['sources']
        else:
            # 多个关键词会重复统计同一批岗位，取最大值而不是累加，避免虚报
            for a, b in zip(agg['sources'], rep['sources']):
                a['count'] = max(a.get('count', 0), b.get('count', 0))
                a['ok'] = a['ok'] or b['ok']
                a['cost'] = round(a.get('cost', 0) + b.get('cost', 0), 2)
        agg['relaxed'] = agg['relaxed'] or rep['relaxed']
        agg['fallback'] = agg['fallback'] or rep['fallback']

    # 多个搜索词一起搜，而不是一个一个来：
    # 串行时慢源（中国公共招聘网一轮就要 10 秒）会被重复跑 5~6 遍，
    # 实测整次要 60 秒，用户以为死了。并行后总耗时就等于最慢的那一轮。
    def _run(w):
        return collect_jobs_with_report(keyword=w, city=city, natures=natures,
                                        exclude=exclude, include_wide=wide)

    with futures.ThreadPoolExecutor(max_workers=max(1, min(len(words), 5))) as ex:
        reps = list(ex.map(_run, words))
    for rep in reps:
        _merge(rep)

    # 岗位太少：补一轮「只看城市+单位性质、不限关键词」的宽搜，保证有得挑
    if len(agg['jobs']) < MIN_KEYWORD_HITS:
        _merge(collect_jobs_with_report(keyword='', city=city, natures=natures,
                                        exclude=exclude, include_wide=wide))

    agg['total'] = len(agg['jobs'])
    if force:
        agg['manual'] = True                    # 前端据此区分「手动实时抓取」

    # 现爬的结果回写岗位缓存，下次同样的搜索直接读库（毫秒级）
    if _JOBS_CACHE_ON and agg['jobs']:
        try:
            models.upsert_jobs(agg['jobs'])
        except Exception:                       # noqa: BLE001 写库失败不影响返回
            pass

    if len(_SEARCH_CACHE) > 30:
        _SEARCH_CACHE.clear()
    _SEARCH_CACHE[ck] = (time.time(), agg['jobs'], agg)
    return agg['jobs'], agg


@app.route('/api/match', methods=['POST'])
def api_match():
    data = request.get_json(force=True, silent=True) or {}
    profile = data.get('profile') or {}
    if data.get('profile_id') and not (profile.get('id') or profile.get('profile_id')):
        profile = dict(profile, profile_id=data.get('profile_id'))
    jobs = data.get('jobs')
    meta = {}
    if jobs is None:
        # force=true：用户在职位列表拉到底手动下拉触发的实时抓取
        # wide=false：用户勾掉了「连全国招聘和本省一起看」，只要本市
        jobs, meta = _collect_for_profile(profile, force=bool(data.get('force')),
                                          wide=data.get('wide') is not False)
    matched, dropped = _filter_apply_window(match_jobs(profile, jobs))
    if dropped:
        meta = dict(meta, dropped_closed=dropped)
    return jsonify(ok=True, count=len(matched), jobs=matched, meta=meta)


@app.route('/api/apply', methods=['POST'])
def api_apply():
    """投递入口，两种通道：

    channel='mailto'  生成邮件（调起求职者自己的邮件 App），同步返回，不建任务
    channel='smtp'    系统代发，后台线程逐条发送，WebSocket 推进度
    """
    data = request.get_json(force=True, silent=True) or {}
    profile_id = data.get('profile_id')
    profile = data.get('profile') or {}
    jobs = data.get('jobs') or []
    channel = data.get('channel') or 'mailto'
    # 按岗位定制简历：岗位 key -> {'path': 定制docx绝对路径, 'name': 显示名}
    tailored_map = data.get('tailored') or {}
    if not jobs:
        return jsonify(ok=False, msg='还没有选择要投的岗位'), 400

    # 通道 1：mailto，同步生成，前端逐个调起邮件 App
    if channel == 'mailto':
        mails = []
        for job in jobs:
            hr_to = (job.get('hr_email') or '').strip()
            item = {
                'key': job.get('key') or '',
                'title': job.get('title', ''),
                'company': job.get('company', ''),
                'has_email': bool(hr_to),
            }
            if not hr_to:
                item['ok'] = False
                item['status'] = '这个岗位没有公开邮箱'
                # 兜底：前端传回来的岗位可能缺字段，按单位性质给一个投递入口
                item['apply_url'] = (job.get('apply_url') or job.get('url')
                                     or DEFAULT_APPLY_URL.get(job.get('nature', ''), ''))
                mails.append(item)
                continue
            item['ok'] = True
            item['hr_email'] = hr_to
            item['subject'] = mailer.build_subject(profile, job)
            item['preview'] = mailer.build_text_body(profile, job)
            item['mailto'] = mailer.mailto_link(profile, job)
            if profile_id:
                models.save_application(
                    profile_id, job, job.get('score', 0),
                    '待本人发送', 'mailto', hr_to)
            mails.append(item)
        return jsonify(ok=True, channel='mailto', mails=mails)

    # 通道 2：代发
    task_id = uuid.uuid4().hex[:12]
    # 没有公开报名邮箱的岗位根本发不出去，必须报出来：前端正常只会传「能代投」那组，
    # 但只要有漏网的，也不能静默跳过——用户会以为投了 10 家、实际只有 8 家。
    no_mail = [j for j in jobs if not (j.get('hr_email') or '').strip()]
    # 演练模式要在前端的进度/结果面板上标明「没真发」——不然后果和
    # 「自动填表其实停在提交前却记成已提交」是同一类：用户以为发出去了。
    _mcfg = mailer.load_config()
    _dry = (not _mcfg) or bool(_mcfg.get('dry_run'))

    # 真发之前先花一次连接做「登录预检」（2026-09-21 加的）。
    # 起因：用户填错了密码点投递，任务照常启动，然后每封之间还要等 20 秒——
    # 4 封要等 80 秒，最后 4 条一模一样的「登录失败」。整个批次其实是同一件事：
    # 登录这一关就过不去。预检失败就**同步**把原因回给用户，别建任务、别让他等。
    if not _dry:
        _pw = _mcfg.get('password') or ''
        _em = _mcfg.get('email') or _mcfg.get('username') or ''
        # 登录名必须单传：中继类（Brevo）username≠email，verify_login 默认拿
        # email 当登录名，会把 535「Authentication failed」误报成密码错（2026-09-24）。
        _ok, _msg = mailer.verify_login(
            _em, _pw,
            host=_mcfg.get('host'), port=_mcfg.get('port'),
            ssl=_mcfg.get('ssl'), starttls=_mcfg.get('starttls'),
            username=_mcfg.get('username') or None)
        if not _ok:
            return jsonify(ok=False, fail_reason='login', msg=(
                '发件邮箱登录没通过，一封都没发出去：%s' % _msg)), 400

    TASKS[task_id] = {
        'id': task_id,
        'status': 'running',
        'total': len(jobs),
        'done': 0,
        'success': 0,
        'fail': 0,
        # 没能代投（没公开邮箱）的条数，由 _run_apply 逐条累加 —— 这里不预热成
        # len(no_mail)，否则前端轮询到的任务里会翻倍
        'skipped': 0,
        'current': '',
        'items': [],
        'dry': _dry,
        # 每封之间的间隔（秒）。前端拿它算「大约还要多久」——4 封等 80 秒
        # 如果不写出来，用户会以为卡死了（他以为是「点了没反应」）。
        'interval': 0 if _dry else SEND_INTERVAL,
        'stopped': '',
        # 登录失败早停时记录「还剩几封没发」。这个键必须在建任务时就有：
        # 发送线程会在任务跑起来之后再往字典里写值，而轮询线程可能正在把这个
        # 字典序列化成 JSON —— 迭代中插入新键会抛
        # 「RuntimeError: dictionary changed size during iteration」，接口直接 500。
        'not_attempted': 0,
    }
    threading.Thread(
        target=_run_apply, args=(task_id, profile_id, jobs, profile, tailored_map), daemon=True
    ).start()
    # dry / interval 一并回给前端：它在等第一次轮询（1.5 秒）之前先用本地
    # 占位数据显示进度面板 —— 少了 interval，那 1.5 秒里会写「大约需要 0 秒」。
    return jsonify(ok=True, task_id=task_id, total=len(jobs), skipped=len(no_mail),
                   dry=_dry, interval=(0 if _dry else SEND_INTERVAL))


# ============================================================= 大模型 + 简历定制
# per-job tailoring：求职者上传简历、到投递这步时，按岗位 JD 改写/重排简历，
# 生成一份「专版」附件发出去。思路来自 GitHub 的 career-ops，但规则层走
# cn-resume-optimizer，工程上只借它的「LLM 只出结构化 JSON + 事实门硬卡」。
@app.route('/api/llm/config', methods=['GET'])
def api_llm_config_get():
    return jsonify(tailor.status())


@app.route('/api/llm/config', methods=['POST'])
def api_llm_config_post():
    d = request.get_json(force=True, silent=True) or {}
    cfg = tailor.save_config(
        base_url=(d.get('base_url') or '').strip(),
        api_key=(d.get('api_key') or '').strip(),
        model=(d.get('model') or '').strip(),
        enabled=d.get('enabled', True))
    return jsonify(ok=True, **tailor.status())


def _profile_of(data):
    """优先用前端传来的档案；没有 profile_id 时回退到库里那份。"""
    p = data.get('profile') or {}
    pid = data.get('profile_id')
    if not p and pid:
        try:
            p = models.get_profile(pid) or {}
        except Exception:                                    # noqa: BLE001
            p = {}
    return p


@app.route('/api/tailor/preview', methods=['POST'])
def api_tailor_preview():
    """只看不改：返回「原文 → 改成 → 为什么」对照，让用户先看懂再决定用不用。

    事实门没过也照样返回（blocked=true），差的清单本身对用户有价值。
    """
    d = request.get_json(force=True, silent=True) or {}
    profile = _profile_of(d)
    if not profile:
        return jsonify(ok=False, msg='找不到档案，请先在第 1 步保存'), 400
    job = d.get('job') or {}
    return jsonify(tailor.plan(profile, job))


@app.route('/api/tailor/build', methods=['POST'])
def api_tailor_build():
    """把定制结果渲染成 docx。返回可下载的 path/url。

    事实门没过的直接拒绝生成——宁可退回原件，也不把带编造内容的简历发到 HR 手里。
    """
    d = request.get_json(force=True, silent=True) or {}
    profile = _profile_of(d)
    if not profile:
        return jsonify(ok=False, msg='找不到档案，请先在第 1 步保存'), 400
    job = d.get('job') or {}
    payload = d.get('payload')
    path, err = tailor.build(profile, job, payload)
    if not path:
        return jsonify(ok=False, msg=err)
    return jsonify(ok=True, key=job.get('key') or '',
                   path=path, url='/api/tailor/file/%s' % os.path.basename(path))


@app.route('/api/tailor/file/<name>', methods=['GET'])
def api_tailor_file(name):
    safe = os.path.basename(name)
    p = os.path.join(tailor.OUT_DIR, safe)
    if not os.path.isfile(p):
        return jsonify(ok=False, msg='文件不存在或已过期'), 404
    return send_file(p, as_attachment=True,
                     download_name='%s-定制简历.docx' % safe.rsplit('_', 1)[0][:20])


@app.route('/api/applied', methods=['POST'])
def api_applied():
    """跳转投递的回执：用户在对方网站投完了，回来点一下，我们记一笔。

    这是最主要的投递方式——央企国企招聘门户都要注册登录，
    我们跳过去，用户在那边投，回来这里标记一下投递进度。
    """
    data = request.get_json(force=True, silent=True) or {}
    profile_id = data.get('profile_id')
    job = data.get('job') or {}
    status = data.get('status') or '已在官网投递'
    if profile_id:
        models.save_application(
            profile_id, job, job.get('score', 0), status, 'link',
            job.get('apply_url') or '')
    return jsonify(ok=True)


@app.route('/api/mail/status')
def api_mail_status():
    """前端用它决定要不要显示「帮我一键全发」按钮 / 配置面板。"""
    cfg = mailer.load_config()
    email = (cfg.get('email') or cfg.get('username') or '') if cfg else ''
    # 这里回完整地址（原来打码成 25***qq.com）。打码看着稳妥，实际上把
    # 「改授权码」这条必经之路堵死了：设置面板要预填邮箱，拿到打码的地址
    # 就没法填，用户只能把邮箱再手打一遍（打错了又是新一轮失败）。
    # 这是本机自用的单用户程序，地址不出这台机器。
    return jsonify(
        ok=True,
        ready=mailer.is_ready(),
        configured=bool(cfg),
        dry_run=bool(cfg and cfg.get('dry_run')),
        email=email,
        # 已保存的授权码形态可疑时，每次打开这一步都能看到提醒 ——
        # 不只在「刚保存那一下」说一次。用户是隔了一会儿才点投递的，
        # 那次说过的话他早看不见了（2026-09-21：他就这么白等了一整批）。
        pwd_warn=mailer.pwd_warning(email, (cfg or {}).get('password') or ''),
    )


@app.route('/api/mail/providers')
def api_mail_providers():
    """常见邮箱服务商清单：SMTP 参数 + 「怎么把 SMTP 开起来」的步骤。

    前端两处用它，数据只有后端这一份（mailer.PROVIDERS）：
      - 第 4 步「设置发件邮箱」弹窗里的说明（照着做就能拿到授权码）
      - 「用我自己的邮箱一封封发」弹窗里挑邮箱（点一下打开它的写信页）
    返回里 smtp 字段由 mailer 从 _SMTP_PRESETS 现取，所以
    「说明里写着支持」和「配置真能连上」不会走散。

    另外两条给界面用的判断依据：
      - smtp_ok=False（微软系）→ 不能用密码代发，界面上要标红提醒
      - oauth_only → 域名清单，用户手打的邮箱也能马上认出是这类
    """
    return jsonify(ok=True, providers=mailer.list_providers(),
                   oauth_only=list(mailer.OAUTH_ONLY_DOMAINS))


@app.route('/api/mail/verify', methods=['POST'])
def api_mail_verify():
    """「测一测」：拿填好的邮箱+授权码真连一次服务器做登录，**不发信**。

    加这个按钮的原因：用户没法自己判断授权码对不对、这个邮箱到底能不能用密码发 ——
    以前唯一的办法就是真发一封碰运气，失败时只看到一串 535。
    现在按一下就出人话：登录成功 / 服务器拒了（点名该填什么）/ 连不上。
    不传邮箱密码时用已保存的配置来测。
    """
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get('email') or '').strip()
    # ⚠️ 原来只有 email 为空才回退到已保存的配置。而界面一打开就会把邮箱预填成
    # 已保存的那个，用户根本没机会让它为空 —— 结果他想「用已保存的授权码测一测」
    # 时，password 恒为空串，登录必拒，还给出一句「密码不对」的误导。
    # 邮箱或授权码**缺任意一个**都要回退。
    password = (data.get('password') or '').strip()
    _vcfg = mailer.load_config() or {}
    if not email or not password:
        # ⚠️ 原来只有 email 为空才回退到已保存的配置。而界面一打开就会把邮箱预填成
        # 已保存的那个，用户根本没机会让它为空 —— 结果他想「用已保存的授权码测一测」
        # 时，password 恒为空串，登录必拒，还给出一句「密码不对」的误导。
        # 邮箱或授权码**缺任意一个**都要回退。
        email = email or (_vcfg.get('email') or _vcfg.get('username') or '').strip()
        password = password or (_vcfg.get('password') or '')
    # 已保存的是中继类配置（host 指到别家）时，测「同一个邮箱」必须按保存的
    # host/登录名来，否则 gmail 域名预设会把连接指去 Gmail SMTP、拿 gmail 地址
    # 当登录名，必挂。但用户在测一个**别的**新邮箱时不能沿用旧 host。
    _same = email and email == (_vcfg.get('email') or '').strip()
    _vhost = (data.get('host') or '').strip() or (_vcfg.get('host') if _same else None)
    _vuser = ((data.get('username') or '').strip()
              or (_vcfg.get('username') if _same else None))
    okc, msg = mailer.verify_login(
        email, password,
        host=_vhost, port=(data.get('port') or (_vcfg.get('port') if _same else None)),
        ssl=(_vcfg.get('ssl') if _same else None),
        starttls=(_vcfg.get('starttls') if _same else None),
        username=_vuser or None)
    # 实测通过就记账：这串密码是真连成功过的，往后不要再拿「位数不像授权码」
    # 去吓唬用户 —— 他按过「测一测」看到「登录成功」，回头却在界面上看到
    # 「密码可疑」，只会觉得这软件自相矛盾，然后跑去反复查邮箱（2026-09-21）。
    if okc:
        mailer.mark_verified(password)
    # 登录失败时，如果这个密码连形态都不对（QQ 一族要 16 位全小写字母），
    # 就把这条放在最前面 —— 它比「服务器拒了」具体得多，直接指着用户去改哪儿。
    shape = '' if okc else mailer.pwd_shape_warning(email, password)
    return jsonify(ok=okc, msg=('%s %s' % (shape, msg)).strip() if shape else msg,
                   email=email, shape_warning=shape)


@app.route('/api/mail/config', methods=['POST'])
def api_mail_config():
    """网页里配置发件账号：填邮箱+授权码即可，SMTP 参数按域名自动预设。

    保存一次就生效 —— 点「一键代投」就是真发。演示用的「演练 / 切到真实发送」
    那一步已经从界面上拿掉了。
    """
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get('email') or '').strip()
    # 空密码 = 「不改授权码」，由 mailer.save_config 沿用已存的那串。
    # 这条是 2026-09-21 那次事故的一半：用户点开设置面板只想改授权码，
    # 密码框被浏览器自动填充了旧登录密码，一点「保存」就把授权码盖掉了。
    password = (data.get('password') or '').strip()

    cfg = mailer.load_config()
    saved_email = (cfg.get('email') or cfg.get('username') or '').strip() if cfg else ''

    if not email:
        return jsonify(ok=False, msg='邮箱地址还没填'), 400

    # 授权码只对**同一个**发件邮箱有效。换了邮箱又没带授权码时，绝不能拿
    # 上一个邮箱的授权码顶上 —— 那会写出一份注定登录失败的配置。
    if saved_email and email.lower() != saved_email.lower() and not password:
        return jsonify(ok=False,
                       msg='换了发件邮箱，请把新邮箱的授权码也填上（旧邮箱的授权码不通用）'), 400

    okc, msg, host = mailer.save_config(
        email, password, from_name=(data.get('from_name') or ''))
    if not okc:
        return jsonify(ok=False, msg=msg), 400
    # 存得下不等于发得出：微软系邮箱存下去也没用，必须当场说，不能只回「已保存」。
    # 顺带查一下授权码的形态（QQ 一族是 16 位全小写字母）——「填了登录密码」
    # 是实际发生过的坑，能在这里拦住就别等真发的时候才发现。
    # 沿用旧授权码时 password 是空串，要用库里那串才检查得出来 ——
    # 否则「只想改邮箱、不改授权码」这条路永远查不出填的是登录密码，
    # 而 /api/mail/status 用同样的值却能报警，同一份数据两种结论。
    warn = mailer.pwd_warning(email, (mailer.load_config() or {}).get('password') or '')
    return jsonify(ok=True, msg='%s（SMTP：%s）' % (msg, host), warn=warn,
                   dry_run=bool((mailer.load_config() or {}).get('dry_run')))


@app.route('/api/task/<task_id>')
def api_task(task_id):
    """WebSocket 不通时前端用这个轮询兜底。"""
    t = TASKS.get(task_id)
    if not t:
        return jsonify(ok=False, msg='任务不存在'), 404
    # 回快照而不是字典本身：items 列表正在被发送线程 append，
    # 直接把活字典交给 jsonify 可能在序列化过程中被改动而报错。
    return jsonify(ok=True, task=dict(t, items=list(t.get('items') or [])))


def _as_int(v, default=None):
    """把入参转成整数。转不动就给默认值 —— 直接 int() 的话随便传个非数字
    进来就是一次 500，而这是本机桌面程序，用户看到的就是整个页面坏了。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


@app.route('/api/records')
def api_records():
    profile_id = request.args.get('profile_id')
    rows = models.list_applications(_as_int(profile_id))
    return jsonify(ok=True, records=rows)


# ------------------------- 自动填表（测试版） -------------------------
def _profile_for_autofill(data):
    """优先用数据库里的规范简历；没有就把前端传的归一化一下。"""
    pid = data.get('profile_id')
    if pid:
        try:
            row = models.get_profile(int(pid))
            if row:
                return row
        except (TypeError, ValueError):
            pass
    p = dict(data.get('profile') or {})
    if 'jobTypes' in p and 'job_types' not in p:
        p['job_types'] = p['jobTypes']
    return p


@app.route('/api/autofill/start', methods=['POST'])
def api_autofill_start():
    """开一个浏览器替用户填表。

    只做到「填完停在提交前」，提交那一下必须由人点——原因见 autofill.py 顶部。
    """
    data = request.get_json(force=True, silent=True) or {}
    job = data.get('job') or {}
    url = (data.get('url') or job.get('apply_url') or job.get('url') or '').strip()
    if not url:
        return jsonify(ok=False, msg='这个岗位没有可以打开的投递页面'), 400
    # 只允许打开 http/https 页面：挡掉 file://、javascript:、data: 这类危险方案，
    # 否则等于把一个本地文件或脚本执行入口交给 Playwright 去 goto
    from urllib.parse import urlparse
    if urlparse(url).scheme not in ('http', 'https'):
        return jsonify(ok=False, msg='链接必须是 http 或 https 开头的有效网址'), 400
    profile = _profile_for_autofill(data)
    if not (profile.get('name') or '').strip():
        return jsonify(ok=False, msg='请先回到第 1 步把简历信息填上'), 400
    tid, _t = autofill.start(
        job=job, profile=profile, url=url,
        headless=not bool(data.get('show_browser')),
        # 重填覆盖：段里已有内容也打开重填一遍再保存（默认关）
        refill=bool(data.get('refill')),
    )
    return jsonify(ok=True, task_id=tid)


@app.route('/api/autofill/task/<task_id>')
def api_autofill_task(task_id):
    t = autofill.get(task_id)
    if not t:
        return jsonify(ok=False, msg='任务不存在'), 404
    return jsonify(ok=True, task=t)


@app.route('/api/autofill/answer', methods=['POST'])
def api_autofill_answer():
    """人工介入：验证码、密码、提交确认都从这里回去。"""
    data = request.get_json(force=True, silent=True) or {}
    ok = autofill.submit_answer(data.get('task_id') or '', data.get('value') or '')
    return jsonify(ok=ok)


@app.route('/api/autofill/required')
def api_autofill_required():
    """投递前预检：国聘必填、缺了保存过不去的档案字段清单。

    前端开投前据此提示用户补全，免得跑到基本信息段才报错卡死。
    字段清单由 autofill.guopin_required_fields() 单一提供，前后端不脱节。
    """
    return jsonify(ok=True, fields=autofill.guopin_required_fields())


@app.route('/api/autofill/shot/<name>')
def api_autofill_shot(name):
    if not name.endswith('.png'):
        return jsonify(ok=False, msg='没有这张图'), 404
    return send_from_directory(autofill.SHOT_DIR, name)


# ------------------------- 投递执行 -------------------------
def _run_apply(task_id, profile_id, jobs, profile=None, tailored_map=None):
    """代发通道：逐封发送。

    发送间隔默认 20 秒，防止被判垃圾邮件；没配置 SMTP 或演练模式时不等待。
    tailored_map: 岗位 key -> {'path': 定制docx绝对路径, 'name': 显示名}。
    命中就用定制版当附件，否则退回原件。
    """
    t = TASKS[task_id]
    profile = profile or {}
    tailored_map = tailored_map or {}
    cfg = mailer.load_config()
    dry = (not cfg) or cfg.get('dry_run')
    t['dry'] = dry
    interval = 0 if dry else SEND_INTERVAL
    attempted = 0
    try:
        for job in jobs:
            t['current'] = job.get('title', '')
            _push(task_id)
            to = (job.get('hr_email') or '').strip()
            if not to:
                # 没有公开报名邮箱 = 这封邮件根本没有收件人。绝不能算成功，
                # 也不能悄悄跳过：如实记一条「没能代投」，让用户在结果面板上
                # 看到是哪几家、并知道要自己去官网投（前端会接一个按钮过去）。
                status = '没能代投：这个岗位没有公开邮箱，需要去它的招聘网站投'
                if profile_id:
                    models.save_application(profile_id, job, job.get('score', 0),
                                            '未代投（无公开邮箱）', 'smtp', '')
                t['done'] += 1
                t['fail'] += 1
                t['skipped'] = t.get('skipped', 0) + 1
                t['items'].append({
                    'title': job.get('title', ''),
                    'company': job.get('company', ''),
                    'nature': job.get('nature', ''),
                    'ok': False,
                    'skipped': True,
                    'status': status,
                    'msg': '没有收件邮箱',
                })
                _push(task_id)
                continue
            # 首封立刻发，别让用户先干等一个间隔 —— 进度条停在 0、屏幕上
            # 什么都不动，跟「点了没反应」长得一模一样。间隔只用在「封与封之间」。
            if interval and attempted:
                time.sleep(interval)
            attempted += 1
            try:
                _tmeta = tailored_map.get(job.get('key')) or {}
                ok, msg = mailer.send_smtp(
                    profile, job, cfg,
                    tailored_path=_tmeta.get('path'),
                    tailored_name=_tmeta.get('name'))

            except Exception as e:                    # noqa: BLE001
                # 一封出意外不能带走整批：以前这里会直接跳到最外面的 except，
                # 整个任务变 error、后面的岗位一封都不发（2026-09-21 实测：
                # 岗位名带斜杠让落盘 open() 失败就是这个后果）。如实记这一条，继续。
                ok, msg = False, '发送出错：%s' % e
            # 演练模式发成功 ≠ 发出去了。状态必须如实写「未真发」，
            # 否则用户在结果面板看到「N 封已发送」会以为招聘方收到了。
            if ok and dry:
                status = '演练：已生成未真发'
            elif ok:
                status = '已发送'
            else:
                status = '发送失败' if '失败' in msg else msg
            if profile_id:
                models.save_application(profile_id, job, job.get('score', 0), status,
                                        'smtp', (job.get('hr_email') or ''))
            t['done'] += 1
            t['success' if ok else 'fail'] += 1
            t['items'].append({
                'title': job.get('title', ''),
                'company': job.get('company', ''),
                'nature': job.get('nature', ''),
                'ok': ok,
                'status': status,
                'msg': msg,
            })
            _push(task_id)
            # 登录这一关没过 = 整批都过不去（同一个发件账号、同一个密码）。
            # 再往下一封封试只是把同一个失败重复 N 遍，而且每封还要白等 20 秒。
            # 停在这里，把原因和「还剩几封没发」如实交代清楚。
            if not ok and mailer.is_auth_failure(msg):
                t['stopped'] = msg
                t['not_attempted'] = t['total'] - t['done']
                break
        t['status'] = 'done'
        t['current'] = ''
        _push(task_id)
    except Exception:
        t['status'] = 'error'
        t['error'] = traceback.format_exc()
        _push(task_id)


@socketio.on('connect')
def on_connect():
    return True


if __name__ == '__main__':
    # 默认只本机访问；设置 HOST=0.0.0.0 可以让同一网络的其他设备也打开
    host = os.environ.get('HOST') or '127.0.0.1'
    port = int(os.environ.get('PORT') or 5000)
    print('简历投递助手启动：http://%s:%d' % ('127.0.0.1' if host in ('0.0.0.0', '') else host, port))
    if host == '0.0.0.0':
        print('同一 WiFi/局域网内的手机、电脑也可以访问上面这个地址（把 127.0.0.1 换成这台电脑的局域网 IP）')
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
