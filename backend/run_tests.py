# -*- coding: utf-8 -*-
import sys, os, io, contextlib
# 关掉服务端岗位缓存层（读库/回写/后台刷新线程）：
# 1) 测试必须走真实采集链路才有意义；2) 后台线程真的去爬网会拖慢/挂住测试。
# 必须在 import app 之前置好——app 模块导入时就决定是否启动刷新线程。
os.environ['WEBAPP_JOBS_CACHE'] = '0'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unittest
loader = unittest.TestLoader()
suite = loader.loadTestsFromName('test_web')
buf = io.StringIO()
runner = unittest.TextTestRunner(stream=buf, verbosity=2)
result = runner.run(suite)
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_result.log')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(buf.getvalue())
    f.write('\nRESULT: %s tests, %s failures, %s errors\n' % (
        result.testsRun, len(result.failures), len(result.errors)))
print('WROTE', out_path)
