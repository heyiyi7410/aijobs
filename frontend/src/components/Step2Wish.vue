<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="tile" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16" />
            <path d="M16 9h3a1 1 0 0 1 1 1v11" />
            <path d="M2 21h20" />
            <path d="M8 7h2M8 11h2M8 15h2" />
          </svg>
        </span>
        <h2 class="card-title">单位性质<span class="soft">可多选</span></h2>
        <button class="text-link" type="button" @click="selectAllNature">不知道怎么选？设为全部</button>
      </div>
      <div class="chips">
        <button v-for="n in NATURES" :key="n.v" class="chip" :class="{ on: p.nature.includes(n.v) }"
          @click="toggle(p.nature, n.v)">{{ n.v }}</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="tile" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="7" width="18" height="13" rx="2.2" />
            <path d="M8.5 7V5.2A1.2 1.2 0 0 1 9.7 4h4.6a1.2 1.2 0 0 1 1.2 1.2V7" />
            <path d="M3 12.5h18" />
          </svg>
        </span>
        <h2 class="card-title">用工方式<span class="soft">可多选</span></h2>
      </div>
      <p class="card-hint">直签是直接跟单位签合同；选“都可以”时，也会把劳务派遣岗位一起纳入。</p>
      <div class="chips">
        <button v-for="h in HIRE_TYPES" :key="h.v" class="chip" :class="{ on: p.hireType === h.v }"
          @click="p.hireType = h.v">{{ h.v }}</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="tile" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
            <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" />
            <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" />
            <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
          </svg>
        </span>
        <h2 class="card-title">想做什么工作<span class="soft">可多选</span></h2>
      </div>
      <div class="chips">
        <button v-for="t in JOB_TYPES" :key="t" class="chip" :class="{ on: p.jobTypes.includes(t) }"
          @click="toggle(p.jobTypes, t)">{{ t }}</button>
      </div>
      <div class="field compact-field">
        <label for="f15">上面没有？自己填关键词</label>
        <input id="f15" class="input input-search" v-model="p.keyword"
          placeholder="请输入职位关键词，例如：工程师、策划、助理等" />
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="tile tile-cyan" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 21s-7-5.4-7-11a7 7 0 0 1 14 0c0 5.6-7 11-7 11z" />
            <circle cx="12" cy="10" r="2.6" />
          </svg>
        </span>
        <h2 class="card-title">城市<span class="soft">可多选</span></h2>
      </div>
      <div class="chips">
        <button v-for="c in CITIES" :key="c" class="chip" :class="{ on: p.city === c }"
          @click="p.city = c">{{ c }}</button>
      </div>
      <div class="field compact-field">
        <label for="f16">其他城市</label>
        <input id="f16" class="input" v-model="p.cityOther" placeholder="例如：无锡" />
      </div>
    </div>

    <div class="card switch-card">
      <label class="switch-row wish-switch">
        <input type="checkbox" class="switch" v-model="store.wide" />
        <span>
          <b>全国/本省一起搜</b>
          <small>同时搜索全国招聘和本省公告，获取更多合适机会</small>
        </span>
      </label>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="tile tile-orange" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 7h16M5 12h14M7 17h10" />
          </svg>
        </span>
        <h2 class="card-title">期望月薪<span class="soft">可多选</span></h2>
      </div>
      <div class="chips">
        <button v-for="s in SALARIES" :key="s" class="chip" :class="{ on: p.salary === s }"
          @click="p.salary = s">{{ s }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { store, toggle } from '../store'
import { NATURES, JOB_TYPES, CITIES, SALARIES, HIRE_TYPES } from '../options'

const p = store.profile

function selectAllNature() {
  p.nature.splice(0, p.nature.length, ...NATURES.map(n => n.v))
}
</script>
