<template>
  <div>
    <!-- ============ 还没开始 ============ -->
    <div v-if="!taskId && !filling" class="card">
      <h2 class="card-title">帮我自动填（测试版）</h2>
      <p class="card-hint">
        我会开一个浏览器，替你注册账号、再把简历一项项填进招聘网站，
        填完停在「提交」前面让你看一眼。
      </p>

      <div class="af-note">
        <b>有三件事必须你亲自来：</b><br>
        手机短信验证码、图形验证码、最后那一下「提交」。<br>
        <span class="muted">
          这三处是网站专门用来确认「对面是真人还是程序」的。绕过去的代价不是多按两下，
          是账号被锁死——你在那家单位就再也投不了了。
        </span>
      </div>

      <div class="af-note af-note-warn">
        <b>还有几段，只有你本人能补：</b><br>
        招聘网站会把「资格证书」「家庭成员」「亲属在系统单位任职」这类段标成必填，
        内容要么是家人的隐私，要么是只有你本人能确认的事实——我填不了，也不该替你编。
        <br>
        <span class="muted">
          真遇到时我会停下来，把这些段逐条列清楚：<b>为什么不能替你补</b>、
          <b>怎么补最快</b>（很多段站点自带「无XX」一键声明）。
          你补完点一下「我补齐了，继续投递」，我接着往下走。
        </span>
      </div>

      <label class="af-label">投到哪里</label>

      <div v-if="!customUrl.trim()">
        <p class="af-target-line" style="margin:.2rem 0 0;">
          {{ job.company }} · {{ job.title }}
        </p>
        <p class="muted" style="margin:.4rem 0 0;">
          直接去这家单位自己的招聘页投，投的就是你选的这个岗位。
        </p>
      </div>

      <div class="af-custom">
        <label class="af-flabel">或者用别处的链接（粘贴任意投递页）</label>
        <input
          class="af-input"
          v-model="customUrl"
          placeholder="把招聘网站的投递页链接粘进来，例如 https://jobs.xxx.com/apply/123"
        >
        <p class="muted" style="margin:.4rem 0 0;" v-if="!customUrl.trim()">
          不限于这里的岗位——任何要填表的招聘页，粘进来我就打开它替你填。
        </p>
        <p class="af-warn" style="margin:.4rem 0 0;" v-else-if="!urlValid">
          链接要以 http:// 或 https:// 开头才有效。
        </p>
        <p class="muted" style="margin:.4rem 0 0;" v-else>
          我会打开这个链接，替你把简历一项项填进去。
        </p>
      </div>

      <label class="af-chk">
        <input type="checkbox" v-model="showBrowser">
        打开浏览器窗口（能看见它在动，慢一点但心里有底）
      </label>

      <label class="af-chk">
        <input type="checkbox" v-model="refill">
        重填一遍（连已经填好的段也重新填，保存即覆盖）
      </label>
      <p class="muted af-chk-note" v-if="refill">
        站点上已有内容的段会打开「编辑」重填一遍再保存——<b>原来那段内容会被档案里的值覆盖</b>。
        适合反复试跑流程；只想补缺的段就别勾。
      </p>

      <button class="btn btn-primary btn-big" style="margin-top:1rem;" @click="start">
        开始自动填
      </button>
      <p v-if="err" class="af-err">{{ err }}</p>
    </div>

    <!-- ============ 先补齐资料：现在填一次，待会儿就不用一项项手打 ============ -->
    <div v-else-if="!taskId && filling" class="card">
      <h2 class="card-title">这几项还空着，补一下</h2>
      <p class="card-hint">
        招聘表都要问这些。现在补上，自动填就能一次填到位；
        不补的话，这几项会空在那里，最后还得你一项项手动敲。
      </p>

      <div v-for="f in fillFields" :key="f.k" class="af-field">
        <label class="af-flabel">{{ f.label }}</label>
        <input class="af-input" v-model="store.profile[f.k]" :placeholder="f.ph" />
      </div>

      <button class="btn btn-primary btn-big" style="margin-top:1rem;" @click="startNow">
        都填好了，开始自动填
      </button>
      <button class="btn btn-big" style="margin-top:.6rem;" @click="skipGaps">
        这几项先空着，照样投
      </button>
      <p class="muted" style="margin:.6rem 0 0; font-size:.92rem;">
        填错也没关系，提交前还有一次让你核对的机会。
      </p>
    </div>

    <!-- ============ 进行中 / 要人 / 完了 ============ -->
    <div v-else class="card">
      <h2 class="card-title">{{ statusText }}</h2>
      <p class="card-hint" v-if="t.status === 'running'">
        正在进行，别关掉这个页面。
      </p>

      <!-- 该你出手了 -->
      <div v-if="t.ask" class="af-ask">
        <p class="af-ask-title">{{ t.ask.title }}</p>
        <p class="af-ask-msg">{{ t.ask.msg }}</p>

        <!-- 站点标红的必填项：为什么不能替你补 + 怎么补最快。
             招聘网站只标红不解释，用户看到红字只会以为程序坏了。
             段和字段都是**按岗位模板现读**的（不同岗位不一样），
             所以这里只是把后端读到的清单渲染出来，前端不写死任何段名。 -->
        <div v-if="t.ask.items && t.ask.items.length" class="af-blockers">
          <div v-for="(it, i) in t.ask.items" :key="it.key || i" class="af-blocker">
            <p class="af-blocker-name">{{ it.label }}</p>
            <p v-if="it.fields && it.fields.length" class="af-blocker-line">
              <span class="af-blocker-tag af-blocker-tag-need">这一段要填</span>
              <span>{{ it.fields.join('、') }}</span>
            </p>
            <p class="af-blocker-line">
              <span class="af-blocker-tag">为什么不能替你补</span>
              <span>{{ it.why }}</span>
            </p>
            <p class="af-blocker-line">
              <span class="af-blocker-tag af-blocker-tag-how">怎么补最快</span>
              <span>{{ it.how }}</span>
            </p>
            <!-- 站点给了一键「无XX」声明的段：让用户当面确认，确认完我就替他点。
                 绝不替他默认「无」——那是拿他的名义下事实声明。 -->
            <button
              v-if="it.declare"
              class="btn af-declare"
              @click="answer('__declare__|' + it.label)"
            >我确实没有，帮我点「{{ it.declare }}」</button>
          </div>
          <p class="af-blockers-tail">
            以上 {{ t.ask.items.length }} 段在浏览器里补完并保存 →
            回到这个页面点下面的按钮，我接着往下投。
          </p>
        </div>

        <template v-if="t.ask.type === 'confirm' || t.ask.type === 'promise'">
          <div style="display:flex; gap:.8rem; margin-top:.8rem;">
            <button class="btn btn-primary" style="flex:1;" @click="answer('yes')">
              {{ yesText }}
            </button>
            <button class="btn" style="flex:1;" @click="answer('no')">
              {{ noText }}
            </button>
          </div>
        </template>

        <template v-else>
          <input
            class="af-input"
            :type="t.ask.input_type || 'text'"
            :placeholder="t.ask.placeholder || ''"
            v-model="input"
            @keyup.enter="answer(input)"
          >
          <button class="btn btn-primary" style="margin-top:.7rem;" @click="answer(input)">
            填好了，继续
          </button>
        </template>
      </div>

      <!-- 最后一步长什么样 -->
      <div v-if="lastShot" style="margin-top:1rem;">
        <p class="muted" style="margin:0 0 .4rem;">{{ lastShot.label }}</p>
        <a :href="shotUrl(lastShot.name)" target="_blank">
          <img :src="shotUrl(lastShot.name)" class="af-shot" alt="当前页面">
        </a>
        <p class="muted" style="margin:.3rem 0 0;">点图片可以看大图。</p>
      </div>

      <!-- 过程 -->
      <div style="margin-top:1.1rem;">
        <div v-for="(s, i) in t.steps" :key="i" class="af-step">
          <span class="af-dot" :class="s.ok ? 'af-dot-ok' : 'af-dot-no'"></span>
          <span>
            {{ s.label }}
            <span class="muted" v-if="s.note">　{{ s.note }}</span>
          </span>
        </div>
      </div>

      <!-- 账目 -->
      <div v-if="t.filled.length" style="margin-top:1.1rem;">
        <p class="af-sub">它替你填好的（{{ t.filled.length }} 项）</p>
        <div v-for="(f, i) in t.filled" :key="i" class="result-item">
          <span>{{ f.label }}</span><span class="ok">{{ f.value }}</span>
        </div>
      </div>

      <div v-if="t.missing.length" style="margin-top:1.1rem;">
        <p class="af-sub">要你自己来的（{{ t.missing.length }} 项）</p>
        <div v-for="(m, i) in t.missing" :key="i" class="result-item">
          <span>{{ m.label }}</span><span class="muted">{{ m.why }}</span>
        </div>
      </div>

      <div v-if="t.receipt" class="done-box" style="margin-top:1rem;">
        <p class="muted">回执编号</p>
        <p class="big">{{ t.receipt }}</p>
      </div>

      <p v-if="t.status === 'error'" class="af-err">
        出错了：{{ (t.error || '').split('\n').slice(-3).join(' ') }}
      </p>

      <button v-if="t.status === 'done' || t.status === 'error'"
              class="btn btn-big" style="margin-top:1rem;" @click="reset">
        再来一次
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { store, profilePayload } from '../store'
import { api } from '../api'

const props = defineProps({ job: { type: Object, default: () => ({}) } })
const emit = defineEmits(['done'])

const showBrowser = ref(false)
const refill = ref(false)
const taskId = ref('')
const input = ref('')
const err = ref('')
const t = ref({ steps: [], shots: [], filled: [], missing: [] })
const filling = ref(false)   // 正在补资料
const skipped = ref(false)   // 用户选了「先空着，照样投」
const starting = ref(false)  // 正在提交启动请求（挡住重复点击）

// 招聘表必问的几项。空着就会在结果里变成「要你自己来的（N 项）」——
// 而那张列表以前只是给人看、没有地方填，等于把人卡死在最后一步。
// 所以开跑前先把这几项的入口摆出来让他补。
const BASE_FIELDS = [
  { k: 'name', label: '姓名', ph: '例如：张三' },
  { k: 'phone', label: '手机号', ph: '11 位手机号' },
  { k: 'email', label: '邮箱', ph: '例如：123456@qq.com' },
  { k: 'gender', label: '性别', ph: '男 或 女' },
  { k: 'birth', label: '出生年月', ph: '例如：1999-05' },
  { k: 'school', label: '毕业学校', ph: '例如：武汉职业技术学院' },
  { k: 'major', label: '学的专业', ph: '例如：电气自动化技术' },
  { k: 'graduation', label: '毕业时间', ph: '例如：2021-06' }
]

const gaps = computed(() =>
  BASE_FIELDS.filter(f => !(store.profile[f.k] || '').trim())
)

// ⚠️ 补填的字段清单必须在进入补填页那一刻**快照**下来，不能实时过滤：
// 输入框就绑在 profile 上，实时过滤会导致「打一个字 → 字段非空 → 整行消失」，
// 剩下的字打不进去，等于根本没法补。清单只在点「开始自动填」时算一次，
// 之后打字、清空都不影响这一页显示哪些行，直到点按钮走人。
const gapKeys = ref([])

const fillFields = computed(() =>
  BASE_FIELDS.filter(f => gapKeys.value.includes(f.k))
)

// 投递目标：用户粘贴的自定义链接优先；否则用当前岗位自己的投递页
const customUrl = ref('')
const urlValid = computed(() => {
  const c = (customUrl.value || '').trim()
  if (!c) return true
  return /^https?:\/\/.+/i.test(c)
})
const targetUrl = computed(() => {
  const c = (customUrl.value || '').trim()
  if (c) return c
  const j = props.job || {}
  return j.apply_url || j.url || ''
})

const lastShot = computed(() => {
  const s = t.value.shots
  return s && s.length ? s[s.length - 1] : null
})

const statusText = computed(() => {
  const s = t.value.status
  if (s === 'need_human') return '需要你出手一下'
  if (s === 'done') return '办完了'
  if (s === 'error') return '出问题了'
  return '正在办'
})

// 需要本人补东西时，按钮得说清「补完点这里我接着投」，
// 而不是含糊的「确认提交」——用户根本不知道点下去会发生什么。
const yesText = computed(() => {
  const a = t.value.ask || {}
  if (a.items && a.items.length) return '我补齐了，继续投递'
  if (a.type === 'promise') return '我同意，帮我勾上'
  return '确认提交'
})

const noText = computed(() => {
  const a = t.value.ask || {}
  if (a.items && a.items.length) return '我还没补好'
  if (a.type === 'promise') return '先不勾'
  return '先不提交'
})

const shotUrl = name => '/api/autofill/shot/' + name

let timer = null
// 轮询序号：==0 表示当前没有在飞的 poll 请求。
// 这里会被两处并发触发（下面的定时器 + answer() 回包后的 setTimeout），
// 晚发先到时会把新状态覆盖成旧状态，界面就会来回跳。
let pollSeq = 0

function startPoll() {
  if (timer) clearInterval(timer)
  timer = setInterval(poll, 1200)
}

async function start() {
  err.value = ''
  if (customUrl.value.trim() && !urlValid.value) {
    err.value = '链接要以 http:// 或 https:// 开头'
    return
  }
  if (!targetUrl.value) { err.value = '这个岗位没有可投的页面'; return }
  // 有空的就先让他补——这是自动投之前唯一一次能顺畅补资料的机会。
  // 进补填页前把缺的字段快照下来（见 fillFields 上的注释）。
  if (gaps.value.length && !skipped.value) {
    gapKeys.value = gaps.value.map(f => f.k)
    filling.value = true
    return
  }
  await doStart()
}

async function startNow() {
  filling.value = false
  await doStart()
}

function skipGaps() {
  skipped.value = true
  filling.value = false
  doStart()
}

async function doStart() {
  if (starting.value) return
  starting.value = true
  try {
    // 补的资料必须落库：自动投优先读数据库里的那份，不存等于白补。
    // 必须用 profilePayload() 而不是裸 store.profile —— 后者带着空的
    // experiences:[]，后端的语义是「带了就覆盖」，会把用户上次填的实习/社团
    // 经历整段清空，而国聘补站内简历恰恰要用这些经历。
    const r = await api.saveProfile(profilePayload())
    if (r && r.ok && r.profile_id) store.profileId = r.profile_id
  } catch (e) { /* 存不上也照投，前端这份 profile 会一起传过去 */ }

  let r
  try {
    r = await api.autofillStart({
      profile_id: store.profileId,
      profile: store.profile,
      job: props.job,
      url: targetUrl.value,
      show_browser: showBrowser.value,
      refill: refill.value
    })
  } catch (e) {
    starting.value = false
    err.value = '连不上服务，请检查程序是不是还在运行'
    return
  }
  starting.value = false
  if (!r.ok) { err.value = r.msg || '启动失败'; return }
  taskId.value = r.task_id
  poll()
  startPoll()
}

async function poll() {
  if (!taskId.value) return
  const s = ++pollSeq
  let r
  try {
    r = await api.autofillTask(taskId.value)
  } catch (e) {
    return   // 网络抖动不该中断轮询，下一拍自然会重试
  }
  if (s !== pollSeq) return          // 有更新的回包了，丢弃这次的旧结果
  if (!r.ok) return
  t.value = r.task
  if (['done', 'error'].includes(r.task.status) && timer) {
    clearInterval(timer)
    timer = null
  }
  if (r.task.status === 'done' && r.task.receipt && !notified) {
    notified = true
    emit('done', r.task)
  }
}

let notified = false

async function answer(value) {
  if (!taskId.value) return
  await api.autofillAnswer(taskId.value, value)
  input.value = ''
  setTimeout(poll, 500)
}

function reset() {
  if (timer) clearInterval(timer)
  timer = null
  pollSeq++                    // 让还在飞的回包作废，别回头覆盖刚重置的界面
  taskId.value = ''
  starting.value = false
  t.value = { steps: [], shots: [], filled: [], missing: [] }
  notified = false
  input.value = ''
  filling.value = false
  gapKeys.value = []
  skipped.value = false
  customUrl.value = ''
}

onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.af-note {
  background: var(--surface-2);
  border-left: 4px solid var(--primary);
  border-radius: .6rem;
  padding: .9rem 1rem;
  margin: .9rem 0;
  font-size: .98rem;
}
.af-note-warn {
  background: var(--warn-1);
  border-left-color: var(--warn);
}
/* 站点标红段的清单：为什么不能替你补 + 怎么补最快 */
.af-blockers {
  background: var(--surface);
  border: 1px solid var(--danger-1);
  border-radius: .7rem;
  padding: .8rem .9rem;
  margin-top: .9rem;
}
.af-blocker { padding: .55rem 0; border-top: 1px dashed var(--danger-1); }
.af-blocker:first-of-type { border-top: 0; padding-top: .1rem; }
.af-blocker-name { font-weight: 700; margin: 0 0 .3rem; font-size: 1.02rem; }
.af-blocker-line {
  margin: .2rem 0; font-size: .93rem; color: var(--ink-2); line-height: 1.55;
}
.af-blocker-tag {
  display: inline-block; margin-right: .5rem; padding: .05rem .4rem;
  font-size: .74rem; font-weight: 700; color: var(--danger);
  background: var(--danger-1); border-radius: .3rem;
}
.af-blocker-tag-how { color: var(--accent); background: var(--accent-1); }
.af-blocker-tag-need { color: var(--primary); background: var(--primary-1); }
/* 一键「无…」声明：只有用户当面点了才发出去（是事实声明，不能替他默认） */
.af-declare {
  margin-top: .45rem; font-size: .88rem; padding: .3rem .7rem;
  border: 1px solid var(--line-2); background: var(--surface-2); color: var(--ink);
  border-radius: .45rem; cursor: pointer;
  transition: background var(--t-pop) var(--ease-out),
    border-color var(--t-pop) var(--ease-out), transform var(--t-press) var(--ease-out);
}
.af-declare:active { transform: scale(0.97); }
@media (hover: hover) and (pointer: fine) {
  .af-declare:hover { background: var(--surface-2); border-color: var(--ink-3); }
}
.af-blockers-tail {
  margin: .6rem 0 0; font-size: .92rem; font-weight: 700; color: var(--warn);
}
.af-label { display: block; font-weight: 700; margin: 1rem 0 .4rem; }
.af-select {
  width: 100%; box-sizing: border-box; padding: .8rem;
  border: 1px solid var(--line-2); border-radius: .6rem;
  font-size: 1rem; font-family: inherit; background: var(--surface); color: inherit;
}
.af-chk {
  display: block; margin-top: 1rem; font-size: 1rem; color: var(--ink-2);
}
.af-chk-note {
  margin: .4rem 0 0; font-size: .9rem; line-height: 1.55;
  background: var(--warn-1); border: 1px solid var(--warn-1); border-radius: .5rem;
  padding: .55rem .7rem; color: var(--warn);
}
.af-warn { color: var(--danger); font-size: .92rem; }
.af-custom { margin-top: 1rem; border-top: 1px dashed var(--line); padding-top: .9rem; }
.af-field { margin-top: 1rem; }
.af-gp-tag {
  display: inline-block; margin-left: .5rem; font-size: .72rem; font-weight: 700;
  color: var(--danger); background: var(--danger-1); border: 1px solid var(--danger-1);
  border-radius: .35rem; padding: .08rem .4rem; vertical-align: middle;
}
.af-gp-note { color: var(--danger); font-size: .85rem; margin: .25rem 0 .4rem; }
.af-gp-warn {
  background: var(--warn-1); border: 1px solid var(--warn-1); border-radius: .6rem;
  padding: .7rem .9rem; margin: .6rem 0 0; font-size: .92rem; color: var(--warn);
}
.af-flabel { display: block; font-weight: 700; margin-bottom: .35rem; }
.af-input {
  width: 100%; box-sizing: border-box; padding: .85rem; margin-top: .6rem;
  border: 1px solid var(--line-2); border-radius: .6rem;
  font-size: 1.15rem; letter-spacing: .08em; font-family: inherit;
}
.af-ask {
  background: var(--warn-1); border: 1px solid var(--warn-1); border-radius: .8rem;
  padding: 1rem; margin: 1rem 0;
}
.af-ask-title { font-weight: 800; font-size: 1.15rem; margin: 0 0 .35rem; color: var(--warn); }
.af-ask-msg { margin: 0; color: var(--warn); font-size: .98rem; }
.af-shot {
  width: 100%; border: 1px solid var(--line); border-radius: .7rem; display: block;
}
.af-step {
  display: flex; align-items: flex-start; gap: .6rem;
  font-size: .98rem; padding: .3rem 0;
}
.af-dot {
  width: .6rem; height: .6rem; border-radius: 50%;
  margin-top: .55rem; flex: 0 0 auto;
}
.af-dot-ok { background: var(--accent); }
.af-dot-no { background: var(--warn); }
.af-sub { font-weight: 700; margin: 0 0 .4rem; }
.af-err {
  background: var(--danger-1); border: 1px solid var(--danger-1); color: var(--danger);
  border-radius: .6rem; padding: .7rem .9rem; margin-top: 1rem; font-size: .95rem;
}
</style>
