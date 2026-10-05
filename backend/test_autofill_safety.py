"""Offline regressions: synthetic data only, no browser/network/submissions."""
import unittest
from unittest.mock import Mock, patch
import autofill as af


def field(label='姓名', **kwargs):
    return dict(dict(idx=0, tag='input', type='text', label=label, ph='',
                     name='', id='', cls='', visible=True, value=''), **kwargs)


class AutofillSafetyTests(unittest.TestCase):
    def task(self, **kwargs):
        t = af.Task('offline-test', {}, {'name': '测试用户', 'phone': '13800000000'},
                    'https://example.invalid/form', **kwargs)
        t.shot = Mock()
        t.ask_human = Mock(return_value='close')
        return t

    def test_personal_fields(self):
        for label, want in [('姓名', 'name'), ('手机号码', 'phone'), ('电子邮箱', 'email')]:
            self.assertEqual(af._match_field(field(label)), want)

    def test_third_party_fields_blocked(self):
        for label in ['紧急联系人姓名', '紧急联系电话', '父亲姓名', '母亲手机号', '配偶姓名', '推荐人姓名']:
            with self.subTest(label=label):
                self.assertIsNone(af._match_field(field(label)))
        self.assertIsNone(af._match_field(field('姓名', context='家庭成员')))
        self.assertIsNone(af._match_field(field('Phone', name='emergencyPhone')))

    def test_generic_placeholder_falls_back_to_name(self):
        self.assertEqual(af._match_field(field('', ph='请输入', name='mobile')), 'phone')
        self.assertIsNone(af._match_field(field('其他联系人', name='mobile')))

    def test_unknown_specific_label_not_overridden(self):
        self.assertIsNone(af._match_field(field('入党时间', name='birth')))

    def test_iframe_scan_and_locator_scope(self):
        main, child, detached = Mock(), Mock(), Mock()
        main.evaluate.return_value = [field()]
        child.evaluate.return_value = [field('手机号')]
        detached.evaluate.side_effect = RuntimeError('detached')
        page = Mock(frames=[main, child, detached])
        fields = af._scan_fields(page)
        self.assertEqual(len(fields), 2)
        af._loc(page, fields[1])
        child.locator.assert_called_once_with('[data-af-idx="0"]')
        page.locator.assert_not_called()

    def fill(self, fields, **kwargs):
        t, page = self.task(**kwargs), Mock()
        with patch.object(af, '_scan_fields', return_value=fields), \
             patch.object(af, '_fill_one', return_value=True) as fill, \
             patch.object(af, '_click_any') as click:
            af._fill_apply_form(t, page)
            click.assert_not_called()
        return t, fill

    def test_fills_ordinary_field_without_submission(self):
        t, fill = self.fill([field()])
        self.assertEqual(len(t.filled), 1)
        self.assertIn('程序未点击最终提交', t.note)

    def test_zero_fills_is_not_claimed_as_complete(self):
        t, fill = self.fill([field('其他必填项')])
        fill.assert_not_called()
        self.assertIn('自动填写 0 项', t.note)
        self.assertNotIn('都填好了', t.note)

    def test_existing_values_preserved(self):
        t, fill = self.fill([field(value='已有姓名')])
        fill.assert_not_called()
        self.assertIn('保留原值', t.missing[0]['why'])

    def test_refill_is_explicit(self):
        t, fill = self.fill([field(value='已有姓名')], refill=True)
        fill.assert_called_once()

    def test_disabled_fields_untouched(self):
        t, fill = self.fill([field(disabled=True)])
        fill.assert_not_called()

    def test_no_fields_reports_error(self):
        t, fill = self.fill([])
        self.assertEqual(t.status, 'error')
        self.assertIn('没有找到', t.error)

    def test_headed_handoff_waits_before_close(self):
        t = self.task(headless=False)
        page = Mock()
        af._handoff(t, page)
        page.remove_listener.assert_called_once_with('dialog', af._dismiss_dialog)
        self.assertEqual(t.ask_human.call_args.args[0], 'handoff')
        self.assertIn('15 分钟', t.ask_human.call_args.args[2])

    def test_headless_has_honest_recovery_message(self):
        t = self.task()
        af._handoff(t, Mock())
        t.ask_human.assert_not_called()
        self.assertIn('截图不能操作', t.note)
        self.assertIn('未保存内容不会自动同步', t.note)

    def test_flow_never_clicks_apply_entry(self):
        t, page = self.task(), Mock(url='https://example.invalid/apply')
        with patch.object(af, '_settle'), patch.object(af, '_wait_real_content'), \
             patch.object(af, '_need_auth', return_value=False), \
             patch.object(af, '_guopin_resume_guard') as guard, \
             patch.object(af, '_fill_apply_form') as fill, \
             patch.object(af, '_save_login'), patch.object(af, '_click_any') as click:
            af._flow(t, page)
            click.assert_not_called()
            guard.assert_not_called()
            fill.assert_called_once()

    def test_special_adapter_has_no_submission_path(self):
        import inspect
        source = inspect.getsource(af._guopin_resume_guard)
        self.assertNotIn('_gp_click_apply(', source)
        self.assertNotIn("('确认同步'", source)
        self.assertIn('_handoff(t, page)', source)

    def test_failed_login_does_not_fill_login_page_as_application(self):
        t, page = self.task(), Mock(url='https://example.invalid/login')
        with patch.object(af, '_settle'), patch.object(af, '_wait_real_content'), \
             patch.object(af, '_need_auth', return_value=True), \
             patch.object(af, '_wait_login'), patch.object(af, '_fill_apply_form') as fill:
            af._flow(t, page)
            self.assertEqual(t.status, 'error')
            fill.assert_not_called()

    def test_login_and_apply_combined_button_blocked(self):
        loc = Mock()
        loc.get_attribute.return_value = ''
        for text in ['登录并投递', '立即申请', 'Submit application', '确认同步简历']:
            loc.inner_text.return_value = text
            self.assertFalse(af._safe_navigation_target(loc))
        loc.inner_text.return_value = '登录'
        self.assertTrue(af._safe_navigation_target(loc))


if __name__ == '__main__':
    unittest.main()
