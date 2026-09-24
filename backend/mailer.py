# -*- coding: utf-8 -*-
"""邮件投递模块，两条通道：

1) mailto —— 生成 mailto: 链接，调起求职者手机上的邮件 App，用**他自己的邮箱**发。
   零密码、零配置，HR 看到的就是本人邮箱，回复直接进他自己的邮箱。
   限制：mailto 只支持纯文本，不能带附件，需要一封一封点发送。

2) smtp —— 用我们配置的发件账号代发，**Reply-To 指向求职者邮箱**。
   一键批量，支持 HTML 正文 + 可打印简历附件。
   需要在 webapp/data/mail.json 里配好 SMTP；没配就发不出去。
"""
import hashlib
import json
import mimetypes
import os
import re
import smtplib
import time
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import quote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(ROOT_DIR, 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'mail.json')
OUTBOX_DIR = os.path.join(DATA_DIR, 'outbox')
RESUME_DIR = os.path.join(DATA_DIR, 'resumes')


# ---------------------------------------------------------------- 配置
# 常见邮箱服务商的 SMTP 预设：用户只填「邮箱地址+授权码」，host/port 自动配
_SMTP_PRESETS = {
    'qq.com':      ('smtp.qq.com', 465, True, False),
    'foxmail.com': ('smtp.qq.com', 465, True, False),
    '163.com':     ('smtp.163.com', 465, True, False),
    '126.com':     ('smtp.126.com', 465, True, False),
    'yeah.net':    ('smtp.yeah.net', 465, True, False),
    'sina.com':    ('smtp.sina.com', 465, True, False),
    'sina.cn':     ('smtp.sina.cn', 465, True, False),
    'sohu.com':    ('smtp.sohu.com', 465, True, False),
    # 运营商邮箱：找工作的人用手机号邮箱的很多（13800138000@139.com）
    '139.com':     ('smtp.139.com', 465, True, False),
    '189.cn':      ('smtp.189.cn', 465, True, False),
    'gmail.com':   ('smtp.gmail.com', 465, True, False),
    # 微软系：个人邮箱的服务器名跟企业版不一样（企业版是 smtp.office365.com）。
    # 微软自己的「Outlook.com 的 POP、IMAP 和 SMTP 设置」页面上写的就是
    # smtp-mail.outlook.com:587 + STARTTLS —— 对着用户截图抄下来的，别改回去。
    'outlook.com': ('smtp-mail.outlook.com', 587, False, True),
    'hotmail.com': ('smtp-mail.outlook.com', 587, False, True),
    'live.com':    ('smtp-mail.outlook.com', 587, False, True),
    'msn.com':     ('smtp-mail.outlook.com', 587, False, True),
    # Brevo（原 Sendinblue）中继：登录名是账户给的 xxxxx@smtp-brevo.com，
    # 端口 587 + STARTTLS。登录名和密码跟「发件人显示什么」是两回事 ——
    # 中继这类服务只认验证过的发件地址，发件人不对会在 DATA 阶段被拒。
    'smtp-brevo.com': ('smtp-relay.brevo.com', 587, False, True),
}

# 微软已经不再为个人邮箱提供「密码 / 应用密码」这条路：官方设置页把
# IMAP/POP/SMTP 的身份验证方法写成了 OAuth2/Modern Auth，应用密码本身也是
# 建在基本验证上的，跟着一起失效。用户照着老教程去找授权码是找不到的
# （2026-09-21 用户实测：页面里根本没有授权码这一项）。
# 这些域名不拦着用户填，但要如实告诉他「这条路微软关了」，别让他反复试。
OAUTH_ONLY_DOMAINS = ('outlook.com', 'hotmail.com', 'live.com', 'msn.com')


def oauth_only(email):
    """这个邮箱是不是「只认 OAuth2、不吃密码」的微软系个人邮箱。"""
    dom = (email or '').strip().lower().split('@')[-1]
    return dom in OAUTH_ONLY_DOMAINS


def _apply_preset(cfg):
    """按发件邮箱域名补全 host/port/ssl/starttls；没有预设就不动（自定义 SMTP）。"""
    email = (cfg.get('email') or cfg.get('username') or '').strip().lower()
    dom = email.split('@')[-1] if '@' in email else ''
    if dom in _SMTP_PRESETS:
        host, port, ssl, starttls = _SMTP_PRESETS[dom]
        cfg.setdefault('host', host)
        cfg.setdefault('port', port)
        cfg.setdefault('ssl', ssl)
        cfg.setdefault('starttls', starttls)
    if not cfg.get('username') and cfg.get('email'):
        cfg['username'] = cfg['email']
    return cfg


# ---------------------------------------------------------------- 服务商说明
# 「第 4 步 → 设置发件邮箱」里那份说明的**唯一出处**。
# 界面直接渲染这张表（前端不再手抄一份 —— 抄一份就会跟 _SMTP_PRESETS 走散，
# 到时候说明里写着能支持、实际发不出去，用户按说明做完了还是失败）。
#
#   auth    要填进「密码」框的到底是什么。这是整个流程最容易卡住的地方：
#           QQ/163/新浪/搜狐 要授权码，139 要自己设的「客户端密码」，
#           189 直接填登录密码 —— 一律填登录密码是错的，且错得没有提示。
#   where   在邮箱网页版的哪一页开服务（照着找得到）
#   steps   一步步照着点，别写「开启 SMTP 服务」这种等于没说的话
#   compose 有的话：写信页支持 URL 预填（目前只有 Gmail / Outlook），
#           点一下标题正文收件人都填好了，不用复制粘贴
PROVIDERS = [
    {
        'id': 'qq', 'name': 'QQ邮箱', 'domain': 'qq.com',
        'webmail': 'https://mail.qq.com/',
        'auth': '授权码',
        'where': '设置 → 账号 → POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务',
        'steps': [
            '电脑上打开 mail.qq.com，登录你的 QQ 邮箱',
            '点页面最上面的「设置」，再点左边「账号」',
            '往下找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务」',
            '把「IMAP/SMTP服务」那一行点「开启」，按提示用手机发一条短信验证',
            '验证通过后屏幕上会给一串 16 位授权码，复制下来（只显示这一次）',
        ],
        'note': '授权码不是 QQ 登录密码，两个不一样。填登录密码一定发不出去。',
    },
    {
        'id': '163', 'name': '网易163邮箱', 'domain': '163.com',
        'webmail': 'https://mail.163.com/',
        'auth': '授权码',
        'where': '设置 → POP3/SMTP/IMAP',
        'steps': [
            '电脑上打开 mail.163.com，登录邮箱',
            '点最上面的「设置」，再点「POP3/SMTP/IMAP」',
            '打开「IMAP/SMTP服务」和「POP3/SMTP服务」',
            '按提示用手机发短信验证',
            '验证通过后给一串授权码，复制下来（只显示这一次）',
        ],
        'note': '126 邮箱、yeah 邮箱是同一家，做法一模一样，填自己的邮箱地址即可。',
    },
    {
        'id': '126', 'name': '网易126邮箱', 'domain': '126.com',
        'webmail': 'https://mail.126.com/',
        'auth': '授权码',
        'where': '设置 → POP3/SMTP/IMAP',
        'steps': [
            '电脑上打开 mail.126.com，登录邮箱',
            '点最上面的「设置」，再点「POP3/SMTP/IMAP」',
            '打开「IMAP/SMTP服务」',
            '按提示用手机发短信验证',
            '验证通过后给一串授权码，复制下来（只显示这一次）',
        ],
        'note': '授权码不是登录密码。',
    },
    {
        'id': 'sina', 'name': '新浪邮箱', 'domain': 'sina.com',
        'webmail': 'https://mail.sina.com.cn/',
        'auth': '授权码',
        'where': '设置区 → 客户端POP/IMAP/SMTP',
        'steps': [
            '电脑上打开 mail.sina.com.cn，登录邮箱',
            '进「设置」，找到「客户端POP/IMAP/SMTP」',
            '打开「POP3/SMTP服务」或「IMAP4/SMTP服务」',
            '按提示生成授权码（16 位字符），复制下来',
        ],
        'note': '新浪的授权码也是 16 位，和登录密码不是一回事。',
    },
    {
        'id': 'sohu', 'name': '搜狐邮箱', 'domain': 'sohu.com',
        'webmail': 'https://mail.sohu.com/',
        'auth': '独立密码',
        'where': '选项 → 设置 → POP3/SMTP/IMAP',
        'steps': [
            '电脑上打开 mail.sohu.com，登录后点上面的「选项」→「设置」',
            '点「POP3/SMTP/IMAP」这一页',
            '勾上「IMAP/SMTP服务」',
            '按提示用手机收验证码，生成「独立密码」，复制下来',
        ],
        'note': '搜狐管它叫「独立密码」，只显示一次，丢了要重新生成。',
    },
    {
        'id': '139', 'name': '139邮箱', 'domain': '139.com',
        'webmail': 'https://mail.10086.cn/',
        'auth': '客户端密码',
        'where': '设置 → 账户与安全 → 邮箱协议设置 / 客户端密码',
        'steps': [
            '电脑上打开 mail.10086.cn（或 mail.139.com），登录',
            '点右上角齿轮图标「设置」→ 左边「账户与安全」',
            '进「邮箱协议设置」，把「IMAP/SMTP服务」打开',
            '再进「客户端密码」，自己设一个新密码（可能要短信验证）',
            '这个密码自己设、自己记住，填到这里就行',
        ],
        'note': '139 邮箱是中国移动的，手机号就是邮箱地址（如 13800138000@139.com）。'
                '这里的密码是你自己设的客户端密码，不是登录密码。',
    },
    {
        'id': '189', 'name': '189邮箱', 'domain': '189.cn',
        'webmail': 'https://mail.189.cn/',
        'auth': '登录密码',
        'where': '设置 → IMAP/POP3/SMTP服务',
        'steps': [
            '电脑上打开 mail.189.cn，登录邮箱',
            '点右上角「设置」',
            '找到「IMAP/POP3/SMTP服务」这一页',
            '勾上「IMAP/SMTP服务」并保存',
            '这里不需要授权码，「密码」框直接填你的登录密码',
        ],
        'note': '189 邮箱是中国电信的，不支持授权码，填登录密码就行'
                ' —— 服务没开的话会一直提示密码错。',
    },
    {
        'id': 'outlook', 'name': 'Outlook / Hotmail', 'domain': 'outlook.com',
        'webmail': 'https://outlook.live.com/mail/',
        'compose': ('https://outlook.live.com/mail/0/deeplink/compose'
                    '?to={to}&subject={subject}&body={body}'),
        # smtp_ok=False：这条路微软已经关了，界面上要标出来，不能还写着「填登录密码就行」
        'smtp_ok': False,
        'auth': '没有可填的东西（微软只认 OAuth2）',
        'where': '账户设置页只看一个地方：「身份验证方法」那一行',
        'steps': [
            '先看你自己的设置页：Outlook 网页版「设置 → 邮件 → 同步电子邮件」，'
            '或者微软的「POP、IMAP 和 SMTP 设置」页',
            '「身份验证方法」如果写的是 OAuth2/Modern Auth —— 就是你的情况，'
            '说明微软已经不接受用密码（也没有授权码）从别的程序发信了',
            '这条路别再找了：不是你不会开，是微软取消了。'
            '用搜索也只会搜到 2024 年前的旧教程，照着做一定失败',
            '想用这个邮箱投简历：关掉这个窗口，点「用我自己的邮箱一封封发」→'
            '选 Outlook —— 我帮你把网页写信页打开，收件人、主题、正文都填好，点发送就行',
            '想让系统替你发（一键代投）：换一个 QQ、163、126、139、189 或新浪邮箱，'
            '按上面那几家的说明拿到授权码，再回来设置一次',
        ],
        'note': '微软从 2024 年起逐步停用 SMTP 的「基本验证」，'
                '应用密码也是建在它上面的，会一起失效 —— 所以别再找应用密码了。'
                'Outlook 的网页写信页是我们支持得最好的一个（能自动填好），用它发送完全没问题。',
    },
    {
        'id': 'gmail', 'name': 'Gmail', 'domain': 'gmail.com',
        'webmail': 'https://mail.google.com/',
        'compose': ('https://mail.google.com/mail/?view=cm&fs=1'
                    '&to={to}&su={subject}&body={body}'),
        'auth': '应用专用密码',
        'where': 'Google 账户 → 安全性 → 两步验证 → 应用专用密码',
        'steps': [
            '先给 Google 账号开启「两步验证」（不开就没有应用专用密码）',
            '进 myaccount.google.com →「安全性」→「应用专用密码」',
            '随便起个名字新建一个，复制那 16 位密码填进来',
            '这个邮箱能直接点开已经填好的写信页，不用复制粘贴',
        ],
        'note': '国内网络多数连不上 smtp.gmail.com，建议换 QQ/163/139 邮箱。',
    },
]


def list_providers():
    """把 PROVIDERS 补全成前端能直接渲染的样子。

    host/port 从 `_SMTP_PRESETS` 现取（不在表里再写一遍），所以
    「说明里说支持的邮箱」和「配置真能解析出服务器的邮箱」永远一致。

    smtp_ok=False 表示这家**不能**用密码走系统代发（目前只有微软系），
    界面据此把「填：授权码」换成红字提醒，别让用户白折腾。
    """
    out = []
    for p in PROVIDERS:
        item = dict(p)
        preset = _SMTP_PRESETS.get(p.get('domain') or '')
        item['smtp'] = ('%s:%d' % (preset[0], preset[1])) if preset else ''
        item['smtp_ok'] = bool(p.get('smtp_ok', True))
        out.append(item)
    return out


# 这几家的「密码框」要填的是 16 位全小写字母的授权码。写死在这里是为了
# 「填了登录密码」这件事能当场认出来 —— 2026-09-21 用户实测就是这么栽的：
# 他填了 13 位、含大写和数字的 QQ 登录密码，QQ 直接掐断连接、连错误码都不给，
# 界面上只看到「发送失败」，完全猜不到是密码类型不对。
_CODE16_DOMAINS = ('qq.com', 'foxmail.com', '163.com', '126.com',
                   'yeah.net', 'sina.com', 'sina.cn')


def pwd_shape_warning(email, password):
    """授权码的形态对不对。只对「该填 16 位小写字母」的那几家做判断 ——
    搜狐/139 是用户自设的独立密码、189 直接填登录密码，长度没法预判，
    对它们做长度校验只会冤枉人。

    2026-09-21 用户被这段话绕住了：他明明收到过靶场的测试邮件，屏幕上却
    还说「密码不像授权码」，于是怀疑「发件邮箱配错了」。真相是他点开设置面板
    点了一次「保存」，而密码框被浏览器自动填充了旧登录密码，把授权码盖掉了 ——
    提示没误报，是**存着的那串**真的不对。所以这段话改成先讲清
    「我看到的已保存的值是什么样」，再给一个动作，不再泛泛地讲原理。
    """
    dom = (email or '').strip().lower().split('@')[-1]
    if dom not in _CODE16_DOMAINS:
        return ''
    pw = (password or '').strip()
    if not pw or (len(pw) == 16 and pw.isalpha() and pw.islower()):
        return ''
    looks_like_login = (not pw.islower()) or (not pw.isalpha()) or len(pw) < 16
    # 纯文本：前端这几处是 {{ }} 插值渲染的，写 <b> 会原样显示出来。
    return ('已保存的那串密码不像授权码：它有 %d 位%s；%s要的是 16 位全小写字母的授权码'
            '（形如 abcdwxyzefghijkl）。%s'
            '点下面的「去改授权码」：把密码框里原来的内容全选删掉，'
            '粘贴新的授权码（复制粘贴，别手打），再点「保存」。'
            % (len(pw),
               '、还带了大写字母或数字' if looks_like_login else '',
               _provider_name(dom),
               '这多半是登录密码，也可能只是浏览器把上次输的密码自动填进了密码框 ——'
               '你一点「保存」，正确的授权码就被它盖掉了。'
               if looks_like_login else ''))


def _provider_name(dom):
    """qq.com → 「QQ 邮箱」。让提示里的人话能指名道姓，而不是甩个域名。"""
    for p in PROVIDERS:
        if p.get('domain') == dom:
            return p.get('name') or dom
    return dom or '这家'


def pwd_fingerprint(password):
    """密码指纹：只用来判断「当前存着的这串，是不是那次实测通过的那串」。

    不是安全用途，所以取 sha1 前 10 位就够 —— 目的是让「测一测成功过」
    这件事压得住形态猜测：实测过就别再拿位数吓唬用户。"""
    return hashlib.sha1((password or '').strip().encode('utf-8')).hexdigest()[:10]


def mark_verified(password):
    """记下「这个密码刚真连登录成功过」，写进配置。

    用户点一次「测一测」拿到「登录成功」，转头进第 4 步却还看到
    「密码不像授权码」，他只会觉得这软件自相矛盾。实测优先于形态猜测。
    """
    cfg = load_config()
    if not cfg:
        return
    cfg['verified_pwd'] = pwd_fingerprint(password)
    _write_config(cfg)


def is_verified(password):
    """现在存着的这串密码，是不是实测登录成功过的那串。"""
    cfg = load_config() or {}
    fp = cfg.get('verified_pwd') or ''
    return bool(fp) and fp == pwd_fingerprint(password)


def pwd_warning(email, password=None):
    """填密码这条路在这个邮箱上还走不走得通。返回 '' 表示没问题。

    单独一个函数给保存接口带话：用户存完 Outlook 配置不能只回一句「已保存」，
    那等于告诉他「设好了，去发吧」—— 而他一定会失败。
    """
    if oauth_only(email):
        return ('Outlook/Hotmail 这类微软个人邮箱现在不能用密码代发（只认 OAuth2），'
                '存下去之后多半发不出去。建议改用「用我自己的邮箱一封封发」走网页发送，'
                '或者换成 QQ / 163 / 126 / 139 / 189 邮箱来代投。')
    if password is not None:
        # 实测压过猜测：这串密码真连登录成功过（用户按过「测一测」），
        # 就别再拿位数说它不像授权码 —— 那只会让用户觉得软件自相矛盾。
        if is_verified(password):
            return ''
        return pwd_shape_warning(email, password)
    return ''


def auth_hint(email):
    """这个邮箱的「密码框该填什么」。拿来拼错误提示 —— 登录失败时最常见的原因
    就是填错东西（把登录密码当授权码填），提示里点名要比「认证失败」有用得多。"""
    if oauth_only(email):
        return '没有可填的（微软只认 OAuth2，不接受密码）'
    dom = (email or '').strip().lower().split('@')[-1]
    for p in PROVIDERS:
        if p.get('domain') == dom:
            return p.get('auth') or ''
    return ''


def load_config():
    """读 data/mail.json；不存在返回 None（此时只能走 mailto 通道）。"""
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return _apply_preset(json.load(f))
    except Exception:
        return None


def _write_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def save_config(email, password, dry_run=None, from_name=''):
    """保存发件账号（网页配置入口用）。服务商参数按邮箱域名自动预设。
    返回 (是否成功, 说明, 解析出的 host)。

    password 传空 = 「不改授权码」，沿用已存的那串。
    2026-09-21 的坑：用户只想改授权码，点开面板点了一下「保存」，密码框被
    浏览器自动填充了旧登录密码，于是把他填好的授权码盖掉了。所以空密码
    不覆盖：只在真的填了新授权码时才写回去。

    dry_run：**只给测试用的内部开关**，默认 None = 真发。产品里（网页配置、
    一键代投）都不再传它 —— 演示用的「演练模式」已经从界面上拿掉了，
    点一键代投就是真的发出去。测试跑不过真发，所以留这个口子让用例能免联网。
    """
    email = (email or '').strip()
    if '@' not in email or '.' not in email.split('@')[-1]:
        return False, '邮箱格式不对', ''
    old = load_config() or {}
    pwd = (password or '').strip() or (old.get('password') or '')
    cfg = _apply_preset({
        'email': email,
        'username': email,
        'password': pwd,
        # 不填就用求职者自己的名字当发件名（send_smtp 里兜底）。
        # 写死「找工作助手」的话，HR 看到的是个机器人名，打开率明显偏低。
        'from_name': from_name or '',
        # 默认真发；只有测试显式传 True 才落盘不真发。
        'dry_run': bool(dry_run),
        # 授权码没换就留着「实测通过」的记录；换了就清掉 —— 新那串还没测过。
        'verified_pwd': (old.get('verified_pwd') or '')
                        if pwd and old.get('verified_pwd') == pwd_fingerprint(pwd) else '',
    })
    # 走外部中继（Brevo 这类）时，发件域名是我们自己配的域名、登录名是中继给的，
    # 两样都不在预设表里。所以：新邮箱域名认不出来、而旧的服务器配置还在，
    # 就沿用旧服务器 —— 否则用户在面板里换个发件域名点「保存」，配置直接被清空。
    # （换成 QQ/163 这种认识得了的域名时，照常走它们自己的预设。）
    if old.get('host') and email.lower().split('@')[-1] not in _SMTP_PRESETS:
        for _k in ('host', 'port', 'ssl', 'starttls', 'username'):
            if old.get(_k):
                cfg[_k] = old[_k]

    if not cfg.get('host'):
        # 认得出来的域名才配得出服务器地址。写清「支持哪些」和「换哪条路」——
        # 只说「不支持」用户会以为是自己填错了，然后反复试同一个邮箱。
        return False, ('这个邮箱（%s）我还不认识，配不出收发服务器。'
                       '换成 QQ、163、126、新浪、搜狐、139、189 或 Gmail，'
                       '或者走下面「用我自己的邮箱一封封发」那条路。（Outlook/Hotmail '
                       '是微软系，已经不能用密码代发，不算在能配的里面。）' % email), ''
    try:
        _write_config(cfg)
        state = '（演练模式，不真发）' if cfg.get('dry_run') else '（真实发送已开）'
        return True, '已保存' + state, cfg['host']
    except Exception as e:                              # noqa: BLE001
        return False, '写配置失败：%s' % e, ''


def _valid_email(s):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', (s or '').strip()))


# ---------------------------------------------------------------- 登录自检
# 「授权码到底对不对、这个邮箱到底能不能用密码发」——用户是没法自己判断的，
# 只能发一封碰运气。所以给一个按钮：真连一次服务器、真登录一次，**不发信**。
# 结果分三类回话：登录成功 / 服务器拒了（多半是填错东西）/ 连不上。
def verify_login(email, password, host=None, port=None, ssl=None, starttls=None, username=None):
    """代发配置自检。返回 (是否登录成功, 说明文案)。

    只做 EHLO → (STARTTLS) → LOGIN，不发送任何邮件、不产生任何记录，
    所以拿它当「测一测」是安全的。
    username：SMTP 登录名。中继类服务（Brevo 等）登录名≠发件地址，
    不传才回退成 email —— QQ/163 这类两者本来就是同一个。
    """
    email = (email or '').strip()
    if not _valid_email(email):
        return False, '邮箱地址填得不对，先补一个完整的邮箱'
    cfg = {'email': email, 'username': (username or email).strip(), 'password': (password or '').strip()}
    if host:
        cfg.update({'host': host, 'port': port, 'ssl': ssl, 'starttls': starttls})
    else:
        _apply_preset(cfg)
    if not cfg.get('host'):
        return False, ('这个邮箱（%s）我还不认识，认不出收发服务器。'
                       '换成 QQ、163、126、新浪、搜狐、139、189、Outlook 或 Gmail。'
                       % email)
    if not cfg.get('password'):
        return False, '密码框还是空的，先填上 %s' % (auth_hint(email) or '密码')

    if oauth_only(email):
        # 不劝、也不假装试过：微软这条路关了，先说实话，再给能走的路。
        return False, ('%s：微软已不接受用密码发信（官方设置页里写的就是 '
                       'OAuth2/Modern Auth，所以你也找不到授权码）。'
                       '请改用「用我自己的邮箱一封封发」走网页发送（能自动填好收件人和正文），'
                       '或者换成 QQ / 163 / 126 / 139 / 189 邮箱来一键代投。' % email)

    try:
        server = _connect(cfg)
    except Exception as e:                              # noqa: BLE001
        return False, _connect_fail_msg(email, cfg, e)
    try:
        server.login(cfg['username'], cfg['password'])
    except Exception as e:                              # noqa: BLE001
        return False, _smtp_fail_msg(email, e)
    finally:
        try:
            server.quit()
        except Exception:                               # noqa: BLE001
            pass
    return True, '登录成功 —— 这个邮箱可以替我代发，下面点「保存」就行'


def _connect(cfg):
    """按配置连服务器（ssl 直连 / starttls 升级）。连接和登录分开，
    是为了能分清「连不上」和「登录被拒」——这两种情况该给的建议完全不同。"""
    port = int(cfg.get('port') or 465)
    if cfg.get('ssl', True) and port != 25:
        return smtplib.SMTP_SSL(cfg['host'], port, timeout=20)
    server = smtplib.SMTP(cfg['host'], port, timeout=20)
    if cfg.get('starttls'):
        server.starttls()
    return server


def _connect_fail_msg(email, cfg, err):
    return ('连不上 %s:%s（%s）。检查网络，或者先换成 QQ/163 试试。'
            % (cfg.get('host'), cfg.get('port'), err))


def _smtp_fail_msg(email, err):
    """把登录/发信阶段的异常翻成人话。

    2026-09-21 拿真服务器实测，各家连「拒绝」的方式都不一样，
    一律回「认证失败」等于没说：
      · 163  → 550 User has no permission（多半是 SMTP 服务没开）
      · 126  → 550（**GBK 编码的中文**，按 utf-8 解会变成乱码）
      · 139  → 454 Authentication failed(...)
      · 新浪 → 535 5.7.8 authentication failed
      · QQ   → 什么都不回，**直接断连接**（SMTPServerDisconnected）
        —— 用户看到的就是一句「Connection unexpectedly closed」，无从下手
    """
    hint = auth_hint(email)
    if isinstance(err, smtplib.SMTPServerDisconnected):
        return ('服务器在登录时把连接直接断了。QQ/163 在授权码不对时就是这么回的'
                '（不给错误码）。两件事确认一下：① 密码框里填的是「%s」，不是登录密码；'
                '② 回邮箱网页版重新生成一次授权码，复制粘贴过来（别手打，容易多空格）。'
                % (hint or '授权码'))
    if isinstance(err, smtplib.SMTPAuthenticationError):
        return ('服务器拒绝了这次登录。两件事确认一下：'
                '①「密码」框里填的是「%s」，不是登录密码；'
                '②那家的「SMTP / IMAP 服务」已经开启'
                '（163/126 的报错就是「User has no permission」，多半是服务没开）。'
                '回邮箱网页版把服务打开、重新生成一次再复制过来（别手打，容易多空格）。%s'
                % (hint or '授权码', _err_tail(err)))
    if isinstance(err, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected,
                        OSError, TimeoutError)):
        return '跟服务器说不上话（%s）。看看网络，或者先换成 QQ/163 邮箱试试。' % err
    return '登录时出错：%s' % err


# 认定「这批邮件其实卡在登录上」的标记。都是上面几个函数自己发的文案，
# 不是去猜服务器的英文报错 —— 猜错会把「网络抖动」误判成「密码错」。
_AUTH_FAIL_MARKERS = (
    '服务器在登录时把连接直接断了',      # QQ 掐连接
    '服务器拒绝了这次登录',              # 535 / 454 一族
    '微软已不接受用密码发信',            # 微软系，根本不是密码问题
)


def is_auth_failure(msg):
    """这条失败是不是「登录」这一关没过。

    批处理里要拿它做早停：整批用同一个发件账号，登录过不去就是整批都过不去，
    再一封封重试只是把同一个失败重复 N 遍（而且每封还要等 interval 秒）。
    """
    m = msg or ''
    return any(k in m for k in _AUTH_FAIL_MARKERS)


def _err_tail(err):
    """带上服务器原话（用户报修时说得清）。字符串要按 gbk 兜底再解一次 ——
    126 就是回 GBK 中文，只按 utf-8 解会得到一片 `�û���Ȩ` 乱码。"""
    code = getattr(err, 'smtp_code', '') or ''
    detail = getattr(err, 'smtp_error', b'')
    if isinstance(detail, bytes):
        for enc in ('utf-8', 'gbk'):
            try:
                detail = detail.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            detail = repr(detail)
    detail = (detail or '').strip()
    return ('（服务器原话：%s %s）' % (code, detail)) if (code or detail) else ''


def is_ready():
    """代发通道是否可用（配置了 SMTP 账号密码）。"""
    cfg = load_config()
    if not cfg:
        return False
    return bool(cfg.get('host') and cfg.get('username') and cfg.get('password'))


# ---------------------------------------------------------------- 内容生成
def _skills(profile):
    return '、'.join(profile.get('skills') or []) or '（未填写）'


def build_subject(profile, job):
    """主题带岗位和姓名，避免被当成群发垃圾邮件。"""
    return '应聘 %s - %s - %s - %s' % (
        job.get('title', '岗位'),
        profile.get('name') or '求职者',
        (profile.get('exp') or '').replace('年', '年经验'),
        profile.get('phone') or '',
    )


def build_text_body(profile, job):
    """纯文本正文，mailto 通道用（必须精简，移动端 mailto 有长度限制）。"""
    return '\n'.join([
        '您好，',
        '',
        '我叫%s，看到贵单位在招「%s」，很感兴趣，想投一份简历。' % (
            profile.get('name') or '（姓名）', job.get('title', '这个岗位')),
        '',
        '【基本信息】',
        '年龄：%s　学历：%s　经验：%s' % (
            profile.get('age') or '未填', profile.get('edu') or '未填', profile.get('exp') or '未填'),
        '期望城市：%s　期望薪资：%s' % (
            profile.get('city') or '不限', profile.get('salary') or '面议'),
        '',
        '【技能】',
        _skills(profile),
        '',
        '【自我介绍】',
        profile.get('intro') or '（无）',
        '',
        '【联系方式】',
        '手机：%s' % (profile.get('phone') or '未填'),
        '邮箱：%s' % (profile.get('email') or '未填'),
        '',
        '期待您的回复，谢谢！',
    ])


def build_html_body(profile, job, attach_note='附件是可打印的完整简历。'):
    """HTML 正文，代发通道用。"""
    p = dict(profile)
    skills_html = ''.join(
        '<span style="display:inline-block;background:#eef3fa;border-radius:12px;'
        'padding:2px 10px;margin:2px 4px 2px 0;font-size:14px;">%s</span>' % s
        for s in (p.get('skills') or [])
    ) or '（未填写）'
    return """<div style="font-family:-apple-system,'Microsoft YaHei',sans-serif;font-size:15px;line-height:1.8;color:#222;">
<p>您好，</p>
<p>我叫 <b>{name}</b>，看到贵单位在招 <b>{title}</b>，很感兴趣，简历如下：</p>
<h3 style="font-size:15px;margin:18px 0 6px;">基本信息</h3>
<table style="font-size:14px;color:#444;border-collapse:collapse;">
<tr><td style="padding:2px 16px 2px 0;">年龄</td><td>{age}</td></tr>
<tr><td style="padding:2px 16px 2px 0;">学历</td><td>{edu}</td></tr>
<tr><td style="padding:2px 16px 2px 0;">工作经验</td><td>{exp}</td></tr>
<tr><td style="padding:2px 16px 2px 0;">期望城市</td><td>{city}</td></tr>
<tr><td style="padding:2px 16px 2px 0;">期望薪资</td><td>{salary}</td></tr>
</table>
<h3 style="font-size:15px;margin:18px 0 6px;">技能</h3>
<p>{skills}</p>
<h3 style="font-size:15px;margin:18px 0 6px;">自我介绍</h3>
<p>{intro}</p>
<h3 style="font-size:15px;margin:18px 0 6px;">联系方式</h3>
<p>手机：{phone}<br/>邮箱：{email}</p>
<p style="margin-top:20px;">期待您的回复，谢谢！</p>
<hr style="border:none;border-top:1px solid #eee;margin:18px 0;"/>
<p style="font-size:12px;color:#999;">{attach_note}</p>
</div>""".format(
        name=p.get('name') or '（姓名）',
        title=job.get('title', '这个岗位'),
        age=p.get('age') or '未填',
        edu=p.get('edu') or '未填',
        exp=p.get('exp') or '未填',
        city=p.get('city') or '不限',
        salary=p.get('salary') or '面议',
        skills=skills_html,
        intro=p.get('intro') or '（无）',
        phone=p.get('phone') or '未填',
        email=p.get('email') or '未填',
        attach_note=attach_note,
    )


def build_resume_html(profile):
    """可打印的完整简历，作为附件。"""
    p = dict(profile)
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>
<title>{name} - 简历</title></head>
<body style="font-family:-apple-system,'Microsoft YaHei',sans-serif;max-width:720px;margin:24px auto;color:#222;line-height:1.8;">
<h1 style="font-size:24px;margin:0 0 4px;">{name}</h1>
<p style="color:#666;margin:0 0 16px;">求职意向：{jobs}　|　{phone}　|　{email}</p>
<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px;">基本信息</h2>
<p>年龄：{age}　学历：{edu}　经验：{exp}<br/>
期望城市：{city}　期望薪资：{salary}</p>
<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px;">技能</h2>
<p>{skills}</p>
<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px;">自我介绍</h2>
<p>{intro}</p>
</body></html>""".format(
        name=p.get('name') or '求职者',
        jobs='、'.join(p.get('jobTypes') or []) or '不限',
        phone=p.get('phone') or '',
        email=p.get('email') or '',
        age=p.get('age') or '未填',
        edu=p.get('edu') or '未填',
        exp=p.get('exp') or '未填',
        city=p.get('city') or '不限',
        salary=p.get('salary') or '面议',
        skills=_skills(p),
        intro=p.get('intro') or '（无）',
    )


# ---------------------------------------------------------------- 附件
def original_resume(profile):
    """求职者自己那份简历原件（第一步选了「保留简历原件」才会有）。

    返回 (绝对路径, 附件文件名)；没有就用 (None, '')，调用方退回生成的 HTML。

    为什么优先生效它：HR 要看到的就是求职者手里那份 Word/PDF 本身（照片、
    排版、证书扫描件都在里面）。我们拼出来的 HTML 只是「换了个样」的替代品，
    而且 .html 附件是钓鱼邮件的经典载体，央企国企的邮件网关经常直接拦掉
    ——简历就等于没送到。这和自动投递那边「上传原件而不是拼文本」是同一条原则。
    """
    raw = (profile.get('resume_path') or '').strip()
    if not raw:
        return None, ''
    try:
        safe = os.path.normpath(os.path.abspath(raw))
        # 只认 data/resumes 目录里的文件，防目录穿越
        if os.path.dirname(safe) != os.path.normpath(RESUME_DIR):
            return None, ''
        if not os.path.isfile(safe):
            return None, ''
        ext = os.path.splitext(safe)[1].lower()
        if not ext:
            return None, ''
        return safe, '%s-简历%s' % (profile.get('name') or '求职者', ext)
    except Exception:                                   # noqa: BLE001
        return None, ''


def _attach_resume(msg, profile, path, att_name):
    """给邮件挂上简历附件：path 有值就用原件，否则用生成的 HTML。

    调用方先用 original_resume() 取一次，好把正文底部那句说明一起对齐。
    """
    if path:
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        maintype, _, subtype = ctype.partition('/')
        with open(path, 'rb') as f:
            raw = f.read()
        att = MIMEBase(maintype or 'application', subtype or 'octet-stream')
        att.set_payload(raw)
    else:
        # 生成的 HTML 附件用 text/html：application/html 是个没注册过的类型，
        # 有些客户端会当成未知二进制，不给预览。
        att = MIMEBase('text', 'html', charset='utf-8')
        att.set_payload(build_resume_html(profile).encode('utf-8'))
        att_name = '%s-简历.html' % (profile.get('name') or '求职者')
    encoders.encode_base64(att)
    # filename 用元组，non-ASCII（中文姓名）会被 RFC2231 正确编码
    att.add_header('Content-Disposition', 'attachment',
                   filename=('utf-8', '', att_name))
    msg.attach(att)


# ---------------------------------------------------------------- 通道 1：mailto
def mailto_link(profile, job, cfg=None):
    """生成 mailto: 链接，点开直接调起手机上的邮件 App。"""
    to = (job.get('hr_email') or '').strip()
    subject = build_subject(profile, job)
    body = build_text_body(profile, job)
    return 'mailto:%s?subject=%s&body=%s' % (
        to, quote(subject), quote(body)
    )


# ---------------------------------------------------------------- 通道 2：代发
def _safe_file_name(s, limit=40):
    """把岗位名变成能当文件名的串。

    dry_run（测试用的内部开关）把邮件落盘到 data/outbox 时用的就是岗位名，
    而岗位名里带斜杠非常常见（「行政专员/助理」「销售/客服」）—— 直接拿去
    open() 会 FileNotFoundError，把整个代发任务打成 error（2026-09-21 实测，
    岗位名正好是「行政专员/助理」）。顺手把 Windows 不允许的字符一起换掉。
    """
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(s or 'job')).strip(' .')
    return s[:limit] or 'job'


def send_smtp(profile, job, cfg=None, tailored_path=None, tailored_name=None):
    """代发一封。返回 (是否成功, 说明)。

    Reply-To 设成求职者自己的邮箱，HR 点回复直接回到他本人邮箱。
    cfg 里 dry_run=true 时不真发，邮件落盘到 data/outbox 供检查。

    tailored_path：若有「按岗位定制」生成的 docx 且路径合法（在 data/tailored 内），
    就改用它当附件、并改一句说明。路径不是我们生成的目录直接忽略，退回原件——
    这是最后一道防线，绝不让任意路径被当附件发出去。
    """
    cfg = cfg or load_config()
    if not cfg:
        return False, '还没配置发件邮箱（data/mail.json）'

    hr_to = (job.get('hr_email') or '').strip()
    to = hr_to
    if not to:
        return False, '这个岗位没有公开邮箱，需要去官网投递'

    subject = build_subject(profile, job)
    text = build_text_body(profile, job)
    # 先定附件（原件 / 定制版 / 生成的 HTML），正文底部那句说明要跟着对齐
    _rpath, _rname = original_resume(profile)
    if tailored_path and os.path.isfile(tailored_path):
        try:
            from tailor import OUT_DIR as _TO
            _ok_dir = os.path.dirname(os.path.abspath(tailored_path)) == os.path.normpath(_TO)
        except Exception:                                    # noqa: BLE001
            _ok_dir = False
        if _ok_dir:
            _rpath, _rname = tailored_path, (tailored_name or os.path.basename(tailored_path))
            _note = '附件是按「%s」岗位定制的简历（已据招聘要求改写）。' % (job.get('title') or '')
        else:
            _note = ('附件是你的简历原件（%s）。' % _rname) if _rpath else '附件是可打印的完整简历。'
    else:
        _note = ('附件是你的简历原件（%s）。' % _rname) if _rpath else '附件是可打印的完整简历。'
    html = build_html_body(profile, job, attach_note=_note)

    msg = MIMEMultipart('mixed')
    msg['Subject'] = Header(subject, 'utf-8')
    # From 必须是「发件地址」(cfg.email)，不能是登录名 (cfg.username)：
    # 走 Brevo 这类中继时两者不是一个东西（登录名是 xxxx@smtp-brevo.com），
    # 拿登录名当 From，中继会因为「发件人未验证」直接不投递 —— SMTP 层照样
    # 回 250 queued，邮件却永远不会到（2026-09-24 实测四封全消失，就是这里）。
    # 信封发件人（sendmail 的第一个参数）才用登录名，那是跟服务器对账用的。
    msg['From'] = formataddr(
        (str(Header(cfg.get('from_name') or (profile.get('name') or '求职者'), 'utf-8')),
         _email_of(cfg) or cfg.get('username') or 'noreply@example.com')
    )
    msg['To'] = to
    reply_to = (profile.get('email') or '').strip()
    if reply_to:
        msg['Reply-To'] = reply_to  # 关键：回复直接进求职者自己的邮箱

    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    _attach_resume(msg, profile, _rpath, _rname)

    if cfg.get('dry_run'):
        os.makedirs(OUTBOX_DIR, exist_ok=True)
        name = '%s_%s.html' % (time.strftime('%Y%m%d-%H%M%S'), _safe_file_name(job.get('title')))
        with open(os.path.join(OUTBOX_DIR, name), 'w', encoding='utf-8') as f:
            f.write('<!-- TO: %s -->\n<!-- SUBJECT: %s -->\n<!-- REPLY-TO: %s -->\n%s'
                    % (to, subject, reply_to, html))
        return True, '演练模式（未真发），已存到 data/outbox'

    # 连接 / 登录 / 发信分三段各自兜异常：整批卡在登录上时（授权码错、
    # SMTP 服务没开），用户看到的第一条必须能照着做，而不是一串 535 或者
    # QQ 那种「Connection unexpectedly closed」。
    try:
        server = _connect(cfg)
    except Exception as e:                              # noqa: BLE001
        return False, _connect_fail_msg(_email_of(cfg), cfg, e)
    try:
        server.login(cfg['username'], cfg['password'])
    except Exception as e:                              # noqa: BLE001
        try:
            server.quit()
        except Exception:                               # noqa: BLE001
            pass
        return False, _smtp_fail_msg(cfg.get('username') or _email_of(cfg), e)
    try:
        server.sendmail(cfg['username'], [to], msg.as_string())
    except Exception as e:                              # noqa: BLE001
        return False, '发送失败：%s' % e
    finally:
        try:
            server.quit()
        except Exception:                               # noqa: BLE001
            pass
    return True, '已发送'


def _email_of(cfg):
    return (cfg or {}).get('email') or (cfg or {}).get('username') or ''
