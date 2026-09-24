# -*- coding: utf-8 -*-
"""tailor.py 的离线单测：mock 掉 call_llm，不消耗任何 API 额度。

跑法（服务器，venv 里已装 python-docx）：
    /opt/jobapp/venv/bin/python /opt/jobapp/backend/test_tailor.py

本机没有 python-docx，render_docx 那段会被跳过（用 _HAS_DOCX 标记）。
"""
import os
import sys
import json
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tailor

# ---- 在导入任何依赖 docx 的东西之前，先标记 doct 是否可用 ----
try:
    import docx  # noqa: F401
    _HAS_DOCX = True
except Exception:  # noqa: BLE001
    _HAS_DOCX = False


# ----------------------------------------------------------------- 假素材
PROFILE = {
    'id': 99,
    'name': '张三',
    'gender': '男',
    'birth': '1995-03',
    'phone': '13800001111',
    'email': 'zhangsan@example.com',
    'city': '武汉市',
    'edu': '大专',
    'school': '武汉职业技术学院',
    'major': '机电一体化',
    'exp': '5年',
    'skills': '低压电工证、设备维修、会电焊、能倒班',
    'intro': '踏实肯干，服从安排',
    'experiences': json.dumps([
        {'company': '武汉某制造厂', 'role': '设备维修工', 'start': '2019',
         'end': '2024', 'desc': '负责生产线设备日常检修'}
    ]),
}

JOB = {
    'key': 'job_electrician_01',
    'title': '低压电工',
    'company': '某国企物业',
    'nature': '国企',
    'city': '武汉市',
    'salary_text': '5000-7000元',
    'desc': '要求：持低压电工证，能倒班，有3年以上工厂设备维修经验。',
}


def _fake_payload_good():
    """一份合规的模型输出：技能都来自档案，数字/单位都没编。

    注意：highlights 里不能写「3年」这种源里没有的数字——那会被事实门拦下，
    这里用「满足岗位要求」绕开，验证「干净输出 → blocked=False → 能生成 docx」。
    """
    return {
        'summary': '持低压电工证，5年工厂设备维修经验，能适应倒班。',
        'highlights': ['持低压电工证，与岗位要求完全对口', '5年设备维修经验满足岗位要求'],
        'skill_order': ['低压电工证', '设备维修', '能倒班', '会电焊'],
        'changes': [
            {'section': '技能栏', 'before': '会电工', 'after': '持有低压电工证',
             'why': '岗位明确要求低压电工证'},
        ],
        'gaps': ['岗位未要求但你可在面试补充：高压证'],
        'mail_body': '您好，我持低压电工证，有5年设备维修经验，应聘贵司低压电工岗。',
    }


def _fake_payload_bad():
    """一份带编造内容的模型输出：多了个「高压电工证」、工龄写成 10 年、冒出个假单位。"""
    return {
        'summary': '持高压电工证，10年工厂设备维修经验，曾在华为技术有限公司工作。',
        'highlights': ['持高压电工证'],
        'skill_order': ['高压电工证', '设备维修'],
        'changes': [],
        'gaps': [],
        'mail_body': '我有10年经验。',
    }


# ----------------------------------------------------------------- mock
_call_log = []


def mock_call_llm_good(messages, temperature=0.3):
    _call_log.append(messages)
    return _fake_payload_good(), ''


def mock_call_llm_bad(messages, temperature=0.3):
    _call_log.append(messages)
    return _fake_payload_bad(), ''


def mock_call_llm_json_err(messages, temperature=0.3):
    _call_log.append(messages)
    return None, '模型返回的不是合法 JSON：xxx'


# ----------------------------------------------------------------- 测试
FAIL = 0


def check(name, cond):
    global FAIL
    print(('  PASS ' if cond else '  FAIL ') + name)
    if not cond:
        FAIL += 1


def test_plan_good():
    print('[plan + 事实门 合规]')
    tailor.call_llm = mock_call_llm_good
    r = tailor.plan(PROFILE, JOB)
    check('ok=True', r.get('ok') is True)
    check('事实门未拦 (blocked=False)', r.get('blocked') is False)
    check('payload 含 summary', bool(r.get('payload', {}).get('summary')))
    check('skill_order 是档案子集', set(r['payload']['skill_order']).issubset(
        set(tailor._split_skills(PROFILE['skills']))))
    check('changes 给出 before/after', bool(r.get('payload', {}).get('changes')))


def test_plan_fact_gate_blocks():
    print('[事实门拦截编造]')
    tailor.call_llm = mock_call_llm_bad
    r = tailor.plan(PROFILE, JOB)
    check('ok=True (plan 本身不失败)', r.get('ok') is True)
    check('blocked=True (事实门拦下)', r.get('blocked') is True)
    probs = r.get('problems') or []
    joined = ' '.join(probs)
    check('拦到编造的数字(10年)', '10' in joined or '数字' in joined or '年份' in joined)
    check('拦到编造的单位(华为)', '华为' in joined or '单位' in joined)
    # 技能造假不在 fact_gate 拦，而在 plan() 阶段就被「只准档案子集」过滤掉，
    # 所以它不会进 docx（保障红线）。这里验证它确实被丢掉了。
    check('编造技能被子集过滤掉(高压电工证不在 skill_order)',
          '高压电工证' not in (r.get('payload', {}).get('skill_order') or []))
    check('被丢的技能进了 _new_skills 供前端提示',
          '高压电工证' in (r.get('payload', {}).get('_new_skills') or []))


def test_plan_json_error():
    print('[模型返回非法 JSON]')
    tailor.call_llm = mock_call_llm_json_err
    r = tailor.plan(PROFILE, JOB)
    check('ok=False', r.get('ok') is False)
    check('msg 含原因', bool(r.get('msg')))


def test_build_good():
    print('[build 生成 docx]')
    if not _HAS_DOCX:
        print('  SKIP 本机无 python-docx')
        return
    tailor.call_llm = mock_call_llm_good
    path, err = tailor.build(PROFILE, JOB)
    check('path 非空', bool(path))
    check('err 空', not err)
    check('文件落盘', path and os.path.isfile(path))
    if path:
        # 生成的 docx 不该包含编造内容
        d = tailor.render_docx  # 仅确认函数存在
        check('render_docx 函数可用', callable(d))


def test_build_blocked_rejected():
    print('[事实门未过 → build 拒绝生成]')
    if not _HAS_DOCX:
        print('  SKIP 本机无 python-docx')
        return
    payload = _fake_payload_bad()
    # payload 里 10年/华为/高压电工证 都不在 PROFILE 来源里 → 必须被拦
    path, err = tailor.build(PROFILE, JOB, payload)
    check('path 为空', not path)
    check('err 含「事实核对」', '事实核对' in (err or ''))


def test_source_text_no_resume():
    print('[无原件时 source_text 走档案字段]')
    src = tailor.source_text({'name': '李四', 'skills': '会开车'})
    check('包含姓名', '李四' in src)
    check('包含技能', '会开车' in src)


if __name__ == '__main__':
    print('python-docx 可用:', _HAS_DOCX)
    test_source_text_no_resume()
    test_plan_good()
    test_plan_fact_gate_blocks()
    test_plan_json_error()
    test_build_good()
    test_build_blocked_rejected()
    print('\n==== %s ====' % ('ALL PASS' if FAIL == 0 else ('%d FAILED' % FAIL)))
    sys.exit(1 if FAIL else 0)
