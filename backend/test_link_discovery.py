"""Company-link discovery contracts; no external submissions."""
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
