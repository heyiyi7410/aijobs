"""Explicit low-frequency public announcement sync (no account/cookies)."""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
import re
import sqlite3
import time
from urllib.request import Request, urlopen
from collectors.gwy_public import CACHE, CHANNELS, ROOT, NOISE, candidates, parse_article
import models


def audit_cache():
    """Recheck only this adapter's rows; preserve removed rows for recovery."""
    from collectors.gov_soe import PROVINCE_CITIES, _CITY_HINTS
    data = json.loads(CACHE.read_text(encoding='utf-8'))
    keep, removed = [], []
    for row in data['jobs']:
        row.setdefault('observed_at', CACHE.stat().st_mtime)
        title = row['title']
        if NOISE.search(title):
            removed.append(row)
            continue
        if not row.get('city'):
            names = _CITY_HINTS + [c for cities in PROVINCE_CITIES.values() for c in cities] + list(PROVINCE_CITIES)
            row['city'] = next((c for c in names if c in title), '')
        if row['source'].endswith('银行') and re.search(r'邮储银行|邮政储蓄银行|中国银行|工商银行|农业银行|建设银行|交通银行|农业发展银行|国家开发银行|进出口银行', title):
            row['nature'] = '国企'
        keep.append(row)
    if removed:
        (CACHE.parent / ('excluded-' + str(int(time.time())) + '.json')).write_text(json.dumps(removed, ensure_ascii=False, indent=2), encoding='utf-8')
        with sqlite3.connect(ROOT / 'data' / 'app.db') as conn:
            conn.executemany("DELETE FROM jobs WHERE key=? AND source LIKE '上岸鸭公开招聘·%'", [(r['key'],) for r in removed])
    models.init_db(str(ROOT / 'data' / 'app.db'))
    models.upsert_jobs(keep)
    temp = CACHE.with_suffix('.tmp')
    temp.write_text(json.dumps({'jobs':keep}, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, CACHE)
    result = {'retained':len(keep), 'removed_noise':len(removed),
              'external_apply_links':sum(bool(r.get('verified_apply_url')) for r in keep),
              'deadline_known':sum(bool(r.get('deadline')) for r in keep),
              'channels':dict(Counter(r['source'] for r in keep))}
    (CACHE.parent / 'audit-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


def run_sync(per_channel=15, selected_channel=None):
    if not 1 <= per_channel <= 50:
        raise ValueError('每栏目 1–50 篇')
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    lock = CACHE.parent / 'sync.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    report = dict(started=datetime.now().isoformat(), candidates=0, fetched=0, imported=0, failures=[], skipped={}, channels={})
    rows = {}
    if CACHE.exists():
        rows = {r['key']:r for r in json.loads(CACHE.read_text(encoding='utf-8')).get('jobs', [])}
    skipped = Counter()
    visited = set()
    last_request = 0.0
    def fetch(url):
        nonlocal last_request
        time.sleep(max(0, 5 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        req = Request(url, headers={'User-Agent': 'AIjobs-Personal-Reader/1.0', 'Accept': 'text/html'})
        with urlopen(req, timeout=20) as response:
            if not response.url.startswith('https://www.gwy.com/'):
                raise RuntimeError('unexpected redirect; stopped')
            return response.read(4000000).decode('utf-8')
    if models._DB_PATH is None:
        models.init_db(str(ROOT / 'data' / 'app.db'))
    try:
        for channel, listing in CHANNELS:
            if selected_channel and selected_channel != channel:
                continue
            pairs = candidates(fetch(listing), listing)[:per_channel]
            report['channels'][channel] = dict(candidates=len(pairs), imported=0)
            report['candidates'] += len(pairs)
            for url, _ in pairs:
                if url in visited:
                    continue
                visited.add(url)
                try:
                    html = fetch(url)
                    report['fetched'] += 1
                    row, reason = parse_article(html, url, channel)
                    if not row:
                        skipped[reason] += 1
                        continue
                    rows[row['key']] = row
                    models.upsert_jobs([row])
                    report['imported'] += 1
                    report['channels'][channel]['imported'] += 1
                    print('入库：' + row['title'], flush=True)
                except Exception as error:
                    report['failures'].append({'url':url, 'error':str(error)[:250]})
                    # Stop on access restriction; do not retry or bypass it.
                    if getattr(error, 'code', None) in (401, 403, 429):
                        raise
        print('本批入库 %d 篇公告' % report['imported'], flush=True)
    finally:
        report['skipped'] = dict(skipped)
        report['finished'] = datetime.now().isoformat()
        for path, data in [(CACHE, {'jobs':list(rows.values())}), (CACHE.parent/'last-report.json', report)]:
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, path)
        lock.unlink(missing_ok=True)


def refresh_if_due(interval=6 * 3600):
    """Called by app background refresh, not user searches. Persist cooldown."""
    from collectors import _enabled
    from collectors.gwy_public import GwyPublicCollector
    if not _enabled(GwyPublicCollector()):
        return False
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    marker = CACHE.parent / 'last-auto-attempt.txt'
    latest = max((p.stat().st_mtime for p in (CACHE, marker) if p.exists()), default=0)
    if time.time() - latest < interval:
        return False
    # The run lock prevents simultaneous processes from fetching the same host.
    if (CACHE.parent / 'sync.lock').exists():
        return False
    marker.write_text(datetime.now().isoformat(), encoding='utf-8')
    try:
        run_sync()
    except FileExistsError:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--per-channel', type=int, default=15)
    parser.add_argument('--channel', choices=[name for name, _ in CHANNELS])
    args = parser.parse_args()
    run_sync(args.per_channel, args.channel)


if __name__ == '__main__':
    main()
