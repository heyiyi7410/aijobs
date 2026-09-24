# -*- coding: utf-8 -*-
"""端到端测试：python backend/test_web.py

用 Flask 测试客户端跑，不需要先启动服务。
覆盖：页面、简历保存、单位性质筛选、AI 匹配、投递任务、投递记录。
"""
import os
import sys
import json
import time
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)

import models  # noqa: E402
import mailer  # noqa: E402
from app import app  # noqa: E402
from collectors import DEFAULT_NATURES  # noqa: E402  # 默认搜索的真实性质集合（全体制内）

# 测试用临时数据库，不污染真实数据
TMP = tempfile.mkdtemp(prefix='resume-test-')
models.init_db(os.path.join(TMP, 'test.db'))

# 邮件走演练模式，不真发、不落盘到项目目录
mailer.OUTBOX_DIR = os.path.join(TMP, 'outbox')
# 配置也要指到临时目录：/api/mail/config、/api/mail/sandbox 这些用例会写配置，
# 以前它们直接覆写真实的 data/mail.json，跑一次测试就要靠人肉还原发件账号
# （2026-09-21 就发生过一次）。测试永远不许动用户真实的配置。
mailer.CONFIG_PATH = os.path.join(TMP, 'mail.json')
mailer.load_config = lambda: {'dry_run': True, 'username': 'test@example.com'}

WANT = ['央企', '国企', '外企']


def setUpModule():
    """开跑前清掉 data/sources.json 可能残留的渠道覆盖。

    历史教训：上一次运行若在「禁用所有源」的用例中途被打断，会残留
    {guopin/mohrss/foreign: false}；用例自己会备份/还原，但会把这份残留
    当成「原值」永久保留，导致后续运行的 _enabled 断言无端失败。
    这里统一在开头清一次，保证每次都是从干净状态起跑。
    """
    try:
        from collectors import _OVERRIDE_PATH
        if os.path.exists(_OVERRIDE_PATH):
            os.remove(_OVERRIDE_PATH)
    except Exception:                            # noqa: BLE001
        pass


def profile(**kw):
    p = {
        'name': '张三',
        'phone': '13800001234',
        'email': 'zhangsan@qq.com',
        'age': '32',
        'city': '杭州',
        'cityOther': '',
        'jobTypes': ['电力运维'],
        'natures': WANT,
        'hireType': '直签',
        'salary': '5000-8000',
        'exp': '1-3年',
        'edu': '高中中专',
        'skills': ['有电工证', '能上夜班'],
        'intro': '在工厂做过三年'
    }
    p.update(kw)
    return p


class TestWeb(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()

    # 1
    def test_01_index(self):
        r = self.c.get('/')
        self.assertIn(r.status_code, (200, 503))

    # 2
    def test_02_meta(self):
        r = self.c.get('/api/meta').get_json()
        self.assertTrue(r['ok'])
        self.assertIn('央企', r['natures'])

    # 3
    def test_03_profile_requires_name(self):
        r = self.c.post('/api/profile', json={'name': '', 'phone': '13800001234'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['ok'])

    # 4
    def test_04_profile_saved(self):
        r = self.c.post('/api/profile', json=profile()).get_json()
        self.assertTrue(r['ok'])
        self.assertIsInstance(r['profile_id'], int)
        self.assertIsNotNone(models.get_profile(r['profile_id']))

    # 5
    def test_05_search_default_includes_private_firms(self):
        # 破茧后默认搜索（不指定 natures）覆盖 DEFAULT_NATURES 全部性质，
        # 其中包含 民营 —— 这正是这次要打破的旧「只收体制内」茧房。
        # 录入来源（mohrss 等）97% 是民企，过去被「默认不带民营」锁死，
        # 现在必须能在默认搜索里看到它们。
        # 真红线只剩一条：返回岗位的性质必须落在 DEFAULT_NATURES 内
        # （不允许出现 未知/杂项 这类游离性质污染信息流）。
        r = self.c.post('/api/jobs/search', json={'city': '杭州'}).get_json()
        self.assertTrue(r['ok'])
        self.assertGreater(r['count'], 0)
        natures_seen = set()
        for j in r['jobs']:
            self.assertIn(j['nature'], DEFAULT_NATURES,
                          '%s 的性质 %s 不在默认集合内' % (j['company'], j['nature']))
            natures_seen.add(j['nature'])
        # 破茧验证：默认搜索应当能看到民营岗位（而非全部体制内）。
        # 若这条失败，说明 mohrss 的民营通道又被默认口径挡住了。
        self.assertIn('民营', natures_seen,
                      '默认搜索应已包含民营岗位（破茧），但本次返回里没有')

    # 6
    def test_06_natures_control_what_we_ask_for(self):
        """「不限」要真的去要全部性质；指定外企时不应混进国企码。"""
        from collectors import net
        from collectors.guopin import GuopinCollector
        captured = {}
        original = net.request

        def fake(url, data=None, **kwargs):
            captured['payload'] = data
            return {'code': 200, 'data': {'list': []}}

        net.request = fake
        try:
            GuopinCollector().fetch(keyword='电力', city='', limit=20, natures=[])
            unlimited = captured['payload']['search']['company_nature']
            GuopinCollector().fetch(keyword='电力', city='', limit=20, natures=['外企'])
            foreign = captured['payload']['search']['company_nature']
        finally:
            net.request = original

        self.assertIn('1145DorR', unlimited)        # 民营企业
        self.assertIn('11AzDak', unlimited)         # 国企（含央企）
        self.assertIn('113yihzr', foreign)          # 外商独资
        self.assertNotIn('11AzDak', foreign)        # 只要外企就别去捞国企

    # 7
    def test_07_match_sorted_desc(self):
        p = profile()
        r = self.c.post('/api/match', json={'profile': p}).get_json()
        self.assertTrue(r['ok'])
        self.assertGreater(r['count'], 0)
        scores = [j['score'] for j in r['jobs']]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for j in r['jobs']:
            self.assertIn(j['level'], ('很适合', '可以考虑', '不太合适'))
            self.assertIn(j['nature'], WANT)

    # 8
    def test_08_dispatch_warning(self):
        """只要直签的人碰上劳务派遣岗，必须扣分并给出提醒。"""
        from ai_matcher import match_jobs
        p = profile(hireType='直签')
        job = {'title': '线路抢修工', 'company': '某电力公司', 'city': '杭州',
               'nature': '央企', 'hire_type': '劳务派遣', 'salary_text': '6000元',
               'exp': '经验不限', 'edu': '不限', 'tags': ['电力'], 'key': 'd1'}
        direct = dict(job, hire_type='直签', key='d2')
        r = match_jobs(p, [job, direct])
        bad = [j for j in r if j['hire_type'] == '劳务派遣'][0]
        good = [j for j in r if j['hire_type'] == '直签'][0]
        self.assertTrue(bad['warning'], '派遣岗位应给出提醒')
        self.assertIn('不是直接跟单位签合同', bad['warning'])
        self.assertLess(bad['score'], good['score'], '派遣应该比直签分低')

    # 8b
    def test_08b_nearby_jobs_ranked_below_local(self):
        """放宽到外地的岗位不该盖过本市的机会。"""
        from ai_matcher import match_jobs
        p = profile(city='武汉')
        local = {'title': '电力运维', 'company': '国网武汉供电', 'city': '武汉',
                 'nature': '央企', 'hire_type': '直签', 'salary_text': '6000元',
                 'exp': '经验不限', 'edu': '不限', 'tags': ['电力'], 'key': 'n1'}
        far = dict(local, city='西宁', company='国网青海', nearby=True, key='n2')
        r = match_jobs(p, [local, far])
        self.assertGreater(r[0]['score'], r[1]['score'])
        self.assertEqual(r[0]['key'], 'n1')

    # 9
    def test_09_apply_requires_jobs(self):
        r = self.c.post('/api/apply', json={'profile_id': 1, 'jobs': []})
        self.assertEqual(r.status_code, 400)

    # 10
    def test_10_apply_smtp_needs_email(self):
        """没公开邮箱的岗位不能邮件投递，会被标记并提示去官网。"""
        jobs = self.c.post('/api/jobs/search', json={'city': '北京'}).get_json()['jobs'][:2]
        # 采集源会从公告正文里挖出真实报名邮箱（gov_soe 的 _extract_apply_target），
        # 联网跑时拿到的岗位可能本来就带 hr_email。这个用例要验的是「没邮箱就不发」，
        # 所以先把邮箱清掉——断言不能取决于外网这次抓到了什么。
        for j in jobs:
            j.pop('hr_email', None)
        r = self.c.post('/api/apply', json={
            'profile_id': None, 'jobs': jobs, 'channel': 'smtp', 'profile': profile()
        }).get_json()
        tid = r['task_id']
        for _ in range(40):
            time.sleep(0.2)
            t = self.c.get('/api/task/%s' % tid).get_json()['task']
            if t['status'] != 'running':
                break
        self.assertEqual(t['done'], 2)
        self.assertEqual(t['success'], 0)
        self.assertIn('没有公开邮箱', t['items'][0]['status'])

    # 16
    def test_16_apply_smtp_with_email(self):
        """数据源提供了 hr_email 时，代发能正常发完。"""
        jobs = self.c.post('/api/jobs/search', json={'city': '北京'}).get_json()['jobs'][:2]
        for j in jobs:
            j['hr_email'] = 'hr@example.com'
        r = self.c.post('/api/apply', json={
            'profile_id': None, 'jobs': jobs, 'channel': 'smtp', 'profile': profile()
        }).get_json()
        tid = r['task_id']
        for _ in range(40):
            time.sleep(0.2)
            t = self.c.get('/api/task/%s' % tid).get_json()['task']
            if t['status'] != 'running':
                break
        self.assertEqual(t['status'], 'done')
        self.assertEqual(t['success'], 2)

    # 11
    def test_11_task_not_found(self):
        r = self.c.get('/api/task/nope')
        self.assertEqual(r.status_code, 404)

    # 12
    def test_12_records_saved(self):
        pid = self.c.post('/api/profile', json=profile(phone='13900005678')).get_json()['profile_id']
        jobs = self.c.post('/api/jobs/search', json={'city': '上海'}).get_json()['jobs'][:1]
        jobs[0]['hr_email'] = 'hr@example.com'
        r = self.c.post('/api/apply', json={
            'profile_id': pid, 'jobs': jobs, 'channel': 'smtp', 'profile': profile()
        }).get_json()
        tid = r['task_id']
        for _ in range(60):
            time.sleep(0.3)
            t = self.c.get('/api/task/%s' % tid).get_json()['task']
            if t['status'] != 'running':
                break
        recs = self.c.get('/api/records?profile_id=%s' % pid).get_json()['records']
        self.assertEqual(len(recs), 1)
        # 测试环境邮件是演练模式（见文件头 mailer.load_config 的替身），
        # 状态必须如实写「未真发」——以前这里断言成「已发送」，
        # 等于把「演练也显示成已投出去」这个谎报固化成了预期。
        self.assertNotEqual(recs[0]['status'], '已发送')
        self.assertIn('演练', recs[0]['status'])
        self.assertEqual(recs[0]['title'], jobs[0]['title'])
        self.assertEqual(recs[0]['channel'], 'smtp')

    # 13
    def test_13_mailto_channel(self):
        """留了邮箱的岗位能生成 mailto 链接，没留的标记出来。"""
        with_mail = dict(
            title='测试岗', company='测试央企', city='北京', nature='央企',
            salary_text='6000-8000元', hr_email='hr@example.com', key='t1')
        without = dict(
            title='无邮箱岗', company='测试外企', city='上海', nature='外企',
            salary_text='6000-8000元', key='t2')
        r = self.c.post('/api/apply', json={
            'profile_id': None, 'jobs': [with_mail, without],
            'channel': 'mailto', 'profile': profile()
        }).get_json()
        self.assertTrue(r['ok'])
        mails = r['mails']
        self.assertTrue(mails[0]['has_email'])
        self.assertTrue(mails[0]['mailto'].startswith('mailto:hr@example.com'))
        self.assertIn('应聘 测试岗', mails[0]['subject'])
        self.assertIn('张三', mails[0]['preview'])
        self.assertFalse(mails[1]['has_email'])

    # 14
    def test_14_applied_receipt(self):
        """跳转投递的回执要能记进投递记录。"""
        pid = self.c.post('/api/profile', json=profile(phone='13700001111')).get_json()['profile_id']
        job = {'title': '电力线路运维工', 'company': '国家电网', 'key': 'k1',
               'apply_url': 'https://www.iguopin.com'}
        r = self.c.post('/api/applied', json={
            'profile_id': pid, 'job': job, 'status': '已在官网投递'}).get_json()
        self.assertTrue(r['ok'])
        recs = self.c.get('/api/records?profile_id=%s' % pid).get_json()['records']
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['channel'], 'link')
        self.assertEqual(recs[0]['status'], '已在官网投递')

    # 15
    def test_15_jobs_have_apply_url_and_mail_status(self):
        """每个岗位都要有可跳转的投递入口（没有公开邮箱时这是唯一路径）。"""
        r = self.c.post('/api/jobs/search', json={'city': '北京'}).get_json()
        self.assertGreater(r['count'], 0)
        for j in r['jobs']:
            self.assertTrue(j.get('apply_url'), '%s 缺少投递入口' % j['title'])
        s = self.c.get('/api/mail/status').get_json()
        self.assertTrue(s['ok'])
        self.assertIn('dry_run', s)


    # 17 —— 央企识别 / 劳务派遣识别（纯本地逻辑，不联网）
    def test_17_nature_and_dispatch_rules(self):
        from collectors.guopin import _nature_of, DISPATCH_RE
        self.assertEqual(_nature_of('国家电网四川电力公司', '国企'), '央企')
        self.assertEqual(_nature_of('中能建（北京）绿色能源科技', '国企'), '央企')
        self.assertEqual(_nature_of('广州发展新能源集团', '国企'), '国企')
        self.assertEqual(_nature_of('特斯拉（上海）有限公司', '外商独资'), '外企')
        self.assertEqual(_nature_of('某某私营工厂', '民营企业'), '民营')
        self.assertTrue(DISPATCH_RE.search('录用后与第三方劳务派遣公司签订劳动合同'))
        self.assertFalse(DISPATCH_RE.search('与公司直接签订劳动合同，缴纳五险一金'))

    # 18 —— 国聘网原始报文 → 内部岗位结构（打桩，不联网）
    def test_18_guopin_convert(self):
        from collectors.guopin import GuopinCollector
        raw = {
            'job_id': '123', 'job_name': '电力线路运维工',
            'company_name': '国家电网四川省电力公司',
            'min_wage': 5000, 'max_wage': 8000, 'wage_unit_cn': '元/月',
            'experience_cn': '1-3年', 'education_cn': '中专',
            'category_cn': '运维', 'recruitment_type_cn': '社会招聘',
            'end_time': '2026-12-31 23:59:59', 'refresh_time': '2026-09-01 10:00:00',
            'contents': '负责线路巡检与抢修。',
            'district_list': [{'area_cn': '四川-成都市-武侯区'}],
            'company_info': {'nature_cn': '国企', 'industry_cn': '电力'},
        }
        job = GuopinCollector()._convert(raw)
        self.assertEqual(job['nature'], '央企')
        self.assertEqual(job['hire_type'], '直签')
        self.assertEqual(job['city'], '四川')
        self.assertEqual(job['district'], '成都市')
        self.assertEqual(job['salary_text'], '5000-8000元/月')
        self.assertEqual(job['apply_url'],
                         'https://www.iguopin.com/job/detail?id=123')
        self.assertEqual(job['source'], 'guopin')
        self.assertEqual(job['deadline'], '2026-12-31')
        self.assertEqual(job['apply_start'], '')
        self.assertIn('运维', job['tags'])

        # 正文出现劳务派遣 → 必须标出来并触发警告
        raw2 = dict(raw, contents='与第三方劳务派遣公司签订劳动合同')
        self.assertEqual(GuopinCollector()._convert(raw2)['hire_type'], '劳务派遣')

        # 报名起止也要带出来（国聘详情页上「报名开始/报名截止」就是这两个字段）
        raw3 = dict(raw, start_time='2026-09-16 00:00:00')
        self.assertEqual(GuopinCollector()._convert(raw3)['apply_start'], '2026-09-16')

    # 18b —— 报名窗口过滤：没开始/已截止的岗位不能推给用户
    def test_18b_guopin_apply_window(self):
        from collectors.guopin import _in_apply_window as win
        # 没到报名时间 → 拦下（用户实测：9-29 才开始报名，9-18 搜出来投不了）
        self.assertFalse(win('2026-09-29', '2026-09-30', today='2026-09-18'))
        # 已截止 → 拦下
        self.assertFalse(win('2026-08-01', '2026-09-10', today='2026-09-18'))
        # 窗口内 → 放行
        self.assertTrue(win('2026-09-16', '2026-09-30', today='2026-09-18'))
        # 起止缺一个不能误杀：只给了截止且没过 → 放行
        self.assertTrue(win('', '2026-12-31', today='2026-09-18'))
        self.assertTrue(win('2026-09-16', '', today='2026-09-18'))
        # 两样都没有 → 放行（别把没标时间的岗位全滤光）
        self.assertTrue(win('', '', today='2026-09-18'))

    # 18c —— 公告类来源的五花八门日期要能归一，出口统一按报名窗口过滤
    def test_18c_apply_window_filter(self):
        import app as appmod
        # 公告里常见的几种写法都要认得
        self.assertEqual(appmod._norm_date('2026-7-22'), '2026-07-22')
        self.assertEqual(appmod._norm_date('2026年7月22日'), '2026-07-22')
        self.assertEqual(appmod._norm_date('2026.07.22'), '2026-07-22')
        self.assertEqual(appmod._norm_date('报名截止：2026-07-21 17:30'), '2026-07-21')
        self.assertEqual(appmod._norm_date(''), '')
        # 出口过滤：已截止的剔掉、没开始的剔掉、好的留下
        jobs = [
            {'title': '已截止', 'deadline': '2026-07-21', 'apply_start': ''},
            {'title': '没开始', 'deadline': '2026-09-30', 'apply_start': '2099-01-01'},
            {'title': '能投', 'deadline': '2099-12-31', 'apply_start': ''},
            {'title': '只到月份不误杀', 'deadline': '2026-07', 'apply_start': ''},
        ]
        kept, dropped = appmod._filter_apply_window(jobs)
        self.assertEqual([j['title'] for j in kept], ['能投', '只到月份不误杀'])
        self.assertEqual(dropped, 2)

    # 19 —— 渠道全挂时静默降级，绝不让用户看到报错或空白
    def test_19_graceful_degradation(self):
        from collectors import net
        original = net.request
        def boom(*a, **k):
            raise RuntimeError('对方服务器炸了')
        net.request = boom
        try:
            from collectors import collect_jobs_with_report
            rep = collect_jobs_with_report(keyword='电力', city='成都', limit=20)
        finally:
            net.request = original
        self.assertIsInstance(rep['jobs'], list)
        self.assertGreater(len(rep['jobs']), 0)      # 兜底源顶上
        self.assertTrue(rep['fallback'])
        self.assertTrue(any(s['kind'] == 'fallback' for s in rep['sources']))

    # 20 —— 搜索接口要回传渠道健康状态，断了的源要能被看见
    def test_20_search_reports_sources(self):
        r = self.c.post('/api/jobs/search',
                        json={'keyword': '电力', 'city': '成都', 'limit': 20}).get_json()
        self.assertTrue(r['ok'])
        self.assertIn('sources', r)
        self.assertIn('relaxed', r)
        for s in r['sources']:
            self.assertIn('label', s)
            self.assertIn('ok', s)
        s = self.c.get('/api/sources').get_json()
        self.assertTrue(s['ok'])
        self.assertTrue(any(x['name'] == 'guopin' for x in s['sources']))

    # 21 —— 投过的岗位不能再推给同一个人（ai-job-search 的 seen_jobs 思路）
    def test_21_applied_jobs_not_shown_again(self):
        from collectors import collect_jobs_with_report
        rep = collect_jobs_with_report(keyword='电力', city='', limit=20)
        self.assertTrue(rep['jobs'])
        victim = rep['jobs'][0]

        # 精确键（同渠道）
        again = collect_jobs_with_report(keyword='电力', city='', limit=20,
                                         exclude={victim['key']})
        self.assertNotIn(victim['key'], [j['key'] for j in again['jobs']])

        # 公司|职位|城市（跨渠道撞车也算投过）
        loose = '%s|%s|%s' % (victim['company'], victim['title'], victim['city'])
        again2 = collect_jobs_with_report(keyword='电力', city='', limit=20,
                                          exclude={loose})
        self.assertNotIn(victim['key'], [j['key'] for j in again2['jobs']])

        # 真走一遍库：建档 -> 记一条投递 -> 再搜，应该看不见了
        pid = models.save_profile(profile(phone='13800005555'))
        models.save_application(pid, victim, 80, '已投递')
        keys = models.list_applied_keys(pid)
        self.assertIn(victim['key'], keys)
        r = self.c.post('/api/jobs/search',
                        json={'keyword': '电力', 'city': '', 'limit': 20,
                              'profile_id': pid}).get_json()
        self.assertNotIn(victim['key'], [j['key'] for j in r['jobs']])

    # 22 —— 渠道可以单独关掉，不用改代码（ai-job-search 的 enabled: 开关）
    def test_22_source_can_be_disabled(self):
        from collectors import _enabled, _active, LIVE_COLLECTORS, \
            collect_jobs_with_report, _OVERRIDE_PATH
        guopin = [c for c in LIVE_COLLECTORS if c.name == 'guopin'][0]
        self.assertTrue(_enabled(guopin))

        # 关掉所有联网真实源，必须自动落到兜底而不是空白。
        # 注：gov_soe（各省国企招聘公告源）也是真实源，2026-09-21 扩到 20+ 个源
        # 之后它能稳定抓到岗位，测试必须一并关掉它，否则这条前提不成立。
        os.makedirs(os.path.dirname(_OVERRIDE_PATH), exist_ok=True)
        had = os.path.exists(_OVERRIDE_PATH)
        old = open(_OVERRIDE_PATH, encoding='utf-8').read() if had else None
        try:
            with open(_OVERRIDE_PATH, 'w', encoding='utf-8') as f:
                json.dump({'guopin': False, 'mohrss': False, 'foreign': False,
                           'gov_soe': False}, f)
            self.assertFalse(_enabled(guopin))
            self.assertNotIn(guopin, _active(LIVE_COLLECTORS))
            rep = collect_jobs_with_report(keyword='电力', city='成都', limit=10)
            self.assertTrue(rep['fallback'])
            self.assertTrue(any(s['name'] == 'guopin' and not s['enabled']
                                for s in rep['sources']))
        finally:
            if old is None:
                if os.path.exists(_OVERRIDE_PATH):
                    os.remove(_OVERRIDE_PATH)
            else:
                with open(_OVERRIDE_PATH, 'w', encoding='utf-8') as f:
                    f.write(old)


    # 23 —— 外企官网直招采集器：把亚马逊中国开放接口转成内部岗位结构
    def test_23_foreign_collector(self):
        from collectors import net
        from collectors.foreign import ForeignCollector
        original = net.request
        def fake(url, **k):
            # 模拟 amazon.jobs 的开放 JSON：无鉴权、返回中国岗位
            # 翻到第二页（offset>=100）就返回空，模拟"到底即停"
            import urllib.parse as _up
            qs = _up.urlparse(url).query
            off = int(_up.parse_qs(qs).get('offset', ['0'])[0])
            if off >= 100:
                return {'hits': 2, 'jobs': []}
            return {
                'hits': 2,
                'jobs': [
                    {
                        'id_icims': 'A1', 'title': 'Warehouse Associate',
                        'company_name': 'Amazon (China)',
                        'city': 'Shanghai', 'state': '上海',
                        'description_short': '负责仓库拣货与发货，<b>不需要经验</b>',
                        'job_path': '/en/jobs/a1', 'url_next_step': 'https://www.amazon.jobs/en/jobs/a1/apply',
                        'job_category': 'Operations', 'business_category': 'Retail',
                        'job_schedule_type': 'Full-Time', 'posted_date': 'September 16, 2026',
                    },
                    {
                        'id_icims': 'A2', 'title': '客户专员',
                        'company_name': 'Amazon (China)',
                        'city': 'Chengdu', 'state': '四川',
                        'description_short': '处理客户咨询，中文岗位名',
                        'job_path': '/en/jobs/a2', 'url_next_step': '',
                        'job_category': 'Customer Service', 'business_category': '',
                        'job_schedule_type': 'Full-Time', 'posted_date': 'August 1, 2026',
                    },
                ],
            }
        net.request = fake
        try:
            c = ForeignCollector()
            jobs = c.fetch(keyword='', city='', limit=10, natures=['外企'])
        finally:
            net.request = original

        self.assertEqual(len(jobs), 2)
        a1 = [j for j in jobs if j['title'] == 'Warehouse Associate'][0]
        # 英文城市名要能映射到中文
        self.assertEqual(a1['city'], '上海')
        self.assertEqual(a1['nature'], '外企')              # 外企官网直招，性质确定
        self.assertEqual(a1['hire_type'], '直签')           # 官网直招默认直签
        self.assertEqual(a1['apply_url'],
                         'https://www.amazon.jobs/en/jobs/a1/apply')
        self.assertEqual(a1['published'], '2026-09-16')
        self.assertIn('Operations', a1['tags'])
        # 第二个中文岗位
        a2 = [j for j in jobs if j['title'] == '客户专员'][0]
        self.assertEqual(a2['city'], '成都')
        # apply_url 缺省时回退到职位详情页
        self.assertEqual(a2['apply_url'], 'https://www.amazon.jobs/en/jobs/a2')

    # 24 —— 外企通道只在用户勾选了外企/合资时才触发，别白花网络开销
    def test_24_foreign_only_when_wanted(self):
        from collectors.foreign import ForeignCollector
        c = ForeignCollector()
        # 只勾央企国企 → 不请求外企接口，直接返回空
        self.assertEqual(c.fetch(natures=['央企', '国企']), [])
        # 勾了外企 → 会尝试请求（这里不联网，只看它不早退）
        self.assertIsInstance(c.fetch(natures=['外企']), list)

    # 25 —— 外企通道也要服从 data/sources.json 的关闭开关
    def test_25_foreign_can_be_disabled(self):
        from collectors import _enabled, LIVE_COLLECTORS
        foreign = [c for c in LIVE_COLLECTORS if c.name == 'foreign'][0]
        self.assertTrue(_enabled(foreign))
        old = None
        _OVERRIDE_PATH = None
        import collectors as C
        _OVERRIDE_PATH = C._OVERRIDE_PATH
        try:
            if os.path.exists(_OVERRIDE_PATH):
                old = open(_OVERRIDE_PATH, 'r', encoding='utf-8').read()
            with open(_OVERRIDE_PATH, 'w', encoding='utf-8') as f:
                import json as _j
                _j.dump({'foreign': False}, f)
            self.assertFalse(_enabled(foreign))
        finally:
            if old is None:
                if os.path.exists(_OVERRIDE_PATH):
                    os.remove(_OVERRIDE_PATH)
            else:
                with open(_OVERRIDE_PATH, 'w', encoding='utf-8') as f:
                    f.write(old)

    # 26 —— 央企 vs 地方国企识别：接口不区分，靠集团名录子串命中
    def test_26_central_vs_local_soe(self):
        from collectors.guopin import _nature_of
        # 子公司名带着集团核心名，应识别为央企
        self.assertEqual(_nature_of('国家电投集团山东能源发展有限公司', '国企'), '央企')
        self.assertEqual(_nature_of('国家电网四川省电力公司', '国企'), '央企')
        self.assertEqual(_nature_of('中国建筑第八工程局有限公司', '国企'), '央企')
        # 广州市属国企，不在央企名录里，只标国企
        self.assertEqual(_nature_of('广州发展新能源集团股份有限公司', '国企'), '国企')
        self.assertEqual(_nature_of('杭州城投建设有限公司', '国企'), '国企')
        # 民营单位
        self.assertEqual(_nature_of('某某科技有限公司', '民营企业'), '民营')

    # 27 —— 招聘人数、校招/社招字段要能采到
    def test_27_guopin_headcount_and_recruit_type(self):
        from collectors.guopin import GuopinCollector
        raw = {
            'job_id': '999', 'job_name': '电气工程师', 'company_name': '国家电网XX公司',
            'min_wage': 8000, 'max_wage': 12000, 'wage_unit_cn': '元/月',
            'experience_cn': '3-5年', 'education_cn': '本科', 'category_cn': '技术',
            'recruitment_type_cn': '校园招聘', 'amount': 4,
            'end_time': '2026-12-01 23:59:59', 'refresh_time': '2026-09-10 10:00:00',
            'contents': '负责电气运维',
            'district_list': [{'area_cn': '北京-北京市-海淀区'}],
            'company_info': {'nature_cn': '国企', 'industry_cn': '电力'},
        }
        job = GuopinCollector()._convert(raw)
        self.assertEqual(job['headcount'], 4)
        self.assertEqual(job['recruit_type'], '校园招聘')
        self.assertEqual(job['nature'], '央企')        # 国家电网 → 央企
        self.assertEqual(job['salary_text'], '8000-12000元/月')


    # 28 —— 中国公共招聘网采集器：报文藏在 HTML 的 findjoblist 里，且只收央企/国企
    def test_28_mohrss_collector(self):
        from collectors import net
        from collectors.mohrss import MohrssCollector
        import json as _json, html as _html

        def make_html(items):
            val = _html.escape(_json.dumps(items))       # " 会被转成 &quot;
            return '<div><input id="findjoblist" value="%s"></div>' % val

        items = [
            {  # 国家电网子公司 → 央企
                'aca112': '电气工程师', 'aab004': '国家电网XX省电力公司',
                'acb241': 8000, 'acb242': 12000, 'area_': '北京市', 'aab302': '海淀区',
                'acb22a': '负责变电站运维', 's_aae398': '2026-12-01', 'acb200': 111,
                'org_': '北京市人社局', 'aca111_': '电力工程',
            },
            {  # 地方城投水务 → 国企
                'aca112': '水务巡检员', 'aab004': '杭州市城投集团水务有限公司',
                'acb241': 5000, 'acb242': 7000, 'area_': '浙江省', 'aab302': '杭州市',
                'acb22a': '负责供水管网巡检', 's_aae398': '2026-11-01', 'acb200': 222,
                'org_': '浙江省人社厅', 'aca111_': '水务',
            },
            {  # 劳务派遣岗位 → 标出来
                'aca112': '保洁员', 'aab004': '上海市城投环境服务有限公司',
                'acb241': 3000, 'acb242': 4000, 'area_': '上海市', 'aab302': '浦东新区',
                'acb22a': '录用后与第三方劳务派遣公司签订劳动合同', 's_aae398': '2026-10-01',
                'acb200': 333, 'org_': '上海市人社局', 'aca111_': '保洁',
            },
            {  # 纯民企 → 标「民营」并带上电话，不再丢弃
                # 2026-09-21 修正：实测这个源 97% 是民企，老逻辑「识别不出就丢弃」
                # 把整个民企池子关在了门外。底线仍在（不许谎标成国企），
                # 但做法是如实标民营，而不是删掉。
                'aca112': '叉车工', 'aab004': '黑龙江省万里润达生物科技有限公司',
                'acb241': 2000, 'acb242': 5000, 'area_': '黑龙江省', 'aab302': '宝清县',
                'acb22a': '须持叉车证', 's_aae398': '2026-10-15', 'acb200': 444,
                'org_': '黑龙江省人社厅', 'aca111_': '制造',
                'aae004': '王师傅', 'aae005': '13912345678',
                'acb202': '黑龙江省双鸭山市宝清县',
            },
            {  # 第二页返回空 → 模拟「翻完即停」
            },
        ]

        calls = {'n': 0}
        original = net.request

        def fake(url, **k):
            calls['n'] += 1
            # 第一页给数据，第二页（pageNo=2）给空盒子，模拟到底
            if 'pageNo=1' in url:
                return make_html(items[:4])
            return make_html([])

        net.request = fake
        try:
            c = MohrssCollector()
            jobs = c.fetch(keyword='', city='', limit=20, natures=['央企', '国企'])
        finally:
            net.request = original

        # 4 条全部保留（民企不再被丢弃）
        self.assertEqual(len(jobs), 4)
        by_title = {j['title']: j for j in jobs}
        self.assertIn('电气工程师', by_title)
        self.assertIn('水务巡检员', by_title)
        self.assertIn('保洁员', by_title)
        self.assertIn('叉车工', by_title)

        elec = by_title['电气工程师']
        self.assertEqual(elec['nature'], '央企')        # 国家电网 → 央企
        self.assertEqual(elec['city'], '北京市')
        self.assertEqual(elec['salary_text'], '8000-12000元/月')
        self.assertEqual(elec['apply_url'],
                         'http://job.mohrss.gov.cn/cjobs/jobinfolist/cb21/showgw?id=111')
        self.assertEqual(elec['source'], 'mohrss')

        water = by_title['水务巡检员']
        self.assertEqual(water['nature'], '国企')        # 城投水务 → 国企

        clean = by_title['保洁员']
        self.assertEqual(clean['hire_type'], '劳务派遣')  # 正文命中派遣 → 标出来

        # 民企这条是本轮修正的重点：必须被保留、必须如实标民营、
        # 且电话直接从列表报文 aae005 拿到（不用抓详情页）
        fork = by_title['叉车工']
        self.assertEqual(fork['nature'], '民营')   # 绝不会被谎标成国企
        self.assertEqual(fork['hr_phone'], '13912345678')
        self.assertIn('王师傅', fork['desc'])      # 联系人姓名带进正文，打电话知道找谁

        # 翻页：第 1 页有数据；后续空页会退避重试，连续两页空才停，
        # 请求次数有上限、绝不会死循环（这里断言它被 MAX_PAGE 封顶）。
        # 另：每条过审岗位至多再抓一次详情页提取报名邮箱（2026-09 起邮箱
        # 从详情页来），所以总数 = 列表封顶 + 岗位数，依然有界不死循环。
        from collectors.mohrss import MAX_PAGE
        self.assertLessEqual(calls['n'], 2 * MAX_PAGE + 1 + len(jobs))

    # 29 —— 这个源现在服务于民营（aab019=10 是「企业」大类，97% 是民企）
    def test_29_mohrss_serves_private_firms(self):
        from collectors.mohrss import MohrssCollector
        from collectors import net
        c = MohrssCollector()
        # 事业单位 / 公务员不在这个源的服务范围 → 连网都不用上，直接空
        self.assertEqual(c.fetch(natures=['事业单位']), [])
        self.assertEqual(c.fetch(natures=['公务员']), [])
        # 民营必须放行 —— 老版本 WANTED 只有央企/国企，用户勾「民营」时这个源
        # 压根不请求，等于把最大的那块池子锁死。
        self.assertIn('民营', c.WANTED)
        self.assertIn('央企', c.WANTED)
        self.assertIn('国企', c.WANTED)
        # 合法性自检：net.request 必须被 import 到，避免坏引用
        self.assertTrue(hasattr(net, 'request'))

    # 30 —— 有关键词时必须带上 textfield 做全文检索，而不是盲扫全量企业库
    def test_30_mohrss_uses_textfield(self):
        from collectors import net
        from collectors.mohrss import MohrssCollector
        seen = []
        original = net.request

        def fake(url, **k):
            seen.append(url)
            # 返回空盒子，让采集器立刻停，省得翻页
            return '<input id="findjoblist" value="[]">'

        net.request = fake
        try:
            MohrssCollector().fetch(keyword='电力', city='', limit=5,
                                    natures=['央企', '国企'])
        finally:
            net.request = original
        self.assertTrue(any('textfield=' in u for u in seen),
                        '有关键词时应把 textfield 带上做全文检索')
        # 无关键词时不应带 textfield（走全量企业层）
        seen.clear()
        net.request = fake
        try:
            MohrssCollector().fetch(keyword='', city='', limit=5,
                                    natures=['央企', '国企'])
        finally:
            net.request = original
        self.assertFalse(any('textfield=' in u for u in seen),
                         '无关键词时不应带 textfield')

    # 31 —— 地方国资委公告采集器：解析 + 国企过滤 + 央企识别 + 城市抽取
    def test_31_gov_soe_collector(self):
        from collectors import net
        from collectors.gov_soe import GovSoeCollector

        list_html = (
            '<html><body>'
            '<a href="https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_1.html">'
            '【国企招聘】广物控股集团公开招聘公告</a>'
            '<a href="https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_2.html">'
            '【国企招聘】广州白云国际机场股份有限公司2026社招</a>'
            '<a href="https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_3.html">'
            '深圳市国资系统召开三季度工作会议</a>'          # 纯新闻，无招聘 → 丢弃
            '<a href="https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_4.html">'
            '【国企招聘】中国航天科技集团某院所招聘</a>'
            '</body></html>'
        )

        def art(title, company, date, signal='招聘'):
            return ('<html><head><title>%s</title></head><body>'
                    '<div class="content">%s公告：%s，发布时间：%s，工作地点广州。</div>'
                    '</body></html>' % (title, company, signal, date))

        articles = {
            'https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_1.html':
                art('广物控股集团招聘', '广物控股集团', '2026-09-10'),
            'https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_2.html':
                art('白云机场招聘', '广州白云国际机场股份有限公司', '2026-09-12'),
            'https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_3.html':
                art('工作会议', '深圳市国资委', '2026-09-01', signal='会议'),
            'https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_4.html':
                art('航天科技招聘', '中国航天科技集团', '2026-09-08'),
        }

        original = net.request

        def fake(url, **k):
            if 'gwkgjt/' in url and 'content/post' not in url:
                return list_html
            return articles.get(url, '')

        net.request = fake
        try:
            jobs = GovSoeCollector().fetch(keyword='', city='', limit=20,
                                          natures=['央企', '国企'])
        finally:
            net.request = original

        self.assertEqual(len(jobs), 3)                    # 工作会议被过滤
        by_company = {j['company']: j for j in jobs}
        self.assertIn('广物控股集团', by_company)
        self.assertIn('广州白云国际机场股份有限公司', by_company)
        self.assertIn('中国航天科技集团', by_company)
        self.assertEqual(by_company['中国航天科技集团']['nature'], '央企')
        self.assertEqual(by_company['广物控股集团']['nature'], '国企')
        self.assertEqual(by_company['广物控股集团']['deadline'], '2026-09-10')
        self.assertEqual(by_company['广物控股集团']['city'], '广州')  # 散文命中城市
        self.assertEqual(by_company['广物控股集团']['source'], 'gov_soe')

    # 32 —— 地方国资委源只认央企/国企，外企等性质不触发联网
    def test_32_gov_soe_only_for_central_state(self):
        from collectors.gov_soe import GovSoeCollector
        self.assertEqual(GovSoeCollector().fetch(natures=['外企']), [])

    # 33 —— 配置驱动：按城市筛选地区。
    #   选了省份下辖的「市」（如深圳∈广东）应匹配该源；选了毫不相干的省/市才跳过。
    def test_33_gov_soe_config_filters_by_city(self):
        from collectors import net
        from collectors.gov_soe import GovSoeCollector
        list_html = ('<a href="https://gzw.gd.gov.cn/qydt/gwkgjt/content/post_1.html">'
                     '【国企招聘】广物控股集团公开招聘公告</a>')
        original = net.request
        net.request = lambda url, **k: list_html
        try:
            c = GovSoeCollector()
            # 指定「深圳」（属于广东）→ 该源不被跳过，能拉到数据
            jobs = c.fetch(keyword='', city='深圳', limit=10,
                           natures=['央企', '国企'])
            self.assertIsInstance(jobs, list)
            self.assertGreater(len(jobs), 0)
            # 指定「北京」（与广东无关）→ 该源整体跳过，返回空
            net.request = lambda url, **k: list_html
            self.assertEqual(c.fetch(keyword='', city='北京', limit=10,
                                    natures=['央企', '国企']), [])
            # 城市为空（全国放宽）→ 正常拉取
            net.request = lambda url, **k: list_html
            jobs = c.fetch(keyword='', city='', limit=10, natures=['央企', '国企'])
            self.assertIsInstance(jobs, list)
        finally:
            net.request = original

    # 34 —— 湖北源（华图国企招聘频道）：huatu 风格列表 + 公告解析 + 噪音过滤
    def test_34_gov_soe_hubei_huatu(self):
        from collectors import net
        from collectors.gov_soe import GovSoeCollector

        list_html = (
            '<html><body>'
            '<a href="https://hb.huatu.com/guoqizp/1878682.html">'
            '黄石长乐投资发展有限公司2026年公开招聘工作人员公告(五)</a>'
            '<a href="https://hb.huatu.com/guoqizp/1878314.html">'
            '湖北交通投资集团有限公司三季度社会招聘公告【27人】</a>'
            '<a href="https://hb.huatu.com/guoqizp/1878375.html">'
            '2026年钟祥市国有企业公开招聘工作人员面试成绩公告</a>'     # 非招聘→丢弃
            '<a href="https://hb.huatu.com/guoqizp/1878448.html">'
            '武汉大学出版社2026年招聘启事【5人】</a>'                 # 无名企主体→丢弃
            '</body></html>'
        )

        def art(company, date, city):
            return ('<html><head><title>%s招聘</title></head><body>'
                    '<div>%s公开招聘，发布时间：%s，工作地点%s。</div>'
                    '</body></html>' % (company, company, date, city))

        articles = {
            'https://hb.huatu.com/guoqizp/1878682.html':
                art('黄石长乐投资发展有限公司', '2026-09-12', '黄石'),
            'https://hb.huatu.com/guoqizp/1878314.html':
                art('湖北交通投资集团有限公司', '2026-09-10', '武汉'),
        }

        original = net.request

        def fake(url, **k):
            if 'guoqizp/zhaopin' in url:
                return list_html
            return articles.get(url, '')

        net.request = fake
        try:
            jobs = GovSoeCollector().fetch(keyword='', city='', limit=20,
                                          natures=['央企', '国企'])
        finally:
            net.request = original

        self.assertEqual(len(jobs), 2)                    # 面试成绩/出版社被过滤
        by_company = {j['company']: j for j in jobs}
        self.assertIn('黄石长乐投资发展有限公司', by_company)
        self.assertIn('湖北交通投资集团有限公司', by_company)
        self.assertEqual(by_company['黄石长乐投资发展有限公司']['nature'], '国企')
        self.assertEqual(by_company['黄石长乐投资发展有限公司']['city'], '黄石')  # 散文命中城市
        self.assertEqual(by_company['黄石长乐投资发展有限公司']['source'], 'gov_soe')
        # 选「武汉」应匹配湖北源（武汉属湖北），不整源跳过
        net.request = fake
        try:
            jobs2 = GovSoeCollector().fetch(keyword='', city='武汉', limit=20,
                                           natures=['央企', '国企'])
        finally:
            net.request = original
        self.assertEqual(len(jobs2), 2)

    # 35 —— net 层应识别 gb2312/gbk 页面编码，避免中文站解码成乱码
    def test_35_net_detects_gb2312(self):
        from collectors import net

        class _FakeResp:
            def __init__(self, headers):
                self.headers = headers

        # Content-Type 头声明 gb2312
        self.assertEqual(
            net._detect_charset(_FakeResp({'Content-Type': 'text/html; charset=gb2312'}),
                                b'<html>'), 'gb18030')
        # 仅 <meta charset=gbk>
        self.assertEqual(
            net._detect_charset(_FakeResp({}),
                                '<meta charset="gbk">'.encode('latin-1')), 'gb18030')
        # 无声明 → 默认 utf-8
        self.assertEqual(net._detect_charset(_FakeResp({}), b'<html>'), 'utf-8')

    # 36 —— 字段识别：表格布局、placeholder、英文 name 三条线索都要认得出
    def test_36_match_field_clues(self):
        from autofill import _match_field

        def meta(label='', ph='', name='', id_='', cls=''):
            return {'label': label, 'ph': ph, 'name': name, 'id': id_, 'cls': cls}

        # 老式政府网站：字段名在左边的表格格里，没有 label
        self.assertEqual(_match_field(meta(label='姓名*')), 'name')
        self.assertEqual(_match_field(meta(label='手机号码*')), 'phone')
        self.assertEqual(_match_field(meta(label='毕业院校')), 'school')
        self.assertEqual(_match_field(meta(label='所学专业')), 'major')
        self.assertEqual(_match_field(meta(label='期望工作城市')), 'city')
        # placeholder 兜底
        self.assertEqual(_match_field(meta(ph='email@example.com')), 'email')
        self.assertEqual(_match_field(meta(ph='11位手机号')), 'phone')
        # 英文 name/id 兜底
        self.assertEqual(_match_field(meta(name='mobile')), 'phone')
        self.assertEqual(_match_field(meta(name='realname')), 'name')
        # 验证码的优先级要压过其它所有规则
        self.assertEqual(_match_field(meta(label='图形验证码*')), '_captcha')
        self.assertEqual(_match_field(meta(label='短信验证码')), '_captcha')
        # 认不出来要老实返回 None，不能猜
        self.assertIsNone(_match_field(meta(label='入党时间')))

    # 37/38 原「模拟靶场」用例已随靶场一起下线——靶场是练手用的假招聘站，
    # 现在自动填表直接对真实投递页，链路由真实站点验证（2026-09-18 国聘实测）。

    # 39 —— 自动填表接口契约：没简历不让开，任务查得到，乱答题不收
    def test_39_autofill_api(self):
        c = app.test_client()
        # 只给了手机号、没给姓名 → 不让开
        r = c.post('/api/autofill/start', json={
            'job': {}, 'profile': {'phone': '13800138000'},
            'url': 'https://example.com/apply'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('简历', r.get_json()['msg'])

        # 没有投递页 → 也不让开
        r = c.post('/api/autofill/start', json={
            'job': {}, 'profile': {'name': '张三'}, 'url': ''})
        self.assertEqual(r.status_code, 400)

        self.assertEqual(c.get('/api/autofill/task/nope').status_code, 404)
        r = c.post('/api/autofill/answer', json={'task_id': 'nope', 'value': '1'})
        self.assertFalse(r.get_json()['ok'])

    # 40 —— 等人介入的状态机：问了才收答案，收完接着跑
    def test_40_ask_human_gate(self):
        import threading
        import autofill

        t = autofill.Task('t-ask', {}, {'name': '张三'}, 'http://x')
        autofill.TASKS['t-ask'] = t
        try:
            # 没人在等的时候，答案不能被塞进去（否则前端乱发就能跳过人工环节）
            self.assertFalse(autofill.submit_answer('t-ask', '123'))
            self.assertFalse(autofill.submit_answer('不存在', '123'))

            got = {}

            def worker():
                got['v'] = t.ask_human('sms', '看手机', '把验证码填进来', timeout=10)

            th = threading.Thread(target=worker)
            th.start()
            for _ in range(60):
                if t.status == 'need_human':
                    break
                time.sleep(0.05)
            self.assertEqual(t.status, 'need_human')
            self.assertEqual(t.ask['type'], 'sms')

            self.assertTrue(autofill.submit_answer('t-ask', '654321'))
            th.join(timeout=5)
            self.assertEqual(got['v'], '654321')
            self.assertEqual(t.status, 'running')      # 收完继续干
            self.assertIsNone(t.ask)
        finally:
            del autofill.TASKS['t-ask']

    # 42 —— 取消：用户在等人那一步直接关掉页面，这一单必须立刻收工，
    # 不能攥着浏览器干等到超时（否则「开始自动填」会长时间点不动）
    def test_42_cancel_wakes_waiter(self):
        import threading
        import autofill

        t = autofill.Task('t-cancel', {}, {}, 'http://x')
        autofill.TASKS['t-cancel'] = t
        try:
            self.assertFalse(autofill.cancel('不存在'))
            out = {}

            def worker():
                try:
                    t.ask_human('sms', '看手机', '把验证码填进来', timeout=30)
                    out['r'] = 'returned'
                except autofill._Cancelled:
                    out['r'] = 'cancelled'

            th = threading.Thread(target=worker)
            th.start()
            for _ in range(60):
                if t.status == 'need_human':
                    break
                time.sleep(0.05)
            self.assertEqual(t.status, 'need_human')

            self.assertTrue(autofill.cancel('t-cancel'))
            th.join(timeout=5)
            self.assertFalse(th.is_alive(), '取消后 ask_human 要马上返回，不能等满 30 秒')
            self.assertEqual(out['r'], 'cancelled')
        finally:
            autofill.TASKS.pop('t-cancel', None)

    # 43 —— 新单开跑，旧单请下场；已经结束的单不用动
    def test_43_new_task_cancels_old(self):
        import autofill

        old = autofill.Task('t-old', {}, {}, 'http://x')
        old.status = 'need_human'
        done = autofill.Task('t-done', {}, {}, 'http://x')
        done.status = 'done'
        autofill.TASKS['t-old'] = old
        autofill.TASKS['t-done'] = done
        try:
            self.assertEqual(autofill.cancel_all(), 1)
            self.assertTrue(old.cancelled)
            self.assertTrue(old._ev.is_set())        # 睡在等人的那一步要被叫醒
            self.assertFalse(done.cancelled)
            self.assertFalse(autofill.cancel('t-done'))   # 已经结束的单没什么可取消
        finally:
            autofill.TASKS.pop('t-old', None)
            autofill.TASKS.pop('t-done', None)

    # ------------------------------------------------ 传简历自动填（第 1 步）
    # 44 —— docx 用标准库就能拆（它就是 zip + XML），不引第三方库
    def test_44_docx_extract(self):
        import io
        import zipfile
        import xml.sax.saxutils as su
        import resume_parser as rp

        paras = ['姓名：周八', '手机：13500007777', '学历：高中', '有叉车证，能上夜班']
        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        body = ''.join(
            '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % su.escape(p)
            for p in paras)
        doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('word/document.xml', doc)

        text, err = rp.extract_text('周八.docx', buf.getvalue())
        self.assertEqual(err, '')
        self.assertIn('周八', text)
        self.assertIn('13500007777', text)

        f = rp.parse_fields(text)['fields']
        self.assertEqual(f['name'], '周八')
        self.assertEqual(f['phone'], '13500007777')
        self.assertEqual(f['edu'], '高中中专')
        self.assertEqual(f['skills'], ['有叉车证', '能上夜班'])

    # 45 —— 字段提取：各种中文排版都要认出来，认出来的值必须是对的
    def test_45_parse_fields(self):
        import resume_parser as rp

        # 带标签的规范简历：手机号带横线、全角数字、学历取最高
        t = ('个 人 简 历\n姓名：张三\n性别：男  年龄：24\n'
             '手机：138-0013-8000  邮箱：zhangsan@example.com\n'
             '期望城市：武汉\n学历：本科  毕业院校：华中科技大学\n'
             '工作经历\n2021.07-2023.06 武汉某某电气有限公司 电工\n'
             '自我评价\n在工厂做过 3 年电工，有电工证，能上夜班。\n')
        f = rp.parse_fields(t)['fields']
        self.assertEqual(f['name'], '张三')
        self.assertEqual(f['phone'], '13800138000')          # 横线要去掉
        self.assertEqual(f['email'], 'zhangsan@example.com')
        self.assertEqual(f['age'], '24')
        self.assertEqual(f['edu'], '本科及以上')
        self.assertEqual(f['city'], '武汉')
        self.assertEqual(f['skills'], ['有电工证', '能上夜班'])
        self.assertIn('电工', f['intro'])

        # 全角数字 + 没有姓名标签 → 名字从第一行猜，要标出来让人核对
        t2 = '李四\n电话 １３５００００１２３４\n邮箱 lisi@qq.com\n２８岁\n' \
             '大专学历，会开车，有叉车证\n求职意向：苏州\n'
        r2 = rp.parse_fields(t2)
        self.assertEqual(r2['fields']['phone'], '13500001234')
        self.assertEqual(r2['fields']['age'], '28')
        self.assertEqual(r2['fields']['edu'], '大专')
        self.assertEqual(r2['fields']['city'], '苏州')
        self.assertIn('会开车', r2['fields']['skills'])
        self.assertIn('name', r2['low'])                     # 猜出来的名字要提示核对

        # 应届生 → 没经验；硕士按本科及以上算
        f3 = rp.parse_fields('姓名：王五\n手机：18912345678\n学历：硕士\n应届毕业生\n')['fields']
        self.assertEqual(f3['exp'], '没经验')
        self.assertEqual(f3['edu'], '本科及以上')

        # 明确写了年限才认年限
        self.assertEqual(rp.parse_fields('工作经验：4年')['fields']['exp'], '3-5年')

    # 46 —— 认不出来就返回空，绝不猜错值（猜错比不填严重得多）
    def test_46_never_guesses(self):
        import resume_parser as rp

        # 「北京大学」里有「北京」，但那是学校不是期望城市 —— 不许当城市填进去
        f = rp.parse_fields('姓名：赵六\n手机：13300001111\n毕业院校：北京大学\n')['fields']
        self.assertNotIn('city', f)

        # 只有工作经历的起止年份时，不许推算年限（2021-2023 是 2 年，按最早那年算会变成 5 年）
        f2 = rp.parse_fields('工作经历\n2021.07-2023.06 某公司 电工\n')['fields']
        self.assertNotIn('exp', f2)

        # 什么都没有的文本 → 一个字段都不给
        self.assertEqual(rp.parse_fields('什么都没有\n')['fields'], {})
        self.assertEqual(rp.parse_fields('')['fields'], {})

    # 62 —— 工作经历：投国聘「工作/实习经历」段唯一的数据来源
    def test_62_work_experiences(self):
        """简历里明明写着公司、职位、起止年月，解析器以前一个字都不认。

        后果不是「少填一项」：国聘投递前要补全站内简历，工作经历是必填段，
        档案里没有就只能停下来让用户一条条手打。用户 2026-09-20 上传简历后
        问「为什么依然找不到工作经历的填写」，根子就在这。
        """
        import resume_parser as rp

        # 双栏 PDF 抽出来的行序：左栏那列时间整列跑到标题前面，公司名在后面。
        # 所以两边各自按出现顺序抽，数量相等才按顺序配对。
        pdf_like = (
            '个人优势\n'
            '干了十年游戏。2023.01-至今 2020.04-2022.12\n'
            '工作经历\n'
            '江西蜂鸟供应链管理有限公司 外卖\n'
            '送外卖\n'
            '长沙奇趣少年网络科技有限公司 创始人\n'
            '内容: 负责公司产品的立项和策划\n'
            '教育经历\n'
            '中南财经政法大学武汉学院 本科 平面设计 2007-2011\n'
        )
        w = rp.parse_fields(pdf_like)['fields'].get('experiences')
        self.assertTrue(w, 'PDF 那种排版的工作经历没认出来')
        self.assertEqual(len(w), 2, '多认或少认了一段：%r' % [e['company'] for e in w])
        self.assertEqual(w[0]['company'], '江西蜂鸟供应链管理有限公司')
        self.assertEqual(w[0]['role'], '外卖')
        self.assertEqual(w[0]['start'], '2023-01')
        self.assertEqual(w[0]['end'], '', '「至今」不该替他填结束时间')
        self.assertEqual(w[1]['company'], '长沙奇趣少年网络科技有限公司')
        self.assertEqual((w[1]['start'], w[1]['end']), ('2020-04', '2022-12'))
        # 教育行压在工作段尾部（在「教育经历」标题上面），不能算成上一段的工作内容
        self.assertNotIn('中南财经政法大学', w[1]['desc'])
        self.assertNotIn('本科', w[1]['desc'])
        # 「负责公司产品的立项和策划」里有「公司」两字，但它不是公司名
        for e in w:
            self.assertFalse(e['company'].startswith('负责'), e['company'])
        # 认出来的年份要标成「核对一下」——PDF 行序不保证规整
        self.assertIn('experiences', rp.parse_fields(pdf_like)['low'])

    # 63 —— 工作经历的两处「宁可少填」
    def test_63_work_experiences_never_guess(self):
        import resume_parser as rp

        # 只有「实习/实践」段、没有「工作经历」标题 → 一个字都不给。
        # 把实习写成正式工作，等于替用户夸大经历。
        intern_only = ('实习/实践\n'
                       '2024.07-2025.01 中国人寿保险股份有限公司 营销部 | 销售\n')
        self.assertNotIn('experiences', rp.parse_fields(intern_only)['fields'])

        # 时间条数跟公司条数对不上 → 老实只给公司/职位，时间留空让人自己填。
        # 按顺序硬配会把 A 公司的年份配到 B 公司头上，比空着严重得多。
        mismatched = ('工作经历\n'
                      '某某科技有限公司 电工\n'
                      '某某机械有限公司 装配工\n'
                      '2021.07-2023.06\n')
        ws = rp.parse_fields(mismatched)['fields']['experiences']
        self.assertEqual(len(ws), 2)
        self.assertEqual((ws[0]['start'], ws[0]['end']), ('', ''),
                         '时间对不上就该留空，不能硬配')
        self.assertEqual(ws[1]['company'], '某某机械有限公司')

    # 64 —— 工作经历要能一路走到投递：档案里的 work 段得被「工作/实习经历」用上
    def test_64_autofill_uses_work_experience(self):
        import autofill

        p = {'experiences': json.dumps([
            {'type': 'work', 'company': '某某科技有限公司', 'role': '电工',
             'start': '2021-07', 'end': '2023-06', 'desc': '负责设备检修'},
            {'type': 'intern', 'company': '某实习单位', 'role': '助理',
             'start': '2020-07', 'end': '2020-09', 'desc': ''},
        ])}
        exps = autofill._profile_experiences(p)
        works = [e for e in exps if e.get('type') == 'work']
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0]['company'], '某某科技有限公司')
        # 正式工作要排在实习前面（站点那一段叫「工作/实习经历」，首选工作）
        self.assertEqual((works + [e for e in exps if e.get('type') == 'intern'])[0]['type'],
                         'work')

    # 65 —— 只勾「央企+国企」，列表里就不该出现事业单位 / 公务员
    def test_65_nature_field_name(self):
        """档案里这个字段叫单数 nature（profiles 表列名、前端 store 都用它）。

        后端读成复数 natures 不会报错，只会悄悄读成 None，然后退回
        DEFAULT_NATURES（含事业单位、公务员、外企）——用户以为自己只要央企国企，
        结果列表里混着事业编岗位。2026-09-21 实测：搜武汉混进「武汉大学出版社」。
        """
        import app as app_mod

        pool = [
            {'key': 'a1', 'company': '某某钢铁集团', 'nature': '国企',
             'city': '武汉', 'title': '电工', 'deadline': ''},
            {'key': 'a2', 'company': '国家电网武汉供电公司', 'nature': '央企',
             'city': '武汉', 'title': '线路运维', 'deadline': ''},
            {'key': 'b1', 'company': '某某医院', 'nature': '事业单位',
             'city': '武汉', 'title': '护工', 'deadline': ''},
            {'key': 'c1', 'company': '某某局', 'nature': '公务员',
             'city': '武汉', 'title': '窗口受理', 'deadline': ''},
            {'key': 'd1', 'company': '某某外资工厂', 'nature': '外企',
             'city': '武汉', 'title': '装配工', 'deadline': ''},
        ]

        def fake_query_jobs(city='', natures=None, keywords=None, limit=200):
            """复刻 models.query_jobs 的性质/城市过滤，只换掉取数那一步。"""
            want = set(natures) if natures else None
            out = []
            for j in pool:
                if want and (j.get('nature') or '民营') not in want:
                    continue
                c = (j.get('city') or '').strip()
                if city and not (city in c or c in city):
                    continue
                out.append(dict(j))
            return out

        orig_q, orig_cache, orig_crawl = (app_mod.models.query_jobs,
                                          app_mod._JOBS_CACHE_ON,
                                          app_mod._kick_city_crawl)
        app_mod.models.query_jobs = fake_query_jobs
        app_mod._JOBS_CACHE_ON = True          # 不走现爬，测试不该联网
        app_mod._kick_city_crawl = lambda *a, **k: None
        app_mod._SEARCH_CACHE.clear()
        try:
            # 前端真实发出的就是单数 nature
            jobs, _meta = app_mod._collect_for_profile(
                {'city': '武汉', 'nature': ['央企', '国企'],
                 'jobTypes': [], 'skills': []})
        finally:
            app_mod.models.query_jobs = orig_q
            app_mod._JOBS_CACHE_ON = orig_cache
            app_mod._kick_city_crawl = orig_crawl
            app_mod._SEARCH_CACHE.clear()

        self.assertTrue(jobs, '一条都没搜到——过滤过头了')
        bad = [j for j in jobs if j.get('nature') not in ('央企', '国企')]
        self.assertFalse(
            bad, '勾了央企国企却混进来：%r'
                 % [(j.get('nature'), j.get('company')) for j in bad])

    # 66 —— 国聘自己标的事业单位，不能再被扭成国企
    def test_66_guopin_institution_keeps_its_nature(self):
        """站点字典里「事业单位」是独立性质（有自己的码），早年并进「国企」是双向错：

        勾国企时混进事业编岗位，勾事业单位时又搜不到它们。
        """
        from collectors.guopin import _nature_of

        self.assertEqual(_nature_of('某某医院', '事业单位'), '事业单位')
        self.assertEqual(_nature_of('武汉大学出版社', '事业单位'), '事业单位')
        # 央企名录命中照旧优先，不受这条影响
        self.assertEqual(_nature_of('国家电网四川电力公司', '国企'), '央企')

    # 67 —— 「只有本人知道」的事实：档案没有就留空报缺失，绝不编默认值
    def test_67_facts_never_defaulted(self):
        """民族 / 婚姻 / 统招 / 外语 / 服从调剂这些，档案里没有就不能猜。

        2026-09-21 之前这里写死过一批：民族「汉族」、婚姻「未婚」、学历性质
        「统招」+「全日制」、外语「英语 + 熟练」、健康「健康」、服从调剂「是」，
        而 profiles 表当时根本没有这些列。后果不是「填得不完美」：
        成人教育被填成统招全日制是**学历性质造假**；凭空安上「英语熟练」，
        面试英文一问就穿帮；替用户勾「服从调剂」，人可能被派到偏远地区。
        """
        import autofill

        # 1) 档案没有 → 返回空串，并且记一条「需要你补」
        t = autofill.Task('t-67a', {}, {}, 'about:blank')
        for key, label in (('nation', '民族'), ('marital', '婚姻状况'),
                           ('edu_regular', '学历性质'), ('edu_fulltime', '学习形式'),
                           ('has_degree', '学位证'), ('foreign_lang', '外语语种'),
                           ('foreign_level', '外语水平'), ('can_arrange', '是否服从调剂'),
                           ('health', '健康状况')):
            self.assertEqual(autofill._gp_fact(t, key, label), '',
                             '%s 档案里没有却填了值' % key)
        got = {m['label'] for m in t.missing}
        self.assertIn('民族', got)
        self.assertIn('是否服从调剂', got)

        # 2) 档案填了 → 原样返回，且不报缺失
        t2 = autofill.Task('t-67b', {},
                           {'nation': '回族', 'can_arrange': '否', 'edu_regular': '非统招'},
                           'about:blank')
        self.assertEqual(autofill._gp_fact(t2, 'nation', '民族'), '回族')
        self.assertEqual(autofill._gp_fact(t2, 'can_arrange', '是否服从调剂'), '否')
        self.assertEqual(autofill._gp_fact(t2, 'edu_regular', '学历性质'), '非统招')
        self.assertEqual(t2.missing, [], '档案里有值却报了缺失')

        # 3) 防回归：源码里不许再出现这些写死的值
        import inspect
        src = inspect.getsource(autofill._guopin_resume_guard)
        for bad in ("'汉族'", "'未婚'", "'统招'", "'全日制'", "'英语'",
                    "'健康'", "'学校未排名'"):
            self.assertNotIn(bad, src, '又把 %s 写回国聘流程了' % bad)

    # 68 —— 「其他说明」段的是非题：只代答承诺类，意愿类留给用户
    def test_68_yes_no_only_commitment(self):
        """以前这里是不分题型统一答「否」的，会把「是否愿意接受调剂」
        这类**个人意愿**也替他答掉。"""
        import autofill

        for s in ('是否有严重违纪违法情形', '是否被列为失信被执行人',
                  '是否受到过党纪政务处分', '是否有竞业限制',
                  '是否有需要回避的亲属关系'):
            self.assertTrue(any(k in s for k in autofill._NO_ANSWER_KEYWORDS),
                            '承诺类题目没被识别：%s' % s)
        for s in ('是否愿意接受调剂', '是否愿意出差', '是否服从岗位安排',
                  '是否可以到岗实习'):
            self.assertFalse(any(k in s for k in autofill._NO_ANSWER_KEYWORDS),
                             '意愿类题目被当成承诺类代答了：%s' % s)

    # 69 —— 投递前预检清单：新的个人事实项都在，且不重复
    def test_69_required_fields_cover_facts(self):
        import autofill

        fields = autofill.guopin_required_fields()
        ks = [f['k'] for f in fields]
        for k in ('nation', 'marital', 'edu_regular', 'edu_fulltime',
                  'has_degree', 'foreign_lang', 'can_arrange', 'health'):
            self.assertIn(k, ks, '投递前预检清单缺 %s' % k)
        self.assertEqual(len(ks), len(set(ks)), '预检清单有重复项：%r' % ks)
        for f in fields:
            self.assertTrue(f.get('label'), f)
            self.assertTrue(f.get('note'), f)

    # 70 —— 新加的个人事实字段必须能存进库、读得回来
    def test_70_fact_columns_roundtrip(self):
        """profiles 表少了列，用户填了也存不下来 —— 只能又退回「猜一个默认值」。"""
        import tempfile
        import os as _os

        tmp = _os.path.join(tempfile.gettempdir(), 'wb_test70.db')
        for f in (tmp, tmp + '-wal', tmp + '-shm'):
            if _os.path.exists(f):
                _os.remove(f)
        old_db = models._DB_PATH
        try:
            models.init_db(tmp)
            pid = models.save_profile({
                'name': '测试', 'phone': '13900000070',
                'nation': '回族', 'marital': '已婚', 'edu_regular': '非统招',
                'edu_fulltime': '非全日制', 'has_degree': '无学位证',
                'foreign_lang': '日语', 'foreign_level': '一般',
                'can_arrange': '否', 'health': '良好',
            })
            p = models.get_profile(pid)
            for k, v in (('nation', '回族'), ('marital', '已婚'),
                         ('edu_regular', '非统招'), ('edu_fulltime', '非全日制'),
                         ('has_degree', '无学位证'), ('foreign_lang', '日语'),
                         ('foreign_level', '一般'), ('can_arrange', '否'),
                         ('health', '良好')):
                self.assertEqual(p.get(k), v, 'profiles 表存不住 %s' % k)
            # 只改一项，其余要保留（合并语义）
            models.save_profile({'phone': '13900000070', 'nation': '汉族'})
            p2 = models.get_profile(pid)
            self.assertEqual(p2.get('nation'), '汉族')
            self.assertEqual(p2.get('marital'), '已婚', '改一项把别的冲掉了')
            self.assertEqual(p2.get('can_arrange'), '否', '改一项把别的冲掉了')
        finally:
            models.init_db(old_db)

    # 71 —— 演练模式发完不能报「已发送」
    def test_71_dry_run_never_says_sent(self):
        """演练模式下邮件只落盘没真发，状态却写成「已发送」的话，
        用户在结果面板看到「N 封」会以为招聘方收到了 —— 和「自动填表
        其实停在提交前却记成已提交」是同一类谎报。"""
        j = dict(title='测试岗', company='某单位', nature='国企',
                 salary_text='5000-8000元', hr_email='hr@example.com', key='dry1')
        r = self.c.post('/api/apply', json={
            'profile_id': None, 'jobs': [j], 'channel': 'smtp', 'profile': profile()})
        self.assertTrue(r.get_json()['ok'])
        tid = r.get_json()['task_id']
        t = None
        for _ in range(50):
            time.sleep(0.1)
            t = self.c.get('/api/task/' + tid).get_json()['task']
            if t['status'] != 'running':
                break
        self.assertTrue(t.get('dry'), '演练模式的任务必须带 dry 标记，前端才能如实提示')
        st = t['items'][0]['status']
        self.assertNotEqual(st, '已发送', '演练模式报成了「已发送」')
        self.assertIn('演练', st)

    # 72 —— 换发件邮箱必须带新授权码（旧的不通用）
    def test_72_changing_mailbox_needs_new_authcode(self):
        """授权码只对同一个发件邮箱有效。拿 A 邮箱的授权码写成 B 邮箱的配置，
        等于写出一份注定登录失败的配置 —— 必须在保存这一步就拦住。
        （原来这条还管「切到真实发送」那个开关，现在整档拿掉了。）"""
        calls = []
        orig_load, orig_save = mailer.load_config, mailer.save_config
        saved = {'email': 'zhangsan@qq.com', 'username': 'zhangsan@qq.com',
                 'password': 'QQ-CODE', 'host': 'smtp.qq.com'}
        mailer.load_config = lambda: saved
        mailer.save_config = lambda email, password, dry_run=None, from_name='': (
            calls.append((email, password)) or (True, '已保存', 'smtp.qq.com'))
        try:
            # 1) 换邮箱但没填授权码 → 拦下来
            r2 = self.c.post('/api/mail/config', json={'email': 'other@163.com'})
            self.assertEqual(r2.status_code, 400, '换了邮箱还沿用旧授权码')
            self.assertIn('授权码', r2.get_json()['msg'])

            # 2) 换邮箱 + 带上新授权码 → 放行
            r3 = self.c.post('/api/mail/config', json={
                'email': 'other@163.com', 'password': 'NEW-CODE'})
            self.assertEqual(r3.status_code, 200, r3.get_json())
            self.assertEqual(calls[-1], ('other@163.com', 'NEW-CODE'))

            # 3) 邮箱空着 → 400
            r4 = self.c.post('/api/mail/config', json={'email': ''})
            self.assertEqual(r4.status_code, 400)
        finally:
            mailer.load_config, mailer.save_config = orig_load, orig_save

    # 73 —— 邮件附件优先附求职者原件
    def test_73_attachment_prefers_original_resume(self):
        """HR 该看到求职者手里那份 Word/PDF 本身（和自动投递上传原件同一条原则）；
        而且 .html 附件是钓鱼邮件的经典载体，企业邮件网关常直接拦掉。
        另外生成的 HTML 附件类型必须是 text/html，不是没注册过的 application/html。"""
        import email as emailmod
        src = os.path.join(TMP, 'resumes')
        os.makedirs(src, exist_ok=True)
        doc = os.path.join(src, '1700000000_我的简历.docx')
        with open(doc, 'wb') as f:
            f.write(b'PK\x03\x04fake-docx-for-test')
        old = mailer.RESUME_DIR
        mailer.RESUME_DIR = src
        try:
            p = profile(resume_path=doc)
            path, name = mailer.original_resume(p)
            self.assertEqual(path, os.path.normpath(os.path.abspath(doc)))
            self.assertTrue(name.endswith('.docx'), name)
            self.assertIn('张三', name)
            m1 = emailmod.mime.multipart.MIMEMultipart()
            mailer._attach_resume(m1, p, path, name)
            a1 = [x for x in m1.walk() if x.get_filename()][0]
            self.assertIn('wordprocessingml', a1.get_content_type(),
                          '原件没按 docx 类型挂上：%s' % a1.get_content_type())

            # 目录外 / 不存在的文件一律不认
            self.assertEqual(
                mailer.original_resume(profile(resume_path='C:/evil/x.docx')), (None, ''))
            self.assertEqual(
                mailer.original_resume(profile(resume_path=os.path.join(src, '没有这个.docx'))),
                (None, ''))

            # 没有原件 → 退回生成的 HTML，类型是 text/html
            m2 = emailmod.mime.multipart.MIMEMultipart()
            mailer._attach_resume(m2, profile(), None, '')
            a2 = [x for x in m2.walk() if x.get_filename()][0]
            self.assertEqual(a2.get_content_type(), 'text/html',
                             'application/html 是没注册的类型，部分客户端不给预览')
            self.assertTrue(a2.get_filename().endswith('简历.html'))
        finally:
            mailer.RESUME_DIR = old

    # 47 —— 读不了的文件要给一句人话，而不是抛错或者静默失败
    def test_47_bad_files(self):
        import resume_parser as rp

        for fn, kw in (('老简历.doc', '另存为'), ('照片.jpg', '照片'), ('包.rar', '只认')):
            text, err = rp.extract_text(fn, b'x')
            self.assertIsNone(text)
            self.assertIn(kw, err)

    # 48 —— /api/resume/parse 接口
    def test_48_resume_api(self):
        import base64
        import resume_parser as rp

        c = app.test_client()
        txt = '姓名：赵六\n手机：13612345678\n邮箱：zhao@163.com\n学历：大专\n期望城市：长沙\n'
        b64 = base64.b64encode(txt.encode('utf-8')).decode()

        r = c.post('/api/resume/parse',
                   json={'filename': '赵六.txt', 'data_b64': b64})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j['ok'])
        self.assertEqual(j['fields']['name'], '赵六')
        self.assertEqual(j['fields']['city'], '长沙')
        self.assertEqual(len(j['got']), 5)
        self.assertTrue(all('label' in g for g in j['got']))

        # 前端传 data:xxx;base64, 前缀也要能收（FileReader 的原始输出）
        r = c.post('/api/resume/parse', json={
            'filename': 'x.txt', 'data_b64': 'data:text/plain;base64,' + b64})
        self.assertTrue(r.get_json()['ok'])

        # 前端传的城市表优先，认得出来就用
        r = c.post('/api/resume/parse', json={
            'filename': 'x.txt', 'data_b64': b64, 'cities': ['长沙', '不限城市']})
        self.assertEqual(r.get_json()['fields']['city'], '长沙')

        # 读不了的格式：不是一个错误，是给人看的一句话
        r = c.post('/api/resume/parse', json={
            'filename': 'a.doc', 'data_b64': base64.b64encode(b'x').decode()})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()['ok'])
        self.assertIn('另存为', r.get_json()['msg'])

        # 扫描件风格：有字但一个字段都认不出来
        empty = base64.b64encode('这是一份扫描件，里面没有可以识别的文字'.encode('utf-8')).decode()
        r = c.post('/api/resume/parse', json={'filename': 'a.txt', 'data_b64': empty})
        self.assertFalse(r.get_json()['ok'])
        self.assertIn('手动填', r.get_json()['msg'])

        # 超大文件、缺参数
        big = base64.b64encode(b'a' * (rp.MAX_BYTES + 10)).decode()
        r = c.post('/api/resume/parse', json={'filename': 'big.txt', 'data_b64': big})
        self.assertFalse(r.get_json()['ok'])
        self.assertIn('太大', r.get_json()['msg'])
        self.assertEqual(c.post('/api/resume/parse', json={}).status_code, 400)

    # 49 —— 后端兜底的城市表要和前端 options.js 一致，别改了一边忘了另一边
    def test_49_cities_in_sync(self):
        import re
        import resume_parser as rp

        src = self.frontend_file('options.js')
        m = re.search(r'export const CITIES = \[(.*?)\]', src, re.S)
        self.assertIsNotNone(m, 'options.js 里找不到 CITIES')
        fe = [s.strip().strip("'\"") for s in m.group(1).split(',')]
        fe = [c for c in fe if c and c != '不限城市']
        self.assertEqual(sorted(fe), sorted(rp.DEFAULT_CITIES),
                         '前端 CITIES 和 resume_parser.DEFAULT_CITIES 不一致了')

    # 50 —— 年限：能认的写法都要认，认不出宁可留空也不能猜错
    def test_50_exp_years(self):
        import resume_parser as rp

        # 各种常见写法都要认出来（口语写法最容易漏：「做电工 5 年」）
        for text, want in (('做电工 5 年，有电工证', '5年以上'),
                           ('干这行三年了', '3-5年'),
                           ('从事电工工作 4 年', '3-5年'),
                           ('5年工作经验', '5年以上'),
                           ('有两年工作经验', '1-3年'),
                           ('工作经历：2年', '1-3年'),
                           ('工龄 8 年', '5年以上'),
                           ('应届毕业生', '没经验'),
                           ('参加工作 0 年', '1年以内')):
            self.assertEqual(rp._find_exp(rp._norm(text)), want, text)

        # 以下几类**绝不能**被读成年限 —— 读错会直接歪掉岗位排序
        for text in ('2019.03 - 2024.05 郑州某某电力工程有限公司 电工',   # 起止日期
                     '2019 年做电工到 2024 年',                        # 起止年份
                     '大专三年制，电气专业'):                           # 学制
            self.assertEqual(rp._find_exp(rp._norm(text)), '', text)

    # 51 —— 学校发的「简历填写说明」不能被当成用户的自我介绍（用户实测踩到的）
    def test_51_guide_doc_not_mistaken(self):
        import resume_parser as rp

        guide = ('【1】提交信息\n把能证明你能力的信息，也一并附上。我们共同的目标是挖掘亮点，'
                 '提高网申通过率。\n2.尽可能准确。比如绩点、参与大赛的完整名称，'
                 '越完整、才越容易高效快速包装。如若信息不够100%纯真实，务必告知老师。\n'
                 '【2】基本信息\n1.照片信息:照片上传后，可以将文本框的边框去掉，'
                 '保证简历的美观度。\n自我介绍\n【3】自我评价怎么写\n'
                 '把上面的信息，也一并附上。\n')
        r = rp.parse_fields(guide)
        self.assertNotIn('intro', r['fields'])       # 说明书不许进简历字段
        self.assertTrue(r['warn'])                   # 而且要提醒用户「这不像简历」
        # 「自我评价怎么写」是小标题，它的尾巴不许被吞进正文
        self.assertNotIn('怎么写', rp._find_intro(rp._norm(guide)))

        # 没有手机号也没有邮箱的文件一律提醒（入职须知 / 证书 / 合同都长这样）
        self.assertTrue(rp.parse_fields('入职须知\n请携带身份证原件到人事部。\n')['warn'])

        # 有电话也算不上简历 —— 就业指导文档里常附咨询电话，靠「说明书味」认出来
        with_phone = guide.replace('【1】提交信息', '【1】提交信息（有问题打 13800001111）')
        r2 = rp.parse_fields(with_phone)
        self.assertIn('phone', r2['fields'])
        self.assertTrue(r2['warn'])

        # 正常简历不受影响：能认出自我介绍，也不该被提醒
        ok = rp.parse_fields('姓名：孙七\n手机：13655555588\n自我评价\n'
                             '做电工 5 年，有电工证，能上夜班。\n')
        self.assertIn('intro', ok['fields'])
        self.assertEqual(ok['warn'], '')

        # 标题写着「个人简历」的也不能被误判成说明文档
        titled = rp.parse_fields('个人简历\n姓名：李明\n手机：13900001111\n学历：本科\n'
                                 '自我评价\n熟悉数控机床，能吃苦。\n')
        self.assertEqual(titled['warn'], '')
        self.assertEqual(titled['fields']['intro'], '熟悉数控机床，能吃苦。')

    # 52 —— 城市放宽：补进来的外地岗标 nearby，但「全国轮里搜到的本市岗」不能跟着被标外地
    def test_52_nearby_marks_only_away_jobs(self):
        import collectors as C
        from collectors.base import BaseCollector

        class Fake(BaseCollector):
            name = 'fake'
            label = '假源'

            @staticmethod
            def _job(city, title, company):
                return {'key': company + title + city, 'title': title, 'company': company,
                        'city': city, 'nature': '国企', 'salary_text': '5000-8000',
                        'exp': '经验不限', 'edu': '不限', 'apply_url': 'http://e.com',
                        'deadline': '', 'headcount': 0}

            def fetch(self, keyword='', city='', limit=200, natures=None):
                if city:                                  # 本市只有 1 条 < MIN_CITY_HITS → 放宽
                    return [self._job('武汉', '电工', '武汉某国企')]
                return [self._job('武汉', '电工', '武汉某国企'),   # 全国轮里也会夹着本市的
                        self._job('兰州', '电工', '兰州某国企')]

        original = C.LIVE_COLLECTORS
        C.LIVE_COLLECTORS = [Fake()]
        try:
            r = C.collect_jobs_with_report(keyword='电工', city='武汉',
                                           natures=['央企', '国企'])
        finally:
            C.LIVE_COLLECTORS = original

        self.assertTrue(r['relaxed'], '本市岗位太少时应该放宽到全国')
        by_city = {j['city']: j for j in r['jobs']}
        self.assertIn('武汉', by_city)
        self.assertIn('兰州', by_city)
        self.assertFalse(by_city['武汉'].get('nearby'), '本市的岗位不许被标成外地')
        self.assertTrue(by_city['兰州'].get('nearby'), '外地的岗位要标出来')

        # 城市判断本身：「武汉市」这样的写法也算本市；没选城市时谁都不算外地
        self.assertTrue(C._in_city({'city': '武汉市'}, '武汉'))
        self.assertTrue(C._in_city({'city': '武汉'}, '武汉'))
        self.assertFalse(C._in_city({'city': '兰州'}, '武汉'))
        self.assertFalse(C._in_city({'city': ''}, '武汉'))
        self.assertFalse(C._in_city({'city': '武汉'}, ''))

    def test_53_company_name_match(self):
        """单位名匹配：全名直接命中，简称（中建三局 / 武钢 / 中铁）也要能搜到。

        中国人搜单位几乎都打简称，而简称往往不是全名的子串
        （「中建三局」不在「中国建筑第三工程局」里），所以要有兜底。
        """
        import app as appmod
        hit, loose = appmod._company_hit, appmod._company_loose

        # 全名里直接包含
        self.assertTrue(hit('中国邮政集团有限公司河北省分公司', '邮政'))
        self.assertTrue(hit('国家电网（市供电公司）', '国家电网'))
        self.assertTrue(hit('施耐德电气（中国）有限公司', '施耐德'))
        # 简称：靠字序兜底
        self.assertTrue(loose('中国建筑第三工程局', '中建三局'))
        self.assertTrue(loose('武汉钢铁（集团）公司', '武钢'))
        self.assertTrue(loose('中国铁路（华铁旅服）', '中铁'))
        # 不能乱命中
        self.assertFalse(loose('中国建筑第三工程局', '八局'))
        self.assertFalse(hit('华润万家', '邮政'))
        self.assertFalse(loose('华润万家', '邮政'))

    def test_54_company_search(self):
        """按单位名搜岗位：只留这家单位的，放宽到全国来的要标「外地」。"""
        import app as appmod

        def fake(**kw):
            rows = [
                {'key': 'a', 'title': '邮件分拣员',
                 'company': '中国邮政集团有限公司武汉分公司', 'city': '武汉',
                 'nature': '央企', 'hire_type': '直签', 'salary_text': '5000-6500元',
                 'edu': '不限', 'exp': '经验不限', 'tags': ['分拣'], 'desc': ''},
                {'key': 'b', 'title': '施工现场电工',
                 'company': '中建三局云居科技有限公司', 'city': '武汉',
                 'nature': '央企', 'hire_type': '直签', 'salary_text': '7000-10000元',
                 'edu': '不限', 'exp': '1-3年', 'tags': ['电工'], 'desc': ''},
                {'key': 'c', 'title': '邮件分拣员',
                 'company': '中国邮政集团河北分公司', 'city': '石家庄',
                 'nature': '央企', 'hire_type': '直签', 'salary_text': '4500-6000元',
                 'edu': '不限', 'exp': '经验不限', 'tags': ['分拣'], 'desc': ''},
            ]
            return {'jobs': rows, 'sources': [], 'relaxed': False,
                    'fallback': False, 'total': len(rows)}

        original = appmod.collect_jobs_with_report
        appmod.collect_jobs_with_report = fake
        try:
            c = app.test_client()
            r = c.post('/api/jobs/company',
                       json={'company': '邮政', 'profile': profile(city='武汉'),
                             'city': '武汉'})
            d = r.get_json()
            self.assertTrue(d['ok'])
            comps = {j['company'] for j in d['jobs']}
            self.assertIn('中国邮政集团有限公司武汉分公司', comps)
            self.assertIn('中国邮政集团河北分公司', comps)
            self.assertNotIn('中建三局云居科技有限公司', comps, '别的单位不该混进来')

            by_city = {j['city']: j for j in d['jobs']}
            self.assertFalse(by_city['武汉'].get('nearby'), '本市的不标外地')
            self.assertTrue(by_city['石家庄'].get('nearby'),
                            '放宽到全国拿回来的外地岗要标出来，别让人以为在本地')

            # 不填单位名要挡住，别拿空条件去扫全库
            r2 = c.post('/api/jobs/company', json={'company': '   '})
            self.assertEqual(r2.status_code, 400)
        finally:
            appmod.collect_jobs_with_report = original


    def test_55_resume_basic_fields(self):
        """简历里的 出生年月/学校/专业/性别 要认得出来。

        招聘表必问这几项。以前解析器根本不产这些字段，自动投只能空着
        让人重打一遍——对不常用电脑的人最劝退的一步。
        """
        import resume_parser as RP
        text = ('个 人 简 历\n\n姓名：张三\n性别：男\n出生年月：1999.05\n'
                '手机：13800138000\n邮箱：z@qq.com\n'
                '毕业院校：武汉职业技术学院\n专业：电气自动化技术\n'
                '毕业时间：2021.06\n学历：大专\n')
        f = RP.parse_fields(text)['fields']
        self.assertEqual(f.get('gender'), '男')
        self.assertEqual(f.get('birth'), '1999-05')
        self.assertEqual(f.get('school'), '武汉职业技术学院')
        self.assertEqual(f.get('major'), '电气自动化技术')
        self.assertEqual(f.get('graduation'), '2021-06')

        # 另一份写法（全角分隔、只给年份）也要认得
        f2 = RP.parse_fields('姓名：李梅\n性 别：女\n出生日期：1995年8月\n'
                             '手机：13900139000\n邮箱：a@b.com\n'
                             '所学专业：工商管理\n毕业年份：2018\n')['fields']
        self.assertEqual(f2.get('gender'), '女')
        self.assertEqual(f2.get('birth'), '1995-08')
        self.assertEqual(f2.get('major'), '工商管理')
        self.assertEqual(f2.get('graduation'), '2018')

        # 不带标签的日期（工作经历起止）绝不能被当成出生年月/毕业时间
        f3 = RP.parse_fields('姓名：王五\n手机：13700137000\n邮箱：c@d.com\n'
                             '2019.03-2022.11 某公司 电工\n')['fields']
        self.assertNotIn('birth', f3)
        self.assertNotIn('graduation', f3)

    def test_56_profile_new_columns(self):
        """建档要存得下这些新字段——自动投优先读数据库，表没列就等于白填。"""
        pid = models.save_profile({
            'name': '测试', 'phone': '13800138002', 'email': 't@q.com',
            'gender': '女', 'birth': '1995-08', 'school': '某大学',
            'major': '工商管理', 'graduation': '2018-06',
            'resume_path': 'C:/x/y.docx',
        })
        row = models.get_profile(pid)
        self.assertEqual(row['gender'], '女')
        self.assertEqual(row['birth'], '1995-08')
        self.assertEqual(row['school'], '某大学')
        self.assertEqual(row['major'], '工商管理')
        self.assertEqual(row['graduation'], '2018-06')
        self.assertEqual(row['resume_path'], 'C:/x/y.docx')

    def test_57_autofill_whitelist(self):
        """白名单漏一个字段，界面就会报「你的资料里没有这项」。

        这三处必须对齐：resume_parser 认得出的字段、profiles 表的列、
        autofill._PROFILE_KEYS。少一处，用户简历上明明有的信息就填不进去。
        """
        import autofill
        for k in ('gender', 'birth', 'school', 'major', 'graduation'):
            self.assertIn(k, autofill._PROFILE_KEYS,
                          '%s 不在自动填表白名单里' % k)

        conn = models._conn()
        try:
            cols = {r[1] for r in conn.execute('PRAGMA table_info(profiles)')}
        finally:
            conn.close()
        for k in ('gender', 'birth', 'school', 'major', 'graduation',
                  'resume_path'):
            self.assertIn(k, cols, 'profiles 表缺列 %s' % k)

    def test_58_resume_original_file(self):
        """同意保留时原件要存下来：自动投上传它，而不是拼出来的一页 txt。"""
        import autofill
        import app as appmod
        old = autofill.RESUME_DIR
        autofill.RESUME_DIR = os.path.join(TMP, 'resumes')
        try:
            dest = appmod._save_resume_file('我的简历.docx', b'PK\x03\x04fake')
            self.assertTrue(dest and os.path.isfile(dest), '原件没存下来')
            self.assertTrue(dest.endswith('.docx'), '扩展名丢了：%s' % dest)
            # 文件名里的 ../ 之类要被洗掉，不能让路径跑到别处去
            dest2 = appmod._save_resume_file('../../evil.docx', b'x')
            self.assertTrue(dest2.startswith(autofill.RESUME_DIR),
                            '文件名没做安全处理：%s' % dest2)
        finally:
            autofill.RESUME_DIR = old

    def test_59_resume_edu_table_row(self):
        """学校/专业写在教育表格行里的简历（真简历最常见的排版）要认得。

        以前只认「毕业院校：XXX」这种填空式。遇到表格行就去正文里裸搜
        「学校」「专业」两个词，一份真简历实测下来：专业填成了
        「专业特色：获BCS」、学校填成了「代表学校参赛多次获得省级」。
        """
        import resume_parser as RP
        text = ('刘志博\n'
                '联系电话：（+86）159-2628-3200电子邮箱：2996897514@qq.com\n'
                '家庭住址：湖北省武汉市出生年月：2002年4月\n'
                '教育背景\n'
                '2026.02-2027.02   东伦敦大学（伦敦第6）   计算机   硕士\n'
                '主修课程：计算机大数据分析、人工智能与机器视觉。\n'
                '专业特色：获BCS、微软官方认证合作，课程对标国际行业标准。\n'
                '2020.09-2024.06   武汉交通职业学院（公办）   航海技术   专科\n'
                '综合表现：在校成绩优异，获校优秀毕业生称号。\n'
                '实习/实践\n'
                '2024.07-2025.01   中国人寿保险股份有限公司（国企）   营销部 | 销售\n'
                '自我评价\n'
                '学习背景：具备海外计算机硕士学习经历。\n')
        f = RP.parse_fields(text)['fields']
        self.assertEqual(f.get('school'), '东伦敦大学', '该取学历最高的那条')
        self.assertEqual(f.get('major'), '计算机')
        self.assertEqual(f.get('graduation'), '2027-02')
        self.assertEqual(f.get('phone'), '15926283200', '带 +86 的号码也要认得出来')
        self.assertEqual(f.get('city'), '武汉')
        self.assertEqual(f.get('birth'), '2002-04')
        # 正文里那些「专业特色」「代表学校」绝不能被当成学校/专业
        self.assertNotIn('BCS', f.get('major', ''))
        self.assertNotIn('参赛', f.get('school', ''))

        # 简历后面附了「网申注意事项」，也不能把整份文件误判成说明文档
        tail = ('一页纸到上面结束，以下为网申注意板块\n'
                '填写注意事项：\n1.尽可能全面，帮助老师精修。\n'
                '2.邮件主题：投递单位+岗位+姓名+学校+专业。\n')
        self.assertEqual(RP.parse_fields(text + tail)['warn'], '',
                         '认得出字段就别吓唬用户，说明附在后面不等于这不是简历')

        # 老写法（只有一所学校，写在标签里）不能被改坏
        f2 = RP.parse_fields('姓名：张三\n手机：13800138000\n'
                             '毕业院校：武汉职业技术学院\n'
                             '专业：电气自动化技术\n')['fields']
        self.assertEqual(f2.get('school'), '武汉职业技术学院')
        self.assertEqual(f2.get('major'), '电气自动化技术')

        # 「主修课程：…」讲的是学了哪些课，不是专业——宁可空着让人填
        f3 = RP.parse_fields('姓名：李四\n手机：13800138001\n'
                             '主修课程：电路基础、电机拖动\n')['fields']
        self.assertIsNone(f3.get('major'))

    def test_60_profile_experiences(self):
        """结构化经历（国聘补站内简历用）要存得下、读得回来、坏数据不炸。

        国聘投递会拦「实习经历缺失/校内活动缺失」，有真实经历就该一段段
        真填，而不是只会勾「无经历」——这依赖档案里的 experiences 字段。
        """
        import json as _json
        import autofill
        pid = models.save_profile({
            'name': '经历测试', 'phone': '13800138003', 'email': 'e@q.com',
            'experiences': [
                {'type': 'edu', 'school': '某大学', 'major': '计算机',
                 'degree': '硕士', 'start': '2026-02', 'end': '2027-02'},
                {'type': 'intern', 'company': '某国企', 'role': '销售',
                 'start': '2024-07', 'end': '2025-01', 'desc': '维护客户'},
                {'type': 'campus', 'company': '篮球社', 'role': '社长',
                 'start': '2021-09', 'end': '2023-06', 'desc': '办比赛'},
                {'type': 'intern', 'company': '', 'role': '空的应丢弃'},
                '不是字典的也丢弃',
            ],
        })
        row = models.get_profile(pid)
        exp = autofill._profile_experiences(row)
        self.assertEqual(len(exp), 3, '空段和非字典段要被丢掉')
        self.assertEqual(exp[0]['type'], 'edu')
        self.assertEqual(exp[1]['company'], '某国企')

        # 库里存的是 JSON 字符串，直接读回来要能解析
        self.assertIsInstance(row['experiences'], str)
        self.assertEqual(len(_json.loads(row['experiences'])), 3)

        # 坏数据不炸：烂 JSON / 不是列表 / 空档案 → 空列表，别把投递流程搞挂
        self.assertEqual(autofill._profile_experiences(
            {'experiences': '{{{不是json'}), [])
        self.assertEqual(autofill._profile_experiences({'experiences': 42}), [])
        self.assertEqual(autofill._profile_experiences({}), [])

    def test_61_profile_experiences_survive_resave(self):
        """前端没带 experiences 重新保存档案时，不能把已存的经历抹掉。"""
        import autofill
        pid = models.save_profile({
            'name': '经历保留', 'phone': '13800138004', 'email': 'k@q.com',
            'experiences': [{'type': 'intern', 'company': '某公司',
                             'role': '职员', 'start': '2023-01', 'end': '2023-06'}],
        })
        # 第二次保存（比如用户改了手机号）不带 experiences 字段
        pid2 = models.save_profile({'name': '经历保留', 'phone': '13800138004',
                                    'email': 'k@q.com'})
        self.assertEqual(pid, pid2)
        exp = autofill._profile_experiences(models.get_profile(pid2))
        self.assertEqual(len(exp), 1, '重存档案不该弄丢经历')
        self.assertEqual(exp[0]['company'], '某公司')

    # 74 —— 报名邮箱提取：既要抓得到，也不能抓不该抓的
    def test_74_email_extraction_quality(self):
        """公告里常同时挂监督/咨询邮箱，草率取第一个会投错人；邮箱的 @ 还常被
        实体编码 / 全角 / 标签切分藏起来防爬，这些都要抓得到。"""
        from collectors.gov_soe import _extract_apply_target
        base = 'http://gzw.hubei.gov.cn/notice/1.html'
        cases = [
            ('标准写法',     '报名邮箱：hr@abc.com.cn，请投递简历。', 'hr@abc.com.cn'),
            ('实体编码@',    '报名邮箱：hr&#64;abc.com.cn ，请投递。', 'hr@abc.com.cn'),
            ('标签切分@',    '报名邮箱：hr<span>@</span>abc.com.cn 请投递。', 'hr@abc.com.cn'),
            ('全角＠',       '报名邮箱：hr＠abc.com.cn', 'hr@abc.com.cn'),
            # 监督举报邮箱排在前时，取第一个等于把简历投进纪检组
            ('监督在前',     '监督举报邮箱 jb@abc.com，报名邮箱 hr@abc.com。', 'hr@abc.com'),
            ('报名在前',     '报名邮箱 hr@abc.com，监督举报邮箱 jb@abc.com。', 'hr@abc.com'),
            ('咨询+报名同现', '咨询及报名邮箱 hr@abc.com', 'hr@abc.com'),
            ('只有中介咨询',  '详情咨询 QQ 邮箱 123456@qq.com', ''),
            # script 内、图片名、占位符、系统信箱都不该当成报名邮箱
            ('script内',     '<script>var s="admin@intra.com";</script>报名邮箱 hr@abc.com', 'hr@abc.com'),
            ('图片名误抓',    'img/banner-2024@2x.com.png 报名邮箱 hr@abc.com', 'hr@abc.com'),
            ('占位符',       '参考 example@example.com 格式，报名邮箱 hr@abc.com', 'hr@abc.com'),
            ('系统信箱',     '报名邮箱 noreply@abc.com', ''),
        ]
        for label, html, want in cases:
            got = _extract_apply_target(html, base)[1]
            self.assertEqual(got, want, '%s：期望 %r，实得 %r' % (label, want, got))

    # 75 —— 官方源用自己的机构域名做报名邮箱是正规做法，不能当"站点邮箱"排除
    def test_75_official_domains_not_mistaken_for_site_mail(self):
        """判来源域曾用 host 最后两段，于是 gzw.gd.gov.cn 被算成 gov.cn，
        hr@xxx.gov.cn 这类真实报名邮箱被整类误杀。政府/高校/事业单位的报名
        邮箱恰恰大量用这些域，必须保留；只有聚合站的站点反馈邮箱要排除。"""
        from collectors.gov_soe import _extract_apply_target
        cases = [
            ('gov.cn源+gov.cn邮箱', 'http://gzw.hubei.gov.cn/n/1.html',
             '报名邮箱：hr@whgzw.gov.cn', 'hr@whgzw.gov.cn'),
            ('edu.cn源+edu.cn邮箱', 'http://rsc.whu.edu.cn/zp/1.html',
             '报名邮箱：zp@whu.edu.cn', 'zp@whu.edu.cn'),
            ('com.cn源+com.cn邮箱', 'http://www.abc.com.cn/zp/1.html',
             '报名邮箱：hr@abc.com.cn', 'hr@abc.com.cn'),
            ('聚合站反馈邮箱要排除', 'https://hb.huatu.com/guoqizp/1.html',
             '投诉建议 fankui@huatu.com，报名邮箱 hr@abc.com', 'hr@abc.com'),
            ('聚合站只有反馈邮箱', 'https://hb.huatu.com/guoqizp/1.html',
             '投诉建议 fankui@huatu.com', ''),
        ]
        for label, base, html, want in cases:
            got = _extract_apply_target(html, base)[1]
            self.assertEqual(got, want, '%s：期望 %r，实得 %r' % (label, want, got))

    # 76 —— 抓到的邮箱必须能在正文里看到出处，用户才能核对
    def test_76_email_visible_in_article_body(self):
        """「报名方式」写在公告最末尾，正文摘要不能把它截掉——否则前端那行
        「报名邮箱：xxx」是个孤零零的地址，用户没法判断它是不是真的报名邮箱。"""
        from collectors.gov_soe import _article_body, _clean, _extract_apply_target
        url = 'http://gzw.hubei.gov.cn/n/1.html'
        head = '某某集团公开招聘公告 ' + '岗位职责与任职要求。' * 80
        html = ('<html><body><p>' + head + '</p>'
                '<p>五、报名方式</p><p>报名邮箱：hr@abc.com.cn，请发送简历。</p>'
                '</body></html>')
        body = _clean(html)
        art = _article_body(html, body, '某某集团公开招聘公告')
        mail = _extract_apply_target(html, url)[1]
        self.assertEqual(mail, 'hr@abc.com.cn')
        self.assertIn(mail, art, '正文里必须能看到邮箱的出处')
        self.assertIn('报名方式', art)
        self.assertLessEqual(len(art), 1500, '正文不能超过 desc 的存储上限')

    # 77 —— 没邮箱的岗位不能「一点反馈都没有」（用户 2026-09-21 反馈）
    def test_77_no_email_job_never_reported_as_sent(self):
        """用户选了 10 个岗位点一键代投，其中没公开邮箱的那些**一点反馈都没有**：
        前端在按钮里 `jobs.filter(j => j.hr_email)` 直接把它们丢掉了，结果面板
        只显示发出去的那几封，用户以为 10 家都投了。

        现在前端会把岗位分成「能代投 / 要自己去官网投」两组，代投只对这组发。
        后端也得兜住：万一混着送来，没邮箱的那条必须如实记「没能代投」，
        既不能算成功（谎报），也不能从 items 里消失（静默跳过）。
        """
        orig = mailer.load_config
        # 演练配置：dry_run 分支只落盘不走网络，测试才不会真发也不会等 20 秒
        mailer.load_config = lambda: {
            'email': 'me@example.com', 'username': 'me@example.com',
            'password': 'x', 'dry_run': True, 'host': 'smtp.example.com',
            'from_name': '找工作助手',
        }
        try:
            with_mail = dict(title='有邮箱岗', company='甲单位', nature='国企',
                             salary_text='6000元', hr_email='hr@example.com', key='m1')
            no_mail = dict(title='没邮箱岗', company='乙单位', nature='国企',
                           salary_text='6000元', key='m2')
            r = self.c.post('/api/apply', json={
                'profile_id': None, 'jobs': [with_mail, no_mail],
                'channel': 'smtp', 'profile': profile()})
            body = r.get_json()
            self.assertTrue(body['ok'])
            self.assertEqual(body.get('skipped'), 1, '响应里没报出「有几个发不了」')

            t = None
            for _ in range(50):
                time.sleep(0.1)
                t = self.c.get('/api/task/' + body['task_id']).get_json()['task']
                if t['status'] != 'running':
                    break
            self.assertEqual(t['status'], 'done')
            self.assertEqual(t['skipped'], 1)
            self.assertEqual(len(t['items']), 2, '没邮箱的岗位从结果里消失了')
            by_title = {it['title']: it for it in t['items']}
            bad = by_title['没邮箱岗']
            self.assertFalse(bad['ok'], '没邮箱的岗位被算成了发送成功')
            self.assertTrue(bad.get('skipped'))
            self.assertIn('没有公开邮箱', bad['status'])
            self.assertNotIn('已发送', bad['status'])
            good = by_title['有邮箱岗']
            self.assertTrue(good['ok'], '有邮箱的岗位应该照常出去')
            self.assertIn('演练', good['status'], '演练模式不能报「已发送」')
        finally:
            mailer.load_config = orig

    # 78 —— 分类这一屏必须真的按「有没有邮箱」劈成两组
    def test_78_step4_splits_jobs_by_email(self):
        """这题盯的是"改动被悄悄改回去"：只要有人再把
        `jobs.filter(j => j.hr_email)` 塞回 sendAll，或者把分组删了，
        用户就会重新掉进"没邮箱的岗位没有反馈"那个坑里。

        源码断言比跑一遍浏览器快得多，也能直接指出是哪个文件不对。
        """
        src = self.read_component('Step4Apply.vue')
        self.assertIn('mailJobs', src, '第 4 步没有「能代投」这一组')
        self.assertIn('linkJobs', src, '第 4 步没有「要自己去官网投」这一组')
        self.assertRegex(src, r"const mailJobs = computed\(\(\) => jobs\.value\.filter",
                         '两组不是按 hr_email 劈的')
        # 一键代投只能对「能代投」那组发，且不能再在按钮里静默过滤一遍
        self.assertIn("api.apply(store.profileId, mailJobs.value, 'smtp'", src,
                      '一键代投没走 mailJobs（会退化成静默丢弃没邮箱的岗位）')
        self.assertNotIn("api.apply(store.profileId, jobs.filter(j => j.hr_email)",
                         src)
        # 没邮箱的岗位要有交代，不能只剩一个数字
        self.assertIn('没能代投', src)
        # 自动填表只该出现在「官网投」那一组
        self.assertIn("runMode === 'link'", src)

        src3 = self.read_component('Step3Match.vue')
        self.assertIn('badge-mail', src3, '第 3 步没有给可代投的岗位打标记')
        self.assertIn('v-if="j.hr_email"', src3)

    # 79 —— 演练邮件的落盘文件名里不能有 '/'（岗位名带斜杠太常见了）
    def test_79_outbox_file_name_safe(self):
        """演练模式把邮件落盘时直接拿岗位名当文件名。岗位名带斜杠的到处都是
        （「行政专员/助理」「销售/客服」），open() 会 FileNotFoundError，
        把整个代发任务打成 error —— 2026-09-21 跑 test_16 时就是这么炸的。"""
        self.assertNotIn('/', mailer._safe_file_name('行政专员/助理'))
        self.assertEqual(mailer._safe_file_name('a\\b:c*d?e"f<g>h|i'), 'a_b_c_d_e_f_g_h_i')
        self.assertEqual(mailer._safe_file_name('   '), 'job')
        self.assertEqual(mailer._safe_file_name(None), 'job')
        # 端到端：带斜杠的岗位名在演练模式下要能正常落盘
        orig = mailer.load_config
        mailer.load_config = lambda: {
            'email': 'me@example.com', 'username': 'me@example.com',
            'password': 'x', 'dry_run': True, 'host': 'smtp.example.com'}
        try:
            ok, msg = mailer.send_smtp(
                profile(),
                {'title': '行政专员/助理', 'company': '某单位', 'hr_email': 'hr@a.com',
                 'salary_text': '6000元'},
                mailer.load_config())
            self.assertTrue(ok, '带斜杠的岗位名把演练落盘搞挂了：%s' % msg)
        finally:
            mailer.load_config = orig

    # 80 —— 一封出意外不能带走整批
    def test_80_one_bad_send_does_not_kill_the_batch(self):
        """以前 send_smtp 抛异常会直接跳到最外层 except：整个任务变 error，
        后面的岗位一封都不发，结果面板上什么都没有 —— 用户只知道「失败了」，
        不知道哪家发了、哪家没发。现在要逐条记，剩下的照发。"""
        orig_cfg, orig_send = mailer.load_config, mailer.send_smtp
        mailer.load_config = lambda: {
            'email': 'me@example.com', 'username': 'me@example.com',
            'password': 'x', 'dry_run': True, 'host': 'smtp.example.com'}

        def fake_send(p, job, cfg=None, **kw):
            # **kw：send_smtp 现在还会收到 tailored_path/tailored_name（按岗位定制
            # 简历的附件覆盖）。替身不收就会 TypeError，被「单封失败不带走整批」的
            # 兜底吞掉 —— 表现成「这一批每一封都失败」，排查时极易误导（2026-09-24）。
            if job.get('title') == '会炸的岗':
                raise RuntimeError('模拟发送过程中抛异常')
            return True, '演练模式（未真发），已存到 data/outbox'

        mailer.send_smtp = fake_send
        try:
            jobs = [dict(title='会炸的岗', company='甲', nature='国企', hr_email='a@b.com', key='x1'),
                    dict(title='正常岗', company='乙', nature='国企', hr_email='c@d.com', key='x2')]
            r = self.c.post('/api/apply', json={
                'profile_id': None, 'jobs': jobs, 'channel': 'smtp', 'profile': profile()})
            body = r.get_json()
            t = None
            for _ in range(50):
                time.sleep(0.1)
                t = self.c.get('/api/task/' + body['task_id']).get_json()['task']
                if t['status'] != 'running':
                    break
            self.assertEqual(t['status'], 'done', '一封炸了就整批中断了')
            self.assertEqual(len(t['items']), 2, '后面的岗位没发、也没记账')
            self.assertEqual(t['fail'], 1)
            self.assertEqual(t['success'], 1)
            self.assertIn('发送出错', t['items'][0]['status'])
            self.assertTrue(t['items'][1]['ok'], '第一封失败后，第二封应该照发')
        finally:
            mailer.load_config, mailer.send_smtp = orig_cfg, orig_send


    # 86 —— 服务商清单：说明里写支持的，配置就必须真能连上
    def test_86_provider_list_matches_smtp_presets(self):
        """「怎么开 SMTP」那份说明和真正用来发信的服务器参数必须是同一份事实。

        分开写两份的下场：说明里告诉用户「用 139 邮箱吧」，配置文件却解析不出
        smtp.139.com，用户照着一字不差做完还是发不出去——而且报错看不出原因。
        """
        b = self.c.get('/api/mail/providers').get_json()
        self.assertTrue(b['ok'])
        provs = b['providers']
        self.assertGreaterEqual(len(provs), 8, '常见邮箱列得太少，用户找不到自己那个')

        doms = set()
        for p in provs:
            for f in ('id', 'name', 'domain', 'webmail', 'auth', 'steps'):
                self.assertTrue(p.get(f), '服务商 %s 缺字段 %s' % (p.get('id'), f))
            self.assertTrue(p['steps'], '%s 没写开启步骤，等于让用户自己猜' % p['name'])
            self.assertTrue(p['webmail'].startswith('https://'), p['webmail'])
            # 关键一条：能列出来给用户选，就必须配得出服务器地址
            self.assertTrue(p['smtp'], '%s 在说明里出现，但 _SMTP_PRESETS 里没有它' % p['name'])
            host, _, port = p['smtp'].partition(':')
            self.assertEqual(mailer._SMTP_PRESETS[p['domain']][:2], (host, int(port)))
            doms.add(p['domain'])

        # 国内常用的这几家必须都在（少了哪家都有人找不到自己的邮箱）
        for d in ('qq.com', '163.com', '126.com', 'sina.com', 'sohu.com',
                  '139.com', '189.cn'):
            self.assertIn(d, doms, '常见邮箱 %s 不在清单里' % d)

    # 87 —— 新增的运营商邮箱要真能解析出服务器
    def test_87_operator_mailboxes_resolve(self):
        """找工作的人大量用手机号邮箱（13800138000@139.com）。加进说明里还不够，
        得真配得出 host/port，否则「支持」只是嘴上说说。"""
        cases = {
            '13800138000@139.com': ('smtp.139.com', 465, True),
            '13800138000@189.cn': ('smtp.189.cn', 465, True),
            'zhang@yeah.net': ('smtp.yeah.net', 465, True),
            'zhang@sina.cn': ('smtp.sina.cn', 465, True),
        }
        for addr, (host, port, ssl) in cases.items():
            cfg = mailer._apply_preset({'email': addr})
            self.assertEqual((cfg.get('host'), cfg.get('port'), cfg.get('ssl')),
                             (host, port, ssl), '%s 没解析出正确的服务器' % addr)

        real_path, orig_load = mailer.CONFIG_PATH, mailer.load_config
        mailer.CONFIG_PATH = os.path.join(TMP, 'mail_unknown.json')
        mailer.load_config = lambda: None
        try:
            # 不认识的域名要给出「换一个」「走哪条路」的可读提示，而不是让它存下去
            ok, msg, host = mailer.save_config('zhang@某个小公司.com', 'x', dry_run=True)
            self.assertFalse(ok, '不认识的邮箱域名被当成配置存下来了，之后每封都会发失败')
            self.assertFalse(os.path.exists(mailer.CONFIG_PATH))
            for kw in ('QQ', '139', '一封封发'):
                self.assertIn(kw, msg, '错误提示没告诉用户还能怎么办：%s' % msg)
        finally:
            mailer.CONFIG_PATH, mailer.load_config = real_path, orig_load

    # 88 —— 第 4 步必须有一个「与内容无关、常驻」的设置邮箱入口
    def test_88_step4_has_persistent_setup_entry(self):
        """用户反馈：「第四步没有设置邮箱的按钮」。原来的配置表单挂在
        「能代投」那一组里、而且只在有报名邮箱的岗位存在时才渲染——
        库里 2749 个岗位只有 14 个有邮箱，于是大多数时候这个入口根本不存在。
        入口必须在岗位分组之前出现，并且点开就有完整的设置界面。"""
        src = self.read_component('Step4Apply.vue')
        i_btn = src.find('openSetup')
        i_group = src.find('v-if="mailJobs.length"')
        self.assertNotEqual(i_btn, -1, '第 4 步没有设置发件邮箱的入口')
        self.assertNotEqual(i_group, -1)
        self.assertLess(i_btn, i_group,
                        '「设置发件邮箱」入口被放在岗位分组里面了——没有报名邮箱时它不会出现')
        # 弹窗本体：填邮箱/授权码 + 说明
        self.assertIn('showSetup', src)
        self.assertIn('saveMailConfig', src)
        self.assertIn('mailProviders', src, '没有从后端取服务商说明（说明会跟实现走散）')

    # 89 —— 「授权码」这件事必须写明，且每家都要有可照着点的步骤
    def test_89_smtp_guide_is_actionable(self):
        """几乎所有人卡住的地方不是填错，是不知道授权码是什么、去哪儿拿。
        说明里必须出现「授权码不等于登录密码」这句，并且每家都给了
        「在哪开」（页面路径）和可点的网页版链接。"""
        provs = self.c.get('/api/mail/providers').get_json()['providers']
        # 至少要有一家表明「填的是授权码」而不是登录密码
        self.assertTrue(any('授权码' in p['auth'] or '密码' in p['auth'] for p in provs))
        qq = [p for p in provs if p['domain'] == 'qq.com'][0]
        self.assertIn('授权码', qq['auth'])
        self.assertIn('不是 QQ 登录密码', qq['note'], '没提醒「授权码不是登录密码」')

        # 189 是个反例：它不吃授权码，说明必须写对，否则用户永远开不起来
        y = [p for p in provs if p['domain'] == '189.cn'][0]
        self.assertIn('登录密码', y['auth'])

        for p in provs:
            self.assertTrue(p.get('where'), '%s 没写「在哪开」' % p['name'])
            self.assertGreaterEqual(len(p['steps']), 3, '%s 的步骤太粗，用户照做不了' % p['name'])

        # 界面上要有那句最关键的提醒
        src = self.read_component('Step4Apply.vue')
        self.assertIn('授权码不等于登录密码', src)

    # 90 —— 挑邮箱那一下的新窗口不能被浏览器拦掉
    def test_90_mailbox_window_opens_before_await(self):
        """踩过的坑：pickProvider 里如果先 await 取邮件内容、再 window.open，
        浏览器会把新窗口当弹窗拦掉——用户点了「QQ邮箱」什么都没发生，
        正是这次要修的那个毛病。window.open 必须在这次点击的同步阶段调。"""
        src = self.read_component('Step4Apply.vue')
        self.assertIn('function pickProvider', src)
        body = src.split('function pickProvider', 1)[1].split('\nfunction ', 1)[0]
        i_open = body.find('window.open')
        i_await = body.find('await')
        self.assertNotEqual(i_open, -1, '挑完邮箱没打开写信页')
        self.assertTrue(i_await == -1 or i_open < i_await,
                        'window.open 排在 await 后面了，新窗口会被当弹窗拦掉')

        # 「用我自己的邮箱一封封发」不能再直接跳 mailto
        self.assertIn("openPicker('batch')", src,
                      '「用我自己的邮箱一封封发」没有先弹选邮箱的界面')
        self.assertNotIn('window.location.href = m.mailto', src)

    # 91 —— 微软系个人邮箱：不能用密码代发，这件事必须被代码认出来
    def test_91_microsoft_mailboxes_are_oauth_only(self):
        """用户 2026-09-21 反馈：打开 Outlook 的邮箱设置页，只有
        「身份验证方法：OAuth2/Modern Auth」，**根本没有授权码这一项**。

        原因是微软已经把 SMTP 的基本验证停掉了，应用密码也建在它上面。
        所以要做两件事：认出这类域名（别再照旧文档写「填登录密码就行」），
        以及把服务器名写对 —— 个人邮箱是 smtp-mail.outlook.com（企业版才用
        smtp.office365.com），微软自己的设置页上就是这么写的。
        """
        for a in ('me@outlook.com', 'me@hotmail.com', 'me@live.com', 'me@msn.com'):
            self.assertTrue(mailer.oauth_only(a), '%s 没被认出来是「只认 OAuth2」的微软系' % a)
        for a in ('me@qq.com', 'me@163.com', 'me@139.com', 'me@gmail.com'):
            self.assertFalse(mailer.oauth_only(a), '%s 被错认成微软系了' % a)

        cfg = mailer._apply_preset({'email': 'me@outlook.com'})
        self.assertEqual(cfg['host'], 'smtp-mail.outlook.com',
                         '个人 Outlook 邮箱的服务器是 smtp-mail.outlook.com，不是 office365')
        self.assertEqual((cfg['port'], cfg['ssl'], cfg['starttls']), (587, False, True))

    # 92 —— 说明里必须说「这条路关了」，并给出能走的路
    def test_92_outlook_entry_says_route_is_closed(self):
        """如果说明还写着「填登录密码就行」，用户就会照着找授权码、反复失败 ——
        他刚就是这么卡住的。标 smtp_ok=False + 明确指向「网页发送 / 换个邮箱」。"""
        b = self.c.get('/api/mail/providers').get_json()
        o = [p for p in b['providers'] if p['domain'] == 'outlook.com'][0]
        self.assertFalse(o['smtp_ok'], 'Outlook 还被标成能用密码代发')
        # 不能给一个「要填什么密码」的假答案：用户会照做，然后失败
        self.assertIn('OAuth2', o['auth'], 'Outlook 那条没写清「只认 OAuth2」')
        # 出路要写在步骤里，而且两条都要有：网页发送 + 换个邮箱
        joined = ' '.join(o['steps']) + o['note']
        for kw in ('一封封发', '网页', '换一个 QQ'):
            self.assertIn(kw, joined, 'Outlook 的说明没告诉用户还能怎么办（缺「%s」）' % kw)
        self.assertIn('应用密码', joined, '没点名「应用密码也没了」——用户还是会去找')

        # 前端要能一眼分出「不能用」和「填：授权码」
        src = self.read_component('Step4Apply.vue')
        self.assertIn('prov-tag-no', src)
        self.assertIn('不能用密码代发', src)

        # 其他家不能被误伤：都得是可用的
        for p in b['providers']:
            if p['domain'] != 'outlook.com':
                self.assertTrue(p['smtp_ok'], '%s 被标成不能代发了' % p['name'])

    # 93 —— 「测一测」接口：不能谎报成功，也不能联网瞎试
    def test_93_verify_endpoint_never_fakes_success(self):
        """用户没法自己判断授权码对不对。这个接口按一下就给结论，
        所以它自己更不能谎报：地址不合法、域名不认识、微软系，都要如实说，
        而且不许真去连服务器（测试里就是不许有网络依赖）。"""
        b = self.c.post('/api/mail/verify', json={'email': '', 'password': ''}).get_json()
        self.assertFalse(b['ok'], '空邮箱 + 没配置也回「成功」了')

        b = self.c.post('/api/mail/verify',
                        json={'email': 'zhang@某个小公司.com', 'password': 'x'}).get_json()
        self.assertFalse(b['ok'])
        self.assertIn('QQ', b['msg'], '域名不认识时没告诉用户能换成什么：%s' % b['msg'])

        # 微软系：直接说清楚，不去连服务器浪费时间
        b = self.c.post('/api/mail/verify',
                        json={'email': 'me@outlook.com', 'password': 'x'}).get_json()
        self.assertFalse(b['ok'])
        self.assertIn('OAuth2', b['msg'])
        self.assertIn('网页', b['msg'])

        # 密码框空着也要当场说，别等连服务器报错
        b = self.c.post('/api/mail/verify',
                        json={'email': 'me@qq.com', 'password': ''}).get_json()
        self.assertFalse(b['ok'])
        self.assertIn('授权码', b['msg'])

    # 94 —— 认证被拒时要说人话（点名该填什么），不能只回一串 535
    def test_94_verify_auth_reject_is_actionable(self):
        """登录被拒最常见的原因是「把登录密码当授权码填」。
        提示里必须点名这家要填什么 —— 只回「认证失败」用户永远试不出来。"""
        orig = mailer.smtplib.SMTP_SSL

        class FakeSMTP:
            def __init__(self, *a, **k):
                pass

            def login(self, u, p):
                raise mailer.smtplib.SMTPAuthenticationError(
                    535, b'5.7.3 Authentication unsuccessful')

            def quit(self):
                pass

        mailer.smtplib.SMTP_SSL = FakeSMTP
        try:
            ok, msg = mailer.verify_login('me@qq.com', 'my-login-password')
            self.assertFalse(ok)
            self.assertIn('授权码', msg, '没点名「这个邮箱要填授权码」：%s' % msg)
            self.assertIn('不是登录密码', msg)
            self.assertIn('535', msg, '没带上服务器原话，用户报修时说不清')

            # 139 是另一个反例：它要的是自己设的客户端密码
            ok, msg = mailer.verify_login('13800138000@139.com', 'x')
            self.assertFalse(ok)
            self.assertIn('客户端密码', msg, '139 的提示没写清要填客户端密码：%s' % msg)
        finally:
            mailer.smtplib.SMTP_SSL = orig

    # 95 —— 群发时整批卡在登录上，也不能只回「发送失败」
    def test_95_send_auth_failure_explains(self):
        """一封封发的时候认证失败，用户看到的是 N 条「发送失败：…535…」。
        这里必须把「该填什么、去哪儿拿」带出来，否则整批都白跑。"""
        orig = mailer.smtplib.SMTP_SSL

        class FakeSMTP:
            def __init__(self, *a, **k):
                pass

            def login(self, u, p):
                raise mailer.smtplib.SMTPAuthenticationError(535, b'5.7.3 nope')

            def sendmail(self, *a, **k):
                raise AssertionError('登录都失败了，不该走到发送')

            def quit(self):
                pass

        mailer.smtplib.SMTP_SSL = FakeSMTP
        try:
            cfg = {'email': 'me@163.com', 'username': 'me@163.com', 'password': 'x',
                   'dry_run': False, 'host': 'smtp.163.com', 'port': 465, 'ssl': True}
            job = {'title': '行政专员', 'company': '某单位', 'hr_email': 'hr@a.com', 'key': 'k1'}
            ok, msg = mailer.send_smtp(profile(), job, cfg)
            self.assertFalse(ok, '登录失败却报了成功')
            self.assertIn('授权码', msg, '发送失败的提示没告诉用户该填什么：%s' % msg)
        finally:
            mailer.smtplib.SMTP_SSL = orig

    # 96 —— 界面：打错邮箱当场提醒 + 「测一测」按钮 + 一条真能走通的出路
    def test_96_step4_warns_on_microsoft_mailbox(self):
        """用户在「你的邮箱地址」里打 outlook.com 的那一刻，就该看到提醒，
        而不是填完授权码、保存、群发、全失败之后才知道。"""
        src = self.read_component('Step4Apply.vue')
        self.assertIn('cfgNoPwd', src, '没有「这个邮箱不能用密码发」的当场提醒')
        self.assertIn('oauth_only', src, '域名清单没从后端取（自己抄一份就会和实现走散）')
        self.assertIn('测一测', src)
        self.assertIn('mailVerify', src, '「测一测」没接后端接口')
        self.assertIn('不发信', src, '没写明「测一测」不会真发信，用户不敢按')
        self.assertIn('useWebmailInstead', src, '微软系邮箱没有给出「改用网页发送」这条出路')
        # 存下来了但发不出去也不行：后端的 warn 要显示出来
        self.assertIn('r.warn', src)

        app_src = open(os.path.join(BASE_DIR, 'app.py'), encoding='utf-8').read()
        self.assertIn('oauth_only', app_src, '接口没把「微软系域名」给前端')

    # 97 —— 读库路径的岗位必须有「去官网投」的入口（url → apply_url 别名）
    def test_97_cached_jobs_keep_apply_url(self):
        """踩过的坑：库里那一列叫 url，采集器/前端/自动填表用的是 apply_url。

        少了别名，走「读库优先」这条路返回的岗位在前端**没有投递入口** ——
        第 3 步点「去官网投」没反应；而现爬那条路是好的，同一份数据两种表现。
        测试环境就是这么翻出来的：test_15 断言「每个岗位都要有投递入口」，
        缓存装满之前一直是绿的，装满之后就红了。
        """
        key = 'utest|别名测试单位|别名测试岗|北京'
        # 只带 apply_url（采集器就是这么给的）也要能存下、并原样读回来
        models.upsert_jobs([{
            'key': key, 'title': '别名测试岗', 'company': '别名测试单位',
            'city': '北京', 'nature': '国企', 'source': 'gov_soe',
            'apply_url': 'https://example.com/apply/123'}])
        j = [x for x in models.query_jobs(city='北京') if x.get('key') == key]
        self.assertEqual(len(j), 1, '写进去的岗位没读出来')
        self.assertEqual(j[0].get('apply_url'), 'https://example.com/apply/123',
                         '读库路径把投递入口弄丢了（前端「去官网投」会没反应）')
        self.assertEqual(models.get_job(key).get('apply_url'),
                         'https://example.com/apply/123',
                         '单岗位读取路径也丢了 apply_url')

    # 98 —— 各家服务商「拒绝」的方式不一样，提示不能只对 535 有效
    def test_98_smtp_failures_are_classified(self):
        """2026-09-21 拿真服务器实测出来的四种拒绝方式（别只按 535 写）：

        | 邮箱 | 真实反应 |
        |---|---|
        | QQ   | 什么都不回，**直接断连接**（SMTPServerDisconnected） |
        | 163  | 550 User has no permission（多半是 SMTP 服务没开） |
        | 126  | 550 + **GBK 编码的中文**（按 utf-8 解是乱码） |
        | 139  | 454 Authentication failed(...) |

        用户看到的必须是能照着做的话：填什么、去哪儿拿、要不要先开服务。
        """
        def fake(err):
            orig = mailer.smtplib.SMTP_SSL

            class S:
                def __init__(self, *a, **k):
                    pass

                def login(self, u, p):
                    raise err

                def quit(self):
                    pass
            mailer.smtplib.SMTP_SSL = S
            return orig

        # ① QQ：断连接 —— 以前这里只会吐出「Connection unexpectedly closed」
        orig = fake(mailer.smtplib.SMTPServerDisconnected('Connection unexpectedly closed'))
        try:
            ok, msg = mailer.verify_login('me@qq.com', 'x')
            self.assertFalse(ok)
            self.assertIn('授权码', msg)
            self.assertIn('断', msg, '没说清「服务器把连接断了」这个真实情况：%s' % msg)
            self.assertNotEqual(msg, '登录时出错：Connection unexpectedly closed',
                                'QQ 的断连接又原样吐给用户了，等于没说')
        finally:
            mailer.smtplib.SMTP_SSL = orig

        # ② 126 的 GBK 中文原话不能变成乱码，而且要把「服务可能没开」也说出来
        err = mailer.smtplib.SMTPAuthenticationError(550, '用户无权登陆'.encode('gbk'))
        orig = fake(err)
        try:
            ok, msg = mailer.verify_login('me@126.com', 'x')
            self.assertFalse(ok)
            self.assertIn('用户无权登陆', msg, 'GBK 的原话被解成了乱码：%s' % msg)
            self.assertNotIn('\ufffd', msg, '提示里有乱码字符')
            self.assertIn('SMTP', msg, '没提「服务可能没开」（163/126 最常见的真因）')
        finally:
            mailer.smtplib.SMTP_SSL = orig

        # ③ 连不上（端口不通/超时）要说「网络」而不是「授权码错」
        orig = fake(OSError('timed out'))
        try:
            ok, msg = mailer.verify_login('me@139.com', 'x')
            self.assertFalse(ok)
            self.assertIn('网络', msg, '连不上时的提示没说可能是网络：%s' % msg)
        finally:
            mailer.smtplib.SMTP_SSL = orig

    # 99 —— 群发时同样要分得清「连不上 / 登录被拒 / 发信失败」
    def test_99_batch_send_classifies_failures(self):
        """整批卡在登录上是最常见的失败。逐封记的那条 status 必须能照着做 ——
        「发送失败：…」后面跟一串异常名，用户只能来问我们。"""
        orig = mailer.smtplib.SMTP_SSL

        class S:
            def __init__(self, *a, **k):
                pass

            def login(self, u, p):
                raise mailer.smtplib.SMTPServerDisconnected('Connection unexpectedly closed')

            def sendmail(self, *a, **k):
                raise AssertionError('登录都失败了，不该走到发送')

            def quit(self):
                pass

        mailer.smtplib.SMTP_SSL = S
        try:
            cfg = {'email': 'me@qq.com', 'username': 'me@qq.com', 'password': 'x',
                   'dry_run': False, 'host': 'smtp.qq.com', 'port': 465, 'ssl': True}
            job = {'title': '行政专员', 'company': '某单位', 'hr_email': 'hr@a.com', 'key': 'k1'}
            ok, msg = mailer.send_smtp(profile(), job, cfg)
            self.assertFalse(ok)
            self.assertIn('授权码', msg, '群发登录失败的提示还是没说该填什么：%s' % msg)
        finally:
            mailer.smtplib.SMTP_SSL = orig

        # 连接阶段失败 → 说「网络」，不要误导成授权码错
        class S2:
            def __init__(self, *a, **k):
                raise OSError('network is unreachable')
        mailer.smtplib.SMTP_SSL = S2
        try:
            ok, msg = mailer.send_smtp(profile(), {'title': 'x', 'hr_email': 'hr@a.com',
                                                   'key': 'k2'}, cfg)
            self.assertFalse(ok)
            self.assertIn('网络', msg, '连不上服务器时没说可能网络问题：%s' % msg)
        finally:
            mailer.smtplib.SMTP_SSL = orig

    # 100 —— 真发之前必须先登一次；登录过不去就别开跑
    def test_100_real_run_preflights_login(self):
        """2026-09-21 用户实测：填错密码点「一键代投」，任务照常启动，
        每封之间还等 20 秒 —— 4 封等了 80 秒，拿到 4 条一模一样的失败，
        他的反馈是「点了没反应」。

        整个批次用的是同一个发件账号，登录过不去就是整批都过不去。
        正确做法：开跑前花一次连接登一下，失败就**同步**告诉他为什么。"""
        orig_cfg, orig_ver = mailer.load_config, mailer.verify_login
        seen = []
        mailer.load_config = lambda: {'email': 'me@qq.com', 'username': 'me@qq.com',
                                      'password': 'bad', 'dry_run': False,
                                      'host': 'smtp.qq.com', 'port': 465, 'ssl': True}
        mailer.verify_login = lambda e, p, **kw: (
            seen.append((e, p, kw.get('host'))), (False, '服务器在登录时把连接直接断了'))[1]
        try:
            r = self.c.post('/api/apply', json={
                'jobs': [{'key': 'k1', 'title': 'a', 'hr_email': 'hr@a.com'}],
                'channel': 'smtp', 'profile': profile()})
            self.assertEqual(r.status_code, 400, '登录过不去还照样启动了批次')
            d = r.get_json()
            self.assertFalse(d['ok'])
            self.assertIn('一封都没发出去', d['msg'])
            self.assertIn('连接直接断了', d['msg'], '没把真实原因带回来：%s' % d['msg'])
            self.assertNotIn('task_id', d, '预检失败却建了任务 → 界面会显示一个假进度条')
            self.assertEqual(d.get('fail_reason'), 'login')
            self.assertEqual(seen[0][0], 'me@qq.com', '预检没用配置里的发件邮箱')
            self.assertEqual(seen[0][2], 'smtp.qq.com', '预检没用配置里的服务器')
        finally:
            mailer.load_config, mailer.verify_login = orig_cfg, orig_ver

    # 101 —— 演练模式不许去连服务器（否则测试和演练都变成「真连一次」）
    def test_101_dry_run_skips_preflight(self):
        orig_ver = mailer.verify_login
        seen = []
        mailer.verify_login = lambda *a, **k: (seen.append(a), (True, ''))[1]
        try:
            # 文件顶部的 monkeypatch 让 load_config 回 dry_run=True
            r = self.c.post('/api/apply', json={
                'jobs': [{'key': 'k1', 'title': 'a', 'hr_email': 'hr@a.com'}],
                'channel': 'smtp', 'profile': profile()})
            self.assertTrue(r.get_json()['ok'], '演练模式被预检挡住了')
            self.assertEqual(seen, [], '演练模式去连服务器了 —— 本该完全不碰网络')
        finally:
            mailer.verify_login = orig_ver

    # 102 —— 授权码的形态：QQ 一族是 16 位全小写字母
    def test_102_auth_code_shape_warning(self):
        """用户实际填过的东西：13 位、含大写和数字（那是登录密码的形态）。
        这种能当场认出来的错，不该等到发完一整批才发现。"""
        w = mailer.pwd_shape_warning('252021763@qq.com', 'Heyiyi1989414')
        self.assertIn('不像授权码', w)
        self.assertIn('13 位', w)
        self.assertIn('登录密码', w, '没点破「这多半是登录密码」')
        self.assertIn('16 位全小写字母', w, '没说清正确形态')
        self.assertNotIn('<b>', w, '提示会以纯文本渲染，写 HTML 标签会原样显示出来')

        # 正确的授权码不该被冤枉
        self.assertEqual(mailer.pwd_shape_warning('252021763@qq.com', 'abcdefghijklmnop'), '')
        self.assertEqual(mailer.pwd_shape_warning('me@163.com', 'zyxwvutsrqponmlk'), '')
        # 139 的「客户端密码」、189 的登录密码是用户自设的，长度没准 —— 不许校验
        self.assertEqual(mailer.pwd_shape_warning('me@139.com', 'Abc12345'), '')
        self.assertEqual(mailer.pwd_shape_warning('me@189.cn', 'mypassword'), '')
        # 空值不该报形态问题（那是「还没填」，由别的分支去说）
        self.assertEqual(mailer.pwd_shape_warning('me@qq.com', ''), '')

    # 103 —— 「卡在登录上」要能被认出来（批处理早停靠它）
    def test_103_auth_failure_is_detectable(self):
        for m in ('服务器在登录时把连接直接断了。QQ/163 在授权码不对时就是这么回的',
                  '服务器拒绝了这次登录。两件事确认一下：①「密码」框里填的是「授权码」',
                  'me@outlook.com：微软已不接受用密码发信（官方设置页里写的就是 OAuth2）'):
            self.assertTrue(mailer.is_auth_failure(m), '认不出这是登录失败：%s' % m[:20])
        for m in ('跟服务器说不上话（timed out）。看看网络',
                  '发送失败：附件不存在', ''):
            self.assertFalse(mailer.is_auth_failure(m), '把非登录问题误判成登录失败：%s' % m)

    # 104 —— 登录失败就早停，别拿同一个密码把后面每封都撞一遍
    def test_104_batch_stops_early_on_login_failure(self):
        import app as appmod
        orig_cfg, orig_send = mailer.load_config, mailer.send_smtp
        orig_interval = appmod.SEND_INTERVAL
        tried = []
        mailer.load_config = lambda: {'email': 'me@qq.com', 'username': 'me@qq.com',
                                      'password': 'bad', 'dry_run': False,
                                      'host': 'smtp.qq.com', 'port': 465, 'ssl': True}
        mailer.send_smtp = lambda prof, job, cfg, **kw: (
            tried.append(job['title']),
            (False, '服务器在登录时把连接直接断了。QQ/163 在授权码不对时就是这么回的'))[1]
        appmod.SEND_INTERVAL = 0
        try:
            tid = 'test-early-stop'
            appmod.TASKS[tid] = {'id': tid, 'status': 'running', 'total': 4, 'done': 0,
                                 'success': 0, 'fail': 0, 'skipped': 0, 'current': '',
                                 'items': [], 'dry': False, 'test_to': '', 'stopped': ''}
            jobs = [{'key': 'k%d' % i, 'title': '岗位%d' % i, 'hr_email': 'hr@a.com',
                     'company': 'c'} for i in range(1, 5)]
            appmod._run_apply(tid, None, jobs)
            t = appmod.TASKS[tid]
            self.assertEqual(tried, ['岗位1'], '登录都失败了还继续撞后面的：%s' % tried)
            self.assertEqual(t['not_attempted'], 3, '没交代还剩几封没发')
            self.assertTrue(t['stopped'], '没记下停下来的原因')
            self.assertEqual(t['status'], 'done')
            self.assertEqual(t['fail'], 1)
        finally:
            mailer.load_config, mailer.send_smtp = orig_cfg, orig_send
            appmod.SEND_INTERVAL = orig_interval
            appmod.TASKS.pop('test-early-stop', None)

    # 105 —— 首封立刻发：不许让用户先干等一个间隔
    def test_105_first_mail_is_sent_immediately(self):
        """原来循环开头先 sleep，首封也要等 20 秒：进度条停在 0/4、
        屏幕上什么都不动，跟「点了没反应」长得一模一样。"""
        import app as appmod
        orig_cfg, orig_send = mailer.load_config, mailer.send_smtp
        orig_interval, orig_sleep = appmod.SEND_INTERVAL, appmod.time.sleep
        order = []
        mailer.load_config = lambda: {'email': 'me@qq.com', 'username': 'me@qq.com',
                                      'password': 'ok', 'dry_run': False,
                                      'host': 'smtp.qq.com', 'port': 465, 'ssl': True}
        mailer.send_smtp = lambda prof, job, cfg, **kw: (order.append('send'), (True, '已发送'))[1]
        appmod.SEND_INTERVAL = 20
        appmod.time.sleep = lambda s: order.append('sleep%d' % s)
        try:
            tid = 'test-first-now'
            appmod.TASKS[tid] = {'id': tid, 'status': 'running', 'total': 3, 'done': 0,
                                 'success': 0, 'fail': 0, 'skipped': 0, 'current': '',
                                 'items': [], 'dry': False, 'test_to': '', 'stopped': ''}
            jobs = [{'key': 'k%d' % i, 'title': '岗位%d' % i, 'hr_email': 'hr@a.com',
                     'company': 'c'} for i in range(1, 4)]
            appmod._run_apply(tid, None, jobs)
            self.assertEqual(order, ['send', 'sleep20', 'send', 'sleep20', 'send'],
                             '首封前不该等待（应是 send → sleep → send…）：%s' % order)
        finally:
            mailer.load_config, mailer.send_smtp = orig_cfg, orig_send
            appmod.SEND_INTERVAL, appmod.time.sleep = orig_interval, orig_sleep
            appmod.TASKS.pop('test-first-now', None)

    # 106 —— 状态接口要能把「授权码形态可疑」和完整邮箱带给界面
    def test_106_status_carries_pwd_warn_and_email(self):
        orig_cfg = mailer.load_config
        try:
            mailer.load_config = lambda: {'email': '252021763@qq.com',
                                          'username': '252021763@qq.com',
                                          'password': 'Heyiyi1989414', 'dry_run': False}
            d = self.c.get('/api/mail/status').get_json()
            self.assertEqual(d['email'], '252021763@qq.com',
                             '回了打码地址 → 设置面板没法预填，用户得把邮箱重打一遍')
            self.assertIn('不像授权码', d['pwd_warn'], '状态里没带上授权码形态提醒')
            # 每次进这一步都能看到提醒，不是只在「保存那一下」说一次
            mailer.load_config = lambda: {'email': 'x@qq.com', 'username': 'x@qq.com',
                                          'password': 'abcdefghijklmnop', 'dry_run': False}
            self.assertEqual(self.c.get('/api/mail/status').get_json()['pwd_warn'], '')
        finally:
            mailer.load_config = orig_cfg

    # 107 —— 结果面板必须说实话：一封都没发出去时不许写成「邮件发完了」
    def test_107_result_panel_reports_failure(self):
        """用户反馈「点了真实投递没反应」的直接原因就是这个面板：
        4 封全失败，它照样写「邮件发完了 / 0 封 / 单位回复会直接发到你的邮箱」，
        失败只体现在下面几行灰色小字里，扫一眼等于没看见。"""
        src = self.read_component('Step4Apply.vue')
        self.assertIn('一封都没发出去', src, '结果面板没有「全失败」这个状态')
        self.assertIn('去改发件邮箱', src, '全失败时没给下一步')
        self.assertIn('done-bad', src)
        self.assertIn('allFailed', src)
        self.assertIn('task.status === \'error\'', src, '后端预检挡回来时面板没有对应状态')
        # 早停过、还有没试过的封数，必须写出来
        self.assertIn('not_attempted', src, '早停后没交代还剩几封没发')
        # 真发要等多久也要说出来
        self.assertIn('etaText', src)
        self.assertIn('task.interval', src)

    def test_108_setup_modal_is_not_closed_by_a_stray_click(self):
        """设置弹窗不能一点就关。

        2026-09-21 用户反馈「在填写账号密码的界面，点击任意地方都会弹出去」。
        根因：弹窗挂着 @click.self，而 .modal 有 1rem 内边距、内容超高时上下还留白，
        这些留白在 DOM 上属于遮罩本身 —— 点一下就关。他填完授权码被误关，
        data/mail.json 里存着的还是旧的登录密码，等于白填一遍。
        要填东西的弹窗不该认「点别处 = 取消」。
        """
        src = self.read_component('Step4Apply.vue')
        self.assertIn('v-if="showSetup" class="modal">', src,
                      '设置弹窗又挂回了遮罩点击关闭')
        self.assertNotIn('@click.self="showSetup = false"', src)
        self.assertIn('@click="closeSetup"', src,
                      '关闭按钮没走 closeSetup —— 未保存就被关掉，用户白填')

    def test_109_unsaved_credentials_are_announced(self):
        """填了没保存必须当场说。

        「测一测」几秒钟就回「登录成功」，很容易让人以为已经设好了 ——
        用户就是这么丢的。输入框要挂未保存标记，并且关弹窗时要拦一下。
        """
        src = self.read_component('Step4Apply.vue')
        self.assertIn('const cfgDirty', src)
        self.assertEqual(src.count('@input="cfgDirty = true"'), 2,
                         '邮箱/授权码两个输入框都要挂未保存标记')
        self.assertIn('cfg-unsaved', src, '没有「改了还没保存」的提醒条')
        self.assertIn('还没保存', src)
        self.assertIn('关掉就白填了', src, '关闭时没有拦一下')

    def test_110_verify_only_checks_and_never_saves(self):
        """「测一测」只连服务器验密码，不许顺手写配置 —— 它和「保存」是两件事。"""
        src = self.read_component('Step4Apply.vue')
        i = src.index('async function verifyCfg')
        seg = src[i:src.index('\n}\n', i)]
        self.assertIn('mailVerify', seg)
        self.assertNotIn('mailConfig', seg, '测一测里混进了保存调用')
        self.assertIn('还没保存', seg, '登录成功后没说明这一步不会保存')

    def test_111_save_needs_an_email_first(self):
        """邮箱空着就发请求，用户收到的是一句和「授权码」八竿子打不着的报错，
        转头又去折腾授权码 —— 先在本地说清，并把焦点送回邮箱框。"""
        src = self.read_component('Step4Apply.vue')
        head = src[src.index('async function saveMailConfig'):]
        head = head[:head.index('await api.mailConfig')]
        self.assertIn('cfgEmail.value.trim()', head)
        self.assertIn('cfg-email', head, '没把焦点送回邮箱框')
        self.assertIn('邮箱地址还没填', head)

    def _tmp_mail_config(self, initial):
        """把配置读写指到临时文件，返回 (读函数, 还原函数)。

        两件事必须一起做，否则用例跑不起来（2026-09-21 踩过）：
        1) test_web.py 顶部把 mailer.CONFIG_PATH 指到了 TMP、把 load_config
           换成了固定桩（没有 password 键）—— 直接调 save_config 会读到那个桩。
        2) 测试永远不许动用户真实的 data/mail.json（跑一次就要人肉还原发件账号）。
        所以照 test_82 的做法：临时换 CONFIG_PATH + 装一个能真读的 load_config。
        """
        import mailer as m
        real_path, real_load = m.CONFIG_PATH, m.load_config
        tmp = os.path.join(TMP, 'mail_guard.json')
        if initial is not None:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(initial, f, ensure_ascii=False)

        def fake_load():
            if not os.path.exists(tmp):
                return None
            with open(tmp, encoding='utf-8') as f:
                return m._apply_preset(json.load(f))

        def read():
            if not os.path.exists(tmp):
                return None
            with open(tmp, encoding='utf-8') as f:
                return json.load(f)

        m.CONFIG_PATH, m.load_config = tmp, fake_load

        def restore():
            m.CONFIG_PATH, m.load_config = real_path, real_load
            if os.path.exists(tmp):
                os.remove(tmp)
        return read, restore

    def _base_cfg(self, email=u'252021763@qq.com', pwd=u'swiitvggktizcaha',
                  dry_run=False):
        return {'email': email, 'username': email, 'password': pwd,
                'host': 'smtp.qq.com', 'port': 465, 'ssl': True,
                'dry_run': dry_run, 'test_to': u'me@outlook.com'}

    # 112 —— 密码框空着保存，不许把已存的授权码覆盖成空
    def test_112_empty_password_keeps_saved_authcode(self):
        """用户点开设置面板没动密码框（或特意清空），一点「保存」不该毁掉已存的
        授权码 —— 空密码 = 「不改」。2026-09-21 他在这里丢过配置，之后每批都发不出去。"""
        read, restore = self._tmp_mail_config(self._base_cfg())
        try:
            ok, msg, _ = mailer.save_config(u'252021763@qq.com', '', dry_run=None)
            self.assertTrue(ok, msg)
            self.assertEqual(read()['password'], u'swiitvggktizcaha',
                             u'空密码把授权码覆盖掉了 —— 用户只点了一下保存就白配了')
        finally:
            restore()

    # 113 —— 保存配置后就是真发（「演练」这一档已从产品里下线）
    def test_113_save_leaves_real_send_on(self):
        """演示用的「演练模式」已经拿掉了：保存配置之后 dry_run 必须是 False，
        点「一键代投」就是真发给招聘方。以前这条防的是「保存顺手把真实发送
        打回演练」；现在整档都不存在了，只剩「永远真发」这一种状态。"""
        read, restore = self._tmp_mail_config(self._base_cfg(email=u'a@qq.com',
                                                            pwd=u'abcdefghijklmnop'))
        try:
            ok, msg, _ = mailer.save_config(u'a@qq.com', u'swiitvggktizcaha', dry_run=None)
            self.assertTrue(ok, msg)
            self.assertFalse(read()['dry_run'], u'保存后还不是真发：%s' % msg)
            self.assertEqual(read()['password'], u'swiitvggktizcaha')
        finally:
            restore()
        # 首次配置（原本没有文件）也直接就是真发 —— 不再默认演练
        read2, restore2 = self._tmp_mail_config(None)
        try:
            mailer.save_config(u'b@163.com', u'abcdefghijklmnop', dry_run=None)
            self.assertFalse(read2()['dry_run'], u'首次配置没走真发')
        finally:
            restore2()

    # 114 —— 接口层：空密码保存不动授权码，且照实回报演练/真发
    def test_114_config_api_keeps_authcode_and_reports_state(self):
        read, restore = self._tmp_mail_config(self._base_cfg())
        try:
            r = self.c.post('/api/mail/config',
                            json={'email': u'252021763@qq.com', 'password': ''})
            d = r.get_json()
            self.assertTrue(d['ok'], d.get('msg'))
            self.assertFalse(d['dry_run'], u'保存接口把真实发送改回演练了')
            self.assertEqual(read()['password'], u'swiitvggktizcaha')
            self.assertIn(u'真实发送', d['msg'], u'没告诉用户现在到底会不会真发')
        finally:
            restore()

    # 115 —— 换邮箱却不带新授权码：拒绝
    def test_115_changing_mailbox_needs_new_authcode(self):
        """授权码只对同一个邮箱有效。拿 A 邮箱的授权码写成 B 邮箱的配置，
        等于写了一份注定登录失败的配置，还顺手把真实发送开着。"""
        read, restore = self._tmp_mail_config(self._base_cfg(email=u'a@qq.com',
                                                            pwd=u'abcdefghijklmnop'))
        try:
            r = self.c.post('/api/mail/config', json={'email': u'b@qq.com', 'password': ''})
            self.assertEqual(r.status_code, 400)
            self.assertIn(u'不通用', r.get_json()['msg'])
            self.assertEqual(read()['email'], u'a@qq.com', u'拒绝时还是把邮箱改了')
        finally:
            restore()

    # 116 —— 「测一测」通过过的那串，不再报「形态可疑」
    def test_116_verified_password_silences_shape_warning(self):
        """用户按「测一测」看到「登录成功」，回头界面还说「密码不像授权码」，
        他只会觉得软件自相矛盾，然后跑去查邮箱（2026-09-21 就卡在这儿）。
        实测优先于按位数猜。"""
        read, restore = self._tmp_mail_config(self._base_cfg(pwd=u'Heyiyi1989414'))
        try:
            self.assertIn(u'不像授权码',
                          mailer.pwd_warning(u'252021763@qq.com', u'Heyiyi1989414'))
            mailer.mark_verified(u'Heyiyi1989414')
            self.assertEqual(mailer.pwd_warning(u'252021763@qq.com', u'Heyiyi1989414'), '',
                             u'实测登录成功过，界面还在拿位数吓唬用户')
            # 换成另一串没测过的：警告要回来（实测只对测过的那串有效）
            self.assertIn(u'不像授权码',
                          mailer.pwd_warning(u'252021763@qq.com', u'Other1234567'))
            # 授权码没换就再保存一次，实测记录不该被清掉
            mailer.save_config(u'252021763@qq.com', u'Heyiyi1989414', dry_run=None)
            self.assertEqual(read().get('verified_pwd'),
                             mailer.pwd_fingerprint(u'Heyiyi1989414'), u'保存把实测记录抹掉了')
        finally:
            restore()

    # 117 —— 提示文案要点准位置：哪家、几位、改完要保存
    def test_117_shape_warning_points_at_the_authcode(self):
        """用户 2026-09-21 看到这段提示，第一反应是「发件邮箱配错了」，跑去反复
        改邮箱 —— 提示必须点准「是哪串密码不对」，而不是让人怀疑邮箱。"""
        w = mailer.pwd_shape_warning('252021763@qq.com', 'Heyiyi1989414')
        self.assertIn('QQ邮箱', w, '没指名是哪家的授权码')
        self.assertIn('13 位', w, '没写出实际位数，用户没法核对是不是被自动填了')
        self.assertIn('保存', w, '没说改完之后要点保存')
        src = self.read_component('Step4Apply.vue')
        self.assertIn('授权码可能不对', src)
        # 只看模板里的标题（注释里会提到旧标题，不能连注释一起算）
        self.assertNotIn('cfg-block-t">发件邮箱可能配错了', src,
                         '标题还写着「发件邮箱配错」，用户会跑去改邮箱')

    # 118 —— 密码框必须挡住浏览器自动填充（事故根因）
    def test_118_password_box_blocks_autofill(self):
        """type="password" 的框会被浏览器记住并自动填充：用户打开面板只想换授权码，
        浏览器把上次输的登录密码填了进来，他一点「保存」，刚填好的授权码就被盖掉了
        —— 这是 2026-09-21 整条事故的根因，不是用户的错。"""
        src = self.read_component('Step4Apply.vue')
        seg = src[src.index('id="cfg-pwd"'):]
        seg = seg[:seg.index('/>')]
        self.assertIn('autocomplete="new-password"', seg,
                      '密码框没挡浏览器自动填充 —— 授权码会被旧登录密码盖掉')
        self.assertIn('留空 = 不改', src, '没告诉用户空着保存等于不改授权码')

    # 119 —— 扩源：中公行业子站必须真的在配置里，且能按「全国」源被选中
    def test_119_zhonggong_industry_sources_present(self):
        """2026-09-21 扩源：用户嫌「邮箱源太少」，实测发现中公的行业子站
        （国家电网/中石化/中海油/中国烟草…）公告带报名邮箱率高达 95%，
        是当时性价比最高的一批新源。配置少了它们，等于白测。"""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'data', 'gov_soe_sources.json'),
                  encoding='utf-8') as f:
            srcs = json.load(f)
        labels = [s['label'] for s in srcs]
        for want in ('中公·国家电网', '中公·中国石化', '中公·中海油', '中公·中国烟草'):
            self.assertTrue(any(want in x for x in labels), u'缺源：%s' % want)
        zg = [s for s in srcs if 'zggqzp.com' in s['list_url']]
        self.assertGreaterEqual(len(zg), 8, u'中公行业源太少，扩源没落地')
        # 每源都要有链接正则，否则采不到东西
        for s in zg:
            self.assertTrue(s.get('list_link_re'), u'%s 没配 list_link_re' % s['label'])

    # 120 —— 「全国」源不能被城市筛选整源跳过（否则扩源等于没扩）
    def test_120_nationwide_sources_survive_city_filter(self):
        """中公行业源的 city 写的是「全国」，它不绑定任何城市。早先 fetch() 里
        `if city and region_city:` 会让用户一选「武汉」就把这些源全部跳过 ——
        扩了 10 个源、结果一个都抓不到（2026-09-21 扩源时一并修）。"""
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'collectors', 'gov_soe.py'), encoding='utf-8').read()
        self.assertIn("region_city != '全国'", src,
                      'fetch() 没给「全国」源开口子，选城市时它们会被整源跳过')

    # 121 —— 公司名前的「2026年度」不能被剥成「度」（扩源后暴露的解析 bug）
    def test_121_company_name_lead_strip(self):
        """「2026年度广东省烟草专卖局」剥前缀时只吃掉「年」，剩个「度」挂到
        公司名头上，产出里出现「度广东省烟草专卖局」这种脏名字 —— 是扩源
        引入中公源之后才被发现（2026-09-21 修）。"""
        from collectors import gov_soe as G
        cases = [
            (u'2026年度广东省烟草专卖局招聘（公司）高校毕业生222名_烟草招聘',
             u'广东省烟草专卖局'),
            (u'2026年度揭阳市投资控股集团有限公司及下属企业招聘公告',
             u'揭阳市投资控股集团有限公司'),
            (u'2026年（第二批）中国石化江苏监理有限公司招聘公告',
             u'中国石化江苏监理有限公司'),
            (u'关于2026年湖北长江运营咨询有限公司招聘公告',
             u'湖北长江运营咨询有限公司'),
        ]
        for src_text, want in cases:
            got = G._find_company(src_text)
            self.assertEqual(got, want, u'%s → 得到 %r' % (src_text, got))
        # 超过 30 字的名字要能剥净前缀再匹配，而不是被截半
        self.assertNotIn(u'度', (G._find_company(
            u'2026年度广东省烟草专卖局招聘公告') or '')[:1], u'前缀剥不干净')

    # 122 —— 城市匹配不能把「全国」源和「省」级岗位挡在门外（扩源的最后一公里）
    def test_122_city_match_reaches_nationwide_and_province_jobs(self):
        """2026-09-21 扩了 10 个中公行业源，它们 city 全写「全国」；同时省里发的
        公告只写「湖北」。而旧的匹配是纯子串：`武汉 in 湖北` / `湖北 in 武汉`
        都是假 —— 用户一选「武汉」，这 10 个新源和全省岗位**整批消失**，
        扩源等于白扩（实测武汉从 2 条带邮箱变 39 条，差的就是这一句）。"""
        import models
        # 全国性岗位：选任何城市都该出现
        self.assertTrue(models._city_hit(u'武汉', u'全国'))
        self.assertTrue(models._city_hit(u'成都', u'全国'))
        # 省份关系：选市→标省、选省→标市，双向都要命中
        self.assertTrue(models._city_hit(u'武汉', u'湖北'))
        self.assertTrue(models._city_hit(u'湖北', u'武汉'))
        # 子串关系仍然成立
        self.assertTrue(models._city_hit(u'武汉', u'武汉市'))

    # 123 —— 电话：民企/基层岗位只留手机号，邮箱这条路天生覆盖不到
    # （2026-09-22 实测：468 条 gov_soe 公告，带邮箱 139 条，含座机 277 条）
    def test_123_phone_picked_by_its_label_not_position(self):
        """「监督电话」常常写在「联系电话」前面，只按出现顺序取第一个，
        用户拨过去就是纪委。判据必须看号码**前面**的标签。"""
        from collectors.gov_soe import _pick_apply_phone
        text = u'监督举报电话：027-88888888。五、报名方式 联系电话：13912345678'
        self.assertEqual(_pick_apply_phone(text), u'13912345678')
        # 只有监督电话时宁可空着，也不能把用户支到纪委去
        self.assertEqual(_pick_apply_phone(u'监督举报电话：027-88888888'), u'')

    def test_124_phone_mobile_beats_switchboard(self):
        """座机多是单位总机/办公室，转接到招人的那一位要碰运气；
        手机号更可能直接是经办人。两者都有标签时选手机号。"""
        from collectors.gov_soe import _pick_apply_phone
        text = u'联系电话：027-87654321，联系手机：13800138000'
        self.assertEqual(_pick_apply_phone(text), u'13800138000')

    def test_125_shared_hotline_is_not_an_hr_phone(self):
        """聚合站页面底部挂着**站点自己的**统一咨询电话，每篇都有。单看一篇，
        它前后也写着「咨询电话」，标签判据认不出来——实测回填时 02787870401
        这一个号同时挂在十几家互不相关的单位上，打过去接电话的是站点客服。
        判据：一个 HR 电话不可能同时属于 3 家不同公司。"""
        from collectors.gov_soe import _drop_shared_phones
        rows = [{'company': u'甲公司', 'hr_phone': u'02787870401'},
                {'company': u'乙公司', 'hr_phone': u'02787870401'},
                {'company': u'丙公司', 'hr_phone': u'02787870401'},
                {'company': u'丁公司', 'hr_phone': u'13900001111'}]
        _drop_shared_phones(rows)
        # 挂在 3 家头上的统一热线被清掉
        self.assertEqual([r['hr_phone'] for r in rows[:3]], [u'', u'', u''])
        # 只属于 1 家的真电话要留下
        self.assertEqual(rows[3]['hr_phone'], u'13900001111')
        # 同一家公司多个岗位共用一个电话，是正常的，不能误杀
        same = [{'company': u'甲公司', 'hr_phone': u'02712345678'}] * 5
        _drop_shared_phones(same)
        self.assertTrue(all(r['hr_phone'] for r in same))

    def test_126_private_jobs_are_not_filtered_out_by_default(self):
        """「茧」是硬编码出来的：默认勾选里没有民营，用户连勾都没地方勾。
        三处必须一致放开——后端默认、采集器门禁、性质字典。"""
        from collectors import DEFAULT_NATURES
        self.assertIn(u'民营', DEFAULT_NATURES)
        # 采集器门禁：只勾民营时不能罢工（早年这一句让它一条都出不来）。
        # 断言集合而不是跑 fetch()——那条路要真联网，单测不能依赖外网。
        from collectors.gov_soe import _SERVED_NATURES
        self.assertIn(u'民营', _SERVED_NATURES)
        # 反向：只勾外企时本源确实该让路（不要为了放开民营把门禁整个拆了）
        self.assertFalse(_SERVED_NATURES & {u'外企'})
        self.assertTrue(models._city_hit(u'武汉市', u'武汉'))
        # 不相关的仍要挡住，否则搜索就失去意义
        self.assertFalse(models._city_hit(u'武汉', u'北京'))
        self.assertFalse(models._city_hit(u'武汉', u'黄石'))
        # 没标城市的岗位不硬塞给用户（宁缺勿错）
        self.assertFalse(models._city_hit(u'武汉', u''))
        # 不选城市 = 全国搜，什么都该命中
        self.assertTrue(models._city_hit(u'', u'北京'))

    def test_127_mohrss_phone_comes_from_list_payload(self):
        """电话在列表报文 aae005 里，不该为它抓详情页。

        抓详情页要一条一次请求，而电话本来就是白送的 —— 老实现每条都抓一次
        详情页却只为了拿邮箱（命中率还很低），是这个源只能翻 2 页的根因。
        """
        from collectors.mohrss import _norm_phone
        self.assertEqual(_norm_phone('18906997962'), '18906997962')
        self.assertEqual(_norm_phone('0519-82697020'), '051982697020')
        self.assertEqual(_norm_phone('010-88511155-5849'), '01088511155')  # 去分机
        # 占位符 / 过短 / 空 一律判无效，绝不把假号码显示成「可电话联系」
        self.assertEqual(_norm_phone('0000000'), '')
        self.assertEqual(_norm_phone('123'), '')
        self.assertEqual(_norm_phone('暂无'), '')
        self.assertEqual(_norm_phone(''), '')
        self.assertEqual(_norm_phone(None), '')

    def test_128_mohrss_matches_city_by_address(self):
        """这个源没有地级市字段，城市匹配必须靠企业自填地址 acb202。

        area_ 只到省、aab302 只到区，中间那层是缺的，而且源数据还会错配
        （实测「北京碧水物业管理」的 area_ 是「内蒙古自治区」）。
        不补地址字段，用户搜任何地级市都会全部落空。
        """
        from collectors.mohrss import _match_city
        # 省级字段里没有「武汉」，只有地址里有
        self.assertFalse(_match_city(u'武汉', u'湖北省', u'洪山区'))
        self.assertTrue(_match_city(u'武汉', u'湖北省', u'洪山区',
                                    u'武汉市洪山区珞喻路1号'))
        # 不选城市 = 全国搜
        self.assertTrue(_match_city('', u'湖北省', u'', ''))
        # 不相关的照样挡住，别为了补字段把搜索放宽
        self.assertFalse(_match_city(u'武汉', u'广东省', u'龙岗区',
                                     u'深圳市龙岗区平湖街道'))

    def test_129_mohrss_marks_agency_as_dispatch(self):
        """人力资源/劳务中介要标成派遣，别让用户满怀希望打过去发现是中介。

        民企池子里这类占比不低（实测「共青城启辰人力资源管理有限公司红安分公司」
        一页刷了 4 条），不标出来等于浪费用户时间。
        """
        from collectors.mohrss import AGENCY_RE, _nature_of
        self.assertTrue(AGENCY_RE.search(u'共青城启辰人力资源管理有限公司'))
        self.assertTrue(AGENCY_RE.search(u'某某劳务服务有限公司'))
        self.assertTrue(AGENCY_RE.search(u'某某企业管理服务有限公司'))
        # 真实用工企业不能被误伤
        self.assertFalse(AGENCY_RE.search(u'黑龙江省万里润达生物科技有限公司'))
        self.assertFalse(AGENCY_RE.search(u'常州斯威克光伏新材料有限公司'))
        # 中介依然是民营，不该因为标了派遣就把它当国企
        self.assertEqual(_nature_of(u'共青城启辰人力资源管理有限公司'), u'民营')

    def test_130_refill_fields_are_snapshotted_not_live_filtered(self):
        """补填页打字不许让整行消失（2026-09-23 用户手机反馈）。

        修复前：gaps 是实时 computed，输入框就绑在 profile 上——敲一个字
        字段就非空，整行立刻被过滤掉，剩下的字打不进去，等于根本没法补。
        修复后：进入补填屏那一刻把缺失字段快照进 gapKeys，渲染用
        fillFields（按快照过滤），点「开始」/「再来一次」才重算。

        源码断言锁三件事：渲染不再用实时 gaps、进屏时确实做了快照、
        reset 时清掉快照。
        """
        src = self.read_component('AutoFillPanel.vue')
        self.assertNotIn('v-for="f in gaps"', src)          # 实时过滤不许回来
        self.assertIn('v-for="f in fillFields"', src)       # 渲染走快照清单
        self.assertIn('gapKeys.value = gaps.value.map', src)  # 进屏时快照
        self.assertIn('gapKeys.value = []', src)            # reset 时清掉

    # 126 —— 定制简历的 docx 必须真的传到发信函数手上
    def test_126_tailored_resume_reaches_sender(self):
        """投递链路里最容易断的一环：后台线程拿到的是 tailored_map，
        真正要用它的是 send_smtp。中间任何一处改名/漏传，用户看到的都是
        「投了但附的还是原件」——静默降级，不报错，没人发现。

        锁两件事：命中 tailored_map 的岗位带上 path/name；没命中的岗位
        必须保持 None（不能串号，否则 A 岗的简历发给 B 岗的 HR）。
        """
        import app as appmod
        orig_cfg, orig_send = mailer.load_config, mailer.send_smtp
        orig_interval = appmod.SEND_INTERVAL
        got = []
        mailer.load_config = lambda: {'email': 'me@example.com', 'username': 'me@example.com',
                                      'password': 'x', 'dry_run': False,
                                      'host': 'smtp.example.com', 'port': 587}

        def fake_send(p, job, cfg=None, **kw):
            got.append((job['key'], kw.get('tailored_path'), kw.get('tailored_name')))
            return True, '已发送'

        mailer.send_smtp = fake_send
        appmod.SEND_INTERVAL = 0
        try:
            tid = 'test-tailored'
            appmod.TASKS[tid] = {'id': tid, 'status': 'running', 'total': 2, 'done': 0,
                                 'success': 0, 'fail': 0, 'skipped': 0, 'current': '',
                                 'items': [], 'dry': False, 'test_to': '', 'stopped': ''}
            jobs = [dict(key='k1', title='低压电工', company='甲', hr_email='a@b.com'),
                    dict(key='k2', title='行政专员', company='乙', hr_email='c@d.com')]
            appmod._run_apply(tid, None, jobs, None, {
                'k1': {'path': '/opt/jobapp/data/tailored/k1.docx',
                       'name': '张三-低压电工.docx'}})
            self.assertEqual(
                got,
                [('k1', '/opt/jobapp/data/tailored/k1.docx', '张三-低压电工.docx'),
                 ('k2', None, None)],
                '定制简历没传到发信函数，或串到了别的岗位：%s' % (got,))
        finally:
            mailer.load_config, mailer.send_smtp = orig_cfg, orig_send
            appmod.SEND_INTERVAL = orig_interval
            appmod.TASKS.pop('test-tailored', None)

    # 127 —— 中继型配置（登录名 ≠ 发件地址）的投递预检必须用登录名
    def test_127_precheck_uses_login_name(self):
        """2026-09-24 线上事故：换手机点一键代投 100% 失败，报「服务器拒绝了
        这次登录（535 Authentication failed）」，但发信自测能收到信。

        根因不是手机：Brevo 这类中继 username=bace29001@smtp-brevo.com 才是
        登录名，email 只是已验证的发件地址；预检把 email 当登录名去连，必 535。
        QQ 时代两者相同，bug 被埋住，换中继才暴露。
        """
        import app as appmod
        orig_cfg, orig_verify, orig_send = (mailer.load_config,
                                            mailer.verify_login, mailer.send_smtp)
        seen = {}
        mailer.load_config = lambda: {'email': 'heyiyi7410@gmail.com',
                                      'username': 'bace29001@smtp-brevo.com',
                                      'password': 'xsmtp-key', 'dry_run': False,
                                      'host': 'smtp-relay.brevo.com', 'port': 587,
                                      'ssl': False, 'starttls': True}

        def fake_verify(email, password, host=None, port=None, ssl=None,
                        starttls=None, username=None):
            seen.update(email=email, username=username, host=host)
            return True, '登录成功'

        mailer.verify_login = fake_verify
        mailer.send_smtp = lambda p, job, cfg=None, **kw: (True, '已发送')
        try:
            r = self.c.post('/api/apply', json={
                'profile_id': None, 'channel': 'smtp', 'profile': profile(),
                'jobs': [{'key': 'k1', 'title': '低压电工', 'company': '甲',
                          'hr_email': 'hr@a.com'}]})
            self.assertNotEqual(r.status_code, 400, '预检把一套正确的中继配置判成登录失败')
            self.assertEqual(seen['username'], 'bace29001@smtp-brevo.com',
                             '预检拿发件地址当登录名了 → 中继型配置必然 535')
            self.assertEqual(seen['email'], 'heyiyi7410@gmail.com',
                             '发件地址也该原样传（决定 From 头）')
            self.assertEqual(seen['host'], 'smtp-relay.brevo.com')
        finally:
            (mailer.load_config, mailer.verify_login,
             mailer.send_smtp) = orig_cfg, orig_verify, orig_send

    def frontend_file(self, *parts):
        """读前端源码做静态断言。

        服务器上只部署 dist、没有 src —— 那种环境应该「跳过」而不是「报错」：
        十几个 FileNotFoundError 混进失败清单里，会把真正的回归淹掉
        （2026-09-24 就因此误判过一次，白查一通）。
        """
        p = os.path.join(ROOT_DIR, 'frontend', 'src', *parts)
        if not os.path.exists(p):
            raise unittest.SkipTest('这台机器没有前端源码（只部署了 dist）：%s' % p)
        with open(p, encoding='utf-8') as f:
            return f.read()

    # 128 —— 设计规范里的硬规则靠人记必然回退，用测试钉住
    def test_128_design_rules_are_held(self):
        """规则来自 webapp/DESIGN.md（Emil 的 emil-design-eng / animate /
        mobile-native 三份技能改写成本项目口径）。

        只钉四条「改错不会报错、而且肉眼极难发现」的：
        ① 未门控的 :hover（触屏点一下会永久粘在悬停态，桌面看不出来）
        ② 禁掉页面缩放（无障碍硬伤）
        ③ transition: all（连带触发重排的动画）
        ④ 入场从 scale(0) 开始 / 按下没有缩放反馈
        """
        import re

        def strip_comments(s):
            # 先去掉注释再扫 —— 否则讲解文字里的 ":hover" 会被当成真规则
            return re.sub(r'/\*.*?\*/', '', s, flags=re.S)

        def ungated_hovers(src):
            """返回不在 @media (hover: hover) and (pointer: fine) 里的 :hover 行。

            逐行维护花括号栈：每一层记下「这一层是否处于门控媒体查询内」，
            看 :hover 时只要栈里有一层是门控就算合格。
            """
            stack, out = [], []
            gated = False
            for line in src.splitlines():
                s = line.strip()
                if not s:
                    continue
                if ':hover' in s and not gated:
                    out.append(s)
                opens, closes = s.count('{'), s.count('}')
                layer = gated or ('@media' in s and 'hover: hover' in s)
                for _ in range(opens):
                    stack.append(layer)
                for _ in range(closes):
                    if stack:
                        stack.pop()
                gated = any(stack)
            return out

        files = {'style.css': strip_comments(self.frontend_file('style.css')),
                 'App.vue': strip_comments(self.frontend_file('App.vue'))}
        for name in ('Step1Profile.vue', 'Step2Wish.vue', 'Step3Match.vue',
                     'Step4Apply.vue', 'AutoFillPanel.vue', 'StepBar.vue'):
            files[name] = strip_comments(self.read_component(name))

        for name, src in files.items():
            bad = ungated_hovers(src)
            self.assertEqual(bad, [], '这些 :hover 没包在 (hover: hover) 里：%s %s' % (name, bad))
            self.assertNotIn('transition: all', src,
                             '%s 里用了 transition: all —— 会连带触发重排的属性' % name)
            self.assertNotIn('scale(0)', src,
                             '%s 里有 scale(0) 入场 —— 现实里没有东西从"什么都没有"里长出来' % name)

        css = files['style.css']
        for tok in ('--ease-out:', '--ease-in-out:', '--ease-drawer:',
                    '--t-press:', '--t-pop:', '--t-menu:', '--t-sheet:'):
            self.assertIn(tok, css, '动效 token 少了 %s（DESIGN.md 第一节第 5 问）' % tok)
        self.assertIn('scale(0.97)', css, '按钮按下没有缩放反馈')
        self.assertIn('overscroll-behavior: none', css,
                      '没禁掉下拉刷新 —— 手机上下拉会把整页重载，用户填的东西全丢')
        # 减少动态效果：必须保留「不产生位移」的过渡，而不是一刀切全灭
        self.assertIn('prefers-reduced-motion', css)
        self.assertNotIn('transition-duration: 0.01ms', css,
                         '减少动态效果退回了"一刀切"写法 —— 颜色/透明度过渡是帮人理解的，不该灭')

        html = os.path.join(ROOT_DIR, 'frontend', 'index.html')
        if not os.path.exists(html):
            raise unittest.SkipTest('这台机器没有前端源码（只部署了 dist）')
        with open(html, encoding='utf-8') as f:
            page = f.read()
        # 注释里的字不算数：源码里写着「不写 maximum-scale」也会被扫描到
        page = re.sub(r'<!--.*?-->', '', page, flags=re.S)
        self.assertNotIn('maximum-scale', page, '禁了缩放：输入框字号才是该修的地方')
        self.assertNotIn('user-scalable=no', page, '禁了缩放（无障碍硬伤）')
        self.assertIn('viewport-fit=cover', page, '没有 viewport-fit，env(safe-area-*) 全是 0')
        self.assertIn('interactive-widget=resizes-content', page,
                      '安卓软键盘不会压缩布局视口，底部按钮会被盖住')
        self.assertIn('theme-color', page)
        self.assertIn('color-scheme', page)

    def read_component(self, name):
        return self.frontend_file('components', name)


if __name__ == '__main__':
    print('webapp 端到端测试\n' + '-' * 40)
    unittest.main(verbosity=2)
