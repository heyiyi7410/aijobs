# -*- coding: utf-8 -*-
"""腾讯云「通用文字识别（高精度版）」—— GeneralAccurateOCR。

它是本地 tesseract 的**增强引擎，不是替代**：本地认不出来的图才交给云
（调用策略由 app._ocr_image_b64 决定，这里只负责「图进、字出」）。
官方说高精度版在手写体、小字、模糊字、倾斜文本上比印刷体版更准，
而这四类恰好是 tesseract 的死角。

只用标准库（hashlib/hmac/urllib），服务器上不用额外 pip install。
签名走 TC3-HMAC-SHA256，照腾讯云文档实现。

密钥**绝不能写进代码**，依次从这三处读：
  1. 环境变量 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY（可加 TENCENTCLOUD_TOKEN）
  2. data/tcloud_ocr.json（自己写的文件，已进 .gitignore）
  3. tccli 浏览器登录写下的 ~/.tccli/default.credential
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

SERVICE = 'ocr'
HOST = 'ocr.tencentcloudapi.com'
ENDPOINT = 'https://' + HOST
ACTION = 'GeneralAccurateOCR'
VERSION = '2018-11-19'
# 官方：本接口只支持 ap-guangzhou，写别的地域会报 UnsupportedRegion
REGION = 'ap-guangzhou'
TIMEOUT = 12
MAX_B64 = 10 * 1024 * 1024   # 官方上限：base64 后 10MB

# webapp/data/tcloud_ocr.json。测试里可以像 mailer 那样整体替换。
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'tcloud_ocr.json')


def _credential():
    """取 (secret_id, secret_key, token)；没配到就返回空串三元组。

    三处来源是 or 关系，谁先有值用谁。token 只有临时凭证（STS / tccli 登录）
    才有，长期密钥没有——没有就不发 X-TC-Token 头，别发空的。
    """
    sid = os.environ.get('TENCENTCLOUD_SECRET_ID') or ''
    key = os.environ.get('TENCENTCLOUD_SECRET_KEY') or ''
    tok = os.environ.get('TENCENTCLOUD_TOKEN') or ''
    if sid and key:
        return sid, key, tok

    for path, keys in ((CONFIG_PATH, ('secret_id', 'secret_key', 'token')),
                       (os.path.expanduser('~/.tccli/default.credential'),
                        ('secretId', 'secretKey', 'token'))):
        try:
            with open(path, encoding='utf-8') as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            continue
        sid, key, tok = (cfg.get(k) or '' for k in keys)
        if sid and key:
            return sid, key, tok
    return '', '', ''


def is_configured():
    """有没有可用的密钥。给 /api/ocr/status 用，方便一眼看出云引擎装没装好。"""
    sid, key, _ = _credential()
    return bool(sid and key)


def _hmac_sha256(key_bytes, msg):
    return hmac.new(key_bytes, msg.encode('utf-8'), hashlib.sha256).digest()


def _sign(secret_id, secret_key, payload, timestamp):
    """TC3-HMAC-SHA256 签名。

    只签 POST / + JSON body 这一种请求形态，CanonicalQueryString 恒为空。
    签名用的日期必须和 X-TC-Timestamp 同一天，否则服务端报签名过期。
    """
    algorithm = 'TC3-HMAC-SHA256'
    date = time.strftime('%Y-%m-%d', time.gmtime(timestamp))
    canonical_headers = 'content-type:application/json\nhost:%s\nx-tc-action:%s\n' % (
        HOST, ACTION.lower())
    signed_headers = 'content-type;host;x-tc-action'
    hashed_payload = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    canonical_request = '\n'.join(
        ['POST', '/', '', canonical_headers, signed_headers, hashed_payload])
    scope = '%s/%s/tc3_request' % (date, SERVICE)
    string_to_sign = '\n'.join(
        [algorithm, str(timestamp), scope,
         hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()])
    secret_date = _hmac_sha256(('TC3' + secret_key).encode('utf-8'), date)
    secret_service = _hmac_sha256(secret_date, SERVICE)
    secret_signing = _hmac_sha256(secret_service, 'tc3_request')
    signature = hmac.new(secret_signing, string_to_sign.encode('utf-8'),
                         hashlib.sha256).hexdigest()
    return '%s Credential=%s/%s, SignedHeaders=%s, Signature=%s' % (
        algorithm, secret_id, scope, signed_headers, signature)


def _to_png_b64(raw):
    """云接口不认 WebP（我们本地反而认），统一转成 PNG 再发出去。

    转不动 / 没装 PIL 就原样发——云接口自己认 PNG/JPG/BMP/PDF，多数情况够用。
    """
    try:
        import io
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(raw)) as im:
            if im.format == 'PNG':
                return None
            buf = io.BytesIO()
            ImageOps.exif_transpose(im).convert('RGB').save(buf, format='PNG')
        import base64
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:  # noqa: BLE001
        return None


def recognize(b64, timeout=TIMEOUT):
    """图片 base64 → 文字。返回 (text, kind)，kind 为空＝成功。

    kind 的取值：no_cred 没配密钥 / too_big 图太大 / auth 密钥或权限不对 /
    fail 调用失败 / no_text 云也没认出字。调用方按自己的语境翻成人话。
    """
    import base64 as _b64
    secret_id, secret_key, token = _credential()
    if not (secret_id and secret_key):
        return '', 'no_cred'

    body_b64 = b64
    try:
        raw = _b64.b64decode(b64, validate=True)
        png = _to_png_b64(raw)
        if png:
            body_b64 = png
    except Exception:  # noqa: BLE001
        pass
    if len(body_b64) > MAX_B64:
        return '', 'too_big'

    payload = json.dumps({'ImageBase64': body_b64})
    timestamp = int(time.time())
    headers = {
        'Content-Type': 'application/json',
        'Host': HOST,
        'X-TC-Action': ACTION,
        'X-TC-Version': VERSION,
        'X-TC-Timestamp': str(timestamp),
        'X-TC-Region': REGION,
        'Authorization': _sign(secret_id, secret_key, payload, timestamp),
    }
    if token:
        headers['X-TC-Token'] = token

    try:
        req = urllib.request.Request(
            ENDPOINT, data=payload.encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 的正文里同样带着 Error 结构，读出来才能分辨是不是密钥问题
        try:
            body = json.loads(exc.read().decode('utf-8'))
        except Exception:  # noqa: BLE001
            return '', 'fail'
    except Exception:  # noqa: BLE001  超时、DNS、连接重置都算「这次没调通」
        return '', 'fail'

    resp_obj = body.get('Response') or {}
    error = resp_obj.get('Error') or {}
    if error:
        code = error.get('Code') or ''
        if code.startswith('AuthFailure'):
            return '', 'auth'
        return '', 'fail'

    # 云已经按阅读顺序返回，不要再自己排序——排错行序比不排更糟
    lines = [(d.get('DetectedText') or '').strip()
             for d in resp_obj.get('TextDetections') or []]
    text = '\n'.join(x for x in lines if x).strip()
    return text, '' if text else 'no_text'
