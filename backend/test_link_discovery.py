"""Company-link discovery and screenshot OCR contracts; no external submissions."""
import base64
import io
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['WEBAPP_JOBS_CACHE'] = '0'
os.environ['WEBAPP_LIVE_COLLECT'] = '0'
import models

# Keep app's initial DB creation away from the user's database.
TEMP = tempfile.TemporaryDirectory(prefix='aijobs-discovery-tests-')
_init_db = models.init_db
with patch.object(models, 'init_db', lambda *_: _init_db(os.path.join(TEMP.name, 'test.db'))):
    import app as web


class LinkDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()

    def test_internal_hit_avoids_external_search_and_deduplicates(self):
        rows = [dict(company='示例科技有限公司', title=title, url=url, source='test')
                for title, url in [('行政文员', 'https://jobs.example.com/admin'),
                                   ('前端工程师', 'https://jobs.example.com/dev'),
                                   ('前端工程师', 'https://jobs.example.com/dev')]]
        rows += [dict(company='其他企业', title='前端工程师', url='https://other.example.com')]
        with patch.object(models, 'query_jobs', return_value=rows), \
                patch.object(web, '_search_external_recruitment_links') as external:
            result = self.client.post('/api/jobs/discover-links', json={
                'company': '示例科技', 'role': '前端'}).get_json()
        self.assertEqual(result['search_scope'], 'indexed_jobs')
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['links'][0]['title'], '前端工程师')
        external.assert_not_called()

    def test_expired_and_unusable_local_results_fall_back_to_web(self):
        rows = [dict(company='示例科技', title='过期职位', deadline='2001-01-01',
                     url='https://jobs.example.com/old', source='test'),
                dict(company='示例科技', title='没有网址', url='javascript:alert(1)')]
        with patch.object(models, 'query_jobs', return_value=rows), \
                patch.object(web, '_search_external_recruitment_links', return_value=([], '外网暂不可用')) as external:
            result = self.client.post('/api/jobs/discover-links', json={'company': '示例科技'}).get_json()
        self.assertEqual(result['search_scope'], 'external_web')
        self.assertEqual(result['search_error'], '外网暂不可用')
        external.assert_called_once_with('示例科技', '')

    def test_invalid_company_is_rejected_before_network(self):
        with patch.object(web, '_search_external_recruitment_links') as external:
            for data in ({}, {'company': ' '}, {'company': 'A'}, ['company'], {'company': {}}):
                self.assertEqual(self.client.post('/api/jobs/discover-links', json=data).status_code, 400)
        external.assert_not_called()

    def test_external_results_unwrap_deduplicate_and_prefer_recruitment_domain(self):
        html = '''<a class="result-link" href="https://www.zhipin.com/a">示例科技前端招聘</a>
        <a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.example.com%2Fapply%3Fa%3D1%252F2">示例科技官方网站招聘</a>
        <a class="result-link" href="https://jobs.example.com/apply?a=1%2F2">示例科技招聘</a>
        <a class="result-link" href="javascript:alert(1)">示例科技官网</a>
        <a class="result-link" href="https://other.example.com">其他企业招聘</a>'''
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': ''}), \
                patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(html.encode())):
            links, error = web._search_external_recruitment_links('示例科技', '前端')
        self.assertEqual(error, '')
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]['url'], 'https://jobs.example.com/apply?a=1%2F2')
        self.assertIn('未核实', links[0]['source'])

    def test_external_failure_is_recoverable(self):
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': ''}), \
                patch('urllib.request.urlopen', side_effect=TimeoutError):
            links, error = web._search_external_recruitment_links('示例科技', '')
        self.assertEqual(links, [])
        self.assertIn('暂时不可用', error)

    def test_external_challenge_is_not_reported_as_no_jobs(self):
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': ''}), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(b'<div id="anomaly">captcha</div>')):
            links, error = web._search_external_recruitment_links('示例科技', '')
        self.assertFalse(links)
        self.assertIn('暂时不可用', error)

    def test_public_search_challenge_uses_alternate_provider(self):
        def response(request, **kwargs):
            if request.full_url.startswith('https://lite.duckduckgo.com/'):
                return io.BytesIO(b'<div id="anomaly">captcha</div>')
            self.assertTrue(request.full_url.startswith('https://www.bing.com/search?'))
            xml = '<rss><channel><item><title>示例科技招聘官网</title><link>https://jobs.example.com</link><description>示例科技社会招聘</description></item></channel></rss>'
            return io.BytesIO(xml.encode())
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': ''}), \
                patch('urllib.request.urlopen', side_effect=response):
            links, error = web._search_external_recruitment_links('示例科技', '')
        self.assertEqual(len(links), 1)
        self.assertEqual(error, '')
        self.assertIn('Bing', links[0]['source'])

    def test_brave_uses_server_key_and_returns_links(self):
        import json
        body = json.dumps({'web': {'results': [dict(title='示例科技招聘', url='https://jobs.example.com')]}}).encode()
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'test-key'}), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(body)) as opening:
            links, error = web._search_external_recruitment_links('示例科技', '')
        request = opening.call_args.args[0]
        self.assertEqual(request.get_header('X-subscription-token'), 'test-key')
        self.assertTrue(request.full_url.startswith('https://api.search.brave.com/'))
        self.assertEqual(len(links), 1)
        self.assertEqual(error, '')

    def screenshot(self, format='PNG'):
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (40, 20), 'white').save(buf, format=format)
        return base64.b64encode(buf.getvalue()).decode()

    def test_screenshot_text_is_returned_for_review(self):
        with patch('pytesseract.image_to_string', return_value='示例科技 | 前端\n示例教育 | 托管老师') as ocr:
            result = self.client.post('/api/jobs/ocr-screenshot', json={
                'data_b64': 'data:image/png;base64,' + self.screenshot()}).get_json()
        self.assertTrue(result['ok'])
        self.assertIn('托管老师', result['text'])
        self.assertEqual(ocr.call_args.kwargs['lang'], 'chi_sim+eng')
        self.assertEqual(ocr.call_args.kwargs['timeout'], web._OCR_TRY_SECONDS)

    def test_missing_ocr_engine_has_paste_text_recovery(self):
        import pytesseract
        with patch('pytesseract.image_to_string', side_effect=pytesseract.TesseractNotFoundError()):
            result = self.client.post('/api/jobs/ocr-screenshot', json={'data_b64': self.screenshot()}).get_json()
        self.assertFalse(result['ok'])
        self.assertIn('粘贴企业名单', result['msg'])

    def test_invalid_screenshot_does_not_reach_ocr(self):
        with patch('pytesseract.image_to_string') as ocr:
            for raw in ('not-base64', '', base64.b64encode(b'not-an-image').decode()):
                self.assertFalse(self.client.post('/api/jobs/ocr-screenshot', json={'data_b64': raw}).get_json()['ok'])
            self.assertEqual(self.client.post('/api/jobs/ocr-screenshot', json={
                'data_b64': self.screenshot('GIF')}).status_code, 400)
            self.assertEqual(self.client.post('/api/jobs/ocr-screenshot', json={
                'data_b64': 'a' * 8_000_001}).status_code, 400)
        ocr.assert_not_called()

    # ---- 通用截图识字（粘贴链接旁边那个按钮）----

    def test_generic_ocr_returns_text_and_accepts_bare_base64(self):
        with patch('pytesseract.image_to_string',
                   return_value='投递页面：https://jobs.example.com/apply/9527 截止 10 月') as ocr:
            result = self.client.post('/api/ocr/text', json={
                'data_b64': self.screenshot()}).get_json()
        self.assertTrue(result['ok'])
        self.assertIn('https://jobs.example.com/apply/9527', result['text'])
        self.assertEqual(ocr.call_args.kwargs['lang'], 'chi_sim+eng')

    def test_generic_ocr_does_not_reuse_company_wording(self):
        # 同一次引擎失败，两个入口该给各自的措辞：企业名单那边引导去粘贴名单，
        # 链接这边引导换一张图——把名单的话原样搬过来会让人莫名其妙。
        import pytesseract
        with patch('pytesseract.image_to_string', side_effect=pytesseract.TesseractNotFoundError()):
            generic = self.client.post('/api/ocr/text', json={'data_b64': self.screenshot()}).get_json()
            company = self.client.post('/api/jobs/ocr-screenshot', json={'data_b64': self.screenshot()}).get_json()
        self.assertFalse(generic['ok'])
        self.assertFalse(company['ok'])
        self.assertNotIn('企业名单', generic['msg'])
        self.assertIn('企业名单', company['msg'])
        self.assertNotEqual(generic['msg'], company['msg'])

    def test_generic_ocr_guards_input(self):
        with patch('pytesseract.image_to_string') as ocr:
            self.assertEqual(self.client.post('/api/ocr/text', json={}).status_code, 400)
            self.assertEqual(self.client.post('/api/ocr/text', json={
                'data_b64': 'a' * 8_000_001}).status_code, 400)
            self.assertEqual(self.client.post('/api/ocr/text', json={
                'data_b64': self.screenshot('GIF')}).status_code, 400)
            for raw in ('not-base64', '', base64.b64encode(b'not-an-image').decode()):
                self.assertFalse(self.client.post('/api/ocr/text', json={'data_b64': raw}).get_json()['ok'])
        ocr.assert_not_called()

    def test_generic_ocr_reports_when_nothing_recognised(self):
        with patch('pytesseract.image_to_string', return_value='   '):
            result = self.client.post('/api/ocr/text', json={
                'data_b64': self.screenshot()}).get_json()
        self.assertFalse(result['ok'])
        self.assertIn('换一张', result['msg'])

    # ---- 预处理：深色底截图不能直接喂给 tesseract ----

    def test_binarize_turns_colored_screenshot_into_pure_black_and_white(self):
        # 彩色底截图直接喂 tesseract 会把中文认成乱码（实测深蓝底「投递页面链接」
        # →「$258 TUM HEHE」），靠的就是这一步把它压成纯黑白、去掉底色干扰。
        # 注意：二值化只做阈值，不翻转极性（深色底出来仍是白字黑底，tesseract 照样认）。
        from PIL import Image, ImageDraw
        im = Image.new('RGB', (320, 80), '#0b4f6c')
        ImageDraw.Draw(im).text((10, 24), '投递页面', fill='white')
        hist = web._ocr_binarize(im).convert('L').histogram()
        self.assertEqual([i for i, n in enumerate(hist) if n], [0, 255],
                         '二值化后只该剩纯黑与纯白两色')

    def test_binarize_upscales_small_but_leaves_big_alone(self):
        # 小字必须放大才认得准；大图不能再放大，否则噪声图会让 tesseract 跑飞（实测 18.6s）。
        from PIL import Image
        self.assertGreater(web._ocr_binarize(Image.new('RGB', (400, 60), 'white')).width, 400)
        self.assertEqual(web._ocr_binarize(Image.new('RGB', (2400, 600), 'white')).size,
                         (2400, 600))

    @unittest.skipUnless(os.environ.get('AIJOBS_LIVE_SEARCH') == '1', 'opt-in external search smoke test')
    def test_live_external_search(self):
        found = 0
        for company, role in [('腾讯', '前端'), ('中国信息通信研究院', '运维')]:
            with patch.object(models, 'query_jobs', return_value=[]):
                result = self.client.post('/api/jobs/discover-links', json={
                    'company': company, 'role': role}).get_json()
            self.assertEqual(result['search_scope'], 'external_web')
            self.assertTrue(result['ok'])
            found += result['count']
            self.assertEqual(result['count'], len(result['links']))
            print({'company': company, 'scope': result['search_scope'],
                   'count': result['count'], 'search_error': result['search_error'], 'results': result['links'][:3]})
        # An engine can legitimately have no usable matches for one company.
        self.assertGreater(found, 0, 'all live searches returned no usable results')


if __name__ == '__main__':
    unittest.main(verbosity=2)
