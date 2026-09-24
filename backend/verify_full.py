# -*- coding: utf-8 -*-
"""端到端验证国聘全流程（测试模式：补全简历但不点最终「申请职位」）。

用法：
    GP_NO_SUBMIT=1 python -u verify_full.py          # 停在提交前（默认就用这个）
    python -u verify_full.py                         # 真投一单（谨慎）

会真实打开浏览器、真实点开各段编辑并保存，所以跑完你的站内简历会被填成
刘志博档案里的内容 —— 这正是要验证的东西。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autofill                                        # noqa: E402
import models                                          # noqa: E402

# 岗位：可用 GP_URL 覆盖；默认一个没投过的岗位
URL = os.environ.get('GP_URL') or \
    'https://www.iguopin.com/job/detail?id=217298617277351072'

# 多用户：用 GP_PROFILE_ID 指定要投递的档案（默认 4，即当前测试档案）。
# 每个真实用户对应一条 profiles 记录 + 各自独立的浏览器登录态（GP_BROWSER_PROFILE）。
PROFILE_ID = int(os.environ.get('GP_PROFILE_ID') or '4')

models.init_db(os.path.join(autofill.ROOT_DIR, 'data', 'app.db'))
profile = models.get_profile(PROFILE_ID)
if not profile:
    print('档案 %s 不存在，先用 /api/profile 建一份再跑' % PROFILE_ID)
    raise SystemExit(1)
rp = os.environ.get('GP_RESUME') or os.path.join(
    autofill.ROOT_DIR, 'data', 'resumes', '1789733497867_刘志博简历.docx')
if os.path.exists(rp):
    profile['resume_path'] = rp

print('URL:', URL)
print('档案:', profile.get('name'), '| 城市:', profile.get('city'),
      '| 期望薪资:', profile.get('salary'))
print('经历段数:', len(profile.get('experiences') or []))
print('测试模式(不真投):', os.environ.get('GP_NO_SUBMIT') == '1')
print()

tid, t = autofill.start(job={'title': '维修电气岗'},
                        profile=profile, url=URL, headless=True)
print('task:', tid, flush=True)

deadline = time.time() + 600
seen = set()
while time.time() < deadline:
    tt = autofill.get(tid)
    if not tt:
        print('task gone', flush=True)
        break
    st = tt.get('status')
    if st == 'need_human' and tt.get('ask'):
        q = tt['ask'].get('title', '')
        if q not in seen:
            seen.add(q)
            print('ASK:', q, flush=True)
            # 顺带验证「要你本人补」的清单有没有随 ask 推给前端
            for it in tt['ask'].get('items') or []:
                print('   要补：%-12s | %s' % (it.get('label'),
                                              (it.get('how') or '')[:46]),
                      flush=True)
        autofill.submit_answer(tid, '继续')
    if st in ('done', 'error'):
        break
    time.sleep(2)

tt = autofill.get(tid) or {}
print('\n================ 结果 ================')
print('状态:', tt.get('status'))
if tt.get('error'):
    print('错误:', tt['error'][-1200:])
print('\n--- 步骤 ---')
for s in tt.get('steps', []):
    print('  %-5s %s %s' % ('OK' if s.get('ok') else '!!',
                            s.get('label'), s.get('note') or ''))
print('\n--- 填成功的 (%d) ---' % len(tt.get('filled', [])))
for f in tt.get('filled', []):
    print('   %-26s = %s' % (f.get('label'), str(f.get('value'))[:40]))
print('\n--- 没填成的 (%d) ---' % len(tt.get('missing', [])))
for m in tt.get('missing', []):
    print('   %-26s : %s' % (m.get('label'), m.get('why')))
