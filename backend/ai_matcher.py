# -*- coding: utf-8 -*-
"""AI 匹配引擎（本地打分，不依赖任何外部 API，断网也能用）。

打分维度与权重：
    技能/标签命中   35%
    求职意向命中    22%
    单位性质        15%   ← 央企/国企/外企
    城市匹配        13%
    薪资匹配         8%
    经验/学历匹配    7%

另外：用户只要「直签」而岗位是「劳务派遣」时，额外扣 8 分并给出明确提醒。
输出：0-100 的匹配度 + 人话理由 + 改进建议。
想接真的大模型时，只要把 `llm_explain` 换成一次 HTTP 调用即可，
其余流程不用动。
"""
import re

TOKEN_RE = re.compile(r'[一-龥A-Za-z0-9]+')

# 常见岗位类型 -> 同义词，用来做意向命中
INTENT_SYNONYMS = {
    '电力运维': ['电力', '运维', '配电', '线路', '变电', '供电', '抄表', '检修', '抢修'],
    '快递物流': ['快递', '分拣', '配送', '骑手', '物流', '仓储', '仓库', '搬运', '装卸', '邮件'],
    '工厂普工': ['普工', '操作工', '流水线', '工厂', '车间', '质检', '包装', '装配', '产线'],
    '司机驾驶': ['司机', '驾驶', '货运', '班车', '公交', '配送'],
    '保安保洁': ['保安', '保洁', '门卫', '秩序', '清运', '环卫', '安检', '值守'],
    '销售客服': ['销售', '客服', '导购', '营业员', '电话客服', '顾问', '收银', '票务'],
    '办公文职': ['文员', '行政', '前台', '助理', '资料', '录入', '站务', '大堂', '值班'],
    '技术工人': ['电工', '焊工', '维修', '机修', '木工', '钳工', '数控', '安装', '车工', '装配'],
    '医护育儿': ['护士', '护理', '育儿', '月嫂', '护工', '养老', '保育'],
    '餐饮服务': ['服务员', '厨师', '后厨', '餐饮', '配菜', '洗碗', '餐厅', '奶茶', '店员', '餐车'],
    '家政保洁': ['保洁', '家政', '清洁', '环卫', '保姆', '月嫂', '育儿', '护工', '收纳', '洗碗', '清洁工'],
    '餐饮后厨': ['厨师', '后厨', '配菜', '餐饮', '面点', '奶茶', '店员', '餐厅', '厨房', '帮厨'],
    '建筑装修': ['建筑', '装修', '木工', '瓦工', '油漆', '水电', '施工', '工地', '贴砖', '吊顶', '泥工'],
    '其他': [],
}

EXP_MAP = {
    '没经验': 0,
    '1年以内': 0.5,
    '1-3年': 2,
    '3-5年': 4,
    '5年以上': 6,
}

# 细分职位只扩展相关叫法，不用「老师」「托管」等泛词，避免金融托管误命中。
ROLE_ALIASES = [
    ['托管老师', '托管教师', '托辅老师', '午托老师', '晚托老师', '课后托管', '作业辅导老师'],
    ['幼儿教师', '幼儿园教师', '幼师'],
    ['保育员', '保育老师'], ['助教', '教学助理'],
    ['行政文员', '行政助理', '办公室文员'], ['前台接待', '前台文员'],
    ['人事专员', '人力资源专员'], ['仓库管理员', '仓管员', '库管员'],
    ['质检员', '质量检验员'], ['货运司机', '货车司机'],
    ['保洁员', '清洁工'], ['家政阿姨', '家政服务员', '保姆'],
    ['育婴师', '育婴员'], ['软件开发', '软件工程师', '程序员'],
]


def intent_labels(profile):
    # 保留英文职位中的空格，如 Java developer；中文标点/换行分隔多个意向。
    typed = re.split(r'[,，、;；/\n]+', profile.get('keyword') or '')
    return list(dict.fromkeys(s.strip() for s in typed + list(profile.get('jobTypes') or []) if s and s.strip()))


def search_words(profile):
    """缓存和评分使用全部词；交错展开，避免第一个大类占满实时搜索配额。"""
    groups = []
    for label in intent_labels(profile):
        aliases = next((g for g in ROLE_ALIASES if label in g), None)
        groups.append(list(dict.fromkeys([label] + aliases)) if aliases else INTENT_SYNONYMS.get(label) or [label])
    if not groups:
        groups = [[s] for s in profile.get('skills') or [] if s]
    return list(dict.fromkeys(group[i] for i in range(max(map(len, groups), default=0)) for group in groups if i < len(group)))

EDU_ORDER = ['不限', '小学', '初中', '高中中专', '大专', '本科及以上']


def _tokens(text):
    return set(TOKEN_RE.findall(text or ''))


# 技能词里的“虚词前缀”，去掉后得到核心词：有电工证 -> 电工证
SKILL_PREFIX_RE = re.compile(r'^(有|会|能|懂|熟练|会用|可以)')


def _skill_hit(skill, job_text, tags):
    """技能是否命中岗位。

    双向匹配，因为两边的说法常常只是包含关系：
      技能「有电工证」 vs 岗位标签「电工证」  -> 去前缀后包含
      技能「有电工证」 vs 岗位标签「电工」    -> 反向包含
    """
    if not skill:
        return False
    core = SKILL_PREFIX_RE.sub('', skill) or skill
    if skill in job_text or core in job_text:
        return True
    for t in tags:
        if t and (t in skill or t in core):
            return True
    return False


def _norm_salary(text):
    """从 '5000-8000元' / '面议' 里取出 (下限, 上限)。"""
    if not text:
        return (0, 0)
    nums = [int(n) for n in re.findall(r'\d+', text)]
    if not nums:
        return (0, 0)
    if len(nums) == 1:
        return (nums[0], nums[0])
    a, b = nums[0], nums[1]
    if b < a:
        a, b = b, a
    # '300元/天' 这类按天算的，粗略折算成月
    if b <= 500:
        a, b = a * 26, b * 26
    return (a, b)


def _expect_salary(text):
    """期望月薪文字 -> (下限, 上限)。"""
    m = {
        '3000以下': (0, 3000),
        '3000-5000': (3000, 5000),
        '5000-8000': (5000, 8000),
        '8000-12000': (8000, 12000),
        '12000以上': (12000, 999999),
    }
    return m.get(text) or _norm_salary(text)


def _ratio(a):
    return max(0.0, min(1.0, a))


def score_job(profile, job):
    """给单个岗位打分，返回 (0-100, 理由列表, 建议列表)。"""
    reasons, tips = [], []

    # 1) 技能 / 标签命中
    skills = set(profile.get('skills') or [])
    tags = job.get('tags') or []
    job_text = ' '.join([job.get('title', '') or '', job.get('desc') or '', ' '.join(tags)])
    hit = [s for s in skills if _skill_hit(s, job_text, tags)]
    skill_score = _ratio(len(hit) / len(skills)) if skills else 0.45
    if hit:
        reasons.append('你会的「%s」正好是这个岗位要的' % '、'.join(hit[:3]))
    elif skills:
        tips.append('这个岗位更看重别的技能，可以看看同类岗位')

    # 2) 求职意向命中
    intents = intent_labels(profile)
    words = [w.casefold() for w in search_words(profile)] if intents else []
    title = (job.get('title') or '').casefold()
    intent_score = 1.0 if any(w in title for w in words if w) else (
        0.5 if any(w in job_text.casefold() for w in words if w) else 0.0
    )
    if intent_score >= 1.0:
        reasons.append('和你选的「%s」是对口的' % '/'.join(intents[:2]))
    elif intent_score == 0 and intents:
        tips.append('这个岗位跟你想做的工作不是一类，可以跳过')

    # 3) 单位性质（央企 / 国企 / 外企 / 合资 / 民营）
    wants = profile.get('natures') or []
    jnature = (job.get('nature') or '').strip()
    if not wants:
        nature_score = 0.8
    elif jnature in wants:
        nature_score = 1.0
        reasons.append('是你要的%s单位' % jnature)
    else:
        nature_score = 0.0
        tips.append('这家是%s，不是你想去的单位类型' % (jnature or '未知性质'))

    # 4) 城市（选了「不限城市」时不加分也不减分）
    # ⚠️ 这里的口径必须和「岗位检索」那一侧的 models._city_hit 完全一致。
    # 以前两边各判一次还判得不一样：库明明允许「全国」和「本省」的岗位进来
    # （默认值就勾着「连全国招聘和本省一起看」），到这里却又判 0 分、
    # 还挂一句「工作地点在全国，离你有点远」—— 把刚刚放宽拿回来的岗位又扣回去，
    # 理由还和事实相反。现在直接复用同一个函数，不留两份口径。
    pcity = (profile.get('city') or '').strip()
    jcity = (job.get('city') or '').strip()
    if pcity in ('不限城市', '不限', '全国', ''):
        city_score = 0.8
    elif not jcity:
        city_score = 0.8        # 岗位没标城市，别反过来冤枉它「离你有点远」
    else:
        try:
            from models import _city_hit
            hit = _city_hit(pcity, jcity)
        except Exception:       # noqa: BLE001 读不到关系表就退回子串匹配
            hit = pcity in jcity or jcity in pcity
        if hit:
            city_score = 1.0
            if jcity in ('全国', '不限城市', '不限', '全国范围'):
                reasons.append('面向全国招人，在哪儿投都成立')
            elif pcity in jcity or jcity in pcity:
                reasons.append('就在你所在的%s' % jcity)
            else:
                reasons.append('在%s省内，离你不远' % jcity)
        else:
            city_score = 0.0
            tips.append('工作地点在%s，离你有点远' % (jcity or '外地'))

    # 5) 薪资
    plo, phi = _expect_salary(profile.get('salary'))
    jlo, jhi = _norm_salary(job.get('salary_text'))
    if not jlo:
        salary_score = 0.6
    elif not phi:
        salary_score = 0.7
    elif jhi >= plo:
        salary_score = 1.0 if jlo >= plo else 0.8
        if jlo >= plo:
            reasons.append('工资%s，达到你的期望' % job.get('salary_text'))
    else:
        salary_score = _ratio(jhi / plo) if plo else 0.5
        tips.append('工资%s，比你期望的低一些' % job.get('salary_text'))

    # 6) 经验 + 学历
    pexp = EXP_MAP.get(profile.get('exp'), 1)
    jexp_text = job.get('exp', '') or ''
    if ('不限' in jexp_text) or ('经验不限' in jexp_text) or (not jexp_text):
        exp_score = 1.0
    else:
        need = 1
        nums = re.findall(r'\d+', jexp_text)
        if nums:
            need = int(nums[0])
        exp_score = 1.0 if pexp >= need else _ratio(0.5 + pexp / max(1, need) * 0.5)
        if pexp < need:
            tips.append('要求%s，你的经验稍微少一点' % jexp_text)

    pedu = (profile.get('edu') or '').strip()
    jedu = (job.get('edu') or '').strip()
    if (not jedu) or jedu == '不限' or pedu not in EDU_ORDER or jedu not in EDU_ORDER:
        edu_score = 1.0
    else:
        edu_score = 1.0 if EDU_ORDER.index(pedu) >= EDU_ORDER.index(jedu) else 0.4
        if edu_score < 1.0:
            tips.append('学历要求%s' % jedu)

    total = (
        skill_score * 0.35
        + intent_score * 0.22
        + nature_score * 0.15
        + city_score * 0.13
        + salary_score * 0.08
        + ((exp_score + edu_score) / 2) * 0.07
    )
    score = int(round(total * 100))

    # 劳务派遣提醒：用户只要直签，而岗位是派遣 —— 扣分并置顶提示
    warning = ''
    if profile.get('hireType') == '直签' and job.get('hire_type') == '劳务派遣':
        score -= 8
        warning = '这家是劳务派遣，不是直接跟单位签合同'
        tips.insert(0, warning)

    # 放宽到外地的岗位：本市机会太少时才出现，不该盖过本市的机会
    if job.get('nearby'):
        score -= 12

    score = max(5, min(99, score))

    if score >= 70:
        level = '很适合'
    elif score >= 50:
        level = '可以考虑'
    else:
        level = '不太合适'

    if not reasons:
        reasons.append('整体条件还算接近，可以先投一份试试')
    return score, level, reasons[:3], tips[:3], warning


def match_jobs(profile, jobs):
    """对一批岗位打分并按匹配度从高到低排序。"""
    out = []
    for j in jobs:
        score, level, reasons, tips, warning = score_job(profile, j)
        item = dict(j)
        item['score'] = score
        item['level'] = level
        item['reasons'] = reasons
        item['tips'] = tips
        item['warning'] = warning
        out.append(item)
    out.sort(key=lambda x: (-x['score'], x.get('hire_type') != '直签'))
    return out
