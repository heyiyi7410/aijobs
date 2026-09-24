# -*- coding: utf-8 -*-
"""数据模型与存储（SQLite，标准库实现，零额外依赖）。

三张表：
  profiles     求职者信息
  applications 投递记录
  jobs_cache   岗位缓存（可选）
"""
import json
import os
import sqlite3
import threading
import time

_DB_PATH = None

# SQLite 同一时刻只允许一个写者。Flask-SocketIO(threading) 的请求线程和
# 后台刷新线程都会写库，用这把锁串行化，避免 "database is locked"。
_WRITE_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    phone       TEXT,
    email       TEXT,
    age         TEXT,
    city        TEXT,
    job_types   TEXT,
    salary      TEXT,
    exp         TEXT,
    edu         TEXT,
    skills      TEXT,
    intro       TEXT,
    gender      TEXT,
    birth       TEXT,
    school      TEXT,
    major       TEXT,
    graduation  TEXT,
    resume_path TEXT,
    created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER,
    job_key     TEXT,
    title       TEXT,
    company     TEXT,
    city        TEXT,
    salary_text TEXT,
    score       INTEGER,
    status      TEXT,
    channel     TEXT,
    to_email    TEXT,
    created_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_app_profile ON applications(profile_id);
CREATE TABLE IF NOT EXISTS jobs (
    key         TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    city        TEXT,
    region      TEXT,
    nature      TEXT,
    salary_text TEXT,
    deadline    TEXT,
    apply_start TEXT,
    source      TEXT,
    url         TEXT,
    desc        TEXT,
    fetched_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_city    ON jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_nature  ON jobs(nature);
CREATE INDEX IF NOT EXISTS idx_jobs_fetched ON jobs(fetched_at);
"""

# 老库升级：缺哪个字段补哪个
EXTRA_COLUMNS = [
    ('profiles', 'email', 'TEXT'),
    ('applications', 'channel', 'TEXT'),
    ('applications', 'to_email', 'TEXT'),
    # 招聘表必问的几项：不存下来，自动投读库时就拿不到，只能空着让人重打一遍
    ('profiles', 'gender', 'TEXT'),
    ('profiles', 'birth', 'TEXT'),
    ('profiles', 'school', 'TEXT'),
    ('profiles', 'major', 'TEXT'),
    ('profiles', 'graduation', 'TEXT'),
    # 国聘「基本信息」段必填，档案里没有就永远卡在保存那一步（2026-09-20 实测）
    ('profiles', 'height', 'TEXT'),
    ('profiles', 'weight', 'TEXT'),
    ('profiles', 'emergency_name', 'TEXT'),
    ('profiles', 'emergency_phone', 'TEXT'),
    ('profiles', 'address', 'TEXT'),
    # 用户同意保留的原始简历文件（Word/PDF），自动投要上传它而不是生成的文本
    ('profiles', 'resume_path', 'TEXT'),
    # 岗位职责/公告摘要：前端「详情」按钮展开要看，缓存路径也得带上
    ('jobs', 'desc', 'TEXT'),
    # 公告里的报名邮箱（邮件报名用）
    ('jobs', 'contact_email', 'TEXT'),
    # 公告里的联系电话（电话/短信联系用）。民企岗位基本不留邮箱，只留手机号，
    # 没有这一列，这批岗位就只能走「跳转投递页」，等于放弃联系。
    ('jobs', 'contact_phone', 'TEXT'),
    # 结构化的经历（工作/教育/实习/校内活动）：国聘补站内简历时要一段一段真填，
    # 光有 school/major 三个标量字段只够补一段教育，工作经历和实习没有数据来源。
    # 存 JSON 数组：[{"type":"work|edu|intern|campus","school":"..","major":"..",
    #               "degree":"..","company":"..","role":"..","start":"2024-07",
    #               "end":"2025-01","desc":".."}]
    # work 那类由简历解析认出来（resume_parser._find_work_experiences），
    # 也在档案页「你的经历」里能手动加改——投递时是「工作/实习经历」段的首选数据。
    ('profiles', 'experiences', 'TEXT'),
    # 党派必填时的「入党时间」：与政治面貌联动，autofill 读取后填进国聘表单。
    # 群众档案这列留空即可（国聘政治面貌下拉只有党派选项，群众身份走站点默认）。
    ('profiles', 'party_join_date', 'TEXT'),
    # 国聘有两个必填段是「你确实没有吗」的事实声明，站点给了一键「无XX」按钮：
    #   资格证书 → 「无资格证书」
    #   亲属在集团公司系统单位任职情况 → 「无亲属在集团公司系统单位任职情况」
    # 这种声明只有本人能下，所以不做成「没数据就默认无」——用户明确勾了「没有」
    # （这两列 = 1），流程才会替他点；没勾（NULL/0）就照旧转人工。
    ('profiles', 'no_certificate', 'INTEGER'),
    ('profiles', 'no_relative', 'INTEGER'),
    # 家庭成员（国聘必填段）。存 JSON 数组，字段全必填：
    #   [{"relation":"父亲","name":"..","birth":"1965-03","political":"中共党员",
    #     "company":"..","position":".."}]
    # 家人的隐私，只存用户自己录的，autofill 只负责往表单里搬、不生成。
    ('profiles', 'family', 'TEXT'),
    # 「我确实没有这一项」的段名列表（JSON 数组），通用版。
    # 为什么需要：每个岗位的简历模板段不一样（国聘按岗位下发 template.module_list），
    # 哪些段带一键「无XX」声明是站点决定的、事先不知道。上面 no_certificate /
    # no_relative 只覆盖了最早遇到的两段，遇到新段就得改表加列。
    # 这里改成一份列表：用户确认哪些段「确实没有」就写进去，段名/段 id 都行，
    # autofill 在段内**现找**「无XX」按钮（_gp_declare_text）再点下去。
    # 例：["资格证书", "亲属在集团公司系统单位任职情况", "荣誉奖励"]
    ('profiles', 'declared_none', 'TEXT'),
    # ---- 下面这些是「只有本人知道」的个人事实，2026-09-21 补 ----
    # 起因：autofill 之前把它们**写死**成 汉族/未婚/统招/全日制/英语熟练/健康，
    # 档案里压根没有这些数据，等于替用户编材料（成人教育被填成统招全日制就是
    # 学历性质造假）。现在一律改成「档案有才填，没有就不填并标出来让用户补」。
    ('profiles', 'nation', 'TEXT'),          # 民族，如「汉族」「回族」
    ('profiles', 'marital', 'TEXT'),         # 婚姻状况：未婚/已婚/离异/丧偶
    ('profiles', 'edu_regular', 'TEXT'),     # 学历性质：统招 / 非统招
    ('profiles', 'edu_fulltime', 'TEXT'),    # 学习形式：全日制 / 非全日制
    ('profiles', 'foreign_lang', 'TEXT'),    # 外语语种，如「英语」；空=没有外语
    ('profiles', 'foreign_level', 'TEXT'),   # 外语水平：精通/熟练/一般/不会
    # 是否服从调剂：**必须用户本人决定**（服从可能被派到偏远地区），
    # 所以不设默认值——没填就不替他选。
    ('profiles', 'can_arrange', 'TEXT'),     # 是 / 否
    ('profiles', 'health', 'TEXT'),          # 健康状况：健康/良好/一般
    ('profiles', 'has_degree', 'TEXT'),      # 学位证：有学位证 / 无学位证
]


def init_db(path):
    global _DB_PATH
    _DB_PATH = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        for table, col, decl in EXTRA_COLUMNS:
            has = conn.execute('PRAGMA table_info(%s)' % table).fetchall()
            if not any(r[1] == col for r in has):
                conn.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, decl))
        conn.commit()
    finally:
        conn.close()


def _conn():
    return sqlite3.connect(_DB_PATH)


def _join(v):
    if isinstance(v, (list, tuple)):
        return ','.join(str(x) for x in v)
    return '' if v is None else str(v)


def save_profile(data):
    """保存一份简历信息，返回 id。有同手机号则更新，避免重复建档。

    合并语义（多用户/部分更新安全）：以库里旧值为底，用本次 data 里「显式出现」
    的键覆盖；data 里没给的字段一律保留旧值，绝不用空串冲掉用户已填的数据。
    这样前端每次「下一步」只回传部分字段时，不会把用户之前填的实习经历、身高、
    入党时间等抹掉。experiences 是 JSON 数组，单独规整。
    """
    conn = _conn()
    try:
        phone = (data.get('phone') or '').strip()

        def norm(v):
            return '' if v is None else str(v).strip()

        # 旧值（按列名）；无匹配则空字典，走 INSERT
        old = {}
        if phone:
            c = conn.execute('SELECT * FROM profiles WHERE phone=?', (phone,))
            c.row_factory = sqlite3.Row
            r0 = c.fetchone()
            if r0:
                old = dict(r0)

        def v(key, transform=None):
            """本次 data 给了就用（规整后），没给就沿用旧值。"""
            if key in data:
                val = data[key]
                return transform(val) if transform else norm(val)
            return old.get(key) or ''

        # experiences：list/tuple -> JSON；str 直接用；缺省沿用旧值
        if 'experiences' in data:
            exp = data['experiences']
            if exp is None:
                exp_out = old.get('experiences') or ''
            elif isinstance(exp, (list, tuple)):
                exp_out = json.dumps(
                    [e for e in exp if isinstance(e, dict)
                     and (str(e.get('company') or '').strip()
                          or str(e.get('school') or '').strip())],
                    ensure_ascii=False)
            elif isinstance(exp, str):
                exp_out = exp
            else:
                exp_out = ''
        else:
            exp_out = old.get('experiences') or ''

        row = (
            v('name'), phone, v('email'), v('age'), v('city'),
            _join(data.get('jobTypes')) if 'jobTypes' in data else (old.get('job_types') or ''),
            v('salary'), v('exp'), v('edu'),
            _join(data.get('skills')) if 'skills' in data else (old.get('skills') or ''),
            v('intro'), v('gender'), v('birth'), v('school'), v('major'),
            v('graduation'), v('resume_path'), v('height'), v('weight'),
            v('emergency_name'), v('emergency_phone'), v('address'),
            v('party_join_date'),
            # 只有本人知道的个人事实（2026-09-21 补）：民族 / 婚姻 / 学历性质 /
            # 学位证 / 外语 / 服从调剂 / 健康状况。autofill 以前把这些**写死**，
            # 现在必须真存下来，否则投递时又只能靠猜。
            v('nation'), v('marital'), v('edu_regular'), v('edu_fulltime'),
            v('has_degree'), v('foreign_lang'), v('foreign_level'),
            v('can_arrange'), v('health'),
            exp_out, int(time.time()),
        )
        if old:
            conn.execute(
                'UPDATE profiles SET name=?,phone=?,email=?,age=?,city=?,job_types=?,salary=?,'
                'exp=?,edu=?,skills=?,intro=?,gender=?,birth=?,school=?,major=?,graduation=?,'
                'resume_path=?,height=?,weight=?,emergency_name=?,'
                'emergency_phone=?,address=?,party_join_date=?,'
                'nation=?,marital=?,edu_regular=?,edu_fulltime=?,has_degree=?,'
                'foreign_lang=?,foreign_level=?,can_arrange=?,health=?,'
                'experiences=?,created_at=? WHERE id=?',
                row + (old['id'],))
            conn.commit()
            return old['id']
        cur = conn.execute(
            'INSERT INTO profiles (name,phone,email,age,city,job_types,salary,exp,edu,skills,intro,'
            'gender,birth,school,major,graduation,resume_path,height,weight,'
            'emergency_name,emergency_phone,address,party_join_date,'
            'nation,marital,edu_regular,edu_fulltime,has_degree,'
            'foreign_lang,foreign_level,can_arrange,health,'
            'experiences,created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
            '?,?,?,?,?,?,?,?,?,?)',
            row)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_profile(profile_id):
    conn = _conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute('SELECT * FROM profiles WHERE id=?', (profile_id,))
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def save_application(profile_id, job, score, status, channel='', to_email=''):
    conn = _conn()
    try:
        conn.execute(
            'INSERT INTO applications (profile_id,job_key,title,company,city,salary_text,score,'
            'status,channel,to_email,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (
                profile_id,
                job.get('key') or job.get('id') or '',
                job.get('title', ''),
                job.get('company', ''),
                job.get('city', ''),
                job.get('salary_text', ''),
                int(score or 0),
                status,
                channel,
                to_email,
                int(time.time()),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_applied_keys(profile_id=None):
    """已经投过的岗位标识集合，用于「投过的不再重复推给同一个人」。

    同时给两种键：
      job_key          完整键（含渠道），同渠道精确去重
      公司|职位|城市    跨渠道去重：同一个岗位被两个源抓到，也算投过
    """
    conn = _conn()
    try:
        if profile_id:
            cur = conn.execute(
                'SELECT job_key,company,title,city FROM applications WHERE profile_id=?',
                (profile_id,))
        else:
            cur = conn.execute('SELECT job_key,company,title,city FROM applications')
        keys = set()
        for k, company, title, city in cur.fetchall():
            if k:
                keys.add(k)
            if company or title:
                keys.add('%s|%s|%s' % (company or '', title or '', city or ''))
        return keys
    finally:
        conn.close()


def list_applications(profile_id=None, limit=100):
    conn = _conn()
    try:
        conn.row_factory = sqlite3.Row
        if profile_id:
            cur = conn.execute(
                'SELECT * FROM applications WHERE profile_id=? ORDER BY id DESC LIMIT ?',
                (profile_id, limit),
            )
        else:
            cur = conn.execute('SELECT * FROM applications ORDER BY id DESC LIMIT ?', (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ------------------------- 岗位缓存（服务端持久化） -------------------------
# 抓到的岗位落进 SQLite，搜索改成读库（毫秒级）而不是每次现爬（数秒）。
# 后台线程定期重爬刷新这张表；写操作全部走 _WRITE_LOCK 串行化。


def _job_key(j):
    """岗位去重键：优先用采集器给的 key，否则 公司|职位|城市。
    与 applications.list_applied_keys 的跨渠道去重口径保持一致。"""
    k = (j.get('key') or '').strip()
    if k:
        return k
    return '%s|%s|%s' % (j.get('company') or '', j.get('title') or '',
                         j.get('city') or '')


def upsert_jobs(jobs, now=None):
    """把抓到的岗位写进 jobs 表，同 key 覆盖（不会越堆越多）。返回写入条数。"""
    if not jobs:
        return 0
    now = int(now or time.time())
    rows = [(
        _job_key(j),
        j.get('title', '') or '',
        j.get('company', '') or '',
        j.get('city', '') or '',
        j.get('region', '') or '',
        j.get('nature', '') or '',
        j.get('salary_text', '') or '',
        j.get('deadline', '') or '',
        j.get('apply_start', '') or '',
        j.get('source', '') or '',
        # 库里这一列叫 url，但采集器给的字段是 apply_url（gov_soe 还会把它设成
        # 「报名系统」的外链，比公告页更该给用户）。以前只取 j['url']，
        # 只带 apply_url 的岗位存进去就是一条**没有投递入口的死岗**。
        (j.get('apply_url') or j.get('url') or '')[:500],
        (j.get('desc', '') or '')[:2000],
        (j.get('contact_email') or j.get('hr_email') or '')[:120],
        # 公告里的联系电话。民企/基层岗位大量只留手机号、没有邮箱，
        # 没有这一列，这半边岗位就永远归到「没有联系方式」那一堆。
        (j.get('contact_phone') or j.get('hr_phone') or '')[:40],
        now,
    ) for j in jobs]
    with _WRITE_LOCK:
        conn = _conn()
        try:
            conn.executemany(
                'INSERT OR REPLACE INTO jobs '
                '(key,title,company,city,region,nature,salary_text,deadline,'
                'apply_start,source,url,desc,contact_email,contact_phone,'
                'fetched_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                rows)
            conn.commit()
        finally:
            conn.close()
    return len(rows)


def _province_cities():
    """省 → 下辖市表。跟采集器共用同一份，避免两处各写一套、改了这边忘那边。

    collectors 不 import models，所以这里反向 import 是安全的；万一将来
    变成循环依赖，退化成空表也只是「省份匹配失效」，不会拖垮查询。
    """
    try:
        from collectors.gov_soe import PROVINCE_CITIES
        return PROVINCE_CITIES
    except Exception:                                # noqa: BLE001
        return {}


def _city_hit(want, got):
    """岗位的 city(got) 是否算落在用户选的城市(want)里。

    三条判据，都是被真实数据逼出来的：

    1. 子串：用户选「武汉」、岗位标「武汉市」——反之亦然。
    2. **全国一律算命中**。2026-09-21 扩的那 10 个中公行业源（国家电网/
       中石化/中海油…）city 全写「全国」，若按严格匹配，用户选任何城市
       都会把它们整批排除掉——源扩了、数据进库了，用户却一条都看不到。
       这类央企岗本来就是面向全国招，在哪儿投都成立。
    3. **省份关系**：用户选「武汉」，岗位标「湖北」（省里发的公告常只写省名）
       ——光靠子串匹配这两者互不包含，会把省内岗位全挡掉。
    """
    want = (want or '').strip()
    got = (got or '').strip()
    if not want:
        return True
    if got == '全国':
        return True
    if not got:
        return False
    if want in got or got in want:
        return True
    table = _province_cities()
    for prov, cities in table.items():
        if want == prov and got in cities:      # 选省、岗位标市
            return True
        if got == prov and want in cities:      # 选市、岗位标省
            return True
    return False


def query_jobs(city='', natures=None, keywords=None, limit=200):
    """从 jobs 表读岗位。返回与采集器同构的 job 字典列表（另带 fetched_at/region）。

    过滤在 Python 里做而不是塞进 SQL：行数就几百上千，毫秒级；
    而城市匹配（「武汉」↔「武汉市」）用 SQL LIKE 反而写不利索。
    """
    conn = _conn()
    try:
        conn.row_factory = sqlite3.Row
        # 带**联系方式**的岗位排前面：邮箱优先、电话次之、都没有的垫底。
        # 原因：库有三千多条，而平台型来源（国聘网，2900+ 条）**从不留邮箱**，
        # 按抓取时间倒序时它们会把这一百来条「能一键代投」的岗位整个挤出
        # limit 之外——用户点「只看能代投」会发现筛完是 0 条（2026-09-21 实测
        # 全国搜索结果：200 条里 0 条带邮箱）。留了邮箱的岗位本来就是投递体验
        # 最好的那批，优先给用户看是合理的取舍；电话那批同理，只是次之
        # （2026-09-22 加）。
        cur = conn.execute(
            'SELECT * FROM jobs ORDER BY '
            "(CASE WHEN contact_email IS NOT NULL AND contact_email<>'' THEN 0 "
            "      WHEN contact_phone IS NOT NULL AND contact_phone<>'' THEN 1 "
            '      ELSE 2 END), fetched_at DESC')
        rows = cur.fetchall()
    finally:
        conn.close()

    want = set(natures) if natures else None
    kws = [k.strip().lower() for k in (keywords or []) if k and k.strip()]
    city = (city or '').strip()
    out = []
    for r in rows:
        j = dict(r)
        # 旧库行的 desc 可能是 NULL（加列前的数据），规范成空串，
        # 不然后面 join/展示遇到 None 会崩
        if j.get('desc') is None:
            j['desc'] = ''
        if j.get('contact_email') is None:
            j['contact_email'] = ''
        if j.get('contact_phone') is None:
            j['contact_phone'] = ''
        # 邮件模块（mailer）和前端用的字段名是 hr_email，库里存的是 contact_email，
        # 这里统一别名，两条路径（读库/现爬）对前端同构
        j['hr_email'] = j.get('contact_email') or ''
        # 同理：电话字段在库里叫 contact_phone，采集器/前端用 hr_phone
        j['hr_phone'] = j.get('contact_phone') or ''
        # 同理：库里是 url，采集器/前端/自动填表用的是 apply_url。
        # 少了这个别名，读库路径返回的岗位在前端**没有「去官网投」的入口**
        # （第 3 步点进去没反应），而现爬路径是有的 —— 同一份数据两种表现。
        j['apply_url'] = j.get('url') or ''
        if want and (j.get('nature') or '民营') not in want:
            continue
        c = (j.get('city') or '').strip()
        if not _city_hit(city, c):
            continue
        if kws:
            hay = ((j.get('title') or '') + ' ' + (j.get('company') or '')
                   + ' ' + (j.get('desc') or '')).lower()
            if not any(k in hay for k in kws):
                continue
        out.append(j)
        if len(out) >= (limit or 200):
            break
    return out


def get_job(key):
    """按 key 取单条岗位（邮件报名等单岗位操作用）。没有返回 None。"""
    conn = _conn()
    try:
        conn.row_factory = sqlite3.Row
        r = conn.execute('SELECT * FROM jobs WHERE key=?', (key,)).fetchone()
        if not r:
            return None
        j = dict(r)
        for f in ('desc', 'contact_email', 'contact_phone'):
            if j.get(f) is None:
                j[f] = ''
        j['hr_email'] = j.get('contact_email') or ''
        j['hr_phone'] = j.get('contact_phone') or ''
        j['apply_url'] = j.get('url') or ''      # 同 query_jobs：url → apply_url 别名
        return j
    finally:
        conn.close()


def prune_jobs(max_age_days=30, now=None):
    """删掉太久没刷新到的岗位（公告类来源只有发布日没有截止日，
    按抓取时间算有效期最稳妥）。返回删除条数。"""
    now = int(now or time.time())
    cutoff = now - max_age_days * 86400
    with _WRITE_LOCK:
        conn = _conn()
        try:
            cur = conn.execute('DELETE FROM jobs WHERE fetched_at < ?', (cutoff,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def prune_missing_companies(source, companies, batch=400):
    """某个源这一轮**没抓到**的公司 → 把它在这个源下的岗位全部下线。

    为什么不能只靠 prune_jobs 的 TTL：
      fetched_at 记的是「最后一次抓到的时间」。源站把岗位撤了、我们下一轮没抓到，
      这条数据在本地还会按 TTL 活满 30 天 —— 用户看到的是一条早就不存在的招聘。
      而同步脚本是整表替换，只要本地库里还留着，就会被一直推上线。
      所以必须按公司做一次对账：本轮抓到的公司名单之外的，直接删。

    ═══ 安全底线：本轮一家公司都没抓到时，一条都不删 ═══
      抓到 0 家几乎不可能是「全站公司集体下线」，只可能是采集本身失败了
      （被限流 / 改版 / 网络抖 / 接口 504）。这时候执行删除，等于把整个源清空，
      而且是通过同步脚本推到线上——比什么都不做危险得多。宁可留着旧数据。

    没有公司名的岗位（COALESCE(company,'')=''）不参与对账：
      没法判断它属于哪家公司，误删代价太大，交给 TTL 处理。
    """
    if not source:
        return 0
    seen = sorted({(c or '').strip() for c in (companies or [])
                   if (c or '').strip()})
    if not seen:
        return 0
    # 公司名可能有几万个（就业在线全量一轮就是 3 万+），拼 NOT IN 会撞 SQLite
    # 的 SQLITE_MAX_VARIABLE_NUMBER 上限，也会把 SQL 撑成几百 KB。改走临时表：
    # 插入一次，再用 NOT IN (SELECT ...) 比对，参数个数恒为 1。
    with _WRITE_LOCK:
        conn = _conn()
        try:
            conn.execute('DROP TABLE IF EXISTS temp._seen_company')
            conn.execute('CREATE TEMP TABLE _seen_company (name TEXT PRIMARY KEY)')
            conn.executemany('INSERT OR IGNORE INTO _seen_company VALUES (?)',
                             [(c,) for c in seen])
            cur = conn.execute(
                "DELETE FROM jobs WHERE source=? AND COALESCE(company,'')<>'' "
                "AND company NOT IN (SELECT name FROM _seen_company)",
                (source,))
            conn.execute('DROP TABLE IF EXISTS temp._seen_company')
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def count_jobs():
    conn = _conn()
    try:
        cur = conn.execute('SELECT COUNT(*) FROM jobs')
        return cur.fetchone()[0]
    finally:
        conn.close()
