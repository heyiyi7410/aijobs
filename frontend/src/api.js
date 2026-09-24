import { io } from 'socket.io-client'

async function req(url, body) {
  const opt = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {}
  const r = await fetch(url, opt)
  return await r.json()
}

export const api = {
  saveProfile: p => req('/api/profile', p),
  // 传上来的简历读成字段（不落库，只是读一遍给用户核对）
  parseResume: body => req('/api/resume/parse', body),
  // force=true：跳过服务端缓存直接现爬（职位拉到底、用户手动下拉要更多时用）
  // wide=false：用户勾掉了「连全国招聘和本省一起看」，后端只留本市岗位
  match: (p, force, wide) =>
    req('/api/match', { profile: p, force: !!force, wide: wide !== false }),
  // 按单位名搜岗位：求职者认准某一家单位时直接敲名字搜
  searchCompany: (company, p, profileId, city) =>
    req('/api/jobs/company', { company, profile: p, profile_id: profileId, city }),
  apply: (profileId, jobs, channel, profile, extra) =>
    req('/api/apply', { profile_id: profileId, jobs, channel, profile, ...(extra || {}) }),
  // 大模型配置（按岗位定制简历用）：base_url / api_key / model / enabled
  llmConfig: (method, body) =>
    req('/api/llm/config', method === 'POST' ? body : undefined),
  // 按岗位做定制方案的「只看不改」预览：返回 原文→改成→为什么 对照 + 缺口 + 事实门结果
  tailorPreview: (profileId, job, profile) =>
    req('/api/tailor/preview', { profile_id: profileId, job, profile }),
  // 把定制方案渲染成 docx（事实门未过直接拒绝）。返回可下载 path/url
  tailorBuild: (profileId, job, profile, payload) =>
    req('/api/tailor/build', { profile_id: profileId, job, profile, payload }),
  // 跳转投递的回执：用户在对方网站投完，回来记一笔
  applied: (profileId, job, status) =>
    req('/api/applied', { profile_id: profileId, job, status }),
  task: id => req('/api/task/' + id),
  mailStatus: () => req('/api/mail/status'),
  // 网页里配置代发邮箱（邮箱+授权码，服务商自动识别）。保存即生效，点投递就是真发
  mailConfig: (email, password) =>
    req('/api/mail/config', { email, password }),
  // 常见邮箱服务商：SMTP 参数 + 「怎么开 SMTP」的分步说明 + 网页版地址。
  // 数据只有后端一份（mailer.PROVIDERS），前端不自己抄 —— 抄一份就会走散。
  mailProviders: () => req('/api/mail/providers'),
  // 「测一测」：真连一次服务器做登录（不发信），把「授权码对不对 / 这个邮箱
  // 到底能不能用密码发」直接告诉用户。email 留空就用已保存的配置测。
  mailVerify: (email, password) =>
    req('/api/mail/verify', { email, password }),

  // 自动填表
  autofillStart: body => req('/api/autofill/start', body),
  autofillTask: id => req('/api/autofill/task/' + id),
  autofillAnswer: (taskId, value) =>
    req('/api/autofill/answer', { task_id: taskId, value }),
  // 投递前预检：国聘必填、缺了保存过不去的字段清单（单一数据源在后端）
  autofillRequired: () => req('/api/autofill/required')
}

let socket = null

/** 连接 WebSocket 接收实时投递进度；连不上时前端自动改用轮询。 */
export function initSocket(onProgress) {
  // 每次「一键代投」都会调一次。不先关掉旧的，就会不断叠加连接和 progress
  // 监听：老监听挂在老 socket 上永远回收不掉，回调也会互相打架。
  closeSocket()
  try {
    socket = io({ transports: ['websocket', 'polling'], timeout: 4000 })
    socket.on('progress', onProgress)
    return socket
  } catch (e) {
    socket = null
    return null
  }
}

/** 断开 WebSocket 并解绑监听。组件卸载、重新投递前都要调，否则连接只增不减。 */
export function closeSocket() {
  if (!socket) return
  try {
    socket.off('progress')
    socket.disconnect()
  } catch (e) { /* 已经断了就算了 */ }
  socket = null
}
