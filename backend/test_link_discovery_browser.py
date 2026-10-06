"""Opt-in real UI integration with synthetic companies and isolated API fixtures.

AIJOBS_DISCOVERY_E2E=1 python -m unittest test_link_discovery_browser -v
Search provider, OCR engine, and autofill task are mocked; no real applications.
"""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('AIJOBS_DISCOVERY_E2E') == '1', 'opt-in UI integration')
class DiscoveryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['WEBAPP_JOBS_CACHE'] = '0'
        os.environ['WEBAPP_LIVE_COLLECT'] = '0'
        import models
        cls.temp = tempfile.TemporaryDirectory(prefix='aijobs-discovery-browser-')
        init = models.init_db
        with patch.object(models, 'init_db', lambda *_: init(os.path.join(cls.temp.name, 'app.db'))):
            import app as web
        cls.web = web
        cls.patches = [
            patch.object(models, 'query_jobs', return_value=[dict(company='示例科技有限公司',
                         title='前端工程师（测试）', url='https://jobs.example.com/apply', source='测试数据')]),
            patch.object(web, '_search_external_recruitment_links', return_value=([
                dict(company='示例教育', title='托管老师（测试）', url='https://education.example.com/apply',
                     host='education.example.com', source='外网测试数据（未核实）', city='', role_match=True)], '')),
            patch('pytesseract.image_to_string', return_value='示例科技 | 前端\n示例教育 | 托管老师'),
        ]
        for item in cls.patches:
            item.start()
        from werkzeug.serving import make_server, WSGIRequestHandler
        class QuietHandler(WSGIRequestHandler):
            def log_request(self, *args, **kwargs):
                pass
        cls.server = make_server('127.0.0.1', 0, web.app, threaded=True, request_handler=QuietHandler)
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.shots = Path(__file__).resolve().parents[1] / 'design-verify' / 'link-discovery'
        cls.shots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        for item in cls.patches:
            item.stop()
        cls.temp.cleanup()

    def test_lists_ocr_result_navigation_and_fill_link_on_desktop_and_mobile(self):
        for width, big in [(1280, False), (390, False), (430, True)]:
            with self.subTest(width=width, large_font=big):
                context = self.browser.new_context(viewport={'width': width, 'height': 900})
                errors = []
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script('localStorage.clear()')
                page.goto(self.base)
                resume = '姓名：测试用户\n手机号：13800000000\n邮箱：synthetic@example.com\n性别：男\n出生年月：2000-01\n毕业学校：测试大学\n专业：计算机\n毕业时间：2022-06'
                page.locator('.up-file').set_input_files({'name': '测试简历.txt',
                    'mimeType': 'text/plain', 'buffer': resume.encode()})
                page.get_by_role('heading', name='找招聘链接，直接填表').wait_for()
                if big:
                    page.get_by_role('button', name='切换为大字号', exact=True).click()
                page.get_by_role('button', name='企业名单 / 截图找链接', exact=True).click()
                page.get_by_role('textbox', name='企业名单文字').fill('示例科技 | 前端\n示例教育 | 托管老师')
                page.get_by_role('button', name='核对企业名单', exact=True).click()
                page.get_by_role('button', name='搜索 2 家企业', exact=True).click()
                page.get_by_text('前端工程师（测试）', exact=True).wait_for()
                page.get_by_role('button', name='下一家', exact=True).click()
                page.get_by_text('托管老师（测试）', exact=True).wait_for()
                page.get_by_text('本站未找到，以下为外网搜索结果', exact=False).wait_for()
                self.assertTrue(page.get_by_role('button', name='下一家', exact=True).is_disabled())
                page.locator('.discover-results').scroll_into_view_if_needed()
                page.screenshot(path=str(self.shots / f'results-{width}-big{int(big)}.png'))
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.get_by_role('button', name='用此链接填表', exact=True).click()
                self.assertEqual(page.locator('#step1-autofill-url').input_value(), 'https://education.example.com/apply')
                # No autofill should start just by selecting a link.
                starts = []
                def start(route):
                    starts.append(route.request.post_data_json)
                    route.fulfill(json={'ok': True, 'task_id': 'synthetic-task'})
                page.route('**/api/autofill/start', start)
                page.route('**/api/autofill/task/*', lambda route: route.fulfill(json={
                    'ok': True, 'task': {'status': 'done', 'steps': [], 'shots': [], 'filled': [], 'missing': []}}))
                self.assertEqual(starts, [])
                page.get_by_text('我已核对以上资料，并了解招聘网站可能自动保存填入内容', exact=False).click()
                page.get_by_role('button', name='开始自动填', exact=True).click()
                if page.get_by_role('button', name='这几项先空着，照样投', exact=True).is_visible():
                    page.get_by_role('button', name='这几项先空着，照样投', exact=True).click()
                page.get_by_text('本次填表流程已结束', exact=True).wait_for()
                self.assertEqual(starts[0]['url'], 'https://education.example.com/apply')
                self.assertEqual(starts[0]['job'], {})
                page.get_by_role('button', name='上一家', exact=True).click()
                page.get_by_role('button', name='用此链接填表', exact=True).click()
                self.assertEqual(page.locator('#step1-autofill-url').input_value(), 'https://jobs.example.com/apply')
                self.assertEqual(len(starts), 1)
                # Verify file upload -> OCR endpoint -> editable text -> two-company review.
                from PIL import Image
                image = io.BytesIO()
                Image.new('RGB', (200, 60), 'white').save(image, format='PNG')
                page.get_by_role('textbox', name='企业名单文字').fill('')
                page.locator('.discover-upload input').set_input_files({'name': '名单.png',
                    'mimeType': 'image/png', 'buffer': image.getvalue()})
                page.get_by_role('button', name='搜索 2 家企业', exact=True).wait_for()
                self.assertIn('托管老师', page.get_by_role('textbox', name='企业名单文字').input_value())
                self.assertEqual(errors, [])
                print(json.dumps({'width': width, 'large_font': big, 'flow': 'passed',
                                  'screenshot': str(self.shots / f'results-{width}-big{int(big)}.png')}, ensure_ascii=False))
                context.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
