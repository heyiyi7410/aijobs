<template>
  <div class="stepbar">
    <div
      v-for="s in steps"
      :key="s.n"
      class="s"
      :class="{
        on: store.step === s.n,
        done: store.step > s.n,
        back: canGo(s.n) && store.step !== s.n
      }"
      :title="canGo(s.n) ? '点这里回到「' + s.t + '」' : ''"
      :role="canGo(s.n) && store.step !== s.n ? 'button' : null"
      :tabindex="canGo(s.n) && store.step !== s.n ? 0 : -1"
      :aria-current="store.step === s.n ? 'step' : null"
      @click="canGo(s.n) && go(s.n)"
      @keydown.enter.prevent="canGo(s.n) && go(s.n)"
      @keydown.space.prevent="canGo(s.n) && go(s.n)"
    >
      <span class="n">{{ s.n }}</span><span class="t">{{ s.t }}</span>
    </div>
  </div>
</template>

<script setup>
import { store, go, canGo } from '../store'

const steps = [
  { n: 1, t: '填信息' },
  { n: 2, t: '选单位' },
  { n: 3, t: '挑岗位' },
  { n: 4, t: '去投递' }
]
</script>
