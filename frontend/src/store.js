import { reactive, computed } from 'vue'
import { DEFAULT_NATURES } from './options'

export const store = reactive({
  step: 1,
  profilePanel: 'basic',
  resumeReview: { got: [], low: [], filled: [], resumeName: '', parsedAt: '' },
  maxStep: 1,       // 已经走到过哪一步：走过的能点回去，没到过的不让跳（点进去会是空白）
  bigFont: false,
  profile: {
    name: '',
    phone: '',
    email: '',
    age: '',
    city: '',
    cityOther: '',
    jobTypes: [],
    keyword: '',      // 第 2 步自己填的工种关键词：后端直接拿它去搜岗位
    nature: DEFAULT_NATURES.slice(), // 单位性质
    hireType: '直签',
    salary: '5000-8000',
    exp: '1-3年',
    edu: '高中中专',
    skills: [],
    intro: '',
    // 招聘表必问的几项：能从简历读出来就读，读不出来下面可以手填。
    // 没有这几项，自动投只能空着让人现打一遍。
    gender: '',
    birth: '',        // 1999-05
    school: '',
    major: '',
    graduation: '',   // 2021-06
    // 国聘「基本信息」段必填，缺了保存就过不去（2026-09-20 实测）
    height: '',
    weight: '',
    emergency_name: '',
    emergency_phone: '',
    address: '',
    party_join_date: '',   // 国聘党员必填；非党员留空
    // ---- 投国聘要用、且只有本人知道的个人事实 ----
    // 这些以前是后端写死填的（汉族 / 未婚 / 统招 / 全日制 / 英语熟练 / 服从调剂），
    // 等于替你编材料。现在你填什么就投什么，不填就留空、投递前提醒你补。
    nation: '',            // 民族，如「汉族」
    marital: '',           // 未婚 / 已婚 / 离异 / 丧偶
    edu_regular: '',       // 统招 / 非统招
    edu_fulltime: '',      // 全日制 / 非全日制
    has_degree: '',        // 有学位证 / 无学位证
    foreign_lang: '',      // 英语 / 日语 …；「无」= 不会外语
    foreign_level: '',     // 精通 / 熟练 / 一般
    can_arrange: '',       // 是 / 否（是否服从调剂，只有你能定）
    health: '',            // 健康 / 良好 / 一般
    // 结构化经历：国聘补站内简历时一段一段真填（有真实实习/社团经历
    // 就不用勾「无经历」）。type: edu | intern | campus
    experiences: [],
    // 用户同意保留的简历原件路径（后端存好后回传），自动投上传附件时用它
    resume_path: ''
  },
  profileId: null,
  // 用户这次会话有没有真的动过经历编辑器。没动过时保存档案不带 experiences，
  // 否则会把库里已有的经历（上次填的实习/社团）当成空数组覆盖掉（2026-09-20 实测踩坑）
  experiencesTouched: false,
  jobs: [],
  allJobs: [],     // 第 2 步匹配出来的完整岗位：搜某家单位时会临时缩小列表，用它随时还原
  companyFilter: '',  // 当前正在看哪家单位的岗位（空=看全部）
  companySearching: false,
  meta: {},         // 数据来源情况：哪些渠道在用、有没有放宽城市
  picked: [],        // 选中的岗位 key
  loading: false,
  err: '',
  filter: 'all',     // all | good
  scope: 'local',    // local | all —— 本市岗位不够时后端会补外地岗，这里由用户决定看不看
  // 默认勾上：连「全国招聘」和「本省其他城市」的岗位一起搜。
  // 国企央企的公告大量写「全国」或只写省名，勾掉就只剩精确写本市的那几条
  // （实测选武汉只剩 1 条），所以默认开；用户取消 = 只要本市。
  wide: true,
  task: null,
  applying: false
})

/** 归一化后的城市：选「不限城市」= 全国（空），手填的城市优先于下拉选项。
 *  后端认这个口径， nationwide/本市 的分档也照它判。 */
export function effectiveCity() {
  const p = store.profile
  if (p.city === '不限城市') return ''
  return (p.cityOther || '').trim() || p.city
}

/** 发给后端的档案副本。
 *  两个必要处理，去掉任何一个都会静默写坏数据：
 *  1) 城市用归一化后的值（否则「不限城市」当字面城市去查，一条都查不到）
 *  2) 这个会话没动过经历编辑器时不带 experiences —— 后端的语义是「带了就覆盖」，
 *     空数组会把库里上次存的实习/社团经历抹掉（2026-09-20 踩坑）
 */
export function profilePayload() {
  const p = { ...store.profile }
  p.city = effectiveCity()
  if (!store.experiencesTouched) delete p.experiences
  return p
}

export const pickedJobs = computed(() =>
  store.jobs.filter(j => store.picked.includes(j.key))
)

export const shownJobs = computed(() => {
  if (store.filter === 'good') return store.jobs.filter(j => j.score >= 75)
  return store.jobs
})

export function toggle(arr, v) {
  const i = arr.indexOf(v)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(v)
}

export function go(step) {
  store.step = step
  if (step > store.maxStep) store.maxStep = step
  store.err = ''
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

/** 能不能点到第 n 步：走过的都能点回去改，没到过的不行（点进去是空的）。 */
export function canGo(step) {
  return step >= 1 && step <= store.maxStep
}

export function pickedCount() {
  return store.picked.length
}
