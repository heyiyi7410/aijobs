<template>
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

    <Step1Profile v-if="store.step === 1" />
    <Step2Wish v-else-if="store.step === 2" />
    <Step3Match v-else-if="store.step === 3" />
    <Step4Apply v-else-if="store.step === 4" />

    <div v-if="store.loading && store.step === 2" class="finding">
      <p class="finding-t">正在一家一家去单位网站找岗位…</p>
      <p class="finding-s">国聘网、中国公共招聘网、地方国资委、外企官网 都找一遍</p>
      <p class="finding-w">已经跑了 {{ waited }} 秒。一般十来秒就好，找到会一次全给你。如果最后显示 0 个，说明当前意向确实没有正在报名的岗位——不是程序出错，换个城市或工种再试。</p>
    </div>

    <p v-if="store.err" class="err-banner">
      {{ store.err }}
    </p>

    <nav class="navbar" v-if="store.step < 4">
      <!-- 第 2 步：当前选择偏好一览（效果图的小条） -->
      <div v-if="store.step === 2" class="pref">
        <span class="pref-t">当前选择偏好</span>
        <span>已选 <b>{{ p.nature.length }}</b> 类单位性质<template v-if="p.hireType">、<b>1</b> 种用工方式</template><template v-if="p.jobTypes.length">、<b>{{ p.jobTypes.length }}</b> 类工作</template><template v-if="cityNow">、城市 <b>{{ cityNow }}</b></template></span>
      </div>
      <div class="navbar-btns">
        <button class="btn" v-if="store.step > 1" @click="go(store.step - 1)">上一步</button>
        <button class="btn btn-primary" :disabled="store.loading" @click="next">
          {{ store.loading ? '正在找…' : nextText }}
        </button>
      </div>
    </nav>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import StepBar from './components/StepBar.vue'
import Step1Profile from './components/Step1Profile.vue'
import Step2Wish from './components/Step2Wish.vue'
import Step3Match from './components/Step3Match.vue'
import Step4Apply from './components/Step4Apply.vue'
import { store, go, profilePayload, effectiveCity } from './store'
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

const nextText = computed(() => '下一步')

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
  }
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
    if (!effectiveCitySync()) return (store.err = '选一个城市，或者自己填上')

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
