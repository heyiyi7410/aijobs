<template>
  <div>
    <div class="card match-toolbar">
      <div class="card-head sr-only">
        <span class="tile" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="7" width="18" height="13" rx="2.2" />
            <path d="M8.5 7V5.2A1.2 1.2 0 0 1 9.7 4h4.6a1.2 1.2 0 0 1 1.2 1.2V7" />
            <path d="M3 12.5h18" />
          </svg>
        </span>
        <h2 class="card-title">挑选适合你的岗位</h2>
      </div>
      <p class="card-hint sr-only">
        按匹配度排序，点圆圈选中，再点「去投递」。
      </p>

      <div class="csearch">
        <label for="company-search" class="sr-only">按单位名搜索</label>
        <div class="csearch-row">
          <input
            id="company-search"
            class="input input-search"
            v-model="q"
            @keyup.enter="doSearch"
            placeholder="按单位名搜索，例如：武钢"
          />
          <button class="btn btn-primary" :disabled="store.companySearching" @click="doSearch">
            {{ store.companySearching ? '正在搜…' : '搜索' }}
          </button>
        </div>
        <div class="csearch-row" v-if="store.companyFilter" style="margin-top:.6rem;">
          <span class="relax-tip" style="flex:1 1 10rem; margin:0;">
            正在看「{{ store.companyFilter }}」的岗位：{{ cityName }} {{ localCount }} 个<template v-if="outCount">、外地 {{ outCount }} 个</template>
            <template v-if="!localCount && outCount">
              <br />这家单位在{{ cityName }}暂时没有岗位，下面这些是外地的。
            </template>
          </span>
          <button class="btn" @click="clearSearch">看全部岗位</button>
        </div>
      </div>

      <div class="chips match-filters">
        <button v-if="outCount" class="chip scope-filter" :class="{ on: store.scope === 'local' }"
          @click="setScope('local')">
          本市（{{ localCount }}）
        </button>
        <button v-if="outCount" class="chip scope-filter" :class="{ on: store.scope === 'all' }"
          @click="setScope('all')">
          外地（{{ outCount }}）
        </button>
        <button class="chip" :class="{ on: store.filter === 'all' }" @click="store.filter = 'all'">
          全部
        </button>
        <button class="chip" :class="{ on: store.filter === 'good' }" @click="store.filter = 'good'">
          很适合
        </button>
        <button class="chip" :class="{ on: store.filter === 'mail' }"
          @click="store.filter = 'mail'">
          能代投
        </button>
      </div>
      <div v-if="shown.length" class="pick-actions">
        <button @click="pickVisible" :disabled="allPicked"><span class="pick-check" aria-hidden="true"></span>全选</button>
        <button @click="clearVisible" :disabled="!shown.some(j => store.picked.includes(j.key))"><span class="pick-minus" aria-hidden="true"></span>取消全选</button>
      </div>
      <p class="match-source-note">岗位按匹配度排序，请核对报名时间与要求再投递。</p>

      <details v-if="sourceLine || relaxed || wideCount || droppedClosed" class="match-explanation">
        <summary>岗位来源与筛选说明</summary>
      <div v-if="sourceLine" class="source-bar">
        <span class="src-dot"></span>{{ sourceLine }}
      </div>

      <p v-if="relaxed && outCount" class="relax-tip info-blue">
        {{ cityName }}的岗位只找到 <b>{{ localCount }}</b> 个，所以另外给你带了
        <b>{{ outCount }}</b> 个其他城市的（都标着「外地」）。
        不想跑外地就点上面「只要{{ cityName }}」，这里会只留本市的。
      </p>
      <p v-if="wideCount" class="relax-tip info-blue">
        里面有 <b>{{ wideCount }}</b> 个公告只写了「全国」或只写了省名
        （标着「全国招聘」「本省」）—— 里面可能有{{ cityName }}的岗位，也可能在别处，
        点开看详情里的地点再决定。只想要明确写着{{ cityName }}的，
        回第 2 步把「连全国招聘和本省其他城市一起找」取消勾选。
      </p>
      <p v-if="droppedClosed" class="relax-tip info-green">
        已经替你剔掉 <b>{{ droppedClosed }}</b> 个投不了的岗位
        （报名还没开始或已截止）——下面这些都是现在能报的。
      </p>
      </details>
      <p v-if="usedFallback" class="relax-tip info-warn">
        暂时连不上招聘网站，下面先显示的是内置示例岗位，投递前请再确认一下。
      </p>

    </div>

    <div v-if="!shown.length" class="card center">
      <template v-if="store.filter === 'mail'">
        <p class="muted" style="font-size:1.1rem;">当前范围里没有留报名邮箱的岗位。</p>
        <p class="muted">
          能代投的岗位本来就少：国聘网这类平台是站内投递、外企走自家官网，
          都不留邮箱，只有国企/事业单位的招聘公告才写「报名邮箱」，
          它们又大多分散在外地。点上面的「连外地一起看」再筛一次，通常就有了。
        </p>
      </template>
      <template v-else>
        <p class="muted" style="font-size:1.1rem;">
          {{ store.companyFilter ? '没找到「' + store.companyFilter + '」这家单位的岗位。' : '这个条件下没找到能投的岗位。' }}
        </p>
        <p class="muted">
          <template v-if="droppedClosed">已剔除 <b>{{ droppedClosed }}</b> 个尚未开始或已经截止的岗位。</template>
          当前招聘来源暂未返回符合条件的可投岗位，不代表这个职位没有招聘。
          可以回到上一步换一个相关职位名称、调整城市或单位性质。找托管老师等机构岗位时，请检查是否勾选“民营”。
        </p>
      </template>
    </div>

    <!-- job-list：宽屏（≥1024px）时这个容器会排成两列，一屏能比对更多岗位 -->
    <div class="job-list">
      <div
        v-for="j in shown"
        :key="j.key"
        class="job"
        :class="{ picked: store.picked.includes(j.key) }"
        @click="toggle(store.picked, j.key)"
      >
        <button class="job-pick" :aria-label="'选择岗位：' + j.title" :aria-pressed="store.picked.includes(j.key)"
          @click.stop="toggle(store.picked, j.key)"></button>
        <div class="job-main">
          <!-- 右上角评分块（效果图：86 分/很适合） -->
          <div class="job-top">
            <div class="job-heading">
            <h3 class="job-title">
              {{ j.title }}
            </h3>
            <p class="job-company">{{ j.company }}</p>
            <div class="job-badges">
              <span class="badge" :class="badgeClass(j.nature)">{{ j.nature }}</span>
              <span v-if="j.hire_type === '劳务派遣'" class="badge badge-warn">劳务派遣</span>
              <span v-else class="badge badge-other">直签</span>
              <span v-if="j.recruit_type" class="badge badge-recruit">{{ j.recruit_type }}</span>
              <!-- 公告里留了报名邮箱的岗位，第 4 步能替你一键代投；
                   没这个徽章的只能自己去它的招聘网站投 —— 提前让用户看得见 -->
              <span v-if="j.hr_email" class="badge badge-mail" :title="'报名邮箱：' + j.hr_email">可代投</span>
              <!-- 民企/基层岗位大多不留邮箱，只留个手机号。这条「可电话联系」
                   是给他们的入口：点开详情直接拨号，不用自己从公告里抄号码 -->
              <span v-if="j.hr_phone && !j.hr_email" class="badge badge-phone" :title="'联系电话：' + j.hr_phone">可电话联系</span>
              <!-- 岗位在哪个范围：本地不标；「全国招聘」「本省」单独标出来，
                   让用户一眼知道这条可能不在本市（公告只写「全国」或省名） -->
              <span v-if="j.city_scope === 'national'" class="badge badge-other">全国招聘</span>
              <span v-else-if="j.city_scope === 'province'" class="badge badge-other">本省</span>
              <span v-if="j.nearby" class="badge badge-other">外地</span>
            </div>
            </div>
            <div class="job-score" :class="scoreClass(j.score)"
              :aria-label="'适合程度 ' + j.score + ' 分，' + j.level">
              <b>{{ j.score }}<i>分</i></b>
              <span class="lvl">{{ j.level }}</span>
            </div>
          </div>
          <!-- 适合原因：浅蓝面板（效果图）；前面的勾由 SVG 画（禁 emoji/字符当图标） -->
          <div v-if="j.reasons && j.reasons.length" class="job-why">
            <p class="job-why-t">适合原因</p>
            <p v-for="r in j.reasons" :key="r" class="reason">{{ r }}</p>
          </div>
          <p v-for="t in j.tips" :key="t" class="tip-line">{{ t }}</p>
          <div class="job-facts">
            <p v-if="j.apply_start || j.deadline"><span>报名时间</span>{{ j.apply_start || '已开始' }} — {{ j.deadline || '见公告' }}</p>
            <p v-if="j.hr_email"><span>投递邮箱</span>{{ j.hr_email }}</p>
            <p v-if="j.headcount"><span>招聘人数</span>{{ j.headcount }} 人</p>
            <p v-if="j.hr_phone"><span>联系电话</span>{{ j.hr_phone }}</p>
            <p><span>工作地点</span>{{ j.city }}{{ j.district }}</p>
            <p><span>薪资待遇</span><b>{{ j.salary_text }}</b></p>
            <p><span>岗位要求</span>{{ j.exp }} / {{ j.edu }}</p>
          </div>

          <!-- 详情：展开看岗位职责/公告摘要、报名时间、原文链接 -->
          <button class="detail-btn" @click.stop="toggleDetail(j)">
            {{ openMap[j.key] ? '收起详情' : '详情' }}
          </button>
          <div v-if="openMap[j.key]" class="job-detail" @click.stop>
            <p v-if="j.desc" class="detail-block">
              <span class="k">岗位职责 / 公告摘要：</span><br />{{ j.desc }}
            </p>
            <p v-if="j.apply_start || j.deadline" class="detail-block">
              <span class="k">报名时间：</span>{{ j.apply_start || '已开始' }} ~ {{ j.deadline || '见公告' }}
            </p>
            <p v-if="j.headcount" class="detail-block">
              <span class="k">招聘人数：</span>{{ j.headcount }} 人
            </p>
            <p v-if="j.hr_email" class="detail-block">
              <span class="k">报名邮箱：</span><b>{{ j.hr_email }}</b>
              <span class="mail-hint">（第 4 步可一键代投）</span>
            </p>
            <p v-if="j.hr_phone" class="detail-block">
              <span class="k">联系电话：</span><b>{{ j.hr_phone }}</b>
              <a class="tel-link" :href="'tel:' + j.hr_phone">拨号</a>
              <span class="mail-hint">（公告里留的招聘电话，打之前先把岗位名说清楚）</span>
            </p>
            <p v-if="j.url" class="detail-block">
              <span class="k">原公告：</span>
              <a :href="j.url" target="_blank" rel="noopener">打开原文查看完整要求</a>
            </p>
            <p v-if="!j.desc && !j.url" class="detail-block none">
              这个岗位暂时没有更多详情，报名方式以原公告为准。
            </p>
          </div>
        </div>

        <!-- 直达链接：不用先展开详情，点一下就到对方的报名/公告页。
             卡片整块是「选中」，所以这里要 @click.stop，不然点链接会顺手把岗位选上。 -->
        <a
          v-if="jobLink(j)"
          class="job-go"
          :href="jobLink(j)"
          target="_blank"
          rel="noopener noreferrer"
          :title="'在新窗口打开：' + jobLink(j)"
          @click.stop
        >
          直达<small>{{ linkHost(j) }}</small>
        </a>
      </div>
    </div>

    <!-- 拉到底「手动抓取更多」：列表读的是服务端缓存（秒回），新出的公告要等
         后台定时刷新才进得来。用户拉到底还想看，就让他下拉（或点这里）
         主动实时抓一次，抓回来立即合并进列表。 -->
    <div v-if="shown.length && !store.companyFilter">
      <div v-if="pulling" class="pull-more loading">
        <span class="pull-spin" aria-hidden="true"></span> 正在手动抓取最新职位…（要跑好几家网站，约 10~30 秒，请稍等）
      </div>
      <div v-else-if="showHint" class="pull-more" @click="loadMore"
        role="button" tabindex="0"
        @keydown.enter.prevent="loadMore" @keydown.space.prevent="loadMore">
        下拉获取更多职位 · 点这里也可以手动抓一次
      </div>
      <div v-else-if="pullMsg" class="pull-more done">{{ pullMsg }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import { store, toggle } from '../store'
import { api } from '../api'

/* 按单位名搜索：很多人是奔着一家单位去的（「我就想进武钢」），
   与其让他在通用岗位里翻，不如让他直接敲单位名字。 */
const q = ref('')

const cityOf = computed(() => {
  const p = store.profile
  if (p.city === '不限城市') return ''
  return (p.cityOther || '').trim() || p.city || ''
})

async function doSearch() {
  const name = (q.value || '').trim()
  if (!name) {
    store.err = '先填一下单位名字，比如「武钢」'
    return
  }
  store.companySearching = true
  store.err = ''
  try {
    const r = await api.searchCompany(name, store.profile, store.profileId, cityOf.value)
    if (!r.ok) {
      store.err = r.msg || '没搜到，换个名字试试'
    } else if (!r.count) {
      store.err = '没找到「' + name + '」的岗位。名字可以只写有特色的两三个字再试试'
    } else {
      store.jobs = r.jobs
      store.companyFilter = name
      // 换了一批岗位，之前勾的不算数了
      store.picked = []
      store.filter = 'all'
      // 本市有就先看本市（不稀里糊涂把外地的投出去）；本市一个都没有才连外地一起看
      store.scope = r.jobs.filter(j => !j.nearby).length ? 'local' : 'all'
    }
  } catch (e) {
    store.err = '连不上服务，请重试一下'
  }
  store.companySearching = false
}

function clearSearch() {
  store.jobs = store.allJobs.slice()
  store.companyFilter = ''
  store.picked = []
  store.scope = 'local'
  q.value = ''
}

const shown = computed(() => {
  if (store.filter === 'good') return base.value.filter(j => j.score >= 70)
  // 「只看能代投」：用户 2026-09-21 反馈「邮箱源太少，找了半天没找到可以投的」。
  // 带邮箱的岗位本来就只占一小部分（平台型来源压根不发邮箱），一条条翻太累，
  // 给个开关直接只剩下能替他发邮件的那批。
  if (store.filter === 'mail') return base.value.filter(j => (j.hr_email || '').trim())
  return base.value
})


/* —— 「全部选择」——
   一次挑十几二十个岗位挨个点圆圈太累，这里一把全选。
   只选**屏幕上现在能看到的**那些（跟着「本市/外地」「只看很适合」走）——
   和 setScope 里那条规矩一致：看不见的不该被投出去。
   已经全选中时按钮变成「取消全选」，点错了能一键撤回。 */
const allPicked = computed(() =>
  shown.value.length > 0 && shown.value.every(j => store.picked.includes(j.key))
)
function pickVisible() {
  store.picked = [...new Set([...store.picked, ...shown.value.map(j => j.key)])]
}
function clearVisible() {
  const keys = new Set(shown.value.map(j => j.key))
  store.picked = store.picked.filter(key => !keys.has(key))
}

function togglePickAll() {
  const keys = shown.value.map(j => j.key)
  if (allPicked.value) {
    const drop = new Set(keys)
    store.picked = store.picked.filter(k => !drop.has(k))
  } else {
    const set = new Set(store.picked)
    keys.forEach(k => set.add(k))
    store.picked = Array.from(set)
  }
}

/** 岗位的直达链接：优先对方招聘/报名页，退到原公告页。两条数据路径都可能是空的。 */
function jobLink(j) {
  return (j && (j.apply_url || j.url)) || ''
}

/** 按钮上显示来源域名（`iguopin.com`），比一长串路径有用——用户能认出是哪个网站。 */
function linkHost(j) {
  const u = jobLink(j)
  if (!u) return ''
  try {
    return new URL(u).hostname.replace(/^www\./, '')
  } catch (e) {
    return ''
  }
}

const relaxed = computed(() => !!(store.meta && store.meta.relaxed))
const usedFallback = computed(() => !!(store.meta && store.meta.fallback))
// 后端把「报名没开始/已截止」的岗位剔掉了几条——告诉用户一声，
// 不然他会奇怪为什么搜出来的比别处少（少的那几条本来也投不了）
const droppedClosed = computed(() => (store.meta && store.meta.dropped_closed) || 0)

// 本市 / 外地。后端在城市岗位太少时会补一批全国的，那些标了 nearby。
const localCount = computed(() => store.jobs.filter(j => !j.nearby).length)
const outCount = computed(() => store.jobs.filter(j => j.nearby).length)
// 公告只写了「全国」或省名的那批：算在本地里（用户默认勾了要），
// 但地点没写死，得提醒一句让他自己看详情。
const wideCount = computed(() =>
  store.jobs.filter(j => j.city_scope === 'national' || j.city_scope === 'province').length
)
const cityName = computed(() => (store.meta && store.meta.city) || store.profile.city || '本市')

/** 当前看的是哪些岗位（先在「本市 / 连外地」里筛，再按分数筛）。 */
const base = computed(() =>
  store.scope === 'local' ? store.jobs.filter(j => !j.nearby) : store.jobs
)

/** 切到「只要本市」时，把已经勾上的外地岗位去掉 —— 屏幕上看不见的不该被投出去。 */
function setScope(s) {
  store.scope = s
  if (s !== 'local') return
  store.picked = store.picked.filter(k => {
    const j = store.jobs.find(x => x.key === k)
    return !j || !j.nearby
  })
}

// 告诉用户岗位是从哪来的，而不是让他对着一堆结果发懵
const sourceLine = computed(() => {
  const m = store.meta || {}
  const srcs = (m.sources || []).filter(s => s.ok && s.count > 0)
  if (!srcs.length) return ''
  const names = srcs.map(s => s.label.replace(/（.*?）/, '')).join('、')
  return '岗位来自 ' + names + '，实时抓取'
})

function badgeClass(n) {
  if (n === '央企') return 'badge-central'
  if (n === '国企') return 'badge-state'
  if (n === '外企') return 'badge-foreign'
  if (n === '事业单位') return 'badge-institution'
  if (n === '公务员') return 'badge-civil'
  return 'badge-other'
}
function scoreClass(s) {
  if (s >= 75) return 'good'
  if (s >= 55) return 'mid'
  return 'low'
}

/* —— 岗位「详情」展开 —— */
const openMap = reactive({})
function toggleDetail(j) {
  openMap[j.key] = !openMap[j.key]
}

/* —— 拉到底「手动抓取更多」——
   正常浏览时岗位读的是服务端缓存（毫秒级），新出的公告要等后台 30 分钟
   定时刷新才进得来。用户滑到列表最底下、又继续往下拉，就触发一次实时
   抓取（force=true），抓回来把新岗位合并进列表。
   触发节奏：滚到底 → 显示「下拉获取更多职位」提示 → 继续下拉才真抓；
   抓完或滚离底部后提示收起，要重新滚到底再拉一次，避免误触连抓。 */
const pulling = ref(false)
const showHint = ref(false)
const pullMsg = ref('')
let latched = false

function onScroll() {
  const doc = document.documentElement
  const atEnd = window.innerHeight + window.scrollY >= doc.scrollHeight - 80
  if (atEnd && store.jobs.length && !store.companyFilter && !pulling.value) {
    if (!latched) {
      latched = true
      showHint.value = true
    }
  } else if (!atEnd) {
    latched = false
    showHint.value = false
  }
}

function onPull(e) {
  if (!showHint.value || pulling.value) return
  const down = (e.type === 'wheel' && e.deltaY > 20) || e.type === 'touchmove'
  if (down) loadMore()
}

async function loadMore() {
  if (pulling.value) return
  pulling.value = true
  showHint.value = false
  pullMsg.value = ''
  store.err = ''
  try {
    const r = await api.match({ ...store.profile, profile_id: store.profileId },
                              true, store.wide)
    if (!r.ok) {
      store.err = '抓取失败，请重试'
    } else {
      const old = new Set(store.jobs.map(j => j.key))
      const fresh = (r.jobs || []).filter(j => !old.has(j.key))
      if (fresh.length) {
        // 新岗位按适合程度排进去；已勾选的不动
        const merged = store.jobs.concat(fresh)
          .sort((a, b) => (b.score || 0) - (a.score || 0))
        store.jobs = merged
        store.allJobs = merged.slice()
      }
      store.meta = r.meta || store.meta
      pullMsg.value =
        r.meta && r.meta.manual === 'throttled'
          ? '刚才已经手动抓过一次了，先看看现有的，过一分钟再拉可以再抓'
          : fresh.length
            ? '手动抓取完成：新增 ' + fresh.length + ' 个岗位（共 ' + store.jobs.length + ' 个）'
            : '手动抓取完成：这次没有抓到新岗位，过几分钟网站更新后再试试'
    }
  } catch (e) {
    store.err = '连不上服务，请重试一下'
  }
  pulling.value = false
  latched = false            // 抓完要重新滚到底再拉，才会触发下一次
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('wheel', onPull, { passive: true })
  window.addEventListener('touchmove', onPull, { passive: true })
  // 进来时如果岗位少、一屏就到底，没有滚动事件，主动查一次
  nextTick(() => setTimeout(onScroll, 300))
})
onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('wheel', onPull)
  window.removeEventListener('touchmove', onPull)
})
</script>

<style scoped>
.source-bar {
  display: flex;
  align-items: center;
  gap: .45rem;
  font-size: .88rem;
  color: var(--accent);
  background: var(--accent-1);
  border-radius: .6rem;
  padding: .55rem .7rem;
  margin-bottom: .8rem;
}
.src-dot {
  width: .5rem;
  height: .5rem;
  border-radius: 50%;
  background: var(--accent);
  flex: none;
}
.csearch {
  background: var(--surface-2);
  border-radius: .7rem;
  padding: .8rem .8rem .2rem;
  margin-bottom: 1rem;
}
.csearch-label {
  display: block;
  font-weight: 700;
  margin-bottom: .5rem;
}
.csearch-row {
  display: flex;
  gap: .5rem;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: .6rem;
}
.csearch-row .input {
  flex: 1 1 11rem;
}
.relax-tip {
  font-size: .92rem;
  color: var(--warn);
  background: var(--warn-1);
  border-radius: .6rem;
  padding: .6rem .75rem;
  margin-bottom: .8rem;
  line-height: 1.55;
}
.pull-more {
  text-align: center;
  padding: .9rem .7rem;
  border-radius: .7rem;
  background: var(--primary-1);
  color: var(--primary-7);
  font-weight: 700;
  cursor: pointer;
  user-select: none;
  margin: 1rem 0 2rem;
}
.pull-more.loading {
  background: var(--warn-1);
  color: var(--warn);
  cursor: default;
}
.pull-more.done {
  background: var(--accent-1);
  color: var(--accent);
  cursor: default;
  font-weight: 600;
}
/* 转圈用 CSS 画的圆环，不用 ⟳ 这类符号字符 */
.pull-spin {
  display: inline-block;
  width: .95rem;
  height: .95rem;
  margin-right: .1rem;
  vertical-align: -0.15rem;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: pullspin .8s linear infinite;
}
@keyframes pullspin {
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  /* 加载圈是「机器正在干活」的唯一信号，减少动态效果下也保留，但放慢一半 ——
     系统偏好要的是「更少更轻」，不是「全砍」。
     必须写在组件内并且带 !important：Vue 会把 scoped 块里的 keyframes 名字
     加 hash 重命名，全局样式表里引用 pullspin 是对不上的；
     而全局那条 animation-duration:1ms !important 会把它压掉，所以这里也要 !important。 */
  .pull-spin { animation: pullspin 1.6s linear infinite !important; }
}
.detail-btn {
  margin-top: .55rem;
  padding: .3rem .8rem;
  border: 1px solid var(--line-2);
  border-radius: .5rem;
  background: var(--surface);
  color: var(--primary-7);
  font-size: .88rem;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-pop) var(--ease-out),
    transform var(--t-press) var(--ease-out);
}
.detail-btn:active {
  background: var(--primary-1);
  transform: scale(0.97);
}
.job-detail {
  background: var(--surface-2);
  border-radius: .6rem;
  padding: .7rem .8rem;
  margin-top: .5rem;
  max-height: 300px;
  overflow-y: auto;
  font-size: .92rem;
  line-height: 1.65;
}
.detail-block { margin: 0 0 .55rem; }
.detail-block:last-child { margin-bottom: 0; }
.detail-block a { color: var(--primary-7); word-break: break-all; }
.detail-block .mail-hint { color: var(--warn); font-size: .88rem; }
.detail-block.none { color: var(--ink-3); }

/* —— 卡片右侧的「直达」链接 ——
   以前要展开详情才看得到原文入口，现在链接直接摆在卡片上，
   点一下就新窗口打开。域名放小字里，是为了让用户一眼认出要去哪个网站。 */
.job-go {
  flex: none;
  align-self: flex-start;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: .1rem;
  max-width: 9rem;
  padding: .48rem .75rem;
  border: 1px solid var(--line-2);
  border-radius: .6rem;
  background: var(--primary-1);
  color: var(--primary-7);
  font-size: .95rem;
  font-weight: 700;
  line-height: 1.25;
  text-align: center;
  text-decoration: none;
  white-space: nowrap;
  transition: background var(--t-pop) var(--ease-out),
    border-color var(--t-pop) var(--ease-out), color var(--t-pop) var(--ease-out),
    transform var(--t-press) var(--ease-out);
}
.job-go small {
  font-size: .72rem;
  font-weight: 500;
  color: var(--ink-3);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color var(--t-pop) var(--ease-out);
}
/* 「直达」是个链接控件，触屏点按下要有反馈；悬停反色只在能悬停的设备上给 */
.job-go:active { transform: scale(0.97); }
@media (hover: hover) and (pointer: fine) {
  .job-go:hover {
    background: var(--primary);
    border-color: var(--primary);
    color: #fff;
  }
  .job-go:hover small { color: rgba(255, 255, 255, .85); }
}

/* 手机屏放不下「内容 + 右侧链接」，让链接整行落到卡片底部（更好点按） */
@media (max-width: 480px) {
  .csearch-row .btn {
    width: 100%;
  }
  .job { flex-wrap: wrap; }
  .job-go {
    flex: 1 1 100%;
    flex-direction: row;
    justify-content: center;
    gap: .35rem;
    max-width: none;
    margin-top: .2rem;
    padding: .6rem .75rem;
  }
}
</style>
