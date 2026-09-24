# -*- coding: utf-8 -*-
"""采集适配器统一接口。

新增一个招聘渠道 = 新建一个类实现 fetch()，在 __init__.py 注册即可。
"""

# 各性质单位的招聘入口。内置演示数据没有精确到职位的投递页，
# 实际使用时请在 jobs.json 里给每个岗位填真实的 apply_url。
DEFAULT_APPLY_URL = {
    '央企': 'https://www.iguopin.com',   # 国聘网，国资委主办的央企国企招聘平台
    '国企': 'https://www.iguopin.com',
    '外企': 'https://www.zhipin.com',
    '合资': 'https://www.zhipin.com',
    '民营': 'https://www.zhipin.com',
}


class BaseCollector:
    """岗位采集器基类。"""

    name = 'base'
    label = '未命名渠道'
    # 渠道开关。某个源改版/失效/出脏数据时，不用改代码也能单独关掉它，
    # 其它源照常出结果。可用 data/sources.json 覆盖，如 {"sample": false}
    enabled = True

    def fetch(self, keyword='', city='', limit=30, natures=None):
        """返回岗位列表，每个岗位是一个 dict。natures 为单位性质白名单。"""
        raise NotImplementedError

    @staticmethod
    def normalize(item, source):
        """补齐字段，保证前端拿到的数据结构一致。"""
        item.setdefault('title', '')
        item.setdefault('company', '未标注企业')
        item.setdefault('city', '')
        item.setdefault('district', '')
        item.setdefault('salary_text', '面议')
        item.setdefault('exp', '经验不限')
        item.setdefault('edu', '不限')
        item.setdefault('tags', [])
        item.setdefault('desc', '')
        item.setdefault('url', '')
        item.setdefault('nature', '民营')
        item.setdefault('hire_type', '直签')
        # 没有公开邮箱的岗位，统一走「跳转到对方投递页」——
        # 央企国企招聘门户都要注册登录，这是最主要的投递方式
        item.setdefault('hr_email', '')
        # 联系电话：民企/基层岗位普遍不留邮箱，电话是另一条能联系上人的路。
        # 没有就留空，前端按「有邮箱→代投，有电话→打电话，都没有→跳转投递页」分流。
        item.setdefault('hr_phone', '')
        # 招聘人数（0 = 未公开）；校招/社招（空 = 未标注）
        item.setdefault('headcount', 0)
        item.setdefault('recruit_type', '')
        # 报名开始/截止（YYYY-MM-DD，没有就空着）——前端用它提醒用户抓紧，
        # 采集器用它把还没开放报名的岗位先筛掉
        item.setdefault('apply_start', '')
        item.setdefault('deadline', '')
        item.setdefault('apply_url', DEFAULT_APPLY_URL.get(item['nature'], DEFAULT_APPLY_URL['民营']))
        item['source'] = source
        # 去重键带上城市：同一集团在不同城市招同名岗位是两个机会，不能合并
        item['key'] = '%s|%s|%s|%s' % (source, item['company'],
                                       item['title'], item['city'])
        return item
