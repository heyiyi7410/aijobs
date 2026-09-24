# -*- coding: utf-8 -*-
"""内置岗位库 —— 只收录「央企 / 国企 / 外企」。

目的：软件装好就能用，不联网也能跑通完整流程。

每个岗位额外带两个字段（重要）：
    nature     单位性质：央企 / 国企 / 外企 / 合资 / 民营
    hire_type  用工方式：直签 / 劳务派遣
    —— 央企国企的基层岗位大部分是「劳务派遣」，一定要让用户看见。

想换成你们自己的岗位数据：
  1) 改 webapp/data/jobs.json（见 collectors/api.py 说明），或
  2) 直接改这个文件里的 SAMPLE_JOBS。
"""
from .base import BaseCollector

# nature: 央企 / 国企 / 外企 / 合资 / 民营
# hire_type: 直签 / 劳务派遣
SAMPLE_JOBS = [
    # ============ 央企 ============
    dict(title='电力线路运维工', company='国家电网（市供电公司）', nature='央企', hire_type='劳务派遣', city='杭州', district='余杭区', salary_text='6000-8500元', exp='1-3年', edu='高中中专', tags=['电力', '运维', '电工证', '高压'], desc='负责配电线路巡视与故障抢修，需低压/高压电工证，登高作业。'),
    dict(title='邮件分拣员', company='中国邮政', nature='央企', hire_type='劳务派遣', city='武汉', district='东西湖区', salary_text='5000-6500元', exp='经验不限', edu='不限', tags=['分拣', '快递', '夜班', '包住'], desc='邮件报刊分拣、扫描、装车，夜班有补贴，包住宿。'),
    dict(title='10086 客服代表', company='中国移动', nature='央企', hire_type='劳务派遣', city='西安', district='雁塔区', salary_text='4500-6500元', exp='经验不限', edu='高中中专', tags=['客服', '电话客服', '电脑'], desc='接听客户来电，解答话费套餐问题，会电脑打字，有岗前培训。'),
    dict(title='营业厅营业员', company='中国联通', nature='央企', hire_type='劳务派遣', city='成都', district='武侯区', salary_text='4500-6000元', exp='经验不限', edu='高中中专', tags=['营业员', '前台', '电脑'], desc='营业厅业务受理，形象端正，会基础电脑操作。'),
    dict(title='宽带装维工程师', company='中国电信', nature='央企', hire_type='劳务派遣', city='长沙', district='雨花区', salary_text='6000-9000元', exp='1年以内', edu='高中中专', tags=['装维', '网络', '维修', '技术工'], desc='上门安装调试宽带，会网线水晶头与路由器配置，有师傅带。'),
    dict(title='施工现场电工', company='中国建筑第三工程局', nature='央企', hire_type='劳务派遣', city='武汉', district='洪山区', salary_text='7000-10000元', exp='3-5年', edu='不限', tags=['电工', '维修', '电工证', '工地'], desc='施工现场临时用电布设与检修，须持低压电工证，包吃住。'),
    dict(title='施工现场安全员', company='中国建筑第八工程局', nature='央企', hire_type='直签', city='深圳', district='龙岗区', salary_text='8000-11000元', exp='3-5年', edu='大专', tags=['安全员', '工地', '安全管理', 'C证'], desc='现场安全巡查与交底，需安全员C证，五险一金齐全。'),
    dict(title='加油站加油员', company='中国石油', nature='央企', hire_type='直签', city='北京', district='昌平区', salary_text='5000-6500元', exp='经验不限', edu='不限', tags=['加油员', '收银', '加油站'], desc='加油操作与便利店收银，倒班制，五险一金，包工作餐。'),
    dict(title='加油站营业员', company='中国石化', nature='央企', hire_type='直签', city='广州', district='白云区', salary_text='5000-7000元', exp='经验不限', edu='初中', tags=['加油员', '营业员', '便利店'], desc='加油与易捷便利店理货收银，国企直签，稳定。'),
    dict(title='高铁餐车服务员', company='中国铁路（华铁旅服）', nature='央企', hire_type='劳务派遣', city='上海', district='上海虹桥', salary_text='5500-7500元', exp='经验不限', edu='不限', tags=['服务员', '餐饮', '铁路'], desc='高铁列车餐吧服务与售货车推售，跟车往返，有乘务补贴。'),
    dict(title='超市理货员', company='华润万家', nature='央企', hire_type='直签', city='深圳', district='福田区', salary_text='4500-5800元', exp='经验不限', edu='不限', tags=['理货', '营业员', '超市'], desc='货架补货、价签核对、保质期检查，早晚班倒班。'),
    dict(title='仓储保管员', company='中粮集团', nature='央企', hire_type='直签', city='北京', district='大兴区', salary_text='5500-7000元', exp='1-3年', edu='高中中专', tags=['仓储', '仓库', '盘点', '保管'], desc='粮仓出入库登记、粮情巡检、账实盘点，需会基础表格。'),
    dict(title='航站楼保洁员', company='中国航空集团（机场地服）', nature='央企', hire_type='劳务派遣', city='北京', district='顺义区', salary_text='4200-5500元', exp='经验不限', edu='不限', tags=['保洁', '环卫', '机场'], desc='航站楼公共区域清洁，倒班，提供班车与工作餐。'),
    dict(title='码头装卸工', company='招商局港口', nature='央企', hire_type='劳务派遣', city='深圳', district='南山区', salary_text='7000-9500元', exp='1年以内', edu='不限', tags=['搬运', '装卸', '港口'], desc='集装箱码头辅助装卸作业，体力岗，有高温补贴与夜班补贴。'),
    dict(title='数控车工', company='东方电气集团', nature='央企', hire_type='直签', city='成都', district='郫都区', salary_text='7000-10000元', exp='3-5年', edu='高中中专', tags=['数控', '车工', '机床', '技术工'], desc='操作数控车床加工零部件，会看机械图纸，国企直签五险一金。'),
    dict(title='焊工（二保焊）', company='中国中车', nature='央企', hire_type='直签', city='长沙', district='长沙县', salary_text='8000-12000元', exp='3-5年', edu='不限', tags=['焊工', '二保焊', '焊工证', '技术工'], desc='轨道交通车辆部件焊接，须持焊工证，国企直签，有技能津贴。'),
    dict(title='电厂巡检值班员', company='国家能源集团', nature='央企', hire_type='直签', city='西安', district='未央区', salary_text='6500-9000元', exp='1-3年', edu='大专', tags=['巡检', '电力', '值班', '运行'], desc='发电机组运行巡检与抄表，五班三倒，国企直签，包住宿。'),

    # ============ 地方国企 ============
    dict(title='地铁站务员', company='上海地铁（申通地铁）', nature='国企', hire_type='直签', city='上海', district='浦东新区', salary_text='5500-7000元', exp='经验不限', edu='高中中专', tags=['站务', '客服', '值班'], desc='站台客流引导、票务处理、应急联动，国企直签，做二休一。'),
    dict(title='公交车驾驶员', company='北京公交集团', nature='国企', hire_type='直签', city='北京', district='丰台区', salary_text='7000-9500元', exp='3-5年', edu='不限', tags=['司机', 'A1A3驾照', '公交'], desc='A1或A3驾照，线路运营驾驶，国企直签，五险一金，有安全奖。'),
    dict(title='自来水管网维修工', company='深圳市水务集团', nature='国企', hire_type='直签', city='深圳', district='罗湖区', salary_text='6500-8500元', exp='1-3年', edu='高中中专', tags=['维修', '管道', '水务'], desc='市政供水管网抢修与阀门维护，户外作业，有加班费。'),
    dict(title='燃气安检维修工', company='杭州燃气集团', nature='国企', hire_type='直签', city='杭州', district='拱墅区', salary_text='6000-8000元', exp='1-3年', edu='高中中专', tags=['维修', '燃气', '安检', '上门'], desc='入户燃气安全检查与灶具维修，国企直签，有交通补贴。'),
    dict(title='轨道安检员', company='成都轨道交通集团', nature='国企', hire_type='劳务派遣', city='成都', district='成华区', salary_text='4200-5200元', exp='经验不限', edu='不限', tags=['安检', '保安', '地铁'], desc='地铁进站安检、违禁品识别，站姿服务，倒班，有加班工资。'),
    dict(title='物业秩序维护员', company='广州城投集团', nature='国企', hire_type='劳务派遣', city='广州', district='天河区', salary_text='4800-5800元', exp='经验不限', edu='初中', tags=['保安', '秩序', '巡逻', '物业'], desc='物业小区门岗与巡逻，包住，国企项目，工作稳定。'),
    dict(title='轧钢操作工', company='武汉钢铁（宝武集团）', nature='国企', hire_type='直签', city='武汉', district='青山区', salary_text='7000-9500元', exp='1-3年', edu='高中中专', tags=['操作工', '工厂', '倒班', '冶金'], desc='轧钢生产线主控室操作，四班三倒，高温津贴，国企直签。'),
    dict(title='设备维修钳工', company='首钢集团', nature='国企', hire_type='直签', city='北京', district='石景山区', salary_text='7000-9500元', exp='3-5年', edu='高中中专', tags=['维修', '钳工', '机修', '技术工'], desc='冶炼设备日常点检与故障抢修，会看图纸，国企直签。'),
    dict(title='总装车间操作工', company='上汽集团', nature='国企', hire_type='直签', city='上海', district='嘉定区', salary_text='6500-8500元', exp='经验不限', edu='高中中专', tags=['操作工', '流水线', '汽车', '工厂'], desc='整车总装线装配作业，两班倒，国企直签，有班车与食堂。'),
    dict(title='泵站值班员', company='长沙水业集团', nature='国企', hire_type='直签', city='长沙', district='开福区', salary_text='4800-6000元', exp='经验不限', edu='不限', tags=['值班', '巡检', '水务'], desc='泵站设备运行值守与记录，上二休二，国企直签。'),
    dict(title='银行大堂引导员', company='江苏银行', nature='国企', hire_type='劳务派遣', city='杭州', district='下城区', salary_text='5000-6500元', exp='经验不限', edu='大专', tags=['大堂', '引导', '客服', '形象好'], desc='厅堂客户引导与叫号协助，形象端正，双休，有转正机会。'),
    dict(title='景区票务员', company='西安城墙管委会', nature='国企', hire_type='劳务派遣', city='西安', district='碑林区', salary_text='3800-5000元', exp='经验不限', edu='高中中专', tags=['票务', '收银', '前台'], desc='景区售票与闸机引导，室外岗位，国企景区项目。'),
    dict(title='地面保障员', company='厦门航空', nature='国企', hire_type='劳务派遣', city='厦门', district='湖里区', salary_text='5500-7500元', exp='经验不限', edu='高中中专', tags=['地勤', '保障', '机场', '搬运'], desc='机下行李装卸与客舱清洁，倒班，有夜航补贴。'),

    # ============ 外企 ============
    dict(title='整车装配操作工', company='特斯拉（上海超级工厂）', nature='外企', hire_type='直签', city='上海', district='浦东新区', salary_text='7000-9000元', exp='经验不限', edu='高中中专', tags=['操作工', '流水线', '汽车', '工厂'], desc='整车装配线作业，两班倒，外企直签，五险一金+补充医疗+免费餐。'),
    dict(title='生产线操作工', company='博世（中国）', nature='外企', hire_type='直签', city='苏州', district='工业园区', salary_text='6500-8500元', exp='1年以内', edu='高中中专', tags=['操作工', '工厂', '汽车零件'], desc='汽车零部件产线操作与自检，恒温车间，德企直签，13薪。'),
    dict(title='电气装配技术员', company='西门子', nature='外企', hire_type='直签', city='上海', district='杨浦区', salary_text='7500-10000元', exp='1-3年', edu='大专', tags=['电工', '装配', '电气', '技术工'], desc='配电柜二次接线与调试，会看电气原理图，外企直签。'),
    dict(title='配电柜装配工', company='施耐德电气', nature='外企', hire_type='直签', city='武汉', district='东湖高新区', salary_text='6500-8500元', exp='1-3年', edu='高中中专', tags=['装配', '电气', '技术工'], desc='成套开关柜装配与布线，法企直签，有免费班车与工作餐。'),
    dict(title='仓库拣货员', company='联合利华', nature='外企', hire_type='劳务派遣', city='合肥', district='经开区', salary_text='5500-7000元', exp='经验不限', edu='不限', tags=['仓储', '拣货', '仓库'], desc='日化品仓库拣货打包，站立作业，加班按法定支付。'),
    dict(title='送货司机 C1', company='可口可乐', nature='外企', hire_type='直签', city='广州', district='黄埔区', salary_text='7000-9500元', exp='3-5年', edu='不限', tags=['司机', 'C1驾照', '配送'], desc='C1驾照，市区商超配送与卸货，外企直签，有安全里程奖。'),
    dict(title='山姆会员店收银员', company='沃尔玛（中国）', nature='外企', hire_type='直签', city='深圳', district='龙岗区', salary_text='5000-6500元', exp='经验不限', edu='不限', tags=['收银', '营业员', '超市'], desc='会员店收银与会员服务，外企直签，带薪年假，员工折扣。'),
    dict(title='商场销售员工', company='宜家家居', nature='外企', hire_type='直签', city='成都', district='成华区', salary_text='5000-7000元', exp='经验不限', edu='高中中专', tags=['店员', '销售', '收银'], desc='家居展间整理与顾客接待，外企直签，五险一金+员工餐。'),
    dict(title='餐厅服务员', company='麦当劳', nature='外企', hire_type='直签', city='杭州', district='西湖区', salary_text='4500-6000元', exp='经验不限', edu='不限', tags=['服务员', '餐饮', '收银'], desc='点餐配餐与大堂清洁，弹性排班，学生兼职亦可，有晋升通道。'),
    dict(title='质检员（电子）', company='三星电子', nature='外企', hire_type='劳务派遣', city='西安', district='高新区', salary_text='5500-7500元', exp='1年以内', edu='高中中专', tags=['质检', '检测', '工厂', '显微镜'], desc='电子元件外观与功能检测，穿无尘服，倒班，有夜班补贴。'),
    dict(title='设备维修技术员', company='松下电器', nature='外企', hire_type='直签', city='杭州', district='钱塘区', salary_text='7000-9500元', exp='3-5年', edu='大专', tags=['维修', '机修', '设备', '技术工'], desc='自动化设备保养与故障处理，会PLC基础优先，外企直签。'),
    dict(title='轮胎成型工', company='大陆集团（马牌轮胎）', nature='外企', hire_type='直签', city='合肥', district='高新区', salary_text='6500-8500元', exp='1年以内', edu='不限', tags=['操作工', '工厂', '倒班'], desc='轮胎成型机台操作，三班倒，德企直签，高温津贴。'),
    dict(title='生产线操作员', company='宝洁（P&G）', nature='外企', hire_type='直签', city='广州', district='萝岗区', salary_text='6500-8500元', exp='经验不限', edu='高中中专', tags=['操作工', '流水线', '日化'], desc='日化产品灌装包装线操作，外企直签，全年13薪+利润分享。'),
    dict(title='项目助理（文职）', company='埃森哲', nature='外企', hire_type='直签', city='上海', district='黄浦区', salary_text='8000-12000元', exp='1-3年', edu='本科及以上', tags=['文员', '助理', '办公软件', '英语'], desc='项目文档整理与会议支持，需基础英语读写，外企直签。'),
    dict(title='IT 技术支持专员', company='IBM（中国）', nature='外企', hire_type='直签', city='北京', district='海淀区', salary_text='9000-14000元', exp='1-3年', edu='本科及以上', tags=['IT', '技术支持', '电脑', '英语'], desc='客户系统运维支持，轮班制，需基础英语，外企直签。'),

    # ---- 电力 / 通信 / 基建（央企主力岗位）----
    dict(title='配电线路工', company='国家电网（北京市电力公司）', nature='央企', hire_type='劳务派遣', city='北京', district='海淀区', salary_text='7000-9000元', exp='1-3年', edu='高中中专', tags=['电力', '运维', '配电', '登高', '电工证'], desc='配电线路巡视检修与抢修，需低压电工证，可登高，夜班轮值。'),
    dict(title='抄表收费员', company='国家电网（武汉供电公司）', nature='央企', hire_type='劳务派遣', city='武汉', district='江岸区', salary_text='4500-5500元', exp='经验不限', edu='不限', tags=['抄表', '电力', '上门', '骑电动车'], desc='上门抄电表、发通知单，会骑电动车，片区固定。'),
    dict(title='95598 客服专员', company='国家电网（四川电力）', nature='央企', hire_type='劳务派遣', city='成都', district='锦江区', salary_text='4500-6000元', exp='经验不限', edu='大专', tags=['客服', '电力', '电脑', '普通话好'], desc='接听用电咨询与报修电话，会电脑，普通话标准。'),
    dict(title='变电运维值班员', company='南方电网（广东电网）', nature='央企', hire_type='直签', city='广州', district='荔湾区', salary_text='7000-9500元', exp='1-3年', edu='大专', tags=['电力', '运维', '变电', '值班'], desc='变电站运行值守、倒闸操作与巡检记录，五班三倒，直签五险一金。'),
    dict(title='输电检修工', company='南方电网（深圳供电局）', nature='央企', hire_type='直签', city='深圳', district='宝安区', salary_text='8000-11000元', exp='3-5年', edu='大专', tags=['电力', '检修', '抢修', '高空'], desc='高压输电线路检修消缺，需登高证，有高空作业津贴。'),
    dict(title='投递员', company='中国邮政', nature='央企', hire_type='直签', city='上海', district='静安区', salary_text='5000-6500元', exp='经验不限', edu='不限', tags=['快递', '投递', '骑电动车'], desc='片区报刊包裹投递，会骑电动车，国企直签，做六休一。'),
    dict(title='邮件处理中心操作工', company='中国邮政', nature='央企', hire_type='劳务派遣', city='西安', district='未央区', salary_text='4800-6000元', exp='经验不限', edu='不限', tags=['分拣', '操作工', '夜班', '包住'], desc='邮件自动分拣线值守与人工补码，夜班为主，包住宿。'),
    dict(title='基站维护员', company='中国铁塔', nature='央企', hire_type='劳务派遣', city='长沙', district='岳麓区', salary_text='5500-7500元', exp='1年以内', edu='高中中专', tags=['维修', '通信', '基站', '电工'], desc='通信基站巡检、发电保障与故障处理，需C1驾照，补贴另计。'),
    dict(title='钢筋工', company='中国建筑第二工程局', nature='央企', hire_type='劳务派遣', city='北京', district='大兴区', salary_text='7000-9500元', exp='1-3年', edu='不限', tags=['钢筋', '工地', '体力好', '建筑施工'], desc='钢筋下料绑扎，工地作业，包吃住，能吃苦优先。'),
    dict(title='塔吊信号工', company='中国建筑第八工程局', nature='央企', hire_type='劳务派遣', city='上海', district='浦东新区', salary_text='6500-8500元', exp='1-3年', edu='不限', tags=['信号工', '工地', '特种作业'], desc='塔吊起吊指挥，须持信号工证，工地包吃住。'),
    dict(title='航空加油员', company='中国航空油料', nature='央企', hire_type='直签', city='深圳', district='宝安区', salary_text='6500-8500元', exp='1-3年', edu='高中中专', tags=['加油', '航空', '危险品', '倒班'], desc='机场机坪航空燃油加注，须培训取证，倒班，安全保障要求高。'),
    dict(title='电厂运行巡检员', company='中国华能集团', nature='央企', hire_type='直签', city='西安', district='阎良区', salary_text='6500-9000元', exp='1-3年', edu='大专', tags=['电力', '运行', '巡检', '倒班'], desc='发电机组集控巡检与参数记录，五班三倒，国企直签包住宿。'),
    dict(title='粮库保管员', company='中储粮', nature='央企', hire_type='直签', city='武汉', district='新洲区', salary_text='5500-7000元', exp='1年以内', edu='高中中专', tags=['仓储', '保管', '巡检', '粮食'], desc='粮仓温湿度监测、粮情检查与出入库登记，包吃住。'),

    # ---- 城市公用事业（地方国企）----
    dict(title='地铁站务员', company='北京地铁', nature='国企', hire_type='直签', city='北京', district='西城区', salary_text='5500-7000元', exp='经验不限', edu='高中中专', tags=['站务', '客服', '值班', '地铁'], desc='站台客流组织、票务处理与应急处置，国企直签，五险一金。'),
    dict(title='地铁安检员', company='广州地铁', nature='国企', hire_type='劳务派遣', city='广州', district='番禺区', salary_text='4300-5300元', exp='经验不限', edu='不限', tags=['安检', '保安', '地铁'], desc='进站安检与违禁品检查，站着上班，倒班，加班费另算。'),
    dict(title='地铁站务员', company='深圳地铁', nature='国企', hire_type='直签', city='深圳', district='福田区', salary_text='5800-7200元', exp='经验不限', edu='大专', tags=['站务', '客服', '地铁'], desc='车站客服中心与站台值守，国企直签，有年终奖。'),
    dict(title='公交车驾驶员', company='深圳巴士集团', nature='国企', hire_type='直签', city='深圳', district='南山区', salary_text='7500-10000元', exp='3-5年', edu='不限', tags=['司机', 'A1A3驾照', '公交'], desc='A1/A3驾照，线路运营驾驶，国企直签，安全奖+节油奖。'),
    dict(title='自来水抄表员', company='上海城投水务', nature='国企', hire_type='直签', city='上海', district='普陀区', salary_text='5000-6200元', exp='经验不限', edu='不限', tags=['抄表', '水务', '上门'], desc='片区水表抄见与账单派送，路线固定，国企直签。'),
    dict(title='天然气维修工', company='武汉市天然气公司', nature='国企', hire_type='直签', city='武汉', district='武昌区', salary_text='6000-8000元', exp='1-3年', edu='高中中专', tags=['维修', '燃气', '上门', '管道'], desc='户内燃气设施维修与改管，国企直签，有工具车。'),
    dict(title='污水处理操作工', company='成都环境集团', nature='国企', hire_type='直签', city='成都', district='龙泉驿区', salary_text='5500-7000元', exp='经验不限', edu='高中中专', tags=['操作工', '水务', '倒班', '巡检'], desc='污水处理厂设备巡检与加药操作，四班二倒，国企直签。'),
    dict(title='供水管网抢修工', company='西安自来水公司', nature='国企', hire_type='直签', city='西安', district='莲湖区', salary_text='6000-8000元', exp='1-3年', edu='不限', tags=['维修', '管道', '抢修', '水务'], desc='市政供水管道爆管抢修，户外作业，有抢修出勤补贴。'),
    dict(title='公交车驾驶员', company='杭州市公交集团', nature='国企', hire_type='直签', city='杭州', district='上城区', salary_text='7000-9000元', exp='3-5年', edu='不限', tags=['司机', 'A1A3驾照', '公交'], desc='A1/A3驾照，市区线路驾驶，国企直签，做五休二。'),
    dict(title='电动车维修电工', company='广州公交集团', nature='国企', hire_type='直签', city='广州', district='白云区', salary_text='6500-8500元', exp='1-3年', edu='高中中专', tags=['维修', '电工', '汽车', '技术工'], desc='纯电公交车三电系统保养维修，需低压电工证，国企直签。'),
    dict(title='行李分拣员', company='上海机场集团', nature='国企', hire_type='劳务派遣', city='上海', district='浦东新区', salary_text='5200-6500元', exp='经验不限', edu='不限', tags=['分拣', '搬运', '机场', '倒班'], desc='航站楼行李系统分拣与装机辅助，倒班，有夜班津贴。'),
    dict(title='轨道站务员', company='苏州市轨道交通集团', nature='国企', hire_type='直签', city='苏州', district='姑苏区', salary_text='5500-6800元', exp='经验不限', edu='大专', tags=['站务', '客服', '地铁'], desc='车站客运服务与设备巡查，国企直签，双休轮班。'),

    # ---- 外企 / 合资 ----
    dict(title='整车质检员', company='特斯拉（上海超级工厂）', nature='外企', hire_type='直签', city='上海', district='浦东新区', salary_text='7000-9000元', exp='1-3年', edu='高中中专', tags=['质检', '检测', '汽车', '工厂'], desc='整车下线质量检查与记录，会用游标卡尺，外企直签。'),
    dict(title='零部件质检员', company='博世（中国）', nature='外企', hire_type='直签', city='苏州', district='工业园区', salary_text='6500-8500元', exp='1-3年', edu='高中中专', tags=['质检', '检测', '工厂'], desc='零部件尺寸与外观检测，会用测量工具，德企直签13薪。'),
    dict(title='仓库管理员', company='施耐德电气', nature='外企', hire_type='劳务派遣', city='武汉', district='东湖高新区', salary_text='5500-7000元', exp='1-3年', edu='高中中专', tags=['仓储', '仓库', '盘点', '叉车'], desc='电气元件仓库收发与盘点，有叉车证优先，外企园区。'),
    dict(title='商场理货员', company='宜家家居', nature='外企', hire_type='直签', city='北京', district='大兴区', salary_text='5000-6500元', exp='经验不限', edu='不限', tags=['理货', '仓储', '商场'], desc='家居货架补货与自提区理货，外企直签，员工餐+折扣。'),
    dict(title='生鲜理货员', company='沃尔玛（中国）', nature='外企', hire_type='直签', city='广州', district='海珠区', salary_text='4800-6000元', exp='经验不限', edu='不限', tags=['理货', '超市', '生鲜'], desc='生鲜区补货、保鲜与保质期管理，早班为主，外企直签。'),
    dict(title='餐厅见习经理', company='麦当劳', nature='外企', hire_type='直签', city='成都', district='青羊区', salary_text='5500-7500元', exp='1-3年', edu='高中中专', tags=['餐饮', '管理', '服务员'], desc='餐厅排班、订货与人员管理，外企直签，有完整晋升体系。'),
    dict(title='生产线操作工', company='达能（中国）', nature='外企', hire_type='直签', city='武汉', district='东西湖区', salary_text='6000-7500元', exp='经验不限', edu='高中中专', tags=['操作工', '流水线', '食品'], desc='饮品灌装线操作与卫生清洁，恒温车间，法企直签。'),
    dict(title='电子产品组装工', company='富士康科技集团', nature='外企', hire_type='直签', city='深圳', district='龙华区', salary_text='5500-7500元', exp='经验不限', edu='不限', tags=['组装', '操作工', '流水线', '包吃住'], desc='电子产品组装与测试，坐着上班，包吃住，加班自愿。'),
    dict(title='光学镜片装配工', company='卡尔蔡司', nature='外企', hire_type='直签', city='广州', district='黄埔区', salary_text='6500-8500元', exp='1-3年', edu='高中中专', tags=['装配', '光学', '无尘车间'], desc='镜片精密装配与检验，无尘车间，德企直签，双休。'),
    dict(title='品质检验员', company='松下电器', nature='外企', hire_type='直签', city='杭州', district='钱塘区', salary_text='6000-8000元', exp='1-3年', edu='高中中专', tags=['质检', '检测', '工厂'], desc='家电产品出货检验与不良分析，外企直签，有班车。'),
    dict(title='整车装配工', company='上汽大众', nature='合资', hire_type='直签', city='上海', district='嘉定区', salary_text='7000-9000元', exp='1年以内', edu='高中中专', tags=['装配', '流水线', '汽车'], desc='整车装配线作业，两班倒，合资企业直签，有班车食堂。'),

    # ---- 杭州补充 ----
    dict(title='地铁站务员', company='杭州地铁集团', nature='国企', hire_type='直签', city='杭州', district='下城区', salary_text='5500-6800元', exp='经验不限', edu='大专', tags=['站务', '客服', '值班', '地铁'], desc='车站客运服务与设备巡查，国企直签，五险一金。'),
    dict(title='邮件分拣员', company='中国邮政', nature='央企', hire_type='劳务派遣', city='杭州', district='滨江区', salary_text='5000-6200元', exp='经验不限', edu='不限', tags=['分拣', '快递', '夜班', '包住'], desc='邮件自动分拣线操作，夜班有补贴，包住宿。'),
    dict(title='宽带装维工程师', company='中国电信', nature='央企', hire_type='劳务派遣', city='杭州', district='西湖区', salary_text='6000-8500元', exp='1年以内', edu='高中中专', tags=['装维', '网络', '维修', '技术工'], desc='上门安装调试宽带，会网线制作与路由器配置，有师傅带。'),
    dict(title='营业厅营业员', company='中国移动', nature='央企', hire_type='劳务派遣', city='杭州', district='拱墅区', salary_text='4500-6000元', exp='经验不限', edu='高中中专', tags=['营业员', '客服', '电脑', '前台'], desc='营业厅业务受理与套餐介绍，会基础电脑操作。'),

    # ============ 民营（对照数据：用于验证筛选确实生效） ============
    dict(title='快递分拣员', company='顺丰速运', nature='民营', hire_type='直签', city='杭州', district='萧山区', salary_text='6000-8000元', exp='经验不限', edu='不限', tags=['分拣', '快递', '夜班'], desc='包裹分拣扫描装车，夜班补贴另算。'),
    dict(title='外卖骑手', company='美团配送', nature='民营', hire_type='直签', city='上海', district='普陀区', salary_text='8000-12000元', exp='经验不限', edu='不限', tags=['骑手', '配送', '电动车'], desc='自带电动车，按单提成，多劳多得。'),
    dict(title='普工（电子厂）', company='立讯精密', nature='民营', hire_type='直签', city='深圳', district='宝安区', salary_text='5500-7500元', exp='经验不限', edu='不限', tags=['普工', '流水线', '包吃住'], desc='坐着上班，恒温车间，包吃住，可预支工资。'),
    dict(title='餐厅服务员', company='老碗家面馆', nature='民营', hire_type='直签', city='杭州', district='西湖区', salary_text='4500-6000元', exp='经验不限', edu='不限', tags=['服务员', '餐饮', '包吃住'], desc='点单上菜收台，包吃包住，月休4天。'),
]

# 单位性质选项（前端与后端共用的一份定义，改这里就够了）
NATURES = ['央企', '国企', '事业单位', '公务员', '外企', '合资', '民营']


class SampleCollector(BaseCollector):
    """内置岗位库（可按单位性质、城市、关键词过滤）。"""

    name = 'sample'
    label = '内置岗位库'

    def fetch(self, keyword='', city='', limit=30, natures=None):
        kw = (keyword or '').strip()
        ct = (city or '').strip()
        want = set(natures or [])
        out = []
        for j in SAMPLE_JOBS:
            if want and j.get('nature') not in want:
                continue
            if ct and ct not in j.get('city', ''):
                continue
            if kw:
                hay = j['title'] + j['company'] + ' '.join(j.get('tags') or []) + j.get('desc', '')
                if kw not in hay:
                    continue
            item = dict(j)
            out.append(self.normalize(item, '内置岗位库'))
            if len(out) >= limit:
                break
        return out
