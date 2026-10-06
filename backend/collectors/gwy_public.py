"""Public announcements only; no cookies and no interactive Gaodun account."""
from datetime import date, datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlparse
from .base import BaseCollector

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'data' / 'gwy_public' / 'jobs.json'
CHANNELS = [('国企', 'https://www.gwy.com/gqzp/'),
            ('事业单位', 'https://www.gwy.com/sydw/zpxx/'),
            ('银行', 'https://www.gwy.com/yhzp/')]
NOISE = re.compile(r'培训|辅导|备考|攻略|技巧|哪个好|有哪些|什么|如何|怎么|条件解析|条件要求|招聘条件|招聘流程|报名条件|报名流程|指南|详解|解读|报名入口|招聘官网|面试|笔试|公示|拟聘|录用名单|成绩|汇总|专业要求|报名时间')


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.body = []
        self.heading = []
        self.links = []
        self.article_links = []
        self.anchor = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        article = 'detail_content' in attrs.get('class', '').split()
        if tag not in ('br', 'img', 'hr', 'input', 'meta', 'link', 'source', 'wbr'):
            self.stack.append((tag, article))
        if tag in ('p', 'div', 'br', 'li', 'tr') and self.in_article():
            self.body.append('\n')
        if tag == 'a':
            self.anchor = [attrs.get('href', ''), [], attrs.get('title', ''), self.in_article()]

    def in_article(self):
        return any(flag for _, flag in self.stack)

    def handle_data(self, data):
        if any(tag in ('script', 'style') for tag, _ in self.stack):
            return
        if self.in_article():
            self.body.append(data)
        if any(tag == 'h1' for tag, _ in self.stack):
            self.heading.append(data)
        if self.anchor:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor:
            href, parts, title, inside = self.anchor
            pair = (href, title or re.sub(r'\s+', ' ', ''.join(parts)).strip())
            self.links.append(pair)
            if inside:
                self.article_links.append(pair)
            self.anchor = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    @property
    def text(self):
        return '\n'.join(x.strip() for x in ''.join(self.body).splitlines() if x.strip())


def candidates(html, base):
    out = {}
    for href, title in Page(html).links:
        # Listing cards include a separate summary/date; don't classify by it.
        title = title.split(' ')[0]
        url = urljoin(base, href).split('?')[0]
        if not re.fullmatch(r'https://www\.gwy\.com/(?:gqzp|sydw|yhzp)/\d+\.html', url):
            continue
        if NOISE.search(title) or not re.search(r'招聘|引进|引才', title):
            continue
        out.setdefault(url, title)
    return list(out.items())


def closing_date(text, year):
    # Never use publication dates as deadlines. Only explicit closing language.
    patterns = [r'(?:截止(?:时间)?(?:为|至|是)?|截至)\s*[：:]?\s*((?:20\d{2}年)?\d{1,2}月\d{1,2}日)',
                r'((?:20\d{2}年)?\d{1,2}月\d{1,2}日)\s*(?:\d{1,2}[：:]\d{2}\s*)?(?:截止|结束)',
                r'报名时间[^\n。]{0,60}?[至—–~～-]\s*((?:20\d{2}年)?\d{1,2}月\d{1,2}日)']
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            parts = re.fullmatch(r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日', match[1])
            try:
                return date(int(parts[1] or year), int(parts[2]), int(parts[3])).isoformat()
            except ValueError:
                continue
    return ''


def parse_article(html, url, channel, today=None):
    today = today or datetime.now().date()
    page = Page(html)
    title = ''.join(page.heading).strip()
    text = page.text
    if not title or len(text) < 100 or NOISE.search(title):
        return None, '非招聘正文或资讯'
    if not re.search(r'招聘|引进|引才', title):
        return None, '非招聘公告'
    years = re.findall(r'20\d{2}', title)
    if years and int(years[0]) < today.year:
        return None, '往年公告'
    # Campus titles may say 2027 while applications close in 2026.
    published = re.search(r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})', re.sub(r'<[^>]+>', ' ', html.split('detail_content')[0])[-6000:])
    year = int(published[1]) if published else today.year
    deadline = closing_date(text, year)
    if deadline and deadline < today.isoformat():
        return None, '已截止'
    # Only inspect article text, not sidebar recommendations/footer contacts.
    urls = re.findall(r'https?://[^\s<>"\u3000]+', text)
    urls += [urljoin(url, href) for href, label in page.article_links
             if re.search(r'报名|应聘|招聘|投递', label)]
    apply = ''
    for link in urls:
        link = link.rstrip('。，；、）).;')
        host = urlparse(link).hostname or ''
        if not host or host.endswith(('gwy.com', 'gaodun.com', 'gaodunwangxiao.com')):
            continue
        if re.search(r'job|career|zhaopin|recruit|campus|zp\.|bm\.|exam|renshi|rsks', link, re.I):
            apply = link
            break
    from .gov_soe import _find_company, _CITY_HINTS, PROVINCE_CITIES
    company = _find_company(title) or ''
    if not company and channel == '银行':
        m = re.search(r'([\u4e00-\u9fff]{2,20}银行(?:[\u4e00-\u9fff]{2,10}分行)?)', title)
        company = m[1] if m else ''
    company = company or '公告内多家单位（详见原文）'
    city = next((c for c in _CITY_HINTS if c in title), '')
    if not city:
        city = next((c for cities in PROVINCE_CITIES.values() for c in cities if c in title), '')
    if not city:
        city = next((p for p in PROVINCE_CITIES if p in title), '')
    nature = '事业单位' if channel == '事业单位' else ('国企' if channel == '国企' else '未注明')
    if channel == '银行' and re.search(r'邮储银行|邮政储蓄银行|中国银行|工商银行|农业银行|建设银行|交通银行|农业发展银行|国家开发银行|进出口银行', title):
        nature = '国企'
    prefix = '【招聘公告，非单一职位】来源：' + url + '\n'
    prefix += '截止日期：' + (deadline or '未确认，请核对原公告') + '\n'
    prefix += '报名入口：' + (apply or '未提取，请从原公告核对') + '\n'
    # Keep application instructions even for long announcements.
    hint = re.search(r'报名方式|招聘流程|简历投递|报名时间', text)
    tail = text[max(0, hint.start()-30):hint.start()+650] if hint else ''
    row = dict(key='gwy_public|' + re.search(r'/(\d+)\.html', url)[1],
               title='【公告】' + title, company=company, city=city, nature=nature,
               source='上岸鸭公开招聘·' + channel, salary_text='未注明',
               url=url, apply_url=apply or url, deadline=deadline,
               desc=(prefix + text[:850] + '\n报名相关摘录：\n' + tail)[:2000],
               hr_email='', hr_phone='', record_type='announcement',
               verified_apply_url=bool(apply), observed_at=time.time())
    return row, ''


class GwyPublicCollector(BaseCollector):
    name = 'gwy_public'
    label = '上岸鸭公开招聘（公告）'

    def fetch(self, keyword='', city='', limit=30, natures=None):
        # Network is handled by an explicit bounded sync, never on every search.
        if not CACHE.exists():
            return []
        data = json.loads(CACHE.read_text(encoding='utf-8'))
        today = datetime.now().date().isoformat()
        out = []
        for row in data.get('jobs', []):
            if time.time() - row.get('observed_at', 0) > 30 * 86400:
                continue
            if row.get('deadline') and row['deadline'] < today:
                continue
            if city and city not in row.get('city', ''):
                continue
            if natures and row.get('nature') not in natures:
                continue
            if keyword and keyword not in row['title'] + row['company'] + row['desc']:
                continue
            out.append(dict(row))
        return out[:limit]
