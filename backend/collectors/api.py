# -*- coding: utf-8 -*-
"""外部岗位来源适配器（可选）。

两个来源，都不配也能正常用（此时只有内置岗位库）：
  1) JsonFileCollector：读 webapp/data/jobs.json，把自家岗位数据放进去即可
  2) HttpCollector：    GET 一个内部接口，返回 {"jobs":[...]} 或 [...] 都支持

jobs.json 示例：
  [
    {"title":"仓库管理员","company":"某某公司","city":"杭州",
     "salary_text":"5000-7000元","exp":"经验不限","edu":"不限",
     "tags":["仓库","盘点"],"desc":"..."}
  ]
"""
import json
import os

from .base import BaseCollector

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'data')


class JsonFileCollector(BaseCollector):
    """从本地 jobs.json 读取岗位。"""

    name = 'json'
    label = '本地岗位文件'

    def fetch(self, keyword='', city='', limit=30, natures=None):
        path = os.path.join(DATA_DIR, 'jobs.json')
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                rows = json.load(f)
        except Exception:
            return []
        if isinstance(rows, dict):
            rows = rows.get('jobs', [])
        want = set(natures or [])
        out = []
        for j in rows:
            if want and (j.get('nature') or '民营') not in want:
                continue
            if city and city not in (j.get('city') or ''):
                continue
            if keyword:
                hay = (j.get('title', '') + j.get('company', '') + ' '.join(j.get('tags') or []))
                if keyword not in hay:
                    continue
            out.append(self.normalize(dict(j), '本地岗位文件'))
            if len(out) >= limit:
                break
        return out


class HttpCollector(BaseCollector):
    """从一个 HTTP 接口拉取岗位（需要在 data/collector.json 里配置 url）。"""

    name = 'http'
    label = '内部岗位接口'

    def fetch(self, keyword='', city='', limit=30, natures=None):
        cfg_path = os.path.join(DATA_DIR, 'collector.json')
        if not os.path.exists(cfg_path):
            return []
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            return []
        url = (cfg or {}).get('url')
        if not url:
            return []
        from urllib import request as urlrequest
        from urllib.parse import urlencode
        query = urlencode({'keyword': keyword or '', 'city': city or '', 'limit': limit})
        sep = '&' if '?' in url else '?'
        req = urlrequest.Request(url + sep + query, headers={'User-Agent': 'resume-assistant/1.0'})
        with urlrequest.urlopen(req, timeout=8) as resp:
            rows = json.loads(resp.read().decode('utf-8'))
        if isinstance(rows, dict):
            rows = rows.get('jobs', [])
        return [self.normalize(dict(j), '内部岗位接口') for j in rows[:limit]]
