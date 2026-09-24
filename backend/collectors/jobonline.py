# -*- coding: utf-8 -*-
"""就业在线（www.jobonline.cn）采集器。

═══ 接口是国密加密的，本文件把整套算法复现了一遍 ═══

站点是 Vue SPA，列表页 HTML 里没有数据，真实接口在 app.<hash>.js 里：
    POST https://api.jobonline.cn/jobtbao-es-api/elastic/api/position/queryPositionByCon
请求体只有一个字段 {"businessData": "<密文>"}，另有若干自定义头。

加密方案（从 bundle 里逐个函数抠出来）：
  * businessData = SM4-CBC(JSON, key=w, iv=C)，**输出 hex 小写**。
    前端是 `sm4.encrypt(json, w, {mode:'cbc', iv:C})`，外面那层
    `"sm-crypto"+...` 前缀在加密函数里被剥掉了，线上发的是纯密文。
  * E-CONTENT-PATH = "04" + SM2("w,C,x,S", 公钥, cipherMode=1)
    w/C 是上面 SM4 用的 key/iv，x/S 是另外两个随机数，四个都是 16 字节。
    SM2 用 **C1C3C2** 顺序。
  * E-SIGN = SM3("business" + businessData + "data")
  * EncryptFlag: 2 / E-VERSION: v2.0.0 / msha: 由 POST /msha/router 拿到
  * 另有 platform:3 / TrackPlatform:JPLN06 —— 拦截器里对所有请求无条件设置

═══ 公钥：用默认公钥 D，不是 jobtbao 那个硬编码公钥 ═══
bundle 里有两个公钥，拦截器逻辑是：先无条件用默认公钥 D 加密一次；然后若
URL 命中列表 ["jobtbao-platform-srv/","jobtbao-es-api/",...] 再用硬编码公钥
加密一次。照代码直读会以为「列表命中的要用硬编码公钥」，我一开始就是这么
写的，结果全是 40202「请求体解密失败」。

拿 /jobtbao-platform-srv/hotCity/findList 当判定器（不管成败都在 0.4s 内
返回）做矩阵实验后确认：**外层必须用默认公钥 D 才解得开**。硬编码那个公钥
在这批接口上解不开。详见 scripts 里的 jobonline_matrix2.py。

═══ 三个 gmssl / 加密相关的坑（都实测过，改代码前先看这里）═══
  1) gmssl 的 crypt_cbc **自己会做 PKCS7 填充**，调用方不能再 pad。
     手动 pad 一次会多出一个分组（实测 96 hex 变 128 hex），服务端解不开。
  2) gmssl 的 SM2 加密输出是 **C1C2C3**，而站点要 **C1C3C2**；
     且 gmssl 收公钥时要**去掉开头的 "04"**。所以必须 `c1 + c3 + c2` 重排。
  3) 单条查询 total 上限 10000，超过就取不全。要么按省份分片，要么按城市。
     `provinceCode` 传 GB/T 2260 的六位码。

═══ 关于电话 ═══
职位行里**没有**任何联系方式字段。公司详情接口有 phone 字段，但抽样 11 家
全部为 null（命中率 ~0）。站点自带的天眼查代理
(/jobtbao-platform-srv/web/tianYanCha/search) 只返回工商信息——法人、注册
资本、统一社会信用代码、存续状态，**也没有电话**。所以本采集器不产出电话，
电话要靠别的源补。
"""
import json
import os
import time

from . import net
from .base import BaseCollector

API = 'https://api.jobonline.cn/jobtbao-es-api/elastic/api/position/queryPositionByCon'
MSHA_URL = 'https://api.jobonline.cn/msha/router'
HOME = 'https://www.jobonline.cn/findPositions'
# 职位详情深链。路由 /positionDetail 是懒加载页，参数名是从它的 chunk
# （75.e81d583a80aa42cf3010.js）里确认的：$route.query.id && this.init()。
# 没有职位 id 时才退回搜索页 —— 那条链接点进去看不到具体岗位，等于死链。
DETAIL = 'https://www.jobonline.cn/positionDetail?id='

# 默认公钥 D（带 04 前缀）。别换成 bundle 里那个 jobtbao 硬编码公钥，
# 见文件头说明——换了就是 40202。
PUB_KEY = ('043f4a9673db98fd52a87e087da75ca8d4978748188e29373acc131887d7b78'
           'ee89b07364f644352e4cb4029d8330509368b27b10638345c8afd41149626d917aa')

PAGE_SIZE = 50
MAX_PAGE = 200          # 单省最多翻多少页（对端每页实际给 40 条，不是 50）
PAGE_GAP = 0.25
STOP_AFTER = 15         # 连续几页零新增就认定到底（见 _one_province 的说明）

# GB/T 2260 省级代码。用来分片绕开单查询 10000 条的上限。
PROVINCES = [
    ('110000', '北京'), ('120000', '天津'), ('130000', '河北'),
    ('140000', '山西'), ('150000', '内蒙古'), ('210000', '辽宁'),
    ('220000', '吉林'), ('230000', '黑龙江'), ('310000', '上海'),
    ('320000', '江苏'), ('330000', '浙江'), ('340000', '安徽'),
    ('350000', '福建'), ('360000', '江西'), ('370000', '山东'),
    ('410000', '河南'), ('420000', '湖北'), ('430000', '湖南'),
    ('440000', '广东'), ('450000', '广西'), ('460000', '海南'),
    ('500000', '重庆'), ('510000', '四川'), ('520000', '贵州'),
    ('530000', '云南'), ('540000', '西藏'), ('610000', '陕西'),
    ('620000', '甘肃'), ('630000', '青海'), ('640000', '宁夏'),
    ('650000', '新疆'),
]


def _load_gmssl():
    """gmssl 装在 webapp/_pylibs 下（不是系统包），这里按需挂到 sys.path。"""
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    lib = os.path.normpath(os.path.join(here, '..', '..', '_pylibs'))
    if os.path.isdir(lib) and lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        from gmssl import sm2 as _sm2, sm3 as _sm3, sm4 as _sm4
        return _sm2, _sm3, _sm4
    except Exception as exc:                              # noqa: BLE001
        raise RuntimeError(
            '缺少 gmssl（国密库）。本机装法：python -m pip install '
            '--target=%s gmssl' % lib) from exc


def sm4_cbc_hex(key_hex, iv_hex, plaintext):
    """SM4-CBC 加密，返回小写 hex。不要自己 padding，gmssl 会做。"""
    _, _, sm4 = _load_gmssl()
    c = sm4.CryptSM4()
    c.set_key(bytes.fromhex(key_hex), sm4.SM4_ENCRYPT)
    return c.crypt_cbc(bytes.fromhex(iv_hex), plaintext.encode('utf-8')).hex()


def sm2_encrypt_c1c3c2(msg, pub_hex=PUB_KEY):
    """SM2 加密 → 站点要的 C1C3C2（带 04 前缀）。"""
    sm2, _, _ = _load_gmssl()
    c = sm2.CryptSM2(public_key=pub_hex[2:], private_key=None)
    ct = c.encrypt(msg.encode('utf-8'))
    if isinstance(ct, bytes):
        ct = ct.hex()
    c1, c3, c2 = ct[:128], ct[-64:], ct[128:-64]
    return '04' + c1 + c3 + c2


def sm3_hex(msg):
    _, sm3, _ = _load_gmssl()
    return sm3.sm3_hash(list(msg.encode('utf-8')))


def rand_hex(n_bytes=16):
    return os.urandom(n_bytes).hex()


def build_request(payload, msha='1', pub_hex=PUB_KEY):
    """组装 (body_dict, headers)。w/C/x/S 每次请求现生成，同一请求内必须一致。"""
    w, C, x, S = rand_hex(), rand_hex(), rand_hex(), rand_hex()
    business_data = sm4_cbc_hex(w, C, json.dumps(payload, ensure_ascii=False))
    headers = {
        'Content-Type': 'application/json;charset=UTF-8',
        'Origin': 'https://www.jobonline.cn',
        'Referer': HOME,
        'EncryptFlag': '2',
        'E-CONTENT-PATH': sm2_encrypt_c1c3c2('%s,%s,%s,%s' % (w, C, x, S),
                                             pub_hex),
        'E-SIGN': sm3_hex('business' + business_data + 'data'),
        'E-VERSION': 'v2.0.0',
        'msha': msha,
        'platform': '3',            # 拦截器对所有请求无条件设置，缺了可能被当成异常客户端
        'TrackPlatform': 'JPLN06',
    }
    return {'businessData': business_data}, headers


def base_payload(**kw):
    p = {'page': 1, 'pagesize': PAGE_SIZE,
         'positionName3': '', 'salaryRange': '',
         'areaCode': '', 'areaCodes': '', 'cityCode': '', 'cityCodes': '',
         'provinceCode': '', 'provinceCodes': '', 'eduDegree': '',
         'industryCode': '', 'jobAge': '', 'nature': '', 'positionCode': '',
         'size': '', 'companyId': '', 'sortType': '', 'areaId': '',
         'channelNumber': '', 'licenseNumber': '', 'positionCode1': '',
         'positionCode1s': '', 'positionCode2': '', 'positionCode3': '',
         'category': '', 'zc': '', 'zcAreaId': '', 'zcKeyword': '',
         'pageType': '', 'introCategory': ''}
    p.update(kw)
    return p


def _post(url, body, headers, timeout=25):
    """发请求。优先 curl_cffi（已验证可用、指纹正常），没装就退回 net.request。"""
    try:
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        lib = os.path.normpath(os.path.join(here, '..', '..', '_pylibs'))
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
        from curl_cffi import requests as creq
        r = creq.post(url, data=json.dumps(body, ensure_ascii=False),
                      headers=headers, impersonate='chrome', timeout=timeout)
        return r.status_code, r.text
    except Exception:                                     # noqa: BLE001
        pass
    resp = net.request(url, data=body, headers=headers, timeout=timeout,
                       retries=0, as_json=False)
    if isinstance(resp, (dict, list)):
        return 200, json.dumps(resp, ensure_ascii=False)
    return 200, resp if isinstance(resp, str) else ''


def _pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, '', []):
            return v
    return ''


class JobOnlineCollector(BaseCollector):
    name = 'jobonline'
    label = '就业在线（人社部·全国岗位池）'

    enabled = True

    def __init__(self):
        self._msha = None
        # 本轮是否「真抓全了」。只有翻到空页才算到底；被 limit 或 MAX_PAGE
        # 截断的都只是抽样。按公司对账必须以它为前提，否则会把「这次没翻到」
        # 的公司误判成「源站下线了」，误删比留着旧数据糟得多。
        self.exhausted = False

    def _get_msha(self):
        if self._msha:
            return self._msha
        try:
            r = net.request(MSHA_URL, data={}, timeout=8, retries=0,
                            as_json=True)
            if isinstance(r, dict) and r.get('data'):
                self._msha = str(r['data'])
                return self._msha
        except Exception:                                  # noqa: BLE001
            pass
        self._msha = '1'                                   # 前端同款兜底值
        return self._msha

    # ── 采集入口 ──────────────────────────────────────────────────
    def fetch_all(self, limit_per_province=0, provinces=None):
        """按省份分片抓全量。返回 (岗位列表, 公司名集合)。

        limit_per_province=0 表示不限制，翻到空页为止。
        """
        self.exhausted = True
        out, seen, companies = [], set(), set()
        for code, nm in (provinces or PROVINCES):
            got, comps, done = self._one_province(code, limit_per_province)
            if not done:
                self.exhausted = False        # 有省份没翻到底 → 本轮只是抽样
            for r in got:
                k = r.get('key')
                if k and k in seen:
                    continue
                seen.add(k)
                out.append(r)
            companies |= comps
            print('  [jobonline] %-8s %5d 条（累计 %d，公司 %d）'
                  % (nm, len(got), len(out), len(companies)), flush=True)
        return out, companies

    def _one_province(self, code, limit=0):
        """翻一个省的所有页。返回 (岗位, 公司集合, 是否翻到底)。

        ══ 怎么算「翻到底」══
        对端的 ES 有个深度上限（实测广东到 3913 条≈100 页就到头了），但**到达
        上限后它不会返回空页，而是把最后一页的内容反复吐出来**（210 页里只有
        103 种不同内容，指纹反复出现）。所以：
          - 只看「空页」永远等不到 → exhausted 恒为 False → 对账永远不执行；
          - 只看「本页 0 新增」又可能因为一次偶发重复提前收工。
        取折中：连续 STOP_AFTER 页零新增才判定到底。实测拐点是「从第 N 页起
        每一页都零新增」，连续两页足够稳，代价只是多打两次接口。

        另外 pagesize 我请求的是 50，对端实际每页只给 40 —— 上限按 40 算，
        MAX_PAGE 是页码上限不是条数上限。
        """
        rows, comps = [], set()
        seen = set()
        done = False
        idle = 0                      # 连续「零新增」页数
        for page in range(1, MAX_PAGE + 1):
            chunk = self._one_page(page, provinceCode=code)
            if not chunk:
                done = True                   # 翻到空页 = 到底
                break
            keys = [r.get('key') for r in chunk]
            gain = sum(1 for k in keys if k not in seen)
            seen.update(keys)
            rows.extend(chunk)
            for r in chunk:
                if r.get('company'):
                    comps.add(r['company'])
            if gain == 0:
                idle += 1
                if idle >= STOP_AFTER:
                    done = True               # 对端在重复吐内容 = 到上限了
                    break
            else:
                idle = 0
            if limit and len(rows) >= limit:
                break                         # 被 limit 截断：不算翻到底
            time.sleep(PAGE_GAP)
        return rows, comps, done

    def fetch(self, keyword='', city='', limit=30, natures=None):
        """BaseCollector 约定的入口，按条件抓一小批（用于按城市补采）。"""
        self.exhausted = False
        kw = base_payload(page=1, pagesize=min(limit or 30, PAGE_SIZE),
                          positionName3=keyword or '')
        chunk = self._one_page(1, **{k: v for k, v in kw.items()
                                     if k not in ('page', 'pagesize')})
        if not chunk:
            self.exhausted = True
        return chunk[:limit]

    # ── 单页 ─────────────────────────────────────────────────────
    def _one_page(self, page, **filters):
        payload = base_payload(page=page)
        payload.update(filters)
        body, headers = build_request(payload, self._get_msha())
        try:
            code, txt = _post(API, body, headers)
        except Exception as exc:                           # noqa: BLE001
            print('[jobonline] 请求失败: %s' % exc)
            return []
        if code != 200:
            print('[jobonline] HTTP %s' % code)
            return []
        try:
            resp = json.loads(txt)
        except Exception:                                  # noqa: BLE001
            return []
        if str(resp.get('code')) not in ('200', '0000', '0'):
            print('[jobonline] code=%s msg=%s' % (resp.get('code'),
                                                  resp.get('message')))
            return []
        obj = resp.get('object') or {}
        rows = obj.get('rows') or []
        if not isinstance(rows, list):
            return []
        items = []
        for j in rows:
            if not isinstance(j, dict):
                continue
            item = self._convert(j)
            if item:
                items.append(item)
        return items

    # ── 字段映射 ──────────────────────────────────────────────────
    @staticmethod
    def _salary(j):
        """薪资字段长得不统一：low/high 有时是裸数字，有时自带单位
        （如 highSalary="1.2万元"）。所以单位只在结果里没有「元」时才补，
        否则会拼出「0.8-1.2万元元/月」这种东西。"""
        low = str(_pick(j, 'lowSalary') or '').strip()
        high = str(_pick(j, 'highSalary') or '').strip()
        unit = str(_pick(j, 'salaryUnitName') or '').strip()
        parts = [x for x in (low, high) if x]
        if not parts:
            return '面议'
        if len(parts) == 2 and parts[0] == parts[1]:
            parts = parts[:1]
        text = '-'.join(parts)
        if unit and '元' not in text:
            text += unit
        return text

    @staticmethod
    def _ymd(ts):
        try:
            return time.strftime('%Y-%m-%d', time.localtime(int(ts)))
        except Exception:                                  # noqa: BLE001
            return ''

    def _convert(self, j):
        title = (_pick(j, 'positionName') or '').strip()
        company = (_pick(j, 'companyName', 'aliasName') or '').strip()
        if not title or not company:
            return None
        city = (_pick(j, 'cityName', 'provinceName') or '').strip()
        light = j.get('light')
        tags = [str(t).strip() for t in light if t] if isinstance(light, list) else []
        pid = str(_pick(j, 'id') or '').strip()
        # 统一社会信用代码：站点不提供电话，但这是唯一能拿工商平台反查联系方式
        # 的稳定主键（公司名会变、会重名，信用代码不会）。先存着备用。
        lic = str(_pick(j, 'licenseNumber') or '').strip()
        return self.normalize({
            'title': title,
            'company': company,
            'city': city,
            'district': (_pick(j, 'areaName') or '').strip(),
            'salary_text': self._salary(j),
            'exp': (_pick(j, 'jobAge') or '经验不限'),
            'edu': (_pick(j, 'eduDegree') or '不限'),
            'tags': tags,
            'desc': (_pick(j, 'address') or '').strip(),
            'nature': (_pick(j, 'nature') or '民营'),
            'hire_type': '直签',
            'url': HOME,
            'apply_url': (DETAIL + pid) if pid else HOME,
            'hr_email': '',
            'hr_phone': '',          # 站点不提供电话，见文件头
            'contact_phone': '',
            'headcount': int(_pick(j, 'num') or 0) or 0,
            'recruit_type': (_pick(j, 'category') or ''),
            'deadline': '',
            'published': self._ymd(_pick(j, 'publishTime')),
            'position_id': pid,      # 详情页用，也方便以后回查
            'credit_code': lic,      # 统一社会信用代码，补电话时的反查主键
        }, self.name)


def _selftest():
    """离线自检：拿 Node sm-crypto 的基准值比对，不用联网。"""
    ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                            '..', '_ref_sm.json')
    ref_path = os.path.normpath(ref_path)
    if not os.path.exists(ref_path):
        print('没有基准文件 %s，跳过 SM4/SM3 比对' % ref_path)
        return
    ref = json.load(open(ref_path, encoding='utf-8'))
    got = sm4_cbc_hex(ref['w'], ref['C'], ref['pt'])
    print('SM4: %s' % ('PASS' if got == ref['businessData'] else
                       'FAIL\n  want %s\n  got  %s' % (ref['businessData'], got)))
    s = sm3_hex('business' + ref['businessData'] + 'data')
    print('SM3: %s' % ('PASS' if s == ref['sign'] else
                       'FAIL\n  want %s\n  got  %s' % (ref['sign'], s)))
    ecp = sm2_encrypt_c1c3c2('%s,%s,%s,%s' % (ref['w'], ref['C'], ref['x'],
                                              ref['S']))
    print('SM2: len=%d（站点基准 %d）%s'
          % (len(ecp), ref['ecp_len'],
             'PASS' if len(ecp) == ref['ecp_len'] else 'FAIL'))


if __name__ == '__main__':
    import sys
    if '--selftest' in sys.argv:
        _selftest()
    else:
        c = JobOnlineCollector()
        jobs, comps = c.fetch_all(limit_per_province=int(
            sys.argv[1]) if len(sys.argv) > 1 else 0)
        print('\n共 %d 条岗位，%d 家公司，exhausted=%s'
              % (len(jobs), len(comps), c.exhausted))
