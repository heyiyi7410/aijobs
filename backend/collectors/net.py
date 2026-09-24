# -*- coding: utf-8 -*-
"""采集用的网络层：拟人化 + 节流 + 退避重试 + 磁盘缓存。

设计原则（对应「打破信息茧房」方案的第二层）：
1. 永不抛异常。任何网络问题都返回 None，让上层静默降级到其它渠道。
2. 低频拟人。随机 UA + 随机间隔，避免被判定为机器人。
3. 缓存优先。同一查询在 TTL 内直接读本地，既快又不会被封。
"""
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# 常见浏览器 UA，每次请求随机取一个
UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) '
    'Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 '
    'Safari/604.1',
]

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'data', 'cache')

# 同一个域名两次请求之间的最小间隔（秒），防止把对方打疼
MIN_INTERVAL = 1.0
JITTER = 0.8
_last_call = {}
# 按域名各配一把锁。搜索是按关键词并行的（collectors 里一次起 8 个 worker
# 跑多个源），同一域名的请求会同时进来；"读上次时间 → 算要等多久 → 写新时间"
# 不是原子操作，所有线程都会看到"还没到间隔"然后一起打出去 ——
# 节流在默认路径上等于没做，MIN_INTERVAL 形同虚设。
_HOST_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _host_lock(host):
    with _LOCKS_GUARD:
        lock = _HOST_LOCKS.get(host)
        if lock is None:
            lock = threading.Lock()
            _HOST_LOCKS[host] = lock
        return lock


def _throttle(host):
    """同一个域名串行节流：不足间隔就睡一会儿，再加点随机抖动。"""
    with _host_lock(host):
        wait = _last_call.get(host, 0) + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait + random.random() * JITTER)
        _last_call[host] = time.time()


# 把常见中文编码名归一化到 Python 能解码的 codec；gb18030 是 gbk/gb2312 的超集。
_CHARSET_NORM = {
    'gbk': 'gb18030', 'gb2312': 'gb18030', 'gb18030': 'gb18030',
    'big5': 'big5', 'utf-8': 'utf-8', 'utf8': 'utf-8',
}


def _detect_charset(resp, raw_bytes):
    """从响应头或 <meta> 标签推断编码，默认 utf-8。中文站常是 gb2312/gbk。"""
    # 1) Content-Type 头
    ct = (resp.headers.get('Content-Type') or '') if resp else ''
    m = re.search(r'charset=([\w-]+)', ct, re.I)
    if m:
        cs = m.group(1).strip().lower()
        if cs in _CHARSET_NORM:
            return _CHARSET_NORM[cs]
    # 2) <meta charset=...> 或 <meta http-equiv=Content-Type content="...charset=...">
    head = raw_bytes[:2048].decode('latin-1', 'ignore')
    for pat in (r'<meta[^>]+charset=["\']?([\w-]+)',
                r'content=["\'][^"\']*charset=([\w-]+)'):
        m = re.search(pat, head, re.I)
        if m:
            cs = m.group(1).strip().lower()
            if cs in _CHARSET_NORM:
                return _CHARSET_NORM[cs]
    return 'utf-8'


def _cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = hashlib.md5(key.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, h + '.json')


def _read_cache(key, ttl):
    p = _cache_path(key)
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > ttl:
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(key, value):
    try:
        with open(_cache_path(key), 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False)
    except Exception:
        pass


def request(url, data=None, headers=None, timeout=15, retries=2,
            cache_ttl=0, as_json=True, bypass_proxy=False):
    """发一个 HTTP 请求，失败返回 None。

    data:    dict/list → 以 JSON 提交（POST）；None 表示 GET
    cache_ttl: >0 时启用磁盘缓存，单位秒
    as_json: True 时尝试解析 JSON，解析失败返回 None
    bypass_proxy: True 时强制不走任何代理，直连目标（部署机上若挂了
                  指向 127.0.0.1 的破损本地代理，HTTP 明文站点会受影响，
                  此时用它绕过；HTTPS 主源默认仍走环境变量里的代理）。
    """
    opener = None
    if bypass_proxy:
        # 显式禁用代理：某些部署环境的 http_proxy 指向本地坏代理，
        # 会让明文 HTTP 站点拿到 000 / 502，必须绕过。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    cache_key = url + '|' + json.dumps(data, ensure_ascii=False, sort_keys=True)
    if cache_ttl > 0:
        hit = _read_cache(cache_key, cache_ttl)
        if hit is not None:
            return hit

    host = urllib.parse.urlparse(url).netloc
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')

    hdrs = {
        'User-Agent': random.choice(UA_POOL),
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if body is not None:
        hdrs['Content-Type'] = 'application/json;charset=UTF-8'
    if headers:
        hdrs.update(headers)

    for attempt in range(retries + 1):
        try:
            _throttle(host)
            req = urllib.request.Request(url, data=body, headers=hdrs)
            if opener is not None:
                resp = opener.open(req, timeout=timeout)
            else:
                resp = urllib.request.urlopen(req, timeout=timeout)
            with resp:
                raw_bytes = resp.read()
                charset = _detect_charset(resp, raw_bytes)
                raw = raw_bytes.decode(charset, 'ignore')
            if as_json:
                value = json.loads(raw)
            else:
                value = raw
            if cache_ttl > 0:
                _write_cache(cache_key, value)
            return value
        except urllib.error.HTTPError as e:
            # 429/403/412 是被风控了，退避久一点再试
            if e.code in (403, 412, 429, 503):
                time.sleep(1.5 * (attempt + 1))
            elif 400 <= e.code < 500:
                return None          # 参数/地址类错误，重试没意义
        except Exception:
            pass
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1) + random.random())
    return None
