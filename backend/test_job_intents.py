"""Deterministic search regressions; no external requests or real applications."""
import os
import unittest
from unittest.mock import patch

os.environ['WEBAPP_LIVE_COLLECT'] = '0'
from ai_matcher import search_words, score_job
import app as web


class JobIntentTests(unittest.TestCase):
    def setUp(self):
        web._SEARCH_CACHE.clear()

    def test_aliases_and_separators(self):
        words = search_words({'keyword': '托管老师、会计，仓管员'})
        self.assertIn('晚托老师', words)
        self.assertIn('仓库管理员', words)
        self.assertIn('会计', words[:5])
        self.assertNotIn('托管', words)

    def test_all_categories_retained_and_balanced(self):
        words = search_words({'jobTypes': ['技术工人', '托管老师']})
        self.assertIn('托管老师', words[:5])
        self.assertIn('作业辅导老师', words)

    def test_keyword_affects_scoring(self):
        p = {'keyword': '托管老师'}
        good = score_job(p, {'title': '招聘晚托老师'})
        bad = score_job(p, {'title': '银行资产托管经理'})
        self.assertGreater(good[0], bad[0])
        self.assertTrue(any('对口' in r for r in good[2]))

    def test_explicit_role_does_not_search_skills(self):
        self.assertNotIn('会用电脑', search_words({'keyword': '托管老师', 'skills': ['会用电脑']}))

    def test_live_search_not_masked_by_unrelated_cache(self):
        queries = []
        def db(**q):
            queries.append(q)
            return [] if q.get('keywords') else [{'key': 'wrong', 'title': '电工', 'city': '武汉'}]
        report = {'jobs': [{'key': 'yes', 'title': '晚托老师'}, {'key': 'no', 'title': '银行资产托管经理'}],
                  'sources': [], 'relaxed': False, 'fallback': False}
        with patch.object(web, '_JOBS_CACHE_ON', True), patch.object(web.models, 'query_jobs', side_effect=db), patch.object(web.models, 'upsert_jobs'), patch.object(web, 'collect_jobs_with_report', return_value=report) as live:
            jobs, _ = web._collect_for_profile({'keyword': '托管老师', 'city': '武汉'}, wide=False)
        self.assertEqual([j['key'] for j in jobs], ['yes'])
        self.assertEqual(live.call_count, 5)
        self.assertTrue(all(q.get('keywords') for q in queries))
        self.assertTrue(all(c.kwargs['city'] == '武汉' and not c.kwargs['include_wide'] for c in live.call_args_list))


if __name__ == '__main__':
    unittest.main()
