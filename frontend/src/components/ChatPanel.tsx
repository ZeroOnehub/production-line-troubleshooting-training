import { useEffect, useRef, useState } from 'react'
import { api, TARGET_NAMES, type Snapshot } from '../api'

export interface FeedItem {
  who: 'me' | 'narrator' | 'coach' | 'phone' | 'system'
  text: string
  meta?: string
}

const WHO: Record<FeedItem['who'], string> = { me: '我', narrator: '主持人', coach: '带班师傅', phone: '电话', system: '系统' }

function useVoicePlayback(enabled: boolean) {
  return async (text: string, speaker: string) => {
    if (!enabled || !text) return
    try {
      const blob = await api.tts(text, speaker)
      if (blob) {
        const url = URL.createObjectURL(blob)
        const a = new Audio(url)
        await a.play()
        return
      }
    } catch {
      /* 服务端合成不可用 */
    }
    if ('speechSynthesis' in window) {
      const u = new SpeechSynthesisUtterance(text)
      u.lang = 'zh-CN'
      window.speechSynthesis.speak(u)
    }
  }
}

function MicButton({ disabled, onAudio }: { disabled: boolean; onAudio: (b: Blob) => void }) {
  const [rec, setRec] = useState<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const start = async () => {
    if (disabled || rec) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunks.current = []
      mr.ondataavailable = (e) => chunks.current.push(e.data)
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        onAudio(new Blob(chunks.current, { type: 'audio/webm' }))
      }
      mr.start()
      setRec(mr)
    } catch {
      alert('无法使用麦克风，请用文字输入')
    }
  }
  const stop = () => {
    rec?.stop()
    setRec(null)
  }
  return (
    <button className={`btn shrink-0 ${rec ? 'border-hmi-red text-hmi-red' : ''}`} disabled={disabled} onMouseDown={start} onMouseUp={stop} onMouseLeave={() => rec && stop()}
      onTouchStart={start} onTouchEnd={stop} title={disabled ? '离线回放 / 降级模式下语音输入关闭' : '按住说话'}>
      {rec ? '松开发送' : '🎙 按住说话'}
    </button>
  )
}

export default function ChatPanel({ snap, mode, busy, feed, onSay, onHint, onAudio }: {
  snap: Snapshot
  mode?: string
  busy: boolean
  feed: FeedItem[]
  onSay: (text: string) => void
  onHint: (q?: string) => void
  onAudio: (b: Blob) => void
}) {
  const phoneActive = !!snap.support && snap.support.kind === 'andon' && !snap.support.reported
  const [tab, setTab] = useState<'narrator' | 'coach' | 'phone'>('narrator')
  const [text, setText] = useState('')
  const [voice, setVoice] = useState(false)
  const play = useVoicePlayback(voice)
  const box = useRef<HTMLDivElement>(null)
  const lastLen = useRef(feed.length)

  useEffect(() => {
    if (phoneActive) setTab('phone')
  }, [phoneActive])
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight })
    if (feed.length > lastLen.current) {
      const last = feed[feed.length - 1]
      if (last.who === 'coach') play(last.text, 'coach')
      if (last.who === 'phone') play(last.text, snap.support?.target || 'maintenance')
    }
    lastLen.current = feed.length
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed])

  const shown = feed.filter((f) => (tab === 'coach' ? f.who === 'coach' || (f.who === 'me' && f.meta === 'coach') : tab === 'phone' ? f.who === 'phone' || (f.who === 'me' && f.meta === 'phone') : f.who !== 'coach' && f.meta !== 'coach' && f.who !== 'phone' && f.meta !== 'phone'))
  const transcript = snap.support?.transcript || []

  const submit = () => {
    const t = text.trim()
    if (!t || busy) return
    setText('')
    if (tab === 'coach') onHint(t)
    else onSay(t)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-hmi-line px-2 pt-1">
        {(['narrator', 'coach', 'phone'] as const).map((k) => (
          <button key={k} className={`rounded-t px-3 py-1 text-xs ${tab === k ? 'bg-hmi-panel2 text-white' : 'text-hmi-dim'}`} onClick={() => setTab(k)}>
            {k === 'narrator' ? '说出你的操作' : k === 'coach' ? '问带班师傅' : `Andon 电话${phoneActive ? ' ●' : ''}`}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-[11px] text-hmi-dim">
          <input type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} /> 语音播报
        </label>
      </div>
      <div ref={box} className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2 text-sm">
        {tab === 'phone' && (
          <div className="space-y-1.5">
            {transcript.length === 0 && <div className="text-xs text-hmi-dim">拉 Andon 后，维修技术员 / 质量员 / 班组长会打电话来。说清：哪个工位、什么报警、做了什么、隔离了几件 NG。</div>}
            {transcript.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'text-right' : ''}>
                <span className={`inline-block max-w-[90%] rounded px-2 py-1 ${m.role === 'user' ? 'bg-hmi-amber/20' : 'bg-hmi-blue/20'}`}>
                  <span className="mr-1 text-[11px] text-hmi-dim">{m.role === 'user' ? '我' : TARGET_NAMES[snap.support?.target || ''] || '电话'}</span>{m.content}
                </span>
              </div>
            ))}
          </div>
        )}
        {tab !== 'phone' && shown.map((f, i) => (
          <div key={i} className={f.who === 'me' ? 'text-right' : ''}>
            <span className={`inline-block max-w-[92%] whitespace-pre-wrap rounded px-2 py-1 ${f.who === 'me' ? 'bg-hmi-amber/20' : f.who === 'coach' ? 'bg-hmi-green/15' : f.who === 'system' ? 'bg-hmi-red/15' : 'bg-hmi-panel2'}`}>
              <span className="mr-1 text-[11px] text-hmi-dim">{WHO[f.who]}{f.meta && f.who !== 'me' ? ` · ${f.meta}` : ''}</span>{f.text}
            </span>
          </div>
        ))}
        {tab === 'coach' && shown.length === 0 && <div className="text-xs text-hmi-dim">卡住了就问师傅。提示分三级：先追问、再指方向、最后示范；用得越多，“提示”这一维扣分越多。</div>}
        {busy && <div className="text-xs text-hmi-dim">处理中…</div>}
      </div>
      <div className="flex gap-1 border-t border-hmi-line p-2">
        {tab === 'coach' && <button className="btn shrink-0" disabled={busy || snap.ended} onClick={() => onHint()}>要个提示</button>}
        <input className="input" placeholder={tab === 'coach' ? '问师傅一个问题（可不填，直接点“要个提示”）' : tab === 'phone' ? '在电话里汇报：工位、报警码、做了什么、几件 NG' : '比如：看看批头 / 先暂停，再把这件放进 NG 箱 / 拉 Andon 叫维修'}
          value={text} disabled={snap.ended || (tab === 'phone' && !phoneActive)} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} />
        <button className="btn btn-primary shrink-0" disabled={busy || snap.ended || !text.trim()} onClick={submit}>发送</button>
        {tab !== 'coach' && <MicButton disabled={mode !== 'online' || snap.ended || busy} onAudio={onAudio} />}
      </div>
    </div>
  )
}
