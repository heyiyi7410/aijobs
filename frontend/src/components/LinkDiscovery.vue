<template>
  <section class="card discover-card" aria-labelledby="discover-title">
    <div class="card-head">
      <span class="tile" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="10.7" cy="10.7" r="6.7"/><path d="m16 16 5 5"/>
        </svg>
      </span>
      <h2 id="discover-title" class="card-title">从企业名单找招聘链接</h2>
    </div>
    <p class="card-hint">粘贴多家企业名称，或上传名单截图。先查本站已收录的岗位；没有时再查外网。每行一家，可写“企业名称 | 岗位”。</p>
    <textarea v-model="sourceText" class="input discover-input" rows="4" :disabled="searching || ocrBusy"
      @paste="onPaste" aria-label="企业名单文字" placeholder="企业名称 | 岗位（每行一家），也可以在此粘贴截图"></textarea>
    <div class="discover-actions">
      <label class="btn discover-upload" :class="{ 'upload-disabled': ocrBusy || searching }">
        {{ ocrBusy ? '正在识别截图…' : '上传截图识别文字' }}
        <input type="file" accept="image/png,image/jpeg,image/webp" :disabled="ocrBusy || searching" aria-label="上传企业名单截图" @change="onImage">
      </label>
      <button class="btn btn-primary" :disabled="ocrBusy || searching" @click="prepare">核对企业名单</button>
    </div>
    <p v-if="ocrBusy" class="discover-status" role="status">正在识别截图文字…</p>
    <p v-if="error" class="discover-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="discover-status" role="status">{{ notice }}</p>

    <div v-if="entries.length" class="discover-review">
      <h3>先核对，再搜索 <small>最多 8 家；识别错误可直接修改</small></h3>
      <div v-for="(entry, index) in entries" :key="index" class="discover-edit">
        <span>{{ index + 1 }}</span>
        <input v-model="entry.company" class="input" :disabled="searching" maxlength="100" :aria-label="`第 ${index + 1} 家企业`" placeholder="企业名称">
        <input v-model="entry.role" class="input" :disabled="searching" maxlength="80" :aria-label="`第 ${index + 1} 家岗位`" placeholder="岗位（选填）">
        <button class="discover-remove" :disabled="searching" :aria-label="`删除第 ${index + 1} 家`" @click="entries.splice(index, 1)">删除</button>
      </div>
      <button class="btn btn-primary discover-search" :disabled="searching" @click="searchAll">
        {{ searching ? `正在搜索第 ${progress} / ${searchTotal} 家…` : `搜索 ${entries.length} 家企业` }}
      </button>
      <button v-if="searching" class="btn discover-search" @click="stopSearch">停止搜索</button>
    </div>

    <div v-if="results.length" class="discover-results" aria-live="polite">
      <div class="discover-pager">
        <button class="btn" :disabled="current === 0" @click="current--">上一家</button>
        <strong>{{ current + 1 }} / {{ results.length }} · {{ active.company }}</strong>
        <button class="btn" :disabled="current === results.length - 1" @click="current++">下一家</button>
      </div>
      <p class="discover-scope">
        {{ active.search_scope === 'external_web' ? '本站未找到，以下为外网搜索结果，企业归属尚未核实。' : '来自本站已收录的招聘信息；链接可能是官网或第三方平台，请打开核对。' }}
      </p>
      <div v-if="active.links.length" class="discover-list">
        <article v-for="(link, index) in active.links" :key="link.url + index" class="discover-result">
          <div class="discover-result-title">{{ link.title }}</div>
          <div class="discover-meta">{{ link.company }}<span v-if="link.city"> · {{ link.city }}</span> · {{ link.source }}</div>
          <div class="discover-meta discover-host">{{ link.host }}</div>
          <div class="discover-link-actions">
            <a :href="link.url" target="_blank" rel="noopener noreferrer" class="btn">打开核对
              <svg class="discover-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 3h7v7M21 3 10 14M10 3H3v18h18v-7"/></svg>
            </a>
            <button class="btn btn-primary" :disabled="fillBusy" @click="$emit('select', link.url)">{{ fillBusy ? '当前链接填表中' : '用此链接填表' }}</button>
          </div>
        </article>
      </div>
      <p v-else class="discover-empty">{{ active.search_error || '暂未找到可用招聘链接。你仍可以手动粘贴填写页链接。' }}</p>
      <p v-if="active.links.length && active.search_error" class="discover-status">{{ active.search_error }}</p>
      <a :href="webSearchUrl(active.company, active.role)" target="_blank" rel="noopener noreferrer" class="discover-more">在搜索引擎继续核对这家企业
        <svg class="discover-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 3h7v7M21 3 10 14M10 3H3v18h18v-7"/></svg>
      </a>
    </div>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, toRefs } from 'vue'
import { api } from '../api'
import { store } from '../store'

defineEmits(['select'])
defineProps({ fillBusy: { type: Boolean, default: false } })
const { sourceText, entries, results, current } = toRefs(store.linkDiscovery)
const progress = ref(0)
const searchTotal = ref(0)
const searching = ref(false)
const ocrBusy = ref(false)
const error = ref('')
const notice = ref('')
let searchController = null
const active = computed(() => results.value[current.value] || { links: [] })

function prepare() {
  error.value = ''
  notice.value = ''
  const lines = sourceText.value.split(/\r?\n|[；;]/).map(line => line.trim())
    .filter(line => line && !/^(企业名称|公司名称|序号|岗位名称)\s*[:：]?$/.test(line))
  const names = new Set()
  const candidates = lines.map(line => {
    const clean = line.replace(/^\s*(?:[\d一二三四五六七八]+[.、．)）\s]+|[-•]+\s*)/, '')
      .replace(/^(?:企业|公司|单位)(?:名称)?\s*[:：]\s*/, '')
    const parts = clean.split(/[|｜\t]|\s+(?:招聘)?岗位\s*[:：]\s*/)
    return { company: (parts[0] || '').trim().slice(0, 100), role: (parts[1] || '').trim().slice(0, 80) }
  }).filter(entry => {
    if (entry.company.length < 2 || names.has(entry.company)) return false
    names.add(entry.company)
    return true
  })
  entries.value = candidates.slice(0, 8)
  if (candidates.length > 8) notice.value = `识别到 ${candidates.length} 家，先搜索前 8 家；其余企业可分批搜索。`
  if (!entries.value.length) error.value = '请粘贴企业名单，或先上传截图识别文字'
}

async function onImage(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  await recognizeImage(file)
}

async function onPaste(event) {
  const file = [...(event.clipboardData?.files || [])].find(item => item.type.startsWith('image/'))
  if (!file) return
  event.preventDefault()
  await recognizeImage(file)
}

async function recognizeImage(file) {
  if (!file) return
  if (searching.value || ocrBusy.value) return
  error.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
    error.value = '请上传不超过 5 MB 的 PNG、JPG 或 WebP 截图'
    return
  }
  ocrBusy.value = true
  try {
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    const result = await api.ocrCompanyScreenshot(data)
    if (!result.ok) throw new Error(result.msg || '没有识别到文字')
    sourceText.value = [sourceText.value.trim(), result.text].filter(Boolean).join('\n')
    prepare()
  } catch (cause) {
    error.value = cause.message || '截图识别失败，请直接粘贴企业名单'
  } finally {
    ocrBusy.value = false
  }
}

async function searchAll() {
  if (searching.value || ocrBusy.value) return
  error.value = ''
  if (!entries.value.length || entries.value.some(entry => entry.company.trim().length < 2)) {
    error.value = '请先核对每家企业的名称（至少两个字）'
    return
  }
  searching.value = true
  const batch = entries.value.map(entry => ({ company: entry.company.trim(), role: entry.role.trim() }))
  searchTotal.value = batch.length
  searchController = new AbortController()
  const controller = searchController
  results.value = []
  current.value = 0
  for (const [index, entry] of batch.entries()) {
    if (controller.signal.aborted) break
    progress.value = index + 1
    try {
      const result = await api.discoverCompanyLinks(entry.company, entry.role, controller.signal)
      results.value.push(result.ok ? result : { ...entry, links: [], search_error: result.msg || '搜索失败' })
    } catch {
      if (controller.signal.aborted) break
      results.value.push({ ...entry, links: [], search_error: '网络暂时不可用，请重试' })
    }
  }
  searching.value = false
  searchController = null
}

function stopSearch() {
  searchController?.abort()
  notice.value = '已停止搜索，已找到的企业结果仍可查看。'
}

onUnmounted(() => searchController?.abort())

function webSearchUrl(company, role) {
  return 'https://www.bing.com/search?q=' + encodeURIComponent(`${company} ${role || ''} 招聘 官网 投递`)
}
</script>

<style scoped>
.discover-card { margin-top: 1rem; }
.discover-input { width: 100%; min-height: 7rem; box-sizing: border-box; resize: vertical; }
.discover-actions, .discover-link-actions { display: flex; flex-wrap: wrap; gap: .55rem; margin-top: .75rem; }
.discover-actions .btn, .discover-link-actions .btn { min-height: 2.75rem; display: inline-flex; align-items: center; justify-content: center; }
.discover-upload { position: relative; overflow: hidden; cursor: pointer; }
.discover-upload input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
.discover-upload:focus-within { outline: .15rem solid var(--primary-7); outline-offset: .15rem; }
.upload-disabled { opacity: .6; cursor: wait; }
.discover-status, .discover-scope, .discover-empty { color: var(--ink-2); line-height: 1.5; }
.discover-error { color: var(--danger); }
.discover-review, .discover-results { border-top: 1px solid var(--line); margin-top: 1rem; padding-top: 1rem; }
.discover-review h3 { margin: 0 0 .7rem; font-size: 1.05rem; }
.discover-review small { color: var(--ink-2); font-weight: 400; }
.discover-edit { display: grid; grid-template-columns: 1.6rem 1fr 1fr auto; gap: .45rem; align-items: center; margin: .5rem 0; }
.discover-edit .input { min-width: 0; width: 100%; box-sizing: border-box; }
.discover-remove { background: none; border: 0; color: var(--danger); cursor: pointer; min-height: 2.75rem; }
.discover-remove:active { background: var(--danger-1); }
.discover-card button:disabled { cursor: not-allowed; opacity: .65; transform: none; }
.discover-search { margin-top: .7rem; min-height: 2.75rem; }
.discover-pager { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.discover-pager strong { text-align: center; min-width: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.discover-pager .btn { min-height: 2.75rem; }
.discover-result { padding: .9rem 0; border-bottom: 1px solid var(--line); }
.discover-result-title { font-weight: 750; color: var(--ink); overflow-wrap: anywhere; }
.discover-meta { margin-top: .25rem; font-size: .9rem; color: var(--ink-2); overflow-wrap: anywhere; }
.discover-host { color: var(--primary-7); }
.discover-more { display: inline-flex; align-items: center; gap: .35rem; min-height: 2.75rem; margin-top: .9rem; color: var(--primary-7); }
.discover-icon { flex: 0 0 auto; width: 1rem; height: 1rem; }
.discover-card :focus-visible { outline: .15rem solid var(--primary-7); outline-offset: .15rem; }
@media (max-width: 31rem) {
  .discover-edit { grid-template-columns: 1.4rem minmax(0, 1fr) auto; }
  .discover-edit input:nth-of-type(2) { grid-column: 2; }
  .discover-edit .discover-remove { grid-column: 3; grid-row: 1 / 3; }
  .discover-actions .btn, .discover-link-actions .btn { width: 100%; }
  .discover-pager { display: grid; grid-template-columns: 1fr 1fr; }
  .discover-pager strong { grid-column: 1 / -1; grid-row: 1; text-align: left; }
  .discover-pager .btn { padding-inline: .6rem; }
}
</style>
