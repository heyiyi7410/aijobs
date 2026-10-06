from datetime import date
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from collectors.gwy_public import Page, candidates, closing_date, parse_article


class PublicTests(unittest.TestCase):
    def test_automatic_refresh_cooldown_and_disable(self):
        import sync_gwy_public as sync
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / 'jobs.json'
            with patch.object(sync, 'CACHE', cache), patch.object(sync, 'run_sync') as run, patch('collectors._enabled', return_value=True):
                self.assertTrue(sync.refresh_if_due())
                self.assertFalse(sync.refresh_if_due())
                run.assert_called_once()
            with patch.object(sync, 'CACHE', cache), patch.object(sync, 'run_sync') as run, patch('collectors._enabled', return_value=False):
                self.assertFalse(sync.refresh_if_due(interval=0))
                run.assert_not_called()

    def test_cached_source_makes_no_network_calls(self):
        import collectors.gwy_public as source
        import json, time
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / 'jobs.json'
            cache.write_text(json.dumps({'jobs':[{'title':'招聘公告','company':'单位','desc':'正文','city':'武汉','nature':'国企','observed_at':time.time()}]}), encoding='utf-8')
            with patch.object(source, 'CACHE', cache), patch('urllib.request.urlopen', side_effect=AssertionError('must not fetch')):
                self.assertEqual(len(source.GwyPublicCollector().fetch(city='武汉')), 1)
    def test_body_excludes_sidebar(self):
        html = '<h1>某银行2026年招聘公告</h1><div class="detail_content"><p>正文</p><div>内层</div></div><div>培训广告</div>'
        self.assertIn('内层', Page(html).text)
        self.assertNotIn('培训广告', Page(html).text)

    def test_noise_and_dedupe(self):
        html = '<a href="/gqzp/1.html">企业招聘公告</a><a href="/gqzp/1.html">企业招聘公告</a><a href="/gqzp/2.html">校园招聘报名流程</a>'
        self.assertEqual(len(candidates(html, 'https://www.gwy.com/')), 1)

    def test_deadline_not_publication(self):
        self.assertEqual(closing_date('发布时间2026年9月29日', 2026), '')
        self.assertEqual(closing_date('简历投递：10月13日截止', 2026), '2026-10-13')
        self.assertEqual(closing_date('报名时间：2026年9月21日—10月20日', 2026), '2026-10-20')

    def test_real_application_and_expiry(self):
        html = '<h1>邮储银行湖北分行2026年社会招聘公告</h1><div class="detail_content"><p>' + '岗位职责相关要求。'*15 + '</p><p>简历投递：10月13日截止</p><p>报名网站https://hbycnz.zhaopin.com。</p></div><a href="https://fake.example/job">广告</a>'
        row, _ = parse_article(html, 'https://www.gwy.com/yhzp/1.html', '银行', date(2026,10,7))
        self.assertEqual(row['deadline'], '2026-10-13')
        self.assertEqual(row['apply_url'], 'https://hbycnz.zhaopin.com')
        self.assertEqual(row['city'], '湖北')
        self.assertEqual(row['hr_email'], '')
        row, reason = parse_article(html, 'https://www.gwy.com/yhzp/1.html', '银行', date(2026,10,14))
        self.assertIsNone(row)
        self.assertEqual(reason, '已截止')


if __name__ == '__main__':
    unittest.main()
