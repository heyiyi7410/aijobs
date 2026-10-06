# -*- coding: utf-8 -*-
"""临时工具：本机 SFTP 在这台服务器上时通时不通（open 随机 ENOENT），
改走 exec 通道分片传 base64 再落地。用完即删。

用法：python _push_b64.py 本地文件 远程绝对路径
"""
import base64
import os
import sys

from deploy_ssh import run

CHUNK = 8192


def push(local, remote):
    raw = open(local, 'rb').read()
    b64 = base64.b64encode(raw).decode('ascii')
    tmp = '/tmp/_push_%d.b64' % os.getpid()
    run('rm -f %s' % tmp)
    for i in range(0, len(b64), CHUNK):
        piece = b64[i:i + CHUNK]
        # 单引号包裹，内容里只有 base64 字符，不会被引号/转义问题咬到
        run("printf '%%s' '%s' >> %s" % (piece, tmp))
    out = run('base64 -d %s > %s && rm -f %s && md5sum %s' % (tmp, remote, tmp, remote))
    print(out[1].read().decode() if hasattr(out[1], 'read') else out)
    print('本地 %d 字节 -> %s' % (len(raw), remote))


if __name__ == '__main__':
    push(sys.argv[1], sys.argv[2])
