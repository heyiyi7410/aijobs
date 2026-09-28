<template>
  <div class="reference-ui">
  <!-- hero 顶栏：蓝色渐变、全宽铺开（效果图）。装饰是纯 CSS 画的纸+放大镜 -->
  <header class="topbar" :class="{ wide: store.step === 3 }">
    <span class="hero-doc" aria-hidden="true"></span>
    <span class="hero-lens" aria-hidden="true"></span>
    <div class="topbar-in">
      <div>
        <h1>找工作助手</h1>
        <p class="sub">帮您准备简历、筛选合适的单位和岗位<br />一步一步完成投递，找工作更轻松</p>
      </div>
      <div class="topbar-actions">
        <template v-if="store.auth.loggedIn">
          <span class="auth-user" :title="'已登录账号'">{{ store.auth.username }}</span>
          <button class="btn-hero" @click="doLogout">退出</button>
        </template>
        <button v-else class="btn-hero" @click="showAuth = true">登录 / 注册</button>
        <button class="btn-hero font-switch" @click="toggleFont" :aria-pressed="store.bigFont"
          :aria-label="store.bigFont ? '切换为标准字号' : '切换为大字号'">
          <span>A-</span><i aria-hidden="true"></i><span>A+</span>
        </button>
        <button class="btn-hero" @click="readAloud" v-if="canSpeak" aria-label="朗读这一步的说明">
          <!-- 禁 emoji 当图标：用 SVG 喇叭 -->
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M4 9.5h3L12 5.5v13L7 14.5H4z" />
            <path d="M15.5 9a4 4 0 0 1 0 6" />
            <path d="M18 6.5a7.5 7.5 0 0 1 0 11" />
          </svg>
          朗读
        </button>
      </div>
    </div>
  </header>

  <div class="shell" :class="{ wide: store.step === 3 }">
    <StepBar />

    <!-- 上次填到一半、跳去招聘站登录后回来：档案和进度已经替他留着了。
         不说明一句，人看着直接落在第 3、4 步会以为进了别人的页面。 -->
    <div v-if="showRestored" class="selected-strip restoretip">
      <span class="selected-dot" aria-hidden="true"></span>
      <span>已接着上次的进度继续</span>
      <button class="btn btn-mini" @click="startOver">清空重填</button>
    </div>

    <Step1Profile v-if="store.step === 1" />
    <Step2Wish v-else-if="store.step === 2" />
    <Step3Match v-else-if="store.step === 3" />
    <Step4Apply v-else-if="store.step === 4" />

    <div v-if="store.loading && store.step === 2" class="finding">
      <p class="finding-t">正在一家一家去单位网站找岗位…</p>
      <p class="finding-s">国聘网、中国公共招聘网、地方国资委、外企官网 都找一遍</p>
      <p class="finding-w">已经跑了 {{ waited }} 秒。一般十来秒就好，找到会一次全给你。如果最后显示 0 个，说明当前意向确实没有正在报名的岗位——不是程序出错，换个城市或工种再试。</p>
    </div>

    <p v-if="store.err && store.step === 4" class="err-banner" role="alert">
      {{ store.err }}
    </p>

    <nav class="navbar" v-if="store.step < 4">
      <p v-if="store.err" class="nav-error" role="alert">{{ store.err }}</p>
      <!-- 第 2 步：当前选择偏好一览（效果图的小条） -->
      <div v-if="store.step === 2" class="pref">
        <span class="pref-t">当前选择偏好</span>
        <span>已选 <b>{{ p.nature.length }}</b> 类单位性质<template v-if="p.hireType">、<b>1</b> 种用工方式</template><template v-if="p.jobTypes.length">、<b>{{ p.jobTypes.length }}</b> 类工作</template><template v-if="cityNow">、城市 <b>{{ cityNow }}</b></template></span>
      </div>
      <div v-if="store.step === 3" class="selected-strip">
        <span class="selected-dot" aria-hidden="true"></span>
        <span>已选中 <b>{{ store.picked.length }}</b> 个岗位</span>
      </div>
      <div class="navbar-btns">
        <button class="btn" v-if="store.step > 1" @click="go(store.step - 1)">上一步</button>
        <button class="btn btn-primary" :disabled="store.loading" @click="next">
          {{ store.loading ? (store.step === 1 ? '正在保存…' : '正在找岗位…') : nextText }}
        </button>
      </div>
    </nav>

    <!-- 登录 / 注册弹窗 -->
    <div v-if="showAuth" class="auth-mask" @click.self="showAuth = false">
      <div class="auth-card" role="dialog" aria-modal="true">
        <div class="auth-tabs">
          <button :class="{ on: authTab === 'login' }" @click="authTab = 'login'">登录</button>
          <button :class="{ on: authTab === 'register' }" @click="authTab = 'register'">注册</button>
        </div>
        <h3>{{ authTab === 'login' ? '登录账号' : '注册账号' }}</h3>
        <p class="auth-tip">登录后简历状态会保存到账号，换手机、清缓存再回来登录，也能接着用，不必每次重传简历。</p>
        <input class="input" v-model="authUser" placeholder="用户名" maxlength="32" />
        <input class="input" type="password" v-model="authPass" placeholder="密码（至少 6 位）"
               @keyup.enter="submitAuth" />
        <p v-if="authErr" class="auth-err" role="alert">{{ authErr }}</p>
        <button class="btn btn-primary auth-submit" :disabled="authBusy" @click="submitAuth">
          {{ authBusy ? '处理中…' : (authTab === 'login' ? '登录' : '注册并登录') }}
        </button>
        <button class="text-link" @click="authTab = authTab === 'login' ? 'register' : 'login'">
          {{ authTab === 'login' ? '没有账号？去注册' : '已有账号？去登录' }}
        </button>
      </div>
    </div>
  </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch, onMounted } from 'vue'
import StepBar from './components/StepBar.vue'
import Step1Profile from './components/Step1Profile.vue'
import Step2Wish from './components/Step2Wish.vue'
import Step3Match from './components/Step3Match.vue'
import Step4Apply from './components/Step4Apply.vue'
import { store, go, profilePayload, effectiveCity, restored, clearStore,
         restoreAuth, setAuth, clearAuth, applyServerProfile } from './store'
import { api } from './api'
import { canSpeak, speak, stopSpeak } from './useSpeech'

const HINTS = {
  1: '第一步，说说你的情况。只有名字和手机号要打字，别的点一下就行。填好点下面的蓝色按钮。',
  2: '第二步，选你想去的单位。央企、国企、外企已经帮你选好了。再选想做什么工作、在哪个城市、想要多少工资。',
  3: '第三步，这些是给你挑出来的岗位，越靠上越合适。点圆圈选中要投的，选好点去投递。',
  4: '第四步，点开始投递，系统一家一家帮你投，等进度条走完就好了。'
}

// 第 2 步底部「当前选择偏好」小条用
const p = store.profile
const cityNow = computed(() => effectiveCity() || '不限城市')

/* 上次填过、这次进来是接着上次的（跳去招聘站登录再回来就是这条路）。
   只在真的恢复出「走过第 1 步」的内容时才提示 —— 第一次进来不该有这条。 */
const showRestored = ref(!!(restored && restored.restored && restored.step > 1))

// 「清空重填」会连他填的档案一起抹掉，先问一句，别让人手滑丢一堆字
function startOver() {
  if (!window.confirm('清空这里填的所有内容，从第 1 步重新开始？')) return
  clearStore()
  location.reload()
}

/* ==========================================================================
   登录态：启动时若本地有 token，先问后端「我还登录着吗」，是就把服务端档案
   填回来（含已保存的简历链接）。token 失效就静默登出，不影响继续使用。
   ========================================================================== */
const authRestored = restoreAuth()
const showAuth = ref(false)
const authTab = ref('login')          // login | register
const authUser = ref('')
const authPass = ref('')
const authErr = ref('')
const authBusy = ref(false)

async function loadLoggedInProfile() {
  try {
    const me = await api.authMe()
    if (!me.ok) { clearAuth(); return }
    const prof = await api.loadProfile()
    if (prof.ok && prof.profile) applyServerProfile(prof.profile)
  } catch (e) { /* 连不上就当没登录，本地进度还在 */ }
}

async function submitAuth() {
  authErr.value = ''
  if (!authUser.value.trim()) return (authErr.value = '请填写用户名')
  if (!authPass.value) return (authErr.value = '请填写密码')
  authBusy.value = true
  try {
    const fn = authTab.value === 'login' ? api.authLogin : api.authRegister
    const r = await fn(authUser.value.trim(), authPass.value)
    if (!r.ok) { authErr.value = r.msg || '操作失败，请重试'; return }
    setAuth(r.token, r.username)
    showAuth.value = false
    authPass.value = ''
    // 登录后把服务端档案接回来：已保存的简历立刻显示，不必重传
    await loadLoggedInProfile()
  } catch (e) {
    authErr.value = '连不上服务，请稍后再试'
  } finally {
    authBusy.value = false
  }
}

async function doLogout() {
  try { await api.authLogout() } catch (e) { /* 忽略 */ }
  clearAuth()
}

onMounted(() => {
  if (authRestored.loggedIn) loadLoggedInProfile()
})

const nextText = computed(() => ({ 1: '下一步：选单位', 2: '帮我找岗位', 3: '去投递' }[store.step]))

function toggleFont() {
  store.bigFont = !store.bigFont
}

/* 第 3 步要一家一家去单位网站找岗位，第一次要 20~30 秒（之后有缓存会快些）。
   光让按钮变成「正在找…」用户会以为死机了，所以把秒数报给他，让他知道机器在干活。 */
const waited = ref(0)
let waitTimer = null

function startWait() {
  stopWait()
  waited.value = 0
  waitTimer = setInterval(() => { waited.value++ }, 1000)
}

function stopWait() {
  if (waitTimer) clearInterval(waitTimer)
  waitTimer = null
}

onUnmounted(stopWait)
watch(
  () => store.bigFont,
  v => {
    document.documentElement.style.fontSize = v ? '23px' : '18px'
  },
  { immediate: true }
)

function readAloud() {
  speak(HINTS[store.step])
}

/* 第 2 步要把「不限城市」换成空、把手填城市换成真值。关键是**写回 store**：
   后面第 3 步「手动抓取更多」直接拿 store.profile 去请求，不写回的话那里
   用的还是原始值 —— 选了不限城市就去字面查「不限城市」、手填了城市又当成空，
   于是要么一条都搜不到，要么一堆外地岗被标成本市混进列表。 */
function effectiveCitySync() {
  const c = effectiveCity()
  store.profile.city = c
  return c
}

// 这个会话没动过经历编辑器时，不带 experiences 字段——后端语义是
// 「带了就覆盖」，空数组会把库里上次存的实习/社团经历抹掉（2026-09-20 踩坑）。
// 统一实现挪到 store.profilePayload()，App/自动填表共用一份，别再各写一遍。
async function next() {
  store.err = ''
  const p = store.profile

  if (store.step === 1) {
    store.profilePanel = 'basic'
    if (!p.name.trim()) return (store.err = '请填写你的名字')
    if (!/^1\d{10}$/.test(p.phone.trim())) return (store.err = '请填写正确的 11 位手机号')
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email.trim()))
      return (store.err = '请填写正确的邮箱，单位要回复给你')
    store.loading = true
    try {
      const r = await api.saveProfile(profilePayload())
      // 后端还会再校验一遍（姓名为空会回 400 + 人话），不判断就往下走的话
      // profileId 是 null，后面整条链路静默失效而屏幕上一个字都不说。
      if (!r.ok) {
        store.loading = false
        return (store.err = r.msg || '资料没保存成功，请检查后再点下一步')
      }
      store.profileId = r.profile_id
    } catch (e) {
      store.loading = false
      return (store.err = '连不上服务，请重新启动一下程序')
    }
    store.loading = false
    go(2)
    return
  }

  if (store.step === 2) {
    if (!p.nature.length) return (store.err = '至少要选一种单位性质')
    // 工种可以选大类，也可以在第 2 步自己填关键词，二者有其一就行
    if (!p.jobTypes.length && !(p.keyword || '').trim())
      return (store.err = '选一下你想做什么工作，或者自己填一个工种')
    effectiveCitySync() // 空城市代表“不限城市”，也是有效的搜索范围。

    store.loading = true
    startWait()
    try {
      // 第 2 步选的意向（城市、工种、salary）也要写回简历。只在第 1 步存过一次的话，
      // 后面自动填表按 profile_id 读到的还是那份旧资料，城市会显示成「你资料里没有这项」。
      const profile = profilePayload()
      const saved = await api.saveProfile(profile)
      if (!saved.ok) {
        // 后端拒收（比如姓名是空的）时不能照往下走：profileId 还是 null，
        // 第 3 步的「已投递过滤」和自动填表全都失效，而用户一声提示都看不到。
        store.err = saved.msg || '资料没保存成功，请检查一下再试'
      } else {
        store.profileId = saved.profile_id

        // 带上 profile_id，后端据此把已经投过的岗位过滤掉
        const r = await api.match({ ...profile, profile_id: store.profileId },
                                  false, store.wide)
        if (!r.ok) {
          store.err = '查找失败，请重试'
        } else {
          store.jobs = r.jobs
          // 留一份完整的：第 3 步搜某家单位时会临时缩小列表，点「看全部岗位」要能还原
          store.allJobs = r.jobs.slice()
          store.companyFilter = ''
          store.meta = r.meta || {}
          store.filter = 'all'
          // 默认只显示本市的（外地岗由用户点「连外地一起看」才出现），
          // 这样不会稀里糊涂把用户看不见的外地岗投出去。
          store.scope = 'local'
          // 不替用户做主：低分的岗位也照常展示、照常能选，不搞「评分低自动跳过」。
          // 默认一个都不预勾，让用户自己点圆圈决定投哪些。
          store.picked = []
          go(3)
        }
      }
    } catch (e) {
      store.err = '连不上服务，请重新启动一下程序'
    }
    stopWait()
    store.loading = false
    return
  }

  if (store.step === 3) {
    if (!store.picked.length) return (store.err = '先选至少一个岗位（点岗位前面的圆圈）')
    go(4)
  }
}
</script>

<style scoped>
/* 顶栏登录态 */
.topbar-actions { display: flex; align-items: center; gap: .5rem; }
.auth-user {
  font-size: .95rem; font-weight: 700; color: var(--primary-7);
  background: var(--surface); border-radius: 1rem; padding: .35rem .8rem;
  max-width: 9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 登录/注册弹窗 */
.auth-mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(20, 40, 80, .45);
  display: flex; align-items: center; justify-content: center;
  padding: 1rem;
}
.auth-card {
  width: 100%; max-width: 22rem;
  background: var(--surface); border-radius: 1.1rem;
  padding: 1.4rem 1.3rem 1.6rem;
  box-shadow: 0 18px 50px rgba(20, 40, 80, .28);
  border: 1px solid var(--line);
}
.auth-tabs { display: flex; gap: .4rem; margin-bottom: 1rem; }
.auth-tabs button {
  flex: 1; padding: .6rem 0; border: 1px solid var(--line);
  background: var(--surface-2); border-radius: .7rem;
  font-weight: 700; color: var(--ink-2); cursor: pointer;
}
.auth-tabs button.on { background: var(--primary); color: #fff; border-color: var(--primary); }
.auth-card h3 { margin: 0 0 .4rem; font-size: 1.25rem; }
.auth-tip { font-size: .92rem; color: var(--ink-2); line-height: 1.6; margin: 0 0 1rem; }
.auth-card .input { width: 100%; margin-bottom: .7rem; }
.auth-err {
  color: var(--danger); font-size: .92rem; margin: 0 0 .6rem;
}
.auth-submit { width: 100%; margin-bottom: .8rem; }
.auth-card .text-link { display: block; text-align: center; }
</style>
