<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="tile" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 16V5" /><path d="m7 9 5-5 5 5" />
            <path d="M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" />
          </svg>
        </span>
        <h2 class="card-title">上传简历并智能解析</h2>
      </div>
      <p class="card-hint">支持 PDF、Word、txt、md 格式，自动读取简历信息。手机或电脑里那份简历都可以，我读一遍，把名字、电话、邮箱这些自动填到下面，你只要核对一下。</p>

      <!-- ============ 有简历就先传，省得打字 ============ -->
      <div class="up-box">
        <input ref="fileEl" class="up-file" type="file"
               accept=".pdf,.docx,.txt,.md" @change="onPick">
        <button class="up-btn" :disabled="busy" @click="fileEl.click()">
          <!-- 云朵上传图标：SVG 画，不用字符 -->
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M6.8 19a4.3 4.3 0 0 1-.6-8.6 5.5 5.5 0 0 1 10.8-1.2A4.6 4.6 0 0 1 17 19z" />
            <path d="M12 12.5V20" /><path d="m8.8 15.6 3.2-3.2 3.2 3.2" />
          </svg>
          <span class="up-btn-t">
            <b>{{ busy ? '正在读，稍等一下…' : '点击上传简历' }}</b>
            <small>支持 PDF / Word / txt / md</small>
          </span>
        </button>
        <p class="up-hint" style="margin:.55rem 0 0;">
          不会找文件？让身边年轻人帮你点一下，或者跳过这步，下面照着填也很快。
        </p>

        <label class="up-keep">
          <input type="checkbox" class="switch" v-model="keepResume">
          <span>留着这份简历，自动投的时候直接用它</span>
        </label>
        <p class="up-hint" style="margin:.35rem 0 0;">
          留着的话，单位收到的就是你这份原件（照片、排版都在）。
          不留的话，自动投只能用下面这些文字拼一页，照片和排版就没有了。
        </p>

        <p v-if="msg" class="up-msg" :class="isWarn ? 'up-warning' : 'up-ok'">{{ msg }}</p>
        <p v-if="err" class="up-msg up-err">{{ err }}</p>
      </div>

      <!-- ============ 简历读到的：单独一张卡（效果图） ============ -->
      <div v-if="got.length" class="card">
        <div class="card-head">
          <span class="tile tile-green" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
              <path d="M14 3v5h5" />
            </svg>
          </span>
          <h2 class="card-title">简历读到的<span class="soft">核对一下</span></h2>
        </div>
        <div class="up-got">
          <div v-for="g in got" :key="g.key" class="up-row">
            <span>{{ g.label }}</span>
            <span :class="low.includes(g.key) ? 'up-warn' : 'up-val'">
              {{ fmt(g.value) }}<template v-if="low.includes(g.key)">　核对一下</template>
            </span>
          </div>
          <p class="up-hint" style="margin-top:.7rem;">
            下面已经照着填好了，从头到尾看一遍，有不对的直接改。
          </p>
        </div>
      </div>

      <div class="card-head">
        <span class="tile" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="8" r="4" />
            <path d="M5 21c0-3.9 3.1-7 7-7s7 3.1 7 7" />
          </svg>
        </span>
        <h2 class="card-title">基本信息<span class="soft">带 * 为必填</span></h2>
      </div>

      <p class="up-or">
        <span>没有简历的话，下面这些问题照着填也行</span>
      </p>

      <div class="field">
        <label for="f1">你叫什么名字<span class="req">*</span><Tag k="name" /></label>
        <div style="display:flex; gap:.6rem;">
          <input id="f1" class="input" v-model="p.name" placeholder="例如：张三" />
          <button v-if="canRec" class="btn" :class="{ 'btn-primary': mic === 'name' }"
            style="flex:0 0 auto; padding:0 .9rem;" @click="toggleMic('name')">
            {{ mic === 'name' ? '说完点这停' : '🎤 说' }}
          </button>
        </div>
      </div>

      <div class="field">
        <label for="f2">手机号<span class="req">*</span><Tag k="phone" /></label>
        <input id="f2" class="input" v-model="p.phone" inputmode="numeric" maxlength="11"
          placeholder="11 位手机号，单位会打这个号找你" />
        <p class="tip">只用来给你打电话通知面试，不会给别人。</p>
      </div>

      <div class="field">
        <label for="f3">你的邮箱<span class="req">*</span><Tag k="email" /></label>
        <input id="f3" class="input" v-model="p.email" placeholder="例如：123456@qq.com" />
        <p class="tip">
          单位回复会直接发到这个邮箱。没有邮箱？用手机号就能注册一个 QQ 邮箱。
        </p>
      </div>

      <div class="field">
        <label for="f4">今年多大（可以不填）<Tag k="age" /></label>
        <input id="f4" class="input" v-model="p.age" inputmode="numeric" maxlength="3" placeholder="例如：32" />
      </div>

      <div class="field">
        <label>你的性别<Tag k="gender" /></label>
        <div class="chips">
          <button v-for="g in SEXES" :key="g" class="chip" :class="{ on: p.gender === g }"
            @click="p.gender = g">{{ g }}</button>
        </div>
      </div>

      <div class="field">
        <label for="f5">出生年月<Tag k="birth" /></label>
        <input id="f5" class="input" v-model="p.birth" maxlength="7"
          placeholder="例如：1999-05" />
        <p class="tip">招聘表基本都问这个。写年份和月份就行，比如 1999-05。</p>
      </div>

      <div class="field">
        <label>你的学历<Tag k="edu" /></label>
        <p class="tip">点一下选中，再点一下取消。</p>
        <div class="chips">
          <button v-for="e in EDUS" :key="e" class="chip" :class="{ on: p.edu === e }"
            @click="p.edu = e">{{ e }}</button>
        </div>
      </div>

      <div class="field">
        <label for="f6">毕业学校<Tag k="school" /></label>
        <input id="f6" class="input" v-model="p.school" placeholder="例如：武汉职业技术学院" />
      </div>

      <div class="field">
        <label for="f7">学的什么专业<Tag k="major" /></label>
        <input id="f7" class="input" v-model="p.major" placeholder="例如：电气自动化技术" />
      </div>

      <div class="field">
        <label for="f8">毕业时间<Tag k="graduation" /></label>
        <input id="f8" class="input" v-model="p.graduation" maxlength="7"
          placeholder="例如：2021-06" />
      </div>

      <!-- 国聘「基本信息」段必填，不填就永远卡在保存那一步（2026-09-20 实测） -->
      <div class="field">
        <label for="f9">身高（cm）<Tag k="height" /></label>
        <input id="f9" class="input" v-model="p.height" inputmode="numeric" maxlength="3"
          placeholder="例如：175" />
      </div>

      <div class="field">
        <label for="f10">体重（kg）<Tag k="weight" /></label>
        <input id="f10" class="input" v-model="p.weight" inputmode="numeric" maxlength="3"
          placeholder="例如：65" />
      </div>

      <div class="field">
        <label for="f11">紧急联系人姓名<Tag k="emergency_name" /></label>
        <input id="f11" class="input" v-model="p.emergency_name" placeholder="例如：张某某" />
      </div>

      <div class="field">
        <label for="f12">紧急联系人电话<Tag k="emergency_phone" /></label>
        <input id="f12" class="input" v-model="p.emergency_phone" inputmode="numeric"
          maxlength="11" placeholder="例如：13800138000" />
      </div>

      <div class="field">
        <label for="f13">通信地址<Tag k="address" /></label>
        <input id="f13" class="input" v-model="p.address"
          placeholder="例如：湖北省武汉市硚口区某某路 1 号" />
      </div>

      <!-- ====== 投国企要用的几项：只有本人知道，AI 不替他猜 ====== -->
      <div class="field">
        <label>下面这几项，只有你本人知道</label>
        <p class="tip">
          国聘投递的「基本信息」「教育经历」「语言能力」会让你填这些。以前我按最常见的
          答案默认填上（汉族 / 未婚 / 统招 / 全日制 / 英语熟练 / 服从调剂），那等于替你
          编材料 —— 现在你不填，我就留空并在投递前提醒你补，绝不猜。
        </p>
      </div>

      <div class="field">
        <label for="f20">民族<Tag k="nation" /></label>
        <input id="f20" class="input" v-model="p.nation" placeholder="例如：汉族" />
      </div>

      <div class="field">
        <label>婚姻状况<Tag k="marital" /></label>
        <div class="chips">
          <button v-for="v in MARITALS" :key="v" class="chip"
            :class="{ on: p.marital === v }" @click="p.marital = v">{{ v }}</button>
        </div>
      </div>

      <div class="field">
        <label>学历性质<Tag k="edu_regular" /></label>
        <p class="tip">成人教育、自考、函授、网络教育要选「非统招」——
          这项填错就是学历性质造假，所以只能你自己确认。</p>
        <div class="chips">
          <button v-for="o in EDU_REGULARS" :key="o.v" class="chip"
            :class="{ on: p.edu_regular === o.v }" @click="p.edu_regular = o.v">{{ o.v }}</button>
        </div>
      </div>

      <div class="field">
        <label>学习形式<Tag k="edu_fulltime" /></label>
        <div class="chips">
          <button v-for="v in EDU_FORMS" :key="v" class="chip"
            :class="{ on: p.edu_fulltime === v }" @click="p.edu_fulltime = v">{{ v }}</button>
        </div>
      </div>

      <div class="field">
        <label>学位证<Tag k="has_degree" /></label>
        <div class="chips">
          <button v-for="v in DEGREE_OPTIONS" :key="v" class="chip"
            :class="{ on: p.has_degree === v }" @click="p.has_degree = v">{{ v }}</button>
        </div>
        <p class="tip">只有毕业证、没有学位证的，照实选「无学位证」。</p>
      </div>

      <div class="field">
        <label>外语<Tag k="foreign_lang" /></label>
        <div class="chips">
          <button v-for="o in FOREIGN_LANGS" :key="o.v" class="chip"
            :class="{ on: p.foreign_lang === o.v }" @click="p.foreign_lang = o.v">{{ o.d }}</button>
        </div>
        <div v-if="p.foreign_lang && p.foreign_lang !== '无'" class="chips">
          <button v-for="v in FOREIGN_LEVELS" :key="v" class="chip"
            :class="{ on: p.foreign_level === v }" @click="p.foreign_level = v">{{ v }}</button>
        </div>
        <p class="tip">不会外语就选「不会外语」。以前这里默认填「英语 · 熟练」，
          等于凭空给你安一门技能，面试一问英文就穿帮。</p>
      </div>

      <div class="field">
        <label>是否服从调剂<Tag k="can_arrange" /></label>
        <div class="chips">
          <button v-for="v in ARRANGE_OPTIONS" :key="v" class="chip"
            :class="{ on: p.can_arrange === v }" @click="p.can_arrange = v">{{ v }}</button>
        </div>
        <p class="tip">这题只有你能定：服从调剂可能被分到偏远地区。拿不准就留空，
          投递时会提醒你，不会替你选。</p>
      </div>

      <div class="field">
        <label>健康状况<Tag k="health" /></label>
        <div class="chips">
          <button v-for="v in HEALTHS" :key="v" class="chip"
            :class="{ on: p.health === v }" @click="p.health = v">{{ v }}</button>
        </div>
      </div>

      <!-- ============ 结构化经历：投国聘时直接补进它的站内简历 ============ -->
      <div class="field">
        <label>你的经历（工作 / 实习 / 校内活动 / 教育）<Tag k="experiences" /></label>
        <p class="tip">
          有几段写几段，不填也能投。填了的用处很实在：国聘投递时要求补全它站内简历，
          我能把这段真实经历直接填进去，不用勾「无工作经历」——单位看到的是加分项。
          传了简历的，工作经历我会照着认出来，你核对一下就行。
        </p>
        <div v-for="(e, i) in expList" :key="'exp' + i" class="exp-card">
          <div class="exp-head">
            <span class="exp-type">{{ TYPE_LABEL[e.type] || '经历' }}</span>
            <button class="exp-del" @click="expList.splice(i, 1)">删除</button>
          </div>
          <input class="input" v-model="e.company"
            :placeholder="e.type === 'edu' ? '学校名称，例如：武汉交通职业学院'
              : (e.type === 'campus' ? '学校 / 社团名称' : '公司 / 单位名称，例如：中国人寿')" />
          <input class="input" v-model="e.role"
            :placeholder="e.type === 'edu' ? '专业，例如：计算机'
              : '担任的职位 / 角色，例如：销售'" />
          <div v-if="e.type === 'edu'" class="exp-row2">
            <input class="input" v-model="e.degree" placeholder="学历：专科 / 本科 / 硕士" />
          </div>
          <div class="exp-row2">
            <input class="input" v-model="e.start" placeholder="开始，例如：2024-07" />
            <input class="input" v-model="e.end" placeholder="结束，例如：2025-01" />
          </div>
          <textarea v-if="e.type !== 'edu'" class="input" rows="3" v-model="e.desc"
            placeholder="做了什么、做成了什么，一两句实话就行"></textarea>
        </div>
        <div class="chips">
          <button class="chip" @click="addExp('work')">＋ 工作经历</button>
          <button class="chip" @click="addExp('intern')">＋ 实习经历</button>
          <button class="chip" @click="addExp('campus')">＋ 校内活动 / 社团</button>
          <button class="chip" @click="addExp('edu')">＋ 教育经历</button>
        </div>
      </div>

      <div class="field">
        <label>做了多久这类工作<Tag k="exp" /></label>
        <div class="chips">
          <button v-for="e in EXPS" :key="e" class="chip" :class="{ on: p.exp === e }"
            @click="p.exp = e">{{ e }}</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="tile tile-cyan" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="m12 3 2.7 5.7 6.3.8-4.6 4.3 1.2 6.2-5.6-3-5.6 3 1.2-6.2L3 9.5l6.3-.8z" />
          </svg>
        </span>
        <h2 class="card-title">你会做什么</h2>
      </div>
      <p class="card-hint">会的都点上，点得越多，找得越准。没有合适的可以跳过。</p>
      <div class="chips">
        <button v-for="s in SKILLS" :key="s" class="chip" :class="{ on: p.skills.includes(s) }"
          @click="toggle(p.skills, s)">{{ s }}</button>
      </div>

      <div class="field" style="margin-top:1.3rem;">
        <label for="f14">还想说说什么（可以不填）<Tag k="intro" /></label>
        <div style="display:flex; gap:.6rem; align-items:flex-start;">
          <textarea id="f14" class="input" v-model="p.intro"
            placeholder="例如：我在工厂做过 3 年，能上夜班，想找家近一点的单位。"></textarea>
          <button v-if="canRec" class="btn" :class="{ 'btn-primary': mic === 'intro' }"
            style="flex:0 0 auto; padding:0 .9rem; min-height:7rem;" @click="toggleMic('intro')"
            :aria-label="mic === 'intro' ? '停止录音' : '用说话的方式填写'">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
              stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0" />
              <path d="M12 18v3" />
            </svg>
            {{ mic === 'intro' ? '停' : '说' }}
          </button>
        </div>
        <p class="tip" v-if="mic">正在听你说话，说完点「停」。</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { h, ref, watch, onUnmounted } from 'vue'
import { store, toggle } from '../store'
import { CITIES, EDUS, EXPS, SKILLS, MARITALS, EDU_REGULARS, EDU_FORMS,
  DEGREE_OPTIONS, FOREIGN_LANGS, FOREIGN_LEVELS, ARRANGE_OPTIONS,
  HEALTHS } from '../options'
import { api } from '../api'
import { canRecord, record, stopRecord } from '../useSpeech'

const p = store.profile
const canRec = canRecord()

// ============ 结构化经历编辑器 ============
// 注意：这个会话没动过编辑器时，不把 experiences 发回后端（App.vue 负责摘掉），
// 不然空数组会把库里上次存的经历抹掉。
const TYPE_LABEL = { work: '工作', intern: '实习', campus: '校内活动', edu: '教育' }
const expList = ref(Array.isArray(p.experiences) ? p.experiences.slice() : [])
watch(expList, (list) => {
  store.experiencesTouched = true
  p.experiences = list.map(e => ({ ...e }))
}, { deep: true })
function addExp(type) {
  expList.value.push(type === 'edu'
    ? { type, company: '', school: '', major: '', degree: '', start: '', end: '' }
    : { type, company: '', school: '', role: '', start: '', end: '', desc: '' })
}
const mic = ref('')
const SEXES = ['男', '女']

const fileEl = ref(null)
const busy = ref(false)
const keepResume = ref(true)   // 留着原件，自动投才能上传真正的简历（带照片排版）
const msg = ref('')
const err = ref('')
const isWarn = ref(false)   // 这句话是提醒（不是简历），不是「读好了」
const got = ref([])        // [{key,label,value}]
const low = ref([])        // 需要用户核对一下的字段
const filled = ref([])     // 哪些字段是从简历来的（用来在标签后面挂个小标记）

const MAX_MB = 5

/** 字段旁边的小标记：「简历读到的」/「核对一下」——让用户一眼看出哪些不是自己填的。 */
const Tag = (props) => {
  if (!filled.value.includes(props.k)) return null
  const warn = low.value.includes(props.k)
  return h('span', { class: ['up-tag', warn ? 'up-tag-warn' : ''] },
           warn ? '简历读到，核对一下' : '简历读到的')
}
Tag.props = { k: { type: String, required: true } }

function fmt(v) {
  return Array.isArray(v) ? v.join('、') : String(v)
}

function readB64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result).replace(/^data:[^,]*,/, ''))
    fr.onerror = () => reject(new Error('读不了这个文件'))
    fr.readAsDataURL(file)
  })
}

async function onPick(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''          // 清掉，方便再传同一个文件
  if (!f) return

  msg.value = ''
  err.value = ''
  isWarn.value = false
  got.value = []
  low.value = []
  filled.value = []

  if (f.size > MAX_MB * 1024 * 1024) {
    err.value = '这个文件太大了（超过 ' + MAX_MB + ' MB）。可以只留前面一两页再传。'
    return
  }

  busy.value = true
  try {
    const b64 = await readB64(f)
    const r = await api.parseResume({
      filename: f.name, data_b64: b64, cities: CITIES, keep: keepResume.value
    })
    if (!r.ok) {
      // 读不出字段也可能保住了原件（比如扫描件），照样记下来，附件还能用
      if (r.resume_path) p.resume_path = r.resume_path
      err.value = r.msg || '这份文件没能读出内容，下面手动填一下吧。'
      return
    }
    apply(r.fields || {})
    if (r.resume_path) p.resume_path = r.resume_path
    got.value = r.got || []
    low.value = r.low || []
    isWarn.value = !!r.warn
    msg.value = (r.msg || '读好了，看看下面填得对不对。') +
      (r.resume_path ? '　原件也留好了，自动投时就用它。' : '')
  } catch (e) {
    err.value = '文件没能读出来（' + (e.message || '网络不通') + '），下面手动填一下吧。'
  } finally {
    busy.value = false
  }
}

/** 把读到的值写进资料。只覆盖读出来的字段，没读到的保持原样，绝不猜。 */
function apply(fields) {
  const keys = []
  for (const [k, v] of Object.entries(fields)) {
    if (k === 'skills') {
      const add = (v || []).filter(s => SKILLS.includes(s) && !p.skills.includes(s))
      if (add.length) p.skills = p.skills.concat(add)
      keys.push(k)
      continue
    }
    // 工作经历是一串条目（公司/职位/起止/职责），不是标量字段，单独处理：
    // 并进上面的编辑器里让用户过一眼——简历认出来的公司名/年份难免有偏差，
    // 让他核对比我们默默填进档案、投出去时才发现错了要安全得多。
    if (k === 'experiences') {
      const list = (Array.isArray(v) ? v : [])
        .filter(e => e && (e.company || e.school))
      if (!list.length) continue
      const have = new Set(expList.value.map(
        e => (e.company || e.school || '') + '|' + (e.role || '')))
      let added = 0
      for (const e of list) {
        const key = (e.company || e.school || '') + '|' + (e.role || '')
        if (have.has(key)) continue          // 已经在编辑器里的不重复加
        expList.value.push({ ...e })         // push 会触发 watch，同步回档案
        have.add(key)
        added++
      }
      if (added) keys.push(k)
      continue
    }
    if (!(k in p)) continue
    if (k === 'city') {
      // 城市认出来了就点中对应的那一个；不在列表里的放到「自己填」那栏
      if (CITIES.includes(v)) { p.city = v; p.cityOther = '' }
      else { p.cityOther = v }
      keys.push(k)
      continue
    }
    p[k] = v
    keys.push(k)
  }
  filled.value = keys
}

/* 语音输入：把识别到的内容放进当前字段。
   回调拿到的 text 是「从开始说话到现在的完整内容」，所以用
   「开口前的原值 + text」覆盖，绝不能 (p[field]||'') + text 累加 ——
   那样每说一个字都会把整段再拼一遍，变成「我我叫我叫张三」。 */
let rec = null
let micBase = ''

function stopMic() {
  stopRecord(rec)
  rec = null
  mic.value = ''
}

function toggleMic(field) {
  if (mic.value === field) {
    stopMic()
    return
  }
  // 换字段时必须先把上一个识别器停掉，否则它还在跑、继续往上一个字段里写
  stopMic()

  mic.value = field
  micBase = p[field] || ''
  rec = record(
    text => { p[field] = micBase + text },
    () => { if (rec) { stopRecord(rec); rec = null } mic.value = '' }
  )
  if (!rec) mic.value = ''
}

// 离开第 1 步时识别器必须停：不停的话它会一直开着麦克风，
// 用户填完了还听到自己的话被写进某个看不见的字段。
onUnmounted(stopMic)
</script>

<style scoped>
/* 上传区：浅蓝底 + 蓝虚线框（效果图的拖放区） */
.up-box {
  background: var(--surface-2);
  border: 2px dashed #a9c9f6;
  border-radius: 0.9rem;
  padding: 1rem;
  margin-bottom: 1.3rem;
}
/* 整块可点的大按钮：云朵图标 + 主文案 + 格式小字（效果图） */
.up-btn {
  width: 100%;
  min-height: 4.4rem;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.7rem;
  border: 1.5px solid #bcd7fa;
  border-radius: 0.8rem;
  background: var(--surface);
  color: var(--primary-7);
  cursor: pointer;
  transition: background var(--t-pop) var(--ease-out),
    transform var(--t-press) var(--ease-out);
}
.up-btn:active { transform: scale(0.98); }
.up-btn:disabled { cursor: wait; opacity: 0.75; }
.up-btn svg { width: 2rem; height: 2rem; flex: none; color: var(--primary); }
.up-btn-t { display: flex; flex-direction: column; align-items: flex-start; line-height: 1.35; }
.up-btn-t b { font-size: 1.12rem; font-weight: 700; }
.up-btn-t small { font-size: 0.85rem; color: var(--ink-2); }
.up-hint {
  font-size: 0.95rem;
  color: var(--ink-2);
  margin: 0 0 0.9rem;
  line-height: 1.6;
}
.up-keep {
  display: flex; align-items: center; gap: .6rem; margin-top: .95rem;
  font-size: .98rem; font-weight: 600; color: var(--ink);
  cursor: pointer;
}
.up-file { display: none; }
.up-msg {
  border-radius: 0.6rem;
  padding: 0.7rem 0.9rem;
  margin: 0.9rem 0 0;
  font-size: 0.98rem;
}
.up-ok { background: var(--accent-1); border: 1px solid var(--accent-1); color: var(--accent-7); }
.up-err { background: var(--danger-1); border: 1px solid var(--danger-1); color: var(--danger-7); }
/* 提醒（这份文件不像简历）—— 用黄色，别让人误当成「读好了」 */
.up-warning { background: var(--warn-1); border: 1px solid var(--warn-1); color: var(--warn); }
.up-got {
  background: var(--surface-2);
  border-radius: 0.7rem;
  padding: 0.9rem 1rem;
}
.up-sub { font-weight: 700; margin: 0 0 0.5rem; }
.up-row {
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  padding: 0.32rem 0;
  font-size: 0.98rem;
  border-bottom: 1px dashed var(--line);
}
.up-row:last-of-type { border-bottom: none; }
.up-val { color: var(--accent-7); font-weight: 700; text-align: right; }
.up-warn { color: var(--warn); font-weight: 700; text-align: right; }
.up-or {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: var(--ink-2);
  font-size: 0.92rem;
  margin: 0 0 1.1rem;
}
.up-or::before,
.up-or::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
}
.up-or span { flex: 0 0 auto; }
.up-tag {
  font-size: 0.72rem;
  font-weight: 400;
  color: var(--accent-7);
  background: var(--accent-1);
  border-radius: 0.4rem;
  padding: 0.1rem 0.35rem;
  margin-left: 0.4rem;
  vertical-align: middle;
}
.up-tag-warn { color: var(--warn); background: var(--warn-1); }
/* 经历卡片：一段经历一张卡，边界画清楚，删起来不心虚 */
.exp-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 0.7rem;
  padding: 0.8rem 0.9rem;
  margin: 0 0 0.8rem;
}
.exp-card .input { margin-bottom: 0.55rem; }
.exp-card .input:last-child { margin-bottom: 0; }
.exp-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.55rem;
}
.exp-type {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--primary);
  background: var(--primary-1);
  border-radius: 0.4rem;
  padding: 0.12rem 0.5rem;
}
.exp-del {
  border: none;
  background: none;
  color: var(--danger);
  font-size: 0.9rem;
  cursor: pointer;
  padding: 0.15rem 0.3rem;
}
.exp-row2 { display: flex; gap: 0.6rem; }
.exp-row2 .input { flex: 1; min-width: 0; }
/* 国聘必填标记：和「简历读到」标记区分开，这个是「不填会卡住」的提醒 */
.gp-req {
  display: inline-block; margin-left: .5rem; font-size: .72rem; font-weight: 700;
  color: var(--danger); background: var(--danger-1); border: 1px solid var(--danger-1);
  border-radius: .35rem; padding: .08rem .4rem; vertical-align: middle;
}
</style>
