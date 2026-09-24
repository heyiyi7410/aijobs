# 找工作助手 · 设计流程规范

本文件是这个产品**做任何界面改动时的唯一流程依据**。规则来源：
Emil Kowalski 的 `emil-design-eng`（设计工程哲学）、`animate`（动效构建序列）、
`mobile-native`（手机端平台层修复）三份技能，按本产品的实际场景改写。

> 产品约束（优先于任何通用规范）：
> 1. **尺寸一律 rem** —— 顶部 A+/A- 会改 `html` 的 font-size（18px ↔ 23px），
>    px 会在这个开关下错位。
> 2. **触控目标 ≥ 44px** —— 目标用户手不稳、不常上网。
> 3. **焦点环必须可见** —— 不许 `outline: none` 了事。
> 4. **不用 emoji / 字符当图标** —— 勾、点、箭头一律 CSS 画，避免字体差异。
> 5. **数字等宽** —— 分数、条数、秒数用 `font-variant-numeric: tabular-nums`。

---

## 一、动效决策门（写任何动画前按顺序回答）

### 第 1 问：这个东西该动吗？看用户一天看它几次

| 频次 | 决策 | 本产品对应 |
| --- | --- | --- |
| 100+ 次/天 | **永不动画** | 键盘操作、A+/A- 字号开关（改字号必须瞬时生效，动画会让阅读中途发飘） |
| 几十次/天 | 只留近乎无感的（快速且细微），否则不做 | 岗位卡选中、勾选行、步骤条回跳 |
| 偶发 | 标准动效 | 弹窗（配置邮箱 / 配置 AI）、抽屉、进度条推进 |
| 罕见 / 首次 | 可以给惊喜 | 投递完成页、首批结果出现的 stagger |

**键盘触发的动作是硬性排除项，不是判断题。** 用户按 A+ 调字号时，`html` 字号必须一帧到位。

### 第 2 问：目的是什么？说不出来就不做

只能说这几个词之一：**反馈**（按钮按下）、**空间一致性**（从哪来、回哪去）、
**状态指示**（状态变了看得出来）、**避免突变**（元素凭空出现会显得坏掉）、
**解释**（演示功能怎么用）、**惊喜**（仅限罕见档）。

同时查功能：**用户正在读、正在判断的数据不许为样式而动。** 岗位列表、投递结果、
匹配分数都是数据，不做装饰性位移。

### 第 3 问：用什么工具（走下来，第一个合适的就停）

| 需要 | 工具 |
| --- | --- |
| hover / 按压缩放 / 颜色 / 由 class 或属性控制的开关 | **CSS transition** |
| 挂载即入场、无 JS 状态 | **CSS `@starting-style`** |
| 预定好的动效、且要在页面忙时仍不掉帧 | **CSS animation**（跑在主线程外） |
| 需要 JS 控制又要 CSS 性能 | **WAAPI** `element.animate()` |
| 弹簧、布局动画、退场动画、手势驱动值 | Motion（本项目暂未引入，非必要不装） |

CSS 动画在页面忙时比 JS 稳（JS 走 rAF，主线程一忙就掉帧）。**预定动效用 CSS，动态可中断的用 JS。**

### 第 4 问：动哪些属性

- **只动 `transform` 和 `opacity`**（跳过 layout 与 paint，走 GPU）。`clip-path` 是官方认可的第四个。
- 不动 `width / height / margin / padding / top / left`。**唯一例外**：手风琴的高度没有 transform 等价物。
- **永远不从 `scale(0)` 入场** —— 用 `scale(0.95) + opacity: 0`。现实里没有东西从"什么都没有"里长出来。
- 弹窗从**中心**缩放（`transform-origin: center`，它不锚在某个触发点上）；
  下拉、浮层、气泡必须从**触发点**缩放，不是中心。
- `translate()` 里用百分比：`translateY(100%)` 就是"移动自身高度"，不用写死 px。

### 第 5 问：曲线与时长（值只能取这里的，不许自己编）

曲线（内置的 `ease-out` 太弱，不够有意图感）：

```css
--ease-out:      cubic-bezier(0.23, 1, 0.32, 1);      /* 入场 / 退场 / UI 默认 */
--ease-in-out:   cubic-bezier(0.77, 0, 0.175, 1);     /* 屏幕内移动、形变 */
--ease-drawer:   cubic-bezier(0.32, 0.72, 0, 1);      /* 抽屉 / 底部弹层（iOS 手感） */
/* 常量运动（跑马灯、不定长进度）用 linear；hover / 颜色变化用 ease */
```

**UI 里永远不用 `ease-in`。** 它开头慢，正好慢在用户盯得最紧的那一瞬间 ——
同样 200ms，`ease-out` 感觉比 `ease-in` 快。

时长阶梯（**UI 动效一律 < 300ms**）：

| 阶梯 | 值 | 用在哪 |
| --- | --- | --- |
| `--t-press` | 120ms | 按钮/卡片按压缩放 |
| `--t-pop` | 180ms | 岗位卡、勾选、徽标等状态切换 |
| `--t-menu` | 200ms | 下拉、浮层、提示条 |
| `--t-sheet` | 280ms | 弹窗、抽屉（含内容入场） |

### 第 6 问：中断与退场

- **可能被连续触发的元素用 transition，不用 keyframes**（进度条推进、结果条逐条出现）——
  transition 从当前值重定向，keyframes 从零重来。
- **退场沿用入场路径**（从下进来的就往下走），对称路径才让"滑走关掉"变得直觉。
- 中间可能被打断的用弹簧；仅在"用户正在决策"的阶段放慢，系统响应永远要快。

### 第 7 问：降级与门控（跟动效同批写，不许留作后续）

- **减少动态效果：是"更少更轻"，不是"全砍"** —— 保留 opacity / 颜色过渡（它们帮助理解），
  去掉位移和缩放。
- **hover 必须能力门控**：`@media (hover: hover) and (pointer: fine)`。
  触屏没有 hover，浏览器会在"第一次点"时伪造一个并挂住不放 —— 按钮点完一直保持在悬停态。
- 触屏仍需按下反馈，交给 `:active`（所有输入方式都生效）。

---

## 二、组件分级（本项目组件 → 档位 → 该有的动效）

| 组件 | 档位 | 目的 | 实现 |
| --- | --- | --- | --- |
| A+/A- 字号开关 | 100+/天 | — | **不动画**，一帧生效 |
| `.btn` / `.chip` / `.job` 按压 | 几十次/天 | 反馈 | `:active` `scale(0.97)`，`--t-press` ease-out |
| `.btn` / `.chip` / `.job` hover | 几十次/天 | — | 只改颜色/边框，`--t-pop`；**门控在细指针设备** |
| `.stepbar .s` 步骤态 | 几十次/天 | 状态指示 | 颜色与边框过渡 `--t-pop`，不位移 |
| 岗位卡选中 `.job.picked` | 几十次/天 | 状态指示 | 边框+底色过渡；勾的 `opacity` 淡入 |
| `.job` 列表首次出现 | 罕见 | 避免突变 | `stagger` 入场，间隔 40ms，最多错开 8 项 |
| 进度条 `.progress > i` | 偶发 | 状态指示 | 只动 `transform: scaleX()`，240ms ease-out |
| 结果条 `.result-item` | 偶发 | 避免突变 | transition 淡入+微位移，`--t-pop` |
| 弹窗 `.modal-box` | 偶发 | 空间一致性 | `@starting-style`：`scale(0.95)`+`opacity:0`，居中 origin，`--t-sheet` |
| 抽屉（若引入） | 偶发 | 空间一致性 | `translateY(100%)` → 0，`--ease-drawer`，`--t-sheet` |
| 投递完成页 | 罕见 | 惊喜 | 允许稍长，仍 ≤ 300ms 的位移 + 数字等宽 |
| `.pull-spin` 加载圈 | 偶发 | 状态指示 | `linear infinite`（**转速要快**，同样的加载时间会让人觉得更快） |

---

## 三、手机端基线（做任何移动端改动前先确认这 12 条）

| 症状 | 处置 |
| --- | --- |
| 点完 hover 卡住 | hover 规则全部包进 `@media (hover: hover) and (pointer: fine)` |
| 点击一片灰/蓝闪 | `html { -webkit-tap-highlight-color: transparent }`，然后每个可点元素必须有 `:active` |
| 高度不对、底部按钮被 URL 栏盖住 | 应用外壳用 `100dvh`；首屏 hero 用 `100svh`；`100vh` 只留给老浏览器的兜底行 |
| 聚焦输入框就放大页面 | 输入框字号 ≥ 16px（本项目 `1.1rem` @18px = 19.8px ✅），**不许用 `maximum-scale` 修** |
| 点了半天才有反应 | 反馈做在 `:active`（不是 `click`）+ `touch-action: manipulation` |
| 下拉刷新把整页刷掉 | `html, body { overscroll-behavior: none }`（本产品是分步向导，刷新=丢掉全部进度，必须禁）；弹窗内滚动容器用 `contain` |
| 内容顶到刘海/黑条下面 | `viewport-fit=cover` + `env(safe-area-inset-*)`；本产品底部行动条 `.navbar` 已加 |
| 长按选中按钮文字 | 控件加 `user-select: none`；**正文绝不许加**（用户要复制地址、错误信息） |
| 横滑却纵向抖动 | 横滑轨道 `touch-action: pan-y`；完全自管手势的面板才 `touch-action: none` |
| 状态栏颜色不搭 | `theme-color` 按 `prefers-color-scheme` 给两套，取值用**页面顶部的颜色**（不是品牌色） |
| 模拟器里好、真机上不对 | 上面每一条都只在真机复现 —— 见第四节的验收 |
| 用户的手机是唯一真相 | 无法在真机验证的，明确说"这条要你在手机上确认"，不许声称已修好 |

---

## 四、验收流程

1. **代码级自查**：跑一遍第五节的 Never Ship 表，逐条对照。
2. **构建与回归**：`vite build` 必须通过；`backend/test_web.py` 里读前端源码的静态断言要同步更新。
3. **真机验收（唯一算数的环节）**：手机连同一 WiFi，开本地或线上地址，逐项确认：
   - 长按按钮 → 不出现选中/复制菜单，没有 hover 粘滞
   - 点按钮 → 有按下反馈，无灰闪，无明显延迟
   - 屏幕顶部下拉 → 不触发整页刷新
   - 底部按钮 → 不被 iPhone 小黑条遮挡
   - 开弹窗 → 从中心轻微放大淡入，不是凭空出现
   - 系统开"减少动态效果" → 页面不再有位移/缩放，但状态切换仍能看懂
   - 键盘弹出 → 底部输入/按钮不被键盘挡住
4. **次日回看**：动效第二天用新眼睛再看一遍，全速下看不见的时序问题这时会露出来。

---

## 五、Never Ship（提交前自查，每条都是硬性打回项）

| 不许 | 改成 |
| --- | --- |
| `transition: all` | 写清属性名 |
| `transform: scale(0)` 入场 | `scale(0.95)` + `opacity: 0` |
| UI 元素上用 `ease-in` | `ease-out` 或上面的强曲线 |
| 该用强曲线的地方用内置 `ease-out` | `cubic-bezier(0.23, 1, 0.32, 1)` |
| 键盘动作 / 每天上百次的操作带动画 | 完全不动画 |
| UI 时长 > 300ms 且没有理由 | 收到 120–280ms |
| 浮层用 `transform-origin: center` | 改为触发点（弹窗豁免，保持居中） |
| 高频触发元素用 keyframes | 改 CSS transition |
| 动 `width/height/margin/padding/top/left` | 改 `transform` / `opacity` |
| 未门控的 `:hover` 位移/缩放 | 包进 `(hover: hover) and (pointer: fine)` |
| 缺少"减少动态效果"分支 | 给更轻的版本，不是零 |
| 所有元素一起出现 | 加 30–80ms stagger |
| `user-scalable=no` / `maximum-scale=1` | 把输入框字号提到 16px，修因不修果 |
| `100vh` 当应用外壳或贴底 UI | `100dvh` |
| 用 `touchmove` + `preventDefault()` 阻止下拉刷新 | `overscroll-behavior` |
| 在 `body` 上写 `user-select: none` | 只写在控件上 |
| 靠 UA 字符串猜触屏 | 用 `(hover)` / `(pointer)` 媒体查询 |
| 只在设备模拟器里验完就宣布好了 | 真机 |

---

## 六、评审输出格式（强制）

评审界面/动效代码时**必须**用这张三列表格，每行一个问题，不许写成
"Before: … / After: …" 的竖排列表：

| Before | After | Why |
| --- | --- | --- |
| `transition: all 300ms` | `transition: transform 200ms ease-out` | 写清属性；避免 all |
| `transform: scale(0)` | `transform: scale(0.95); opacity: 0` | 现实里没有东西从无到有 |
| 下拉用 `ease-in` | `ease-out` + 强曲线 | `ease-in` 开头慢，显得迟钝 |
| 按钮没有 `:active` | 按下 `scale(0.97)` | 按下去必须有反馈 |
| 浮层 `transform-origin: center` | 触发点 origin | 应从触发它的地方长出来 |
