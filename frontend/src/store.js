import { reactive, computed, watch } from 'vue'
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
    resume_path: '',
    // 简历原件「原名」，登录后回填展示「已保存的简历：xxx.pdf」
    resume_name: ''
  },
  // 登录态：用户名 + 密码。token 单独存一份（AUTH_KEY），不清进度也能单独退出。
  auth: { loggedIn: false, username: '', token: '' },
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

/* ==========================================================================
   存到手机本地：跳去招聘站登录再回来，进度不能归零
   ------------------------------------------------------------------------
   手机浏览器的真实行为：点「去官网投」跳到招聘站（或微信里扫码登录），
   回来时这个页面常被系统回收后**重新加载** —— 以前 store 只在内存里，
   一重载就回到第 1 步，档案、挑好的岗位、走到第几步全没了。
   用户的话就是「扫完码再回来啥都没了」。

   所以把「人填的东西」和「走到哪一步」写进 localStorage：
   重新打开时先恢复，再往下走。

   两个刻意的取舍：
   1) 只存「人填的」，不存「正在跑的」（loading/err/task/applying）——
      存了会在恢复瞬间让界面停在假的「正在加载」，而实际什么都没在跑。
   2) 存坏了不能让整站打不开：localStorage 里可能是上个版本的结构、也可能
      被人手动改过，读出来一律 try/catch 包住，坏了就当没存过。
   ========================================================================== */

const PERSIST_KEY = 'zhaojobs.state.v1'

// 正在跑的状态不落盘：恢复出来是假的「忙」，反而把人卡住
const NOT_PERSISTED = ['loading', 'err', 'task', 'applying', 'companySearching', 'auth']

// 岗位列表可能很长（放宽城市时几百条），localStorage 只有 5M 左右。
// 超过这个量就只存挑中的岗位 key，列表重载后重新匹配一次即可。
const SIZE_LIMIT = 1.5 * 1024 * 1024

function _snapshot() {
  const out = {}
  for (const k of Object.keys(store)) {
    if (NOT_PERSISTED.includes(k)) continue
    out[k] = JSON.parse(JSON.stringify(store[k]))
  }
  return out
}

export function saveStore() {
  try {
    let snap = _snapshot()
    let text = JSON.stringify(snap)
    if (text.length > SIZE_LIMIT && Array.isArray(snap.jobs)) {
      // 太大就丢掉岗位明细：下次进来重新搜一次就行，
      // 但「挑了哪些」必须留着，那是人一个个点出来的
      snap = { ...snap, jobs: [], allJobs: [] }
      text = JSON.stringify(snap)
    }
    localStorage.setItem(PERSIST_KEY, text)
  } catch (e) {
    /* 隐私模式 / 配额满 / 存不进：不存也能用，只是回来要重填，别打断当前流程 */
  }
}

/** 把上次存的东西填回来。返回的 flag 只给界面用（比如提示「已接着上次」）。 */
export function restoreStore() {
  try {
    const raw = localStorage.getItem(PERSIST_KEY)
    if (!raw) return { restored: false }
    const o = JSON.parse(raw)
    if (!o || typeof o !== 'object') return { restored: false }
    for (const k of Object.keys(o)) {
      if (NOT_PERSISTED.includes(k)) continue
      if (!(k in store)) continue          // 结构变了就忽略不认识的东西
      const v = o[k]
      if (k === 'profile') {
        // 逐字段盖：留着新版本新增字段的默认值，老存档不会把它清成空
        if (v && typeof v === 'object') Object.assign(store.profile, v)
      } else if (store[k] && typeof store[k] === 'object' && !Array.isArray(store[k])) {
        Object.assign(store[k], v)         // resumeReview / meta 这类小对象
      } else {
        store[k] = v
      }
    }
    // 存档里的 step 不能越过 maxStep（点进没到过的步骤是空白页）
    if (!Number.isFinite(store.step) || store.step < 1) store.step = 1
    if (store.step > store.maxStep) store.maxStep = store.step
    if (store.maxStep < 1) store.maxStep = 1
    return { restored: true, step: store.step }
  } catch (e) {
    return { restored: false }             // 读坏了当作没存过，绝不让整站打不开
  }
}

export function clearStore() {
  try {
    localStorage.removeItem(PERSIST_KEY)
  } catch (e) { /* 同上，清不掉也不影响继续用 */ }
}

/* ==========================================================================
   登录态（用户名 + 密码）：单独存一份，跟「填的进度」分开。
   ------------------------------------------------------------------------
   为什么分开：登录态是账号维度的，进度是这次会话的。清进度（「清空重填」）
   不该把人登出；退出登录也不该丢掉他填到一半的简历。两者各自 localStorage。
   token 走 Authorization 头传给后端，简历状态就归属到账号，换设备登录能恢复。
   ========================================================================== */

const AUTH_KEY = 'zhaojobs.auth.v1'

/** 拿请求头里要带的 Authorization。没登录返回空对象，api.req 直接展开。 */
export function getAuthHeader() {
  const t = store.auth.token
  return t ? { Authorization: 'Bearer ' + t } : {}
}

/** 启动时把上次存的 token 填回 store。返回是否处于登录态（只给界面判断用）。 */
export function restoreAuth() {
  try {
    const raw = localStorage.getItem(AUTH_KEY)
    if (!raw) return { loggedIn: false }
    const o = JSON.parse(raw)
    if (o && o.token) {
      store.auth.token = o.token
      store.auth.username = o.username || ''
      store.auth.loggedIn = true
      return { loggedIn: true }
    }
  } catch (e) { /* 坏了就当没登录 */ }
  return { loggedIn: false }
}

export function setAuth(token, username) {
  store.auth.token = token
  store.auth.username = username || ''
  store.auth.loggedIn = true
  try {
    localStorage.setItem(AUTH_KEY, JSON.stringify({ token, username: store.auth.username }))
  } catch (e) { /* 隐私模式存不进：本次会话内仍算登录，只是刷新后要重登 */ }
}

export function clearAuth() {
  store.auth.token = ''
  store.auth.username = ''
  store.auth.loggedIn = false
  try { localStorage.removeItem(AUTH_KEY) } catch (e) {}
}

/** 把服务端档案填回 store：登录后调一次，简历状态就接上了，不必重传。
 *  只覆盖「服务端有值」的字段，绝不用空值冲掉用户本地已填的内容。 */
export function applyServerProfile(sp) {
  if (!sp) return
  const p = store.profile
  const str = (col) => (sp[col] != null && sp[col] !== '' ? sp[col] : null)
  const set = (key, val) => { if (val != null) p[key] = val }

  set('name', str('name'))
  set('phone', str('phone'))
  set('email', str('email'))
  set('age', str('age'))
  set('city', str('city'))
  set('salary', str('salary'))
  set('exp', str('exp'))
  set('edu', str('edu'))
  set('intro', str('intro'))
  set('gender', str('gender'))
  set('birth', str('birth'))
  set('school', str('school'))
  set('major', str('major'))
  set('graduation', str('graduation'))
  set('height', str('height'))
  set('weight', str('weight'))
  set('emergency_name', str('emergency_name'))
  set('emergency_phone', str('emergency_phone'))
  set('address', str('address'))
  set('party_join_date', str('party_join_date'))
  set('nation', str('nation'))
  set('marital', str('marital'))
  set('edu_regular', str('edu_regular'))
  set('edu_fulltime', str('edu_fulltime'))
  set('has_degree', str('has_degree'))
  set('foreign_lang', str('foreign_lang'))
  set('foreign_level', str('foreign_level'))
  set('can_arrange', str('can_arrange'))
  set('health', str('health'))
  // 简历原件链接 + 原名：这就是「换设备登录还能接着用」的关键
  set('resume_path', str('resume_path'))
  set('resume_name', str('resume_name'))

  // 逗号分隔的字段还原成数组
  if (sp.job_types) p.jobTypes = String(sp.job_types).split(',').filter(Boolean)
  if (sp.skills) p.skills = String(sp.skills).split(',').filter(Boolean)

  // 经历是 JSON 字符串，还原成数组（丢了或格式坏就不动本地）
  if (sp.experiences) {
    try {
      const e = typeof sp.experiences === 'string' ? JSON.parse(sp.experiences) : sp.experiences
      if (Array.isArray(e)) { p.experiences = e; store.experiencesTouched = true }
    } catch (e) { /* 忽略坏数据 */ }
  }

  store.profileId = sp.id
}

/* 真正的存档时机：任何改动后 400ms 写一次。
   不用 immediate —— 初始化时会先 restore 再 watch，immediate 会把刚恢复的
   内容原样写回去，虽然结果一样，但没必要多一次磁盘写。
   防抖是必须的：输入框每敲一个字都会触发，逐字写盘在手机上会卡。 */
let _saveTimer = null
watch(
  store,
  () => {
    clearTimeout(_saveTimer)
    _saveTimer = setTimeout(saveStore, 400)
  },
  { deep: true }
)

// 关页面/切到后台前补一次：防抖窗口里的最后一次改动别丢。
// 手机上「切到别的 App」比「关闭页面」常见得多，visibilitychange 才是主路径。
const _flush = () => {
  clearTimeout(_saveTimer)
  saveStore()
}
window.addEventListener('pagehide', _flush)
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') _flush()
})

// 模块一加载就把上次的进度填回来
export const restored = restoreStore()
