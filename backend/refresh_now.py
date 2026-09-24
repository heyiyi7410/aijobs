# -*- coding: utf-8 -*-
"""立即执行一次「后台刷新」等价的采集入库，让新扩的公告源马上进库。

后台线程每 30 分钟才刷一次，这里手动触发一遍，用户下次打开就能看到新岗位。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import models
from collectors import collect_jobs, NATURES

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  'data', 'app.db')
models.init_db(DB)

t0 = time.time()
jobs = collect_jobs(keyword='', city='', limit=1200, natures=list(NATURES))
print('抓取 %d 条，耗时 %.1f 秒' % (len(jobs), time.time() - t0))
n = models.upsert_jobs(jobs)
print('入库 %d 条' % n)

conn = models._conn()
try:
    tot = conn.execute('select count(*) from jobs').fetchone()[0]
    mail = conn.execute(
        "select count(*) from jobs where contact_email is not null "
        "and contact_email<>''").fetchone()[0]
    print('库中岗位总数 = %d，带邮箱 = %d' % (tot, mail))
    print()
    print('带邮箱岗位按城市（前 12）：')
    for row in conn.execute(
            "select city, count(*) from jobs where contact_email<>'' "
            "group by city order by 2 desc limit 12"):
        print('   %-10s %d' % (row[0] or '(空)', row[1]))
    print()
    print('武汉 / 湖北 带邮箱岗位：')
    for row in conn.execute(
            "select title, company, contact_email from jobs "
            "where contact_email<>'' and (city like '%武汉%' or city like '%湖北%')"):
        print('   %-30s %-24s %s' % ((row[0] or '')[:28], (row[1] or '')[:22],
                                     row[2]))
finally:
    conn.close()
