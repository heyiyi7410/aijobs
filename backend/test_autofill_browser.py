"""Opt-in real HTTP -> autofill worker -> Chromium -> local form E2E.

Run: AIJOBS_BROWSER_E2E=1 python -m unittest test_autofill_browser -v
Only synthetic data, temporary DB/cookies, loopback fixtures. No real applications.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen


@unittest.skipUnless(os.environ.get('AIJOBS_BROWSER_E2E') == '1', 'opt-in browser integration test')
class BrowserAutofillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['WEBAPP_JOBS_CACHE'] = '0'
        os.environ['WEBAPP_LIVE_COLLECT'] = '0'
        from unittest.mock import patch
        import models
        cls.temp = tempfile.TemporaryDirectory(prefix='aijobs-browser-e2e-')
        # Only redirect initial database setup; autofill/browser logic is not mocked.
        init_db = models.init_db
        with patch.object(models, 'init_db', lambda *_: init_db(os.path.join(cls.temp.name, 'test.db'))):
            import app as web
        cls.web = web
        cls.af = web.autofill
        for name, folder in [('PROFILE_DIR', 'browser'), ('LOGIN_DIR', 'logins'),
                             ('SHOT_DIR', 'shots'), ('RESUME_DIR', 'resumes')]:
            path = os.path.join(cls.temp.name, folder)
            os.makedirs(path, exist_ok=True)
            setattr(cls.af, name, path)
        cls.events = {}
        cls.clicks = []
        from flask import request, jsonify

        @web.app.route('/e2e/report/<case>', methods=['POST'])
        def report(case):
            cls.events[case] = request.get_json()
            return jsonify(ok=True)

        @web.app.route('/e2e/click/<case>', methods=['POST'])
        def click(case):
            cls.clicks.append(case)
            return jsonify(ok=True)

        @web.app.route('/e2e/form/<case>')
        def form(case):
            fields = {
                'basic': '<label for="name">姓名</label><input id="name">'
                         '<div><input id="mobile" name="mobile" placeholder="请输入"></div>'
                         '<label for="edu">最高学历</label><select id="edu"><option value="">请选择</option><option>本科及以上</option></select>'
                         '<label for="existing">电子邮箱</label><input id="existing" value="existing@example.com">'
                         '<label for="emergency">紧急联系人姓名</label><input id="emergency">'
                         '<fieldset><legend>家庭成员</legend><label for="relative">手机号</label><input id="relative"></fieldset>'
                         '<label for="locked">年龄</label><input id="locked" readonly>',
                'frame': '<iframe title="教育资料" src="/e2e/form/child"></iframe>',
                'child': '<label for="school">毕业院校</label><input id="school">',
                'unknown': '<label for="unknown">其他必填项目</label><input id="unknown">',
                'detail': '<button type="button" onclick="hit()">立即申请</button>',
                'handoff': '<label for="name">姓名</label><input id="name">',
            }.get(case, '')
            return '''<!doctype html><meta charset="utf-8"><title>本地自动填表测试</title>
            <style>body{font:18px sans-serif;padding:20px}input,select{display:block;margin:10px}iframe{height:160px}</style>
            <h1>本地隔离测试，不是真实招聘网站</h1><p>''' + ('本页面仅用虚构资料验证自动填写，不关联任何外部招聘机构。' * 8) + '''</p>
            <form onsubmit="event.preventDefault();hit()">''' + fields + '''
            <button type="submit">提交申请</button></form><script>
            const key=''' + json.dumps(case) + ''';
            function hit(){fetch('/e2e/click/'+key,{method:'POST'});}
            function report(){let data={}; document.querySelectorAll('input,select').forEach(e=>data[e.id]=e.value);
              fetch('/e2e/report/'+key,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),keepalive:true});}
            document.addEventListener('input',report); document.addEventListener('change',report); report();
            </script>'''

        from werkzeug.serving import make_server, WSGIRequestHandler
        class QuietHandler(WSGIRequestHandler):
            def log_request(self, *args, **kwargs):
                pass
        cls.server = make_server('127.0.0.1', 0, web.app, threaded=True, request_handler=QuietHandler)
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.af.cancel_all()
        # Browser worker releases lock only after its context is closed.
        if cls.af._LOCK.acquire(timeout=20):
            cls.af._LOCK.release()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def api(self, path, data=None):
        req = Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    def start_case(self, case, headed=False, refill=False):
        result = self.api('/api/autofill/start', {'url': self.base + '/e2e/form/' + case,
            'show_browser': headed, 'refill': refill, 'profile': {'name': '本地测试用户',
            'phone': '13800000000', 'email': 'synthetic@example.com', 'edu': '本科及以上',
            'school': '测试大学', 'age': '30'}})
        self.assertTrue(result['ok'])
        return result['task_id']

    def wait_task(self, tid, states=('done', 'error'), timeout=55):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.api('/api/autofill/task/' + tid)['task']
            if task['status'] in states:
                return task
            time.sleep(.2)
        self.fail('Task timed out: ' + str(task))

    def finish(self, tid, status='done'):
        task = self.wait_task(tid)
        self.assertEqual(task['status'], status, task.get('error'))
        self.assertTrue(self.af._LOCK.acquire(timeout=10), 'browser did not close')
        self.af._LOCK.release()
        self.assertEqual(self.clicks, [], 'application/submit button was clicked')
        return task

    def test_basic_real_values_and_no_submission(self):
        task = self.finish(self.start_case('basic'))
        values = self.events['basic']
        self.assertEqual(values['name'], '本地测试用户')
        self.assertEqual(values['mobile'], '13800000000')
        self.assertEqual(values['edu'], '本科及以上')
        self.assertEqual(values['existing'], 'existing@example.com')
        for key in ['emergency', 'relative', 'locked']:
            self.assertEqual(values[key], '')
        self.assertEqual(len(task['filled']), 3)

    def test_iframe_real_input(self):
        task = self.finish(self.start_case('frame'))
        self.assertEqual(self.events['child']['school'], '测试大学')
        self.assertEqual(len(task['filled']), 1)

    def test_unknown_not_reported_as_filled(self):
        task = self.finish(self.start_case('unknown'))
        self.assertEqual(task['filled'], [])
        self.assertIn('自动填写 0 项', task['note'])
        self.assertEqual(self.events['unknown']['unknown'], '')

    def test_detail_does_not_click_apply(self):
        task = self.finish(self.start_case('detail'), status='error')
        self.assertEqual(task['filled'], [])
        self.assertIn('没有找到可识别的表单', task['error'])

    def test_explicit_refill_replaces_existing_value(self):
        self.finish(self.start_case('basic', refill=True))
        self.assertEqual(self.events['basic']['existing'], 'synthetic@example.com')
        self.assertEqual(self.events['basic']['emergency'], '')

    def test_visible_handoff_stays_open_until_user_ends(self):
        tid = self.start_case('handoff', headed=True)
        task = self.wait_task(tid, ('need_human', 'error'))
        self.assertEqual(task['status'], 'need_human', task.get('error'))
        self.assertEqual(task['ask']['type'], 'handoff')
        self.assertFalse(self.af._LOCK.acquire(blocking=False), 'browser lock released before handoff')
        self.assertEqual(self.events['handoff']['name'], '本地测试用户')
        self.assertTrue(self.api('/api/autofill/answer', {'task_id': tid, 'value': 'close'})['ok'])
        task = self.finish(tid)
        self.assertIn('窗口已按你的要求关闭', task['note'])


if __name__ == '__main__':
    unittest.main()
