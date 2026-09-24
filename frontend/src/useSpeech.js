// 语音：朗读提示（给不识字/看不清的人）+ 语音输入（给不会打字的人）

let utter = null

export function canSpeak() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export function speak(text) {
  if (!canSpeak() || !text) return
  stopSpeak()
  utter = new SpeechSynthesisUtterance(text)
  utter.lang = 'zh-CN'
  utter.rate = 0.95
  window.speechSynthesis.speak(utter)
}

export function stopSpeak() {
  if (canSpeak()) window.speechSynthesis.cancel()
}

export function canRecord() {
  return typeof window !== 'undefined' &&
    !!(window.SpeechRecognition || window.webkitSpeechRecognition)
}

/** 开始语音输入。
 *  onResult(text, isFinal)：text 是**到此刻为止的完整内容**（不是本次增量），
 *  调用方应该用「开始说话前的原值 + text」覆盖字段，不要拿 text 去累加 ——
 *  continuous + interimResults 下同一句话会触发多次，
 *  每次回传的都是从头累积的整段，累加会变成「我我叫我叫张三」。
 *  给不会打字的人用的功能，他们更难发现并改正这串乱码。
 */
export function record(onResult, onEnd) {
  if (!canRecord()) return null
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  const r = new SR()
  r.lang = 'zh-CN'
  r.continuous = true
  r.interimResults = true
  let finished = false
  let committed = ''            // 已经定稿（isFinal）的部分
  r.onresult = e => {
    let finalTxt = ''
    let interim = ''
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i]
      if (res.isFinal) finalTxt += res[0].transcript
      else interim += res[0].transcript
    }
    if (finalTxt) committed += finalTxt
    onResult && onResult(committed + interim, !!finalTxt)
  }
  const end = cause => () => {
    if (finished) return
    finished = true
    committed = ''
    onEnd && onEnd(cause)
  }
  r.onerror = end('error')
  r.onend = end('end')
  try {
    r.start()
    return r
  } catch (e) {
    return null
  }
}

/** 停掉识别器并解绑回调。换字段、卸载组件前必须调，
 *  否则上一个识别器还在跑，继续往上一个字段里写字。 */
export function stopRecord(r) {
  if (!r) return
  try {
    r.onresult = null
    r.onerror = null
    r.onend = null
    r.stop()
  } catch (e) { /* 已经停了就算了 */ }
}
