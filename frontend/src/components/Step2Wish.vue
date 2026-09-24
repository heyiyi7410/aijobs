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
        <h2 class="card-title">想去什么样的单位<span class="soft">可多选</span></h2>
      </div>
      <p class="card-hint">你只要央企、国企、外企，下面已经帮你选好了。想改就点一下。</p>

      <div class="field">
        <label>单位性质<span class="req">*</span></label>
        <p class="tip">至少选一个，不选就找不到岗位。</p>
        <div class="chips">
          <button v-for="n in NATURES" :key="n.v" class="chip" :class="{ on: p.nature.includes(n.v) }"
            @click="toggle(p.nature, n.v)">{{ n.v }}</button>
        </div>
        <p class="tip" style="margin-top:.6rem;">
          <span v-for="n in NATURES" :key="n.v" style="display:block;">
            <b>{{ n.v }}</b>：{{ n.d }}
          </span>
        </p>
      </div>

      <div class="field">
        <label>用工方式</label>
        <p class="tip">「直签」是直接跟单位签合同，更有保障；很多央企国企的基层岗位是「劳务派遣」。</p>
        <div class="chips">
          <button v-for="h in HIRE_TYPES" :key="h.v" class="chip" :class="{ on: p.hireType === h.v }"
            @click="p.hireType = h.v">{{ h.v }}</button>
        </div>
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
      <p class="card-hint">可以多选，比如又想开车又想做保安都行。</p>
      <div class="chips">
        <button v-for="t in JOB_TYPES" :key="t" class="chip" :class="{ on: p.jobTypes.includes(t) }"
          @click="toggle(p.jobTypes, t)">{{ t }}</button>
      </div>
      <div class="field" style="margin-top:1rem;">
        <label for="f15">上面没有你想做的？自己填关键词</label>
        <p class="tip">填一个词（比如「数控车床」「食堂帮厨」），就按这个词帮你找岗位；跟上面选的可以一起用。</p>
        <input id="f15" class="input input-search" v-model="p.keyword" placeholder="请输入职位关键词，例如：工程师、策划、助理" />
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
        <h2 class="card-title">在哪个城市找<span class="soft">选一个</span></h2>
      </div>
      <p class="card-hint">选「不限城市」可以看到全国的岗位，适合愿意去外地的。</p>
      <div class="chips">
        <button v-for="c in CITIES" :key="c" class="chip" :class="{ on: p.city === c }"
          @click="p.city = c">{{ c }}</button>
      </div>
      <div class="field" style="margin-top:1rem;">
        <label for="f16">上面没有你的城市？自己填</label>
        <input id="f16" class="input" v-model="p.cityOther" placeholder="例如：无锡" />
      </div>

      <!-- 默认勾上：国企央企的公告大多只写「全国」或省名（实测选武汉时，
           40 条里「全国」23 条、「湖北」4 条，真正写「武汉」的只有 1 条），
           不勾上等于没得挑。用户取消就是「我只要本市」。 -->
      <div class="field" style="margin-top:.8rem;">
        <label class="check-line">
          <input type="checkbox" v-model="store.wide" />
          <span>连「全国招聘」和本省其他城市的岗位一起找</span>
        </label>
        <p class="tip">
          央企国企的招聘公告常常只写「全国」或只写省份，里面也可能有你所在城市的岗位。
          取消勾选，就只找明确写着这个城市的。
        </p>
      </div>

      <div class="field">
        <label>期望一个月多少钱</label>
        <div class="chips">
          <button v-for="s in SALARIES" :key="s" class="chip" :class="{ on: p.salary === s }"
            @click="p.salary = s">{{ s }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { store, toggle } from '../store'
import { NATURES, JOB_TYPES, CITIES, SALARIES, HIRE_TYPES } from '../options'

const p = store.profile
</script>
