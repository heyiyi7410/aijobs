<template>
  <div>
    <!-- ============ 还没开始：先按「能不能替你代投」分成两组 ============
         用户 2026-09-21 反馈：「选了岗位点一键代投，没邮箱的那些一点反馈都没有」。
         根因是 sendAll 里 jobs.filter(j => j.hr_email) 把没邮箱的岗位静默丢掉了。
         现在改成在这一步就把两组摆出来（能代投 = 公告里留了报名邮箱），
         代投按钮只对能代投那组生效、并且在按钮上写明是几个；
         没邮箱的那组明确告诉用户「只能去官网投」，并提供逐个投递 + 自动填表。
         屏幕上看不见的岗位不该被投出去，也不该被悄悄跳过。 -->
    <div v-if="mode === 'idle'" class="card">
      <!-- 发件邮箱的入口摆在标题这一行，谁都看得见。
           之前它藏在「能代投」那一组里、而且只在没配过邮箱时才渲染 ——
           库里 2749 个岗位只有 14 个有报名邮箱，所以大多数时候那一组压根不出现，
           用户找遍这一屏也找不到「在哪儿设置邮箱」。入口必须与内容无关地常驻。 -->
      <div class="card-head">
        <span class="tile tile-purple" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 3 10.5 13.5" />
            <path d="M21 3 14 21l-3.5-7.5L3 10z" />
          </svg>
        </span>
        <h2 class="card-title">去投递</h2>
        <span class="head-btns">
          <button class="btn btn-mini setup-btn" :class="{ on: mailReady }" @click="openSetup">
            {{ setupBtnText }}
          </button>
          <button class="btn btn-mini setup-btn" :class="{ on: llmReady }" @click="openLlm">
            配置 AI
          </button>
        </span>
      </div>
      <p class="card-hint">
        你选了 {{ jobs.length }} 个单位。能不能替你代投，看公告里有没有留报名邮箱——
        下面按这个分成两组，怎么投写在每组里。
      </p>

      <!-- ---- 组 1：公告里留了报名邮箱 → 能替你代投 ---- -->
      <div v-if="mailJobs.length" class="group group-mail">
        <div class="group-head">
          <span class="tile tile-green tile-sm" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 3 10.5 13.5" />
              <path d="M21 3 14 21l-3.5-7.5L3 10z" />
            </svg>
          </span>
          <span class="group-title">能代投（有邮箱）</span>
          <span class="tag tag-ok">推荐</span>
          <b class="group-count">{{ mailJobs.length }} 个</b>
        </div>
        <p class="group-note">
          这些岗位公告里留了报名邮箱。<b>配上你的发件邮箱，点一下就能全发出去</b>，
          不用一家家跑网站。
        </p>
        <div v-for="j in mailJobs" :key="j.key" class="result-item">
          <span>{{ j.title }} · {{ j.company }}</span>
          <span class="ri-extra">{{ j.hr_email }}</span>
        </div>

        <!-- 没配发件邮箱时，这里不再自己长出一套表单（原来那套只在有邮箱的岗位上
             才出现，用户根本找不到）。改成一句话把人指到标题那行的按钮上。 -->
        <div v-if="!mailReady" class="cfg">
          <p class="group-note">
            <b>还没设置发件邮箱，所以这几封我发不出去。</b>
            点右上角「设置发件邮箱」，填你的邮箱和授权码就能替你发 ——
            要先在邮箱网页版把 SMTP 服务打开，那里面写了每一步怎么点。
            不想设置也可以走下面的「用我自己的邮箱一封封发」。
          </p>
          <button class="btn btn-primary" style="width:100%;" @click="openSetup">
            设置发件邮箱（开启 SMTP）
          </button>
        </div>

        <template v-if="mailReady">
          <!-- 存进去的授权码形态可疑（比如填成了登录密码）就常驻在这儿。
               2026-09-21 的坑：这段话只在「保存那一下」说过一次，用户隔了几分钟
               来点投递，早就不在屏幕上了，于是他白等了一整批才知道失败。 -->
          <div v-if="mailPwdWarn" class="cfg-block">
            <!-- 标题原来写的是「发件邮箱可能配错了」，用户 2026-09-21 看到它就
                 以为是邮箱地址写错了，跑去反复改邮箱 —— 其实箱是对的，
                 不对的是那串授权码。标题必须点准位置。 -->
            <p class="cfg-block-t">授权码可能不对（发件邮箱本身没问题）</p>
            <p class="muted" style="margin:0;">{{ mailPwdWarn }}</p>
            <button class="btn" style="margin-top:.6rem;" @click="openSetup">去改授权码</button>
          </div>

          <button class="btn btn-primary btn-big" :disabled="busy" @click="sendAll">
            {{ busy ? '正在发送中，请别关页面' : '一键代投这 ' + mailJobs.length + ' 个' }}
          </button>
          <p class="muted" style="margin:.6rem 0 0;">
            点下去会<b>直接发给招聘方</b>，请先确认上面这些岗位和你的简历没问题。
          </p>

          <!-- ============ 按岗位定制简历（AI 据 JD 改写）============
               链路：toggle 打开 → 立刻拉每个岗位的预览（只读，给「原文→改成→为什么」）
               → 用户看清后点「生成 N 份」→ 后端把定制版 docx 落盘 → 一键代投时
               替原简历当附件发出去。事实门没过的岗位退回原件（绝不发编造内容）。 -->
          <div class="cfg-block" style="margin-top:.9rem;">
            <label class="switch-row">
              <input type="checkbox" class="switch" v-model="tailorOn" @change="onTailorToggle" />
              <span>按岗位定制简历（AI 据招聘要求改写/重排，生成专版附件）</span>
            </label>
            <p class="muted" v-if="tailorOn && !llmReady" style="margin:.4rem 0 0;">
              还没配置大模型，定制功能用不了。点右上角「配置 AI」填一下 base_url / 密钥 / 模型。
            </p>

            <button v-if="tailorOn && llmReady" class="btn" style="margin-top:.5rem;"
              :disabled="tailoring || !mailJobs.length" @click="buildTailored">
              {{ tailoring ? '正在为每个岗位生成定制版…'
                : ('生成 ' + mailJobs.length + ' 份定制简历') }}
            </button>

            <div v-if="tailorReports.length" class="tailor-reports">
              <p class="muted" style="margin:.5rem 0 .3rem;">
                下面是 AI 针对每个岗位改了什么（<b>原文 → 改成 → 为什么</b>）。
                事实核对没过的会退回你的原件。
              </p>
              <div v-for="t in tailorReports" :key="t.key" class="tailor-card">
                <div class="tc-head">
                  <b>{{ t.title }}</b>
                  <span v-if="t.blocked" class="tag tag-link">事实核对未过 · 用原件</span>
                  <span v-else-if="t.done" class="tag tag-ok">已生成专版</span>
                </div>
                <ul v-if="t.changes && t.changes.length" class="tc-list">
                  <li v-for="(c, i) in t.changes" :key="i">
                    <span class="tc-sec">{{ c.section }}</span>：
                    <span class="tc-before">{{ c.before }}</span>
                    <span class="tc-arrow">→</span>
                    <b class="tc-after">{{ c.after }}</b>
                    <span class="muted tc-why">（{{ c.why }}）</span>
                  </li>
                </ul>
                <ul v-if="t.gaps && t.gaps.length" class="tc-gaps">
                  <li v-for="(g, i) in t.gaps" :key="i">岗位要、简历没看到：{{ g }}</li>
                </ul>
                <p v-if="t.error" class="muted">{{ t.error }}</p>
              </div>
            </div>
          </div>
        </template>

        <!-- 这条也会先弹一个「用哪个邮箱发」的选择框。
             原来直接调 mailto: —— 电脑上没装邮件 App 时什么都不会发生，
             用户点了没反应，也不知道该去哪儿发。 -->
        <button class="btn" style="margin-top:.8rem;" @click="openPicker('batch')">
          用我自己的邮箱一封封发（{{ mailJobs.length }} 封）
        </button>
        <p class="muted" style="margin:.4rem 0 0;">
          这条不经过服务器，由你亲手发出去：选一个你常用的邮箱，我帮你打开它的写信页，
          标题和正文复制好，你粘上就发。
        </p>
      </div>

      <p v-else class="group-empty">
        这次选的岗位<b>都没有公开邮箱</b>，代投发不出去，只能去它们的招聘网站投 ——
        走下面那一组。
      </p>

      <!-- ---- 组 2：没留邮箱 → 只能自己去官网投（自动填表帮忙） ---- -->
      <div v-if="linkJobs.length" class="group group-link">
        <div class="group-head">
          <span class="tile tile-sm" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round">
              <path d="M10 14a5 5 0 0 0 7.1 0l2.4-2.4a5 5 0 0 0-7.1-7.1L11 5.9" />
              <path d="M14 10a5 5 0 0 0-7.1 0l-2.4 2.4a5 5 0 0 0 7.1 7.1L13 18.1" />
            </svg>
          </span>
          <span class="group-title">去官网投（无邮箱）</span>
          <b class="group-count">{{ linkJobs.length }} 个</b>
        </div>
        <p class="group-note">
          这些单位只在自家招聘网站上收简历，没有公开邮箱，邮件发不出去。
          点「开始逐个投递」，我一家一家帮你打开，并用<b>自动填表</b>把简历填进去——
          你只要在旁边看着，遇到需要你本人出面的地方（验证码、承诺书、最终提交）点一下。
        </p>
        <div v-for="j in linkJobs" :key="j.key" class="result-item">
          <span>{{ j.title }} · {{ j.company }}</span>
          <span class="ri-extra muted">无公开邮箱</span>
        </div>
        <button class="btn btn-primary btn-big" @click="startLink">
          开始逐个投递（{{ linkJobs.length }} 个）
        </button>
      </div>

      <p v-if="mailJobs.length && linkJobs.length" class="group-split">
        两组可以分开做、也可以都做：上面那组点一下按钮就全发完了，
        下面那组要一家家过。建议先把邮件发掉，再慢慢投官网。
      </p>

      <button class="btn btn-big" style="margin-top:1rem;" @click="go(3)">
        ← 回去再挑几个岗位
      </button>
    </div>

    <!-- ============ 逐个投递 ============ -->
    <div v-else-if="mode === 'running'" class="card">
      <h2 class="card-title">第 {{ idx + 1 }} / {{ runList.length }} 家</h2>
      <p class="card-hint" v-if="runMode === 'link'">
        这家没有公开邮箱，只能去它的招聘网站投。先点下面的按钮把它的招聘页打开。
      </p>
      <p class="card-hint" v-else>
        这封用你自己的邮箱发，一步一步来：<b>打开写信页 → 把下面三样挨个粘进去 → 发送</b>。
        内容我已经准备好了，你不用自己打字。
      </p>

      <div class="cur-box">
        <p style="font-size:1.2rem; font-weight:700; margin:0 0 .3rem;">
          {{ cur.title }}
          <span class="badge" :class="badgeClass(cur.nature)">{{ cur.nature }}</span>
        </p>
        <p style="margin:.2rem 0; font-size:1.05rem;">{{ cur.company }}</p>
        <p style="margin:.2rem 0;" class="muted">{{ cur.city }} · {{ cur.salary_text }}</p>
      </div>

      <button v-if="runMode === 'link'" class="btn btn-primary btn-big" @click="openApply">
        打开它的招聘页
      </button>

      <!-- 用自己邮箱发：邮箱网站不让别人替它填内容，所以给三块「复制」，
           打开写信页按顺序粘一遍。比调 mailto: 可靠 —— 电脑上没装邮件 App 时
           mailto 点了是没反应的（用户以为程序坏了）。 -->
      <template v-else>
        <p class="muted" v-if="!mailFor(cur)">正在准备这封的内容…</p>
        <template v-else>
          <div class="mailpane">
            <div class="mp-row">
              <span class="mp-k">收件人</span>
              <span class="mp-v">{{ mailTo(cur) }}</span>
              <button class="btn btn-mini" @click="copyField('to', cur)">
                {{ copied === 'to' ? '已复制' : '复制' }}
              </button>
            </div>
            <div class="mp-row">
              <span class="mp-k">主题</span>
              <span class="mp-v">{{ mailFor(cur).subject }}</span>
              <button class="btn btn-mini" @click="copyField('subject', cur)">
                {{ copied === 'subject' ? '已复制' : '复制' }}
              </button>
            </div>
            <div class="mp-row">
              <span class="mp-k">正文</span>
              <span class="mp-v mp-body">{{ mailFor(cur).preview }}</span>
              <button class="btn btn-mini" @click="copyField('body', cur)">
                {{ copied === 'body' ? '已复制' : '复制' }}
              </button>
            </div>
          </div>

          <button class="btn btn-primary btn-big" @click="openMailSite">
            打开 {{ chosenProvider ? chosenProvider.name : '邮箱' }} 写信
          </button>
          <p class="muted" style="margin:.5rem 0 0;" v-if="chosenProvider">
            打开后点「写信」，按<b>收件人 → 主题 → 正文</b>的顺序挨个粘贴，然后发送。
          </p>

          <div style="display:flex; gap:.8rem; margin-top:.8rem;">
            <button class="btn" style="flex:1;" @click="openPicker('single')">换一个邮箱</button>
            <button class="btn" style="flex:1;" @click="openMailApp">用邮件 App 打开</button>
          </div>
        </template>
      </template>

      <div style="display:flex; gap:.8rem; margin-top:1.2rem;">
        <button class="btn" style="flex:1;" @click="next(runMode === 'link' ? '已在官网投递' : '已用自己邮箱发送')">
          {{ runMode === 'link' ? '投完了，下一个' : '发完了，下一个' }}
        </button>
        <button class="btn" style="flex:1;" @click="next('已跳过')">这家算了</button>
      </div>

      <button class="btn" style="width:100%; margin-top:.8rem;" @click="go(3)">
        ← 先回去再挑几个岗位
      </button>

      <p class="muted" style="margin-top:1rem;">
        已经处理 {{ results.length }} / {{ runList.length }} 家
      </p>
    </div>

    <!-- ============ 自动填表（测试版）：只管「要自己去官网投」那组 ============ -->
    <AutoFillPanel v-if="mode === 'running' && runMode === 'link'" :job="cur"
                   :tailored="tailoredMap[cur.key]" @done="onAutoDone" />

    <!-- ============ 邮件代发进度 ============ -->
    <div v-if="task" class="card">
      <template v-if="task.status === 'running'">
        <h2 class="card-title">正在发邮件，请不要关掉页面</h2>
        <p class="card-hint">{{ task.done }} / {{ task.total }}
          <span v-if="task.current">　正在处理：{{ task.current }}</span>
        </p>
        <div class="progress"><i :style="{ transform: 'scaleX(' + (percent / 100) + ')' }"></i></div>
        <!-- 真发要等，等多久必须写出来：4 封等 80 秒，只说「间隔一会儿」
             用户会以为卡死了 —— 他就是这么以为的。 -->
        <p class="muted" style="margin-top:.6rem;" v-if="task.interval">
          每封之间要隔 {{ task.interval }} 秒（免得被判成垃圾邮件），
          这一批大约需要 {{ etaText }}，请别关页面。
        </p>
        <p class="muted" style="margin-top:.6rem;" v-else>正在准备发送，请别关页面。</p>
      </template>

      <!-- 没能开始：后端在发之前就先登了一次服务器，登录过不去就一封都不发。
           以前这种「整批卡在登录上」会照常跑完，用户等 80 秒拿到 4 条一样的
           失败，还以为是点了没反应。 -->
      <template v-else-if="task.status === 'error'">
        <div class="done-box done-bad">
          <h2 class="card-title">一封都没发出去</h2>
          <p class="muted">{{ task.msg || '这次投递没能开始。' }}</p>
        </div>
        <button class="btn btn-primary btn-big" style="margin-bottom:.6rem;" @click="openSetup">
          去改发件邮箱 / 授权码
        </button>
        <button class="btn btn-big" @click="openPicker('batch')">改用我自己的邮箱一封封发</button>
      </template>

      <template v-else>
        <div class="done-box" :class="{ 'done-bad': allFailed }">
          <h2 class="card-title">{{ doneTitle }}</h2>
          <p class="big" :class="{ bad: allFailed }">{{ doneBig }}</p>
          <!-- 全军覆没时，这一屏原来的写法是「邮件发完了 / 0 封 / 单位回复会
               直接发到你的邮箱」—— 用户扫一眼以为没事，这就是他说的「没反应」。
               现在最显眼的位置直接说结果：一封都没发出去。 -->
          <p class="muted" v-if="allFailed">
            <b>招聘方一封都没收到。</b>原因就在下面那条记录里 —— 基本都是发件邮箱这一关
            没过（授权码填成了登录密码、或那家不给用密码发信）。把邮箱设置改好，再点一次就行。
          </p>
          <p class="muted" v-else-if="task.fail">
            有 <b>{{ task.fail }}</b> 封发失败了，原因见下面的记录。
          </p>
          <p class="muted" v-if="!allFailed && !task.fail">单位回复会直接发到你的邮箱：{{ email }}</p>
          <!-- 早停：登录没过就不再用同样的密码把后面每封都撞一遍。
               必须交代还剩几封没发，否则用户以为整批都试过了。 -->
          <p class="muted" v-if="task.not_attempted">
            登录没通过，剩下的 <b>{{ task.not_attempted }}</b> 封<b>没有再试</b> ——
            这一批用的是同一个发件邮箱，登录过不去，再发也是同样的结果。
            把发件邮箱改好，再点一次「一键代投」。
          </p>
          <!-- 没邮箱的岗位要单独交代一句：它们不在上面这个数字里 -->
          <p class="muted" v-if="task.skipped">
            另外有 <b>{{ task.skipped }}</b> 个岗位没有公开邮箱，<b>没能代投</b>，
            需要到它们的招聘网站投。
          </p>
        </div>
        <button v-if="allFailed" class="btn btn-primary btn-big" @click="openSetup">
          去改发件邮箱 / 授权码
        </button>
      </template>

      <div v-if="task.items && task.items.length" style="margin-top:1rem;">
        <div v-for="(it, i) in task.items" :key="i" class="result-item">
          <span>{{ it.title }} · {{ it.company }}</span>
          <span :class="it.ok ? 'ok' : 'bad'">{{ it.status }}</span>
        </div>
      </div>
      <!-- 邮件发完直接把用户接到下一组，别让他自己回头找 -->
      <button v-if="linkJobs.length && task.status !== 'running'" class="btn btn-big"
        style="margin-top:1rem;" @click="startLink">
        接下来逐个去官网投（{{ linkJobs.length }} 个）
      </button>
    </div>

    <!-- ============ 完成 ============ -->
    <div v-if="mode === 'done'" class="card">
      <div class="done-box">
        <h2 class="card-title">都处理完了</h2>
        <p class="big">{{ doneCount }} 家</p>
        <p class="muted">接下来留意手机 {{ phone }} 和邮箱 {{ email }}，单位会联系你。</p>
      </div>
      <button class="btn btn-big" style="margin-bottom:.8rem;" @click="printPage">打印这份清单</button>
      <button class="btn btn-big" @click="again">再找一次</button>

      <div style="margin-top:1rem;">
        <div v-for="(r, i) in results" :key="i" class="result-item">
          <span>{{ r.title }} · {{ r.company }}</span>
          <span :class="r.ok ? 'ok' : 'muted'">{{ r.status }}</span>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗 1：设置发件邮箱 ============
         用户 2026-09-21 反馈：第 4 步根本没有「设置邮箱」的按钮，
         配置那套表单只在「有报名邮箱的岗位存在」时才渲染，等于找不到。
         现在从标题那行的按钮进来，并且带上「SMTP 怎么开」的分步说明 ——
         这个说明不能省：绝大多数人卡住的地方不是填不对，是不知道要去哪儿拿授权码。 -->
    <!-- 2026-09-21 用户反馈：「在填写账号密码的界面，点击任意地方都会弹出去」。
         原因是这里挂了 @click.self 关弹窗，而 .modal 有 1rem 内边距、内容超高时
         上下还留白 —— 那些留白在 DOM 上属于遮罩本身，点一下就关。
         这是一个**要填东西**的弹窗，误关的代价是用户白填一遍授权码（他就这么丢的），
         所以去掉遮罩点击关闭，只认右上角那个「关闭」按钮。 -->
    <div v-if="showSetup" class="modal">
      <div class="modal-box" role="dialog" aria-modal="true" aria-label="设置发件邮箱">
        <div class="modal-head">
          <h3>设置发件邮箱</h3>
          <button class="modal-x" @click="closeSetup" aria-label="关闭">关闭</button>
        </div>
        <div class="modal-body">
          <p class="modal-lead">
            <template v-if="cfgNoPwd">
              这个邮箱（微软系）<b>不能用密码替我发信了</b> —— 下面说清了原因和还能怎么用。
              想走「系统代发」就换一个邮箱，填它的<b>授权码</b>；想继续用这个邮箱，
              走「网页发送」那条路。
            </template>
            <template v-else>
              填你自己的邮箱和它给的<b>授权码</b>，我就能替你把这些邮件发出去。
              单位回的邮件会直接回到你本人邮箱，不会经过我。
            </template>
          </p>

          <p class="modal-state" v-if="mailReady">
            当前已设置{{ email ? '（' + email + '）' : '' }}：<b>可以真实发送</b>
          </p>

          <label class="field-label" for="cfg-email">你的邮箱地址</label>
          <input id="cfg-email" v-model="cfgEmail" type="email" class="cfg-in"
            @input="cfgDirty = true"
            placeholder="如 13800138000@139.com 或 xxx@qq.com" />

          <!-- 微软系邮箱：在他动手填之前就说清这条路是关着的。
               用户 2026-09-21 反馈「打开 Outlook 没看到授权码」——
               不是他找错地方，是微软已改成只认 OAuth2，页面上确实没有这一项。 -->
          <div v-if="cfgNoPwd" class="cfg-block">
            <p class="cfg-block-t">这个邮箱不能用密码代发 —— 不是你找错了地方</p>
            <p class="cfg-block-p">
              微软已经把 Outlook/Hotmail 的发信方式改成 <b>OAuth2（Modern Auth）</b>：
              账户设置页里「身份验证方法」那一行写的就是它，
              <b>不再提供授权码</b>，应用密码也一样失效（它就是建在旧验证方式上的）。
              所以你翻遍设置也找不到授权码，是对的。
            </p>
            <button class="btn" style="width:100%;" @click="useWebmailInstead">
              改用这个邮箱的网页发送（推荐，内容自动填好）
            </button>
            <p class="cfg-block-p">
              想让我帮你<b>一键代投</b>：换一个 QQ / 163 / 126 / 139 / 189 邮箱，
              照下面那几家的说明拿授权码再回来设置 —— 那几家才是填授权码的。
            </p>
          </div>

          <label class="field-label" for="cfg-pwd">密码（授权码，不是登录密码）</label>
          <!-- autocomplete="new-password" 不是为了好看：type="password" 的输入框
               会被浏览器记住并自动填充。用户 2026-09-21 就是这么翻车的 ——
               他打开这个面板只想换授权码，浏览器把上次输的登录密码填了进来，
               他一点「保存」，刚填好的授权码就被盖掉了。
               这个属性让浏览器不当它是「要填已有密码」的框。 -->
          <input id="cfg-pwd" v-model="cfgPwd" type="password" class="cfg-in"
            name="wb-mail-authcode" autocomplete="new-password"
            autocapitalize="off" autocorrect="off" spellcheck="false"
            @input="cfgDirty = true"
            placeholder="16 位授权码 / 客户端密码" />
          <p v-if="mailReady" class="modal-note" style="margin-top:.35rem;">
            只想换授权码就<b>只填这一格</b>；这一格<b>留空 = 不改</b>，沿用已保存的那串。
          </p>
          <div class="cfg-row">
            <button class="btn btn-primary" style="flex:1;" :disabled="cfgBusy" @click="saveMailConfig">
              {{ cfgBusy ? '保存中…' : '保存' }}
            </button>
            <button class="btn cfg-test" :disabled="verifyBusy" @click="verifyCfg">
              {{ verifyBusy ? '连服务器中…' : '测一测' }}
            </button>
          </div>
          <!-- 改了没保存必须当场说。「测一测」只要 6 秒就能回「登录成功」，
               很容易让人以为已经设好了 —— 用户 2026-09-21 就是这么丢的。 -->
          <p v-if="cfgDirty" class="cfg-unsaved">
            改了还没保存 —— 点左边那个<b>「保存」</b>才会生效。光点「测一测」只检查，不存。
          </p>
          <p class="modal-note">
            「保存」只改发件账号 —— 改完授权码点投递，就是用这个邮箱真发出去。
          </p>
          <p v-if="cfgMsg" class="cfg-msg">{{ cfgMsg }}</p>
          <p v-if="verifyMsg" class="cfg-msg" :class="{ 'cfg-msg-bad': verifyOk === false }">
            {{ verifyMsg }}
          </p>
          <p class="modal-note">
            「测一测」会真连一次你邮箱的服务器做登录，<b>不发信、不留记录</b> ——
            按一下就知道授权码填对没填对，比真发一封碰运气强。
          </p>

          <h4 class="modal-h4">授权码怎么拿？照着你用的邮箱做一次</h4>
          <p class="modal-warn">
            <b>授权码不等于登录密码。</b>填登录密码一定发不出去 ——
            这是最常卡住的地方。下面是各家邮箱「在哪儿开服务、要填什么」。
          </p>

          <div class="prov">
            <div v-for="p in providers" :key="p.id" class="prov-item">
              <p class="prov-t">
                {{ p.name }}
                <small>{{ p.domain }}</small>
                <span v-if="p.smtp_ok !== false" class="prov-tag">填：{{ p.auth }}</span>
                <span v-else class="prov-tag prov-tag-no">不能用密码代发</span>
              </p>
              <p class="prov-where" v-if="p.where">在哪开：{{ p.where }}</p>
              <ol class="prov-steps">
                <li v-for="(s, i) in p.steps" :key="i">{{ s }}</li>
              </ol>
              <p class="prov-note" v-if="p.note">{{ p.note }}</p>
              <p class="prov-smtp" v-if="p.smtp && p.smtp_ok !== false">
                服务器（不用你填，我自动认）：{{ p.smtp }}
              </p>
              <p class="prov-smtp" v-else-if="p.smtp">
                服务器 {{ p.smtp }} —— 只认 OAuth2，密码连不上，别照旧教程折腾
              </p>
              <a class="prov-link" :href="p.webmail" target="_blank" rel="noopener noreferrer">
                先打开 {{ hostOf(p.webmail) }} 去开服务
              </a>
            </div>
            <p v-if="!providers.length" class="modal-note">说明正在加载，稍等一下再打开。</p>
          </div>

        </div>
      </div>
    </div>

    <!-- ============ 弹窗 1.5：配置大模型（按岗位定制简历用）============ -->
    <div v-if="showLlm" class="modal" @click.self="closeLlm">
      <div class="modal-box" role="dialog" aria-modal="true" aria-label="配置大模型">
        <div class="modal-head">
          <h3 class="modal-title">配置大模型（定制简历用）</h3>
          <button class="modal-x" @click="closeLlm" aria-label="关闭">关闭</button>
        </div>
        <div class="modal-body">
          <p class="modal-lead">
            按岗位定制简历会把招聘要求发给一个兼容 OpenAI 的大模型，让它据 JD 改写你的简历。
            任何兼容 <code>/v1/chat/completions</code> 的厂商都行（DeepSeek / 通义 / 智谱 / 硅基流动 / Agnes 等）。
          </p>
          <label class="field">
            <span>API 地址</span>
            <input v-model="llmBase" placeholder="https://apihub.agnes-ai.com/v1" />
          </label>
          <label class="field">
            <span>API Key</span>
            <input v-model="llmKey" type="password" placeholder="sk-..." autocomplete="off" />
          </label>
          <label class="field">
            <span>模型名</span>
            <input v-model="llmModel" placeholder="agnes-2.5-flash" />
          </label>
          <p class="modal-note">
            密钥只存在服务器 <code>data/llm.json</code>（权限 600），不会上传到别处。留空不填 = 沿用已存的。
          </p>
          <div class="modal-actions">
            <button class="btn" :disabled="llmBusy" @click="saveLlm">保存</button>
            <span v-if="llmMsg" class="modal-state">{{ llmMsg }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗 2：用哪个邮箱发 ============
         点「用我自己的邮箱一封封发」原来直接跳 mailto: ——
         电脑上没装邮件 App 时点了一点反应都没有，用户不知道下一步该干什么。
         这里先让他挑一个自己认识的邮箱（只列国内常用的 + 两家国外），
         挑了就把它的写信页打开，再把收件人/主题/正文挨个复制好。 -->
    <div v-if="showPicker" class="modal" @click.self="closePicker">
      <div class="modal-box" role="dialog" aria-modal="true" aria-label="选择邮箱">
        <div class="modal-head">
          <h3>用哪个邮箱发？</h3>
          <button class="modal-x" @click="closePicker" aria-label="关闭">关闭</button>
        </div>
        <div class="modal-body">
          <p class="modal-lead">
            <template v-if="pickerFor === 'batch'">
              挑一个你<b>已经在用</b>的邮箱。挑好之后我一位一位帮你打开写信页，
              每一封都给你「收件人 / 主题 / 正文」三块复制按钮，粘上就能发。
            </template>
            <template v-else-if="pickerFor === 'setup'">
              挑一个你<b>已经在用</b>的邮箱 —— 微软系（Outlook/Hotmail）不能用密码
              替我发信，但用它<b>网页发送</b>完全没问题，点一下就打开它的写信页。
            </template>
            <template v-else>
              挑一个你<b>已经在用</b>的邮箱，我帮你打开它的写信页。
            </template>
          </p>

          <div class="pgrid">
            <button v-for="p in providers" :key="p.id" class="pbtn" @click="pickProvider(p)">
              <b>{{ p.name }}</b>
              <small>{{ hostOf(p.webmail) }}</small>
              <small class="pbtn-tag" v-if="p.compose">能自动填好</small>
            </button>
            <p v-if="!providers.length" class="modal-note">列表正在加载，稍等一下再打开。</p>
          </div>

          <div class="modal-note">
            <b>手机上更省事：</b>不用挑邮箱，直接
            <button class="linklike" @click="openMailApp">用手机上自带的邮件 App 打开</button>
            —— 收件人、标题、正文都是填好的，点发送就行。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { store, pickedJobs, go } from '../store'
import { api, initSocket, closeSocket } from '../api'
import AutoFillPanel from './AutoFillPanel.vue'

const jobs = pickedJobs
const email = store.profile.email
const phone = store.profile.phone

/* 这一屏的核心就这两行：把选中的岗位按「公告里有没有留报名邮箱」劈成两组。
   - mailJobs：有邮箱 → 能替你代投（邮件通道）
   - linkJobs：没邮箱 → 代投发不出去，只能去官网投（自动填表帮忙）
   不要再像以前那样在按钮里 filter 一遍把没邮箱的悄悄丢掉。 */
const mailJobs = computed(() => jobs.value.filter(j => (j.hr_email || '').trim()))
const linkJobs = computed(() => jobs.value.filter(j => !(j.hr_email || '').trim()))

const mode = ref('idle')          // idle | running | done
// 代发任务正在跑 / 正在提交。邮件发出去就收不回来，按钮必须在这期间锁住。
const sending = ref(false)
const busy = computed(() => sending.value ||
  (!!store.task && store.task.status === 'running'))

// 任务结束就解锁。走 WebSocket 时轮询会提前 return，解锁只能靠这里，
// 不然按钮会永久灰着，用户以为程序卡了。
watch(() => (store.task ? store.task.status : null), st => {
  if (st && st !== 'running') sending.value = false
})
const idx = ref(0)
const results = ref([])
// 逐个投递跑的是哪一组、怎么投：'link'=官网投（配自动填表）/ 'mailto'=用自己邮箱发
const runList = ref([])
const runMode = ref('link')
const mailReady = ref(false)
// 已保存的授权码形态可疑（比如填成了登录密码）时，后端给的提醒。
// 每次进这一步都重新拉一次 —— 不能只在「保存那一下」说过就完。
const mailPwdWarn = ref('')
// 已保存的发件邮箱（完整地址）。设置面板要拿它预填 —— 用户来这一步十有八九
// 是为了改授权码，不该逼他把邮箱再手打一遍（打错了又是一轮失败）。
const mailEmail = ref('')
// 代发邮箱配置面板
const cfgEmail = ref('')
const cfgPwd = ref('')
const cfgBusy = ref(false)
const cfgMsg = ref('')
// 框里改了东西但还没保存。用户 2026-09-21 的坑：授权码填进密码框、然后点了别处
// （弹窗被遮罩关掉），以为已经生效 —— 实测 data/mail.json 里还是旧的登录密码。
// 有这个标记，关弹窗之前就能拦住他，而不是等真发失败再来找原因。
const cfgDirty = ref(false)
// 「测一测」的结果（null=还没测过）
const verifyBusy = ref(false)
const verifyMsg = ref('')
const verifyOk = ref(null)

/* —— 邮箱服务商清单（后端 mailer.PROVIDERS 一份，界面两处共用）——
   设置弹窗里的「怎么开 SMTP」说明 + 挑邮箱弹窗里的按钮列表。 */
const providers = ref([])
// 不吃密码、只认 OAuth2 的域名（微软系）：手打邮箱时也要能当场认出来。
// 由后端给，前端不自己抄一份 —— 抄了就会和 mailer.OAUTH_ONLY_DOMAINS 走散。
const noPwdDomains = ref([])

// 用户填的邮箱是不是「微软系、不能用密码发信」的那种
const cfgNoPwd = computed(() => {
  const dom = (cfgEmail.value.split('@')[1] || '').trim().toLowerCase()
  return !!dom && noPwdDomains.value.includes(dom)
})

async function loadProviders() {
  try {
    const r = await api.mailProviders()
    providers.value = (r && r.providers) || []
    noPwdDomains.value = (r && r.oauth_only)
      || providers.value.filter(p => p.smtp_ok === false).map(p => p.domain)
  } catch (e) {
    providers.value = []
    noPwdDomains.value = []
  }
}

/** 从网址取域名，给按钮当小字用（`mail.qq.com` 比「QQ邮箱」更能让人确认是同一家）。 */
function hostOf(u) {
  try {
    return new URL(u).hostname.replace(/^www\./, '')
  } catch (e) {
    return ''
  }
}

// 标题那行的按钮文案要如实说出现在是什么状态：没设 / 已设。
const setupBtnText = computed(() => {
  if (!mailReady.value) return '设置发件邮箱'
  return '发件邮箱已设置'
})

const showSetup = ref(false)
const showPicker = ref(false)
const pickerFor = ref('batch')      // 'batch'=开始一封封发之前 / 'single'=当前这一封
const chosenProvider = ref(null)    // 用户挑了哪家邮箱
const copied = ref('')              // 刚复制的是哪一块（to/subject/body），用来出「已复制」
// 邮件内容按岗位缓存。key 是岗位 key —— 同一封不要每点一次都去后端重算一遍。
const mailCache = ref({})

function openSetup() {
  showSetup.value = true
  // 预填已保存的邮箱：用户点开这个面板多半是「授权码要重填」，
  // 邮箱这一格不该让他重打一遍。
  if (!cfgEmail.value) cfgEmail.value = mailEmail.value
  if (!providers.value.length) loadProviders()
}

/* 关设置弹窗。填了东西还没保存就拦一下 —— 用户 2026-09-21 填完授权码被
   误关（遮罩点击），以为已经设好了，结果配置里还是旧的登录密码。
   误关的代价是整个流程白走一遍，值得多问这一句。 */
function closeSetup() {
  if (cfgDirty.value && (cfgEmail.value.trim() || cfgPwd.value)) {
    if (!confirm('你填的邮箱/授权码还没保存，关掉就白填了。\n\n'
      + '点「取消」回去点一下「保存」；确实要放弃就点「确定」。')) return
  }
  showSetup.value = false
}

// 提前把这一组每封邮件的内容取回来。写信页链接要在点击的同步阶段拼，
// 那里等不了 await —— 不预热的话用户第一次点进去永远是空的邮箱首页，
// 收件人/主题/正文一个都不带。
async function prefetchMail(list) {
  for (const j of list) await loadMail(j)
}

function openPicker(scope) {
  pickerFor.value = scope || 'single'
  showPicker.value = true
  if (!providers.value.length) loadProviders()
  // 面板一开就预热：用户挑邮箱要点几秒，那时内容已经取回来了，
  // pickProvider 里拼的链接就带得上收件人/主题/正文。
  prefetchMail(scope === 'batch' ? mailJobs.value
    : (scope === 'setup' ? [] : [cur.value].filter(Boolean)))
}

function closePicker() {
  showPicker.value = false
}

/* 复制到剪贴板。http://127.0.0.1 之外的地址（比如打包后按局域网 IP 打开）
   拿不到 navigator.clipboard —— 那种情况下退回到老办法，
   不然用户点了「复制」看着像成功，其实什么都没复制进去。 */
async function copyText(text) {
  const s = text || ''
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(s)
      return true
    }
  } catch (e) { /* 往下走兜底 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = s
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    document.body.appendChild(ta)
    ta.select()
    const okc = document.execCommand('copy')
    document.body.removeChild(ta)
    return okc
  } catch (e) {
    return false
  }
}

function flashCopied(which) {
  copied.value = which
  setTimeout(() => { if (copied.value === which) copied.value = '' }, 2000)
}

// 邮件内容：收件人 / 主题 / 正文。正文由后端和 smtp 通道共用同一套生成逻辑，
// 保证「自己发的」和「替你发的」内容一模一样。
function mailFor(job) {
  return (job && mailCache.value[job.key]) || null
}

function mailTo(job) {
  return (job && job.hr_email) || ''
}

async function loadMail(job) {
  if (!job || !job.key) return null
  if (mailCache.value[job.key]) return mailCache.value[job.key]
  try {
    const r = await api.apply(store.profileId, [job], 'mailto', store.profile)
    const m = (r.mails || [])[0]
    if (m && m.ok) {
      mailCache.value = { ...mailCache.value, [job.key]: m }
      return m
    }
  } catch (e) { /* 取不到就让界面上那句「正在准备」留着，用户能重开 */ }
  return null
}

// 进到某一封（或者翻到下一封）时，把它的内容准备好
function ensureMail() {
  const j = cur.value
  if (runMode.value === 'mailto' && j && j.key) loadMail(j)
}

async function copyField(which, job) {
  const m = mailFor(job)
  if (!m) return
  const text = which === 'to' ? mailTo(job)
    : which === 'subject' ? m.subject
      : m.preview
  if (await copyText(text)) flashCopied(which)
}

/* 挑邮箱。
   关键：window.open 必须在**这次点击的同步阶段**就调 —— 下面要 await 拿邮件内容，
   等异步回来再开窗口会被浏览器当成弹窗拦掉，用户点了没反应（还是那个老毛病）。 */
function pickProvider(p) {
  chosenProvider.value = p
  const scope = pickerFor.value
  // 必须先把「这一轮」同步起来，cur 才指向这批里的第一封。
  // 原来这里 window.open 之后才 beginRun，取 mailFor(cur) 时 cur 还是上一轮的
  // （第一次压根是空的）→ 拼不出预填写信页，退化成只打开邮箱首页。
  if (scope === 'batch' && !runList.value.length) {
    beginRun(mailJobs.value, 'mailto')
  } else if (scope === 'single') {
    ensureMail()
  }
  const m = mailFor(cur.value)
  const url = (p.compose && m)
    ? p.compose
        .replace('{to}', encodeURIComponent(mailTo(cur.value)))
        .replace('{subject}', encodeURIComponent(m.subject))
        .replace('{body}', encodeURIComponent(m.preview))
    : p.webmail
  window.open(url, '_blank', 'noopener')
  closePicker()
  if (scope === 'single' && mode.value !== 'running') {
    // 'setup' 只是「先去把这个邮箱打开看看」，不该顺手开一次投递流程
    ensureMail()
  }
}

// 重新打开写信页（同一封里想再贴一次时用）
function openMailSite() {
  const p = chosenProvider.value
  if (!p) return openPicker('single')
  pickProvider(p)
}

/* 调起系统自带的邮件 App（手机上最省事，收件人/标题/正文都是填好的）。
   电脑上没装邮件 App 时点了不会发生任何事 —— 所以这只是**选项之一**，
   旁边永远留着「挑邮箱 + 复制」那条能走通的路。 */
async function openMailApp() {
  const m = mailFor(cur.value) || await loadMail(cur.value)
  const link = (m && m.mailto) || (cur.value && cur.value.mailto)
  if (link) {
    window.location.href = link
  } else {
    store.err = '没能打开邮件 App。请用上面的「打开邮箱写信」，复制内容后粘贴发送。'
  }
}

async function refreshMailStatus() {
  try {
    const r = await api.mailStatus()
    mailReady.value = !!r.ready
    mailPwdWarn.value = r.pwd_warn || ''
    mailEmail.value = r.email || ''
  } catch (e) {
    mailReady.value = false
    mailPwdWarn.value = ''
    mailEmail.value = ''
  }
}

async function saveMailConfig() {
  // 邮箱空着就不要发请求了：后端会回「邮箱地址填得不对」，而用户看到的是
  // 一句和「授权码」八竿子打不着的提示，转头又去折腾授权码。
  if (!cfgEmail.value.trim()) {
    cfgMsg.value = '邮箱地址还没填 —— 先在上面把你要用来发信的邮箱写上，再点保存。'
    const el = document.getElementById('cfg-email')
    if (el) el.focus()
    return
  }
  cfgBusy.value = true
  cfgMsg.value = ''
  try {
    const r = await api.mailConfig(cfgEmail.value.trim(), cfgPwd.value.trim())
    cfgMsg.value = r.ok
      ? (r.msg || '已保存') + '　点「一键代投」就会用这个邮箱真的发出去。'
      : (r.msg || '保存失败，检查邮箱格式')
    // 存得下不等于发得出：后端会回一句 warn（微软系邮箱），必须当场转告，
    // 不能只显示「已保存」让用户以为设好了。
    if (r.ok && r.warn) verifyMsg.value = r.warn
    if (r.ok) {
      cfgDirty.value = false
      await refreshMailStatus()
    }
  } catch (e) {
    cfgMsg.value = '网络错误，稍后再试'
  } finally {
    cfgBusy.value = false
  }
}

/* 「测一测」：真连一次服务器登录（不发信），把结论用大白话回给用户。
   没有这个按钮，用户唯一能验证的办法就是真发一封 —— 失败时只剩一串 535。 */
async function verifyCfg() {
  verifyBusy.value = true
  verifyMsg.value = ''
  verifyOk.value = null
  try {
    const r = await api.mailVerify(cfgEmail.value.trim(), cfgPwd.value)
    verifyOk.value = !!r.ok
    let m = r.msg || (r.ok ? '登录成功' : '没通过')
    // 登录成功 ≠ 已经设置好了。「测一测」只是拿框里的值去连一次服务器，
    // 不落盘 —— 必须把这句话接上，否则用户测完就走（2026-09-21 实际发生）。
    if (r.ok && cfgDirty.value) {
      m += '　—— 注意：这一步只是检查，还没保存。点左边「保存」才会生效。'
    }
    verifyMsg.value = m
  } catch (e) {
    verifyOk.value = false
    verifyMsg.value = '网络错误，稍后再试'
  } finally {
    verifyBusy.value = false
  }
}

/* 微软系邮箱的出路：关掉设置面板，直接去挑邮箱用网页发送。
   这条路不用授权码、也不会失败，比让用户在设置里反复试要有用。 */
function useWebmailInstead() {
  showSetup.value = false
  openPicker(mode.value === 'running' && runMode.value === 'mailto' ? 'single' : 'setup')
}

const cur = computed(() => runList.value[idx.value] || {})
const doneCount = computed(() => results.value.filter(r => r.ok).length)

const task = computed(() => store.task)
const percent = computed(() =>
  task.value && task.value.total ? Math.round((task.value.done / task.value.total) * 100) : 0
)

/* —— 结果面板的措辞 ——
   原来的面板只有「成功」一种口气：一封都没发出去时也会写「邮件发完了 / 0 封 /
   单位回复会直接发到你的邮箱」，用户扫一眼以为投出去了，所以反馈的是「没反应」。
   下面这几个计算属性就是为了让标题和数字如实反映失败。 */
const allFailed = computed(() => {
  const t = task.value
  return !!t && t.success === 0 && t.fail > 0
})
const doneTitle = computed(() => {
  const t = task.value || {}
  if (allFailed.value) return '一封都没发出去'
  if (t.fail) return '发出 ' + t.success + ' 封，' + t.fail + ' 封失败'
  return '邮件发完了'
})
const doneBig = computed(() => {
  const t = task.value || {}
  if (allFailed.value) return t.fail + ' 封失败'
  return t.success + ' 封'
})
// 真发时把等待时长说清楚（首封是立刻发的，所以按 total-1 估）
const etaText = computed(() => {
  const t = task.value || {}
  const sec = Math.max(0, (t.total || 0) - 1) * (t.interval || 0)
  if (sec < 60) return sec + ' 秒'
  return Math.floor(sec / 60) + ' 分 ' + (sec % 60) + ' 秒'
})

// ---------------------------------------------------------------- 按岗位定制简历
// 链路：toggle 打开 → 拉每个岗位的只读预览 → 用户点「生成 N 份」→ 后端落 docx →
// 一键代投时替原简历当附件。事实门没过的岗位退回原件（绝不发编造内容）。
const tailorOn = ref(false)
const llmReady = ref(false)
const tailorReports = ref([])   // [{key,title,job,payload,blocked,changes,gaps,error,done,url}]
const tailoring = ref(false)
const tailoredMap = ref({})      // 岗位 key -> {path, name}，传给一键代投

async function onTailorToggle() {
  tailoredMap.value = {}
  tailorReports.value = []
  if (!tailorOn.value) return
  if (!llmReady.value) await loadLlm()
  if (!llmReady.value) return
  tailoring.value = true
  const out = []
  for (const j of mailJobs.value) {
    try {
      const r = await api.tailorPreview(store.profileId, j, store.profile)
      if (r && r.ok) {
        out.push({ key: j.key, title: j.title, job: j, payload: r.payload,
          blocked: r.blocked, changes: (r.payload && r.payload.changes) || [],
          gaps: (r.payload && r.payload.gaps) || [], error: '', done: false })
      } else {
        out.push({ key: j.key, title: j.title, job: j, payload: null, blocked: true,
          changes: [], gaps: [], error: (r && r.msg) || '预览失败', done: false })
      }
    } catch (e) {
      out.push({ key: j.key, title: j.title, job: j, payload: null, blocked: true,
        changes: [], gaps: [], error: '预览出错', done: false })
    }
  }
  tailorReports.value = out
  tailoring.value = false
}

async function buildTailored() {
  if (!llmReady.value) return
  tailoring.value = true
  const map = {}
  for (const t of tailorReports.value) {
    if (t.blocked || !t.payload) continue
    try {
      const r = await api.tailorBuild(store.profileId, t.job, store.profile, t.payload)
      if (r && r.ok) {
        map[t.key] = { path: r.path, name: (t.title || '简历') + '-定制版.docx' }
        t.done = true
        t.url = r.url
      } else {
        t.error = (r && r.msg) || '生成失败'
      }
    } catch (e) {
      t.error = '生成出错'
    }
  }
  tailoredMap.value = map
  tailoring.value = false
}

// ---------------------------------------------------------------- 大模型配置
const showLlm = ref(false)
const llmBase = ref('')
const llmKey = ref('')
const llmModel = ref('')
const llmBusy = ref(false)
const llmMsg = ref('')

async function loadLlm() {
  try {
    const r = await api.llmConfig('GET')
    if (r) {
      llmReady.value = !!r.ready
      llmBase.value = r.base_url || ''
      llmModel.value = r.model || ''
      // 出于安全不回显 key，只在保存时发送
    }
  } catch (e) { /* 配置接口挂了就先当没配 */ }
}

function openLlm() {
  showLlm.value = true
  if (!llmBase.value) loadLlm()
}
function closeLlm() { showLlm.value = false }

async function saveLlm() {
  llmBusy.value = true
  llmMsg.value = ''
  try {
    const r = await api.llmConfig('POST', {
      base_url: llmBase.value, api_key: llmKey.value,
      model: llmModel.value, enabled: true })
    if (r && r.ok) {
      llmReady.value = !!r.ready
      llmMsg.value = r.ready ? '已保存，可用' : '已保存，但还不可用（检查密钥/模型名）'
      llmKey.value = ''   // 不留内存
    } else {
      llmMsg.value = '保存失败'
    }
  } catch (e) {
    llmMsg.value = '保存出错'
  } finally {
    llmBusy.value = false
  }
}

onMounted(() => {
  refreshMailStatus()
  loadProviders()
  loadLlm()
})

/* 从某一组开始逐个投递。两组走的是同一套「一家一家过」的界面，
   只是按钮和收尾状态文案不同（runMode 决定）。 */
function beginRun(list, kind) {
  runList.value = list.slice()
  runMode.value = kind
  idx.value = 0
  results.value = []
  mode.value = 'running'
  ensureMail()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function startLink() { beginRun(linkJobs.value, 'link') }

function openApply() {
  const url = cur.value && (cur.value.apply_url || cur.value.url)
  if (url) window.open(url, '_blank')
}

async function next(status) {
  const j = cur.value
  const ok = status !== '已跳过'
  if (ok && store.profileId) {
    try { await api.applied(store.profileId, j, status) } catch (e) { /* 记不住也不影响用户 */ }
  }
  results.value.push({
    title: j.title, company: j.company, ok, status: ok ? '已投递' : '已跳过'
  })
  if (idx.value + 1 >= runList.value.length) {
    mode.value = 'done'
  } else {
    idx.value++
    // 翻到下一封就把它的内容准备好（「复制」按钮要马上能用）
    ensureMail()
  }
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// 自动填表结束后在这里记一笔，用户点「下一个」时就不会重复记。
// 注意「流程走完」不等于「投出去了」——只有真的提交成功（拿到回执，
// 或者明确说已经替你提交了）才记「已自动填报提交」，其余记真实状态，
// 不然用户以为投了，其实卡在登录墙或者停在提交前，坑的是他。
async function onAutoDone(t) {
  if (!store.profileId) return
  const submitted = !!(t && (t.receipt || /已经替你提交/.test(t.note || '')))
  const status = submitted ? '已自动填报提交' : (t && t.note) || '自动填表未完成'
  try {
    await api.applied(store.profileId, cur.value, status)
  } catch (e) { /* 记不住也不影响用户 */ }
}

// ---------- 邮件代发 ----------
let taskId = ''
let timer = null
let gotSocket = false
// 每次投递的序号。旧任务的定时器/回包靠它自我了断——否则第二次点「一键代投」
// 后会有两个定时器并发轮询，句柄又被覆盖，旧的那个永远清不掉。
let runSeq = 0

function onProgress(t) {
  if (t && t.id === taskId) {
    gotSocket = true
    store.task = t
  }
}

function stopPoll() {
  if (timer) clearInterval(timer)
  timer = null
}

/* 一键代投：只发「能代投」那一组（公告里留了报名邮箱的）。
   以前这里是 jobs.filter(j => j.hr_email)，没邮箱的岗位被静默丢掉、
   用户看不到任何交代 —— 现在这组在界面上是明摆着的，数量也写在按钮上。

   这里还必须挡住重复点击：邮件发出去就收不回来，同一个人点两下，
   招聘方会收到两封一模一样的简历。以前面板出现后上面那张卡片还在、
   按钮照样能点，等于替用户承担了这个后果。 */
async function sendAll() {
  if (sending.value) return
  if (!mailJobs.value.length) {
    alert('这次选的岗位都没有公开报名邮箱，代投发不出去，请去它们的招聘网站投。')
    return
  }
  sending.value = true
  stopPoll()
  closeSocket()
  gotSocket = false
  const seq = ++runSeq
  store.task = {
    id: '', status: 'running', total: mailJobs.value.length,
    done: 0, success: 0, fail: 0, skipped: 0, current: '', items: []
  }
  let r
  try {
    r = await api.apply(store.profileId, mailJobs.value, 'smtp', store.profile,
      { tailored: tailoredMap.value })
  } catch (e) {
    // 网络/服务挂了不能有头无尾：以前这里面板会永远停在「正在发邮件，请不要关掉页面」
    store.task.status = 'error'
    store.task.msg = '连不上服务，请检查程序是不是还在运行'
    sending.value = false
    return
  }
  if (seq !== runSeq) return            // 已经不是最新一轮了，别再动界面
  if (!r.ok) {
    // 后端在开跑之前就把「登录过不去」挡回来了（发信前的登录预检）。
    // 原因必须原样显示出来 —— 只把面板切成一个空白的「出错」，
    // 用户看到的就是又一次「点了没反应」。
    store.task.status = 'error'
    store.task.msg = r.msg || '投递没能开始，稍后再试'
    refreshMailStatus()
    sending.value = false
    return
  }
  taskId = r.task_id
  store.task.id = taskId
  // 后端把 interval 一起回来了，占位面板立刻就是准的 ——
  // 否则在第一次轮询之前的 1.5 秒里，面板会写「大约需要 0 秒」。
  store.task.interval = r.interval || 0
  // 后端如果发现里面混了没邮箱的岗位，会回一个 skipped 数，照实显示
  if (r.skipped) store.task.skipped = r.skipped
  initSocket(onProgress)
  timer = setInterval(async () => {
    if (seq !== runSeq) { stopPoll(); return }
    if (gotSocket && store.task.status !== 'running') return
    const t = await api.task(taskId)
    if (seq !== runSeq) { stopPoll(); return }
    if (t.ok) store.task = t.task
    if (store.task && store.task.status !== 'running') {
      stopPoll()
      sending.value = false
    }
  }, 1500)
}

function printPage() {
  window.print()
}

function again() {
  stopPoll()
  closeSocket()
  gotSocket = false
  runSeq++                 // 让还在飞的回包作废
  sending.value = false
  store.task = null
  store.picked = []
  mode.value = 'idle'
  runList.value = []
  results.value = []
  go(2)
}

onUnmounted(() => {
  stopPoll()
  closeSocket()
})

function badgeClass(n) {
  if (n === '央企') return 'badge-central'
  if (n === '国企') return 'badge-state'
  if (n === '外企') return 'badge-foreign'
  if (n === '事业单位') return 'badge-institution'
  if (n === '公务员') return 'badge-civil'
  return 'badge-other'
}
</script>

<style scoped>
/* —— 两组的分隔与标签 ——
   颜色一律走 style.css 的 token，不在这里写死色值。 */
.group {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: .85rem .9rem;
  margin-bottom: 1rem;
  background: var(--surface);
}
.group-head {
  display: flex;
  align-items: center;
  gap: .5rem;
  margin-bottom: .5rem;
}
.group-count {
  font-size: 1.05rem;
}
.tag {
  display: inline-block;
  font-size: .85rem;
  font-weight: 700;
  padding: .18rem .6rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.tag-ok { background: var(--accent-1); color: var(--accent-7); }
.tag-link { background: var(--surface-2); color: var(--ink-2); }
.group-note {
  font-size: .95rem;
  line-height: 1.6;
  color: var(--ink-2);
  margin: 0 0 .7rem;
}
.group-note b { color: var(--ink); }
.group-empty {
  font-size: .95rem;
  line-height: 1.6;
  color: var(--warn);
  background: var(--warn-1);
  border-radius: var(--radius-sm);
  padding: .6rem .75rem;
  margin: 0 0 1rem;
}
.group-split {
  font-size: .92rem;
  line-height: 1.6;
  color: var(--ink-2);
  margin: 0 0 1rem;
}
.cfg {
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: .75rem .8rem;
  margin: .8rem 0;
}
.cfg-in {
  width: 100%;
  padding: .6rem .75rem;
  margin-bottom: .6rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
}
.ri-extra {
  font-size: .85rem;
  color: var(--ink-2);
  word-break: break-all;
  text-align: right;
}
.cur-box {
  background: var(--surface-2);
  border-radius: var(--radius);
  padding: 1rem;
  margin-bottom: 1rem;
}
/* 代投结果里「没发出去」的那几行要看得出来是坏的，不是灰成一样的 */
.result-item .bad { color: var(--danger); font-weight: 600; }

/* 一封都没发出去时的结果面板：整块染成警示红，和「发完了」一眼分得开。
   原来的面板不分成败都是同一个绿色标题 + 大字数字，失败只体现在下面几行
   灰色小字里 —— 用户扫一眼就是「没反应」。 */
.done-box.done-bad {
  background: var(--danger-1);
  border: 1px solid var(--danger);
  border-radius: var(--radius-sm);
}
.done-box .big.bad { color: var(--danger); }

/* —— 标题那行的「设置发件邮箱」入口 ——
   常驻、与这一屏的内容无关：库里带报名邮箱的岗位极少（2749 里 14 个），
   「能代投」那一组多数时候是空的，入口挂在组里就等于没有。 */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .6rem;
  flex-wrap: wrap;
  margin-bottom: .35rem;
}
.card-head .card-title { margin: 0; }
.head-btns {
  margin-left: auto;
  display: flex;
  gap: .4rem;
  flex-shrink: 0;
}
.setup-btn { flex: none; }
.setup-btn.on { background: var(--accent-1); border-color: var(--accent-7); color: var(--accent-7); }

@media (max-width: 430px) {
  .head-btns {
    width: 100%;
    margin-left: 0;
  }
  .head-btns .setup-btn {
    flex: 1;
  }
  .group-head {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .group-title {
    flex: 1 1 9rem;
  }
  .group-count {
    margin-left: auto;
  }
  .result-item {
    align-items: flex-start;
    flex-direction: column;
    gap: .2rem;
  }
  .ri-extra {
    text-align: left;
  }
}

/* 分组标题里的小号图标块（tile-sm：比卡片头的 2.3rem 小一号） */
.tile-sm {
  width: 1.9rem;
  height: 1.9rem;
  border-radius: 0.55rem;
}
.tile-sm svg {
  width: 1.05rem;
  height: 1.05rem;
}

/* —— 弹窗 ——
   入场用 @starting-style（CSS 原生进场，不需要 JS 里 setMounted）。
   过程中只动 opacity 和 transform，走 GPU，不触发重排。 */
.modal {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(18, 38, 58, .45);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 1rem;
  overflow-y: auto;
  /* 弹窗里滚到底，别把背后的整页带着一起动 */
  overscroll-behavior: contain;
  opacity: 1;
  transition: opacity var(--t-menu) var(--ease-out);
}
.modal-box {
  background: var(--surface);
  border-radius: var(--radius);
  width: 100%;
  max-width: 36rem;
  margin: auto;                    /* 内容比屏幕高时上下都留白，不顶到边 */
  box-shadow: 0 12px 40px rgba(18, 38, 58, .22);
  opacity: 1;
  transform: scale(1);
  transition: opacity var(--t-sheet) var(--ease-out),
    transform var(--t-sheet) var(--ease-out);
}
@starting-style {
  .modal { opacity: 0; }
  .modal-box {
    /* 从中心轻微放大淡入。弹窗不锚在某个触发点上，所以 origin 保持 center
       （下拉/气泡才要改成触发点）。起点 scale(0.95) 不是 0 ——
       现实里没有东西是从「什么都没有」里长出来的。 */
    opacity: 0;
    transform: scale(0.95);
  }
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .6rem;
  padding: .9rem 1.1rem;
  border-bottom: 1px solid var(--line);
}
.modal-head h3 { margin: 0; font-size: 1.1rem; }
.modal-x {
  min-height: 2.75rem;
  padding: 0 .9rem;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink-2);
  font-size: .95rem;
  font-weight: 600;
  cursor: pointer;
  transition: border-color var(--t-pop) var(--ease-out),
    color var(--t-pop) var(--ease-out), transform var(--t-press) var(--ease-out);
}
.modal-x:active { transform: scale(0.97); }
.modal-body {
  padding: 1rem 1.1rem 1.3rem;
  /* 软键盘弹出时弹窗内部要能继续滚到被挡住的那一栏 */
  overscroll-behavior: contain;
}
.modal-lead,
.modal-note {
  font-size: .95rem;
  line-height: 1.65;
  color: var(--ink-2);
  margin: 0 0 .9rem;
}
.modal-lead b, .modal-note b { color: var(--ink); }
.modal-state {
  font-size: .92rem;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: .5rem .7rem;
  margin: 0 0 .8rem;
  color: var(--ink-2);
}
.modal-state b { color: var(--ink); }
.modal-h4 { font-size: 1rem; margin: 1.2rem 0 .5rem; }
.modal-warn {
  font-size: .95rem;
  line-height: 1.65;
  color: var(--warn);
  background: var(--warn-1);
  border-radius: var(--radius-sm);
  padding: .6rem .75rem;
  margin: 0 0 .9rem;
}
.field-label {
  display: block;
  font-size: .92rem;
  font-weight: 600;
  margin: 0 0 .3rem;
}
.cfg-msg { margin: .6rem 0 0; font-size: .92rem; color: var(--ink-2); }
.cfg-msg-bad { color: var(--warn); }
/* 「改了还没保存」的提醒条：紧贴保存按钮，橙底深字，扫一眼就能看见。
   用户 2026-09-21 就是填完授权码没保存（弹窗还被误关），以为已经设好了。 */
.cfg-unsaved {
  margin: .55rem 0 0;
  padding: .5rem .65rem;
  border-radius: var(--radius-sm);
  background: var(--warn-1);
  color: var(--warn);
  font-size: .9rem;
  line-height: 1.6;
}
.cfg-unsaved b { color: var(--warn); }
/* 填邮箱/授权码那两个按钮并排：左边保存，右边「测一测」 */
.cfg-row { display: flex; gap: .5rem; margin-top: .2rem; }
.cfg-row .btn { margin-top: 0; }
.cfg-test { white-space: nowrap; }
/* 微软系邮箱的说明块：不是用户的错，是「这家服务商把这条路关了」，
   所以用提醒色（琥珀），不用报错红。 */
.cfg-block {
  border: 1px solid var(--warn);
  background: var(--warn-1);
  border-radius: var(--radius-sm);
  padding: .7rem .8rem;
  margin: .5rem 0 .8rem;
}
.cfg-block-t { margin: 0 0 .4rem; font-weight: 700; font-size: .95rem; }
.cfg-block-p { margin: 0 0 .6rem; font-size: .9rem; line-height: 1.65; color: var(--ink-2); }
.cfg-block-p:last-child { margin-bottom: 0; }
.cfg-block .btn { margin-top: 0; margin-bottom: .7rem; }

/* —— 「怎么开 SMTP」说明：一家一块，照着点就行 —— */
.prov-item {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: .7rem .8rem;
  margin-bottom: .7rem;
  background: var(--surface-2);
}
.prov-item:last-child { margin-bottom: 0; }
.prov-t { margin: 0 0 .3rem; font-weight: 700; font-size: 1rem; }
.prov-t small { color: var(--ink-3); font-weight: 400; margin-left: .3rem; }
.prov-tag {
  display: inline-block;
  margin-left: .4rem;
  font-size: .8rem;
  font-weight: 700;
  padding: .1rem .5rem;
  border-radius: var(--radius-pill);
  background: var(--primary-1);
  color: var(--primary-7);
}
.prov-where { margin: 0 0 .4rem; font-size: .9rem; color: var(--ink-2); }
/* 「不能用密码代发」：和旁边的「填：授权码」必须一眼分得开，
   否则用户还是会照着 Outlook 那块去找授权码 */
.prov-tag-no { background: var(--danger-1); color: var(--danger); }
.prov-steps { margin: 0 0 .4rem; padding-left: 1.3rem; font-size: .92rem; line-height: 1.7; }
.prov-note { margin: 0 0 .4rem; font-size: .9rem; color: var(--warn); line-height: 1.6; }
.prov-smtp { margin: 0 0 .4rem; font-size: .85rem; color: var(--ink-3); }
.prov-link { font-size: .92rem; color: var(--primary-7); font-weight: 600; }

/* —— 挑邮箱 —— */
.pgrid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: .55rem;
}
.pbtn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: .1rem;
  min-height: 3.6rem;
  padding: .6rem .7rem;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
  font-size: 1rem;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--t-pop) var(--ease-out),
    color var(--t-pop) var(--ease-out), transform var(--t-press) var(--ease-out);
}
.pbtn:active { transform: scale(0.97); }
/* hover 必须门控在「真能悬停」的设备上：触屏点一下会伪造 hover 并挂住不放 */
@media (hover: hover) and (pointer: fine) {
  .pbtn:hover { border-color: var(--primary); color: var(--primary-7); }
}
.pbtn small { font-size: .78rem; font-weight: 400; color: var(--ink-3); }
.pbtn .pbtn-tag { color: var(--accent-7); font-weight: 700; }
.linklike {
  border: none;
  background: none;
  padding: 0;
  color: var(--primary-7);
  font-size: inherit;
  font-weight: 700;
  text-decoration: underline;
  cursor: pointer;
}

/* —— 用自己邮箱发：收件人/主题/正文三块，各带一个「复制」 —— */
.mailpane {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: .7rem .8rem;
  margin-bottom: .8rem;
  background: var(--surface-2);
}
.mp-row {
  display: flex;
  align-items: flex-start;
  gap: .5rem;
  padding: .45rem 0;
  border-top: 1px solid var(--line);
}
.mp-row:first-child { border-top: none; padding-top: 0; }
.mp-k {
  flex: none;
  width: 3.4rem;
  font-size: .9rem;
  font-weight: 700;
  color: var(--ink-2);
}
.mp-v {
  flex: 1 1 auto;
  min-width: 0;
  font-size: .92rem;
  line-height: 1.55;
  word-break: break-all;
}
/* 正文里的换行要保住，不然粘到邮箱里是一坨 */
.mp-body {
  white-space: pre-wrap;
  max-height: 11rem;
  overflow-y: auto;
  font-size: .88rem;
  color: var(--ink-2);
}
.mp-row .btn-mini { flex: none; }

/* 手机屏：把「复制」挪到第一行右边，正文整行铺开 ——
   否则窄屏上正文只剩百来像素宽，用户没法核对要发出去的内容。 */
@media (max-width: 480px) {
  .mp-row { flex-wrap: wrap; }
  .mp-k { order: 1; }
  .mp-row .btn-mini { order: 2; margin-left: auto; }
  .mp-v { order: 3; flex: 1 1 100%; margin-top: .2rem; }
}

/* —— 按岗位定制简历 —— */
.switch-row {
  display: flex; align-items: center; gap: .6rem;
  font-size: .95rem; cursor: pointer; line-height: 1.5;
}
.tailor-reports { margin-top: .6rem; }
.tailor-card {
  border: 1px solid var(--line); border-radius: var(--radius-sm);
  padding: .6rem .7rem; margin-bottom: .6rem; background: var(--surface-2);
}
.tc-head { display: flex; align-items: center; gap: .5rem; margin-bottom: .35rem; }
.tc-head b { font-size: .98rem; }
.tc-list { margin: 0; padding-left: 1.1rem; }
.tc-list li { margin: .2rem 0; line-height: 1.55; }
.tc-sec { color: var(--ink-2); font-size: .9rem; }
.tc-before { text-decoration: line-through; color: var(--warn); }
.tc-arrow { margin: 0 .35rem; color: var(--ink-2); }
.tc-after { color: var(--accent-7); }
.tc-why { font-size: .85rem; }
.tc-gaps { margin: .3rem 0 0; padding-left: 1.1rem; color: var(--ink-2); font-size: .9rem; }

/* —— 大模型配置弹窗的表单 —— */
.field { display: block; margin: .6rem 0; }
.field > span { display: block; font-size: .85rem; color: var(--ink-2); margin-bottom: .25rem; }
.field input {
  width: 100%; box-sizing: border-box; padding: .5rem .6rem;
  border: 1px solid var(--line); border-radius: var(--radius-sm);
  background: var(--surface); color: var(--ink); font-size: .95rem;
}
.modal-actions { display: flex; align-items: center; gap: .8rem; margin-top: .8rem; }
.modal-actions .modal-state { font-size: .9rem; color: var(--accent-7); }
</style>
