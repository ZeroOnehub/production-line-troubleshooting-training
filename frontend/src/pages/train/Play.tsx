import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Hotspot, type Json, type Snapshot } from '../../api'
import ActionPanel from '../../components/ActionPanel'
import ChatPanel, { type FeedItem } from '../../components/ChatPanel'
import Panorama from '../../components/Panorama'
import { ChecklistCard, HMIPanel, Observation, StatusStrip, TourCard } from '../../components/Widgets'
import { ErrorLine, Panel } from '../../components/ui'
import { useApp } from '../../store'

function feedFromSnapshot(s: Snapshot): FeedItem[] {
  const out: FeedItem[] = [{ who: 'system', text: s.scenario.story }]
  for (const e of s.log) {
    if (e.actor === 'system') continue
    if (e.source === 'nl' || e.source === 'click') out.push({ who: 'narrator', text: e.message, meta: e.source === 'click' ? '点选' : undefined })
  }
  return out
}

export default function Play() {
  const { id } = useParams()
  const nav = useNavigate()
  const { meta, station, loadStation, loadMeta } = useApp()
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [obs, setObs] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    loadStation().catch((e) => setError(String(e)))
    if (!meta) loadMeta()
  }, [loadStation, loadMeta, meta])

  useEffect(() => {
    if (!id) return
    api.session(id).then((s) => {
      setSnap(s)
      setFeed(feedFromSnapshot(s))
      const last = [...s.log].reverse().find((e) => e.observation)
      if (last?.observation) setObs(last.observation)
    }).catch((e) => setError(String(e.message || e)))
    // WebSocket 状态推送（多窗口同步，例如投屏）
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    let ws: WebSocket | null = null
    try {
      ws = new WebSocket(`${proto}://${location.host}/ws/sessions/${id}`)
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data)
        if (m.type === 'snapshot') setSnap(m.snapshot)
      }
    } catch {
      /* 推送不可用时以接口返回为准 */
    }
    return () => ws?.close()
  }, [id])

  const pushResults = useCallback((results: Json[], fallbackReply?: string) => {
    const lastObs = [...results].reverse().find((r) => r.observation || r.closeup)
    if (lastObs) setObs({ name: lastObs.params?.target ? station?.targets[lastObs.params.target] || lastObs.params.target : lastObs.action, text: lastObs.observation || lastObs.message, closeup: lastObs.closeup })
    if (fallbackReply) return
  }, [station])

  const act = async (action: string, params: Json = {}) => {
    if (!id) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.act(id, action, params)
      setSnap(r.snapshot)
      const res = r.result
      setFeed((f) => [...f, { who: res.rejected ? 'system' : 'narrator', text: res.message, meta: '点选' }])
      if (res.observation) setObs(res.observation)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const say = async (text: string) => {
    if (!id || !snap) return
    const phone = !!snap.support && snap.support.kind === 'andon' && !snap.support.reported
    setFeed((f) => [...f, { who: 'me', text, meta: phone ? 'phone' : undefined }])
    setBusy(true)
    setError(null)
    try {
      const r = await api.say(id, text)
      setSnap(r.snapshot)
      if (r.kind === 'phone') {
        setFeed((f) => [...f, { who: 'phone', text: r.ai.reply }])
      } else {
        const ai = r.ai
        const tag = ai.mode === 'online' ? '' : ai.mode === 'replay' ? '离线回放' : '规则'
        setFeed((f) => [...f, { who: 'narrator', text: ai.reply + (ai.guard_blocked ? '（安全守卫已拦截一条越权建议）' : ''), meta: tag || undefined }])
        pushResults(ai.results || [])
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const sayAudio = async (blob: Blob) => {
    if (!id) return
    setBusy(true)
    try {
      const r = await api.sayAudio(id, blob)
      if (r.kind === 'asr_failed') {
        setFeed((f) => [...f, { who: 'system', text: `语音识别失败：${r.error}。请用文字输入。` }])
      } else {
        setSnap(r.snapshot)
        setFeed((f) => [...f, { who: 'me', text: r.transcript?.text || '（语音）' }, { who: r.kind === 'phone' ? 'phone' : 'narrator', text: r.ai?.reply || '' }])
        pushResults(r.ai?.results || [])
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const hint = async (q?: string) => {
    if (!id) return
    if (q) setFeed((f) => [...f, { who: 'me', text: q, meta: 'coach' }])
    setBusy(true)
    try {
      const r = await api.hint(id, q)
      setSnap(r.snapshot)
      const lv = ['', '一级·追问', '二级·指方向', '三级·示范'][r.hint.level]
      setFeed((f) => [...f, { who: 'coach', text: r.hint.text, meta: lv + (r.hint.mode === 'online' ? '' : r.hint.mode === 'replay' ? ' · 离线回放' : ' · 规则') }])
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const onHotspot = (h: Hotspot) => {
    if (h.target === 'hmi') {
      act('inspect', { target: 'hmi' })
      return
    }
    act('inspect', { target: h.target })
  }

  // 正常生产阶段：开局后自动“继续生产”一次，让事件发生（学员也可以手动点）
  useEffect(() => {
    if (!snap || started.current) return
    started.current = true
  }, [snap])

  if (error && !snap) return <div className="p-6"><ErrorLine error={error} /></div>
  if (!snap || !station) return <div className="p-6 text-hmi-dim">加载训练局…</div>

  const litTargets = snap.tour ? snap.tour.codex.filter((c) => c.lit).flatMap((c) => c.targets) : []

  return (
    <div className="flex h-[calc(100vh-48px)] flex-col gap-2 p-2">
      <div className="flex items-center gap-2">
        <Link to="/train/map" className="btn btn-ghost !px-2 text-xs">← 关卡地图</Link>
        <div className="font-semibold text-white">{snap.scenario.title}</div>
        <div className="truncate text-xs text-hmi-dim">目标：{snap.scenario.objective}</div>
        <div className="ml-auto flex gap-1">
          {snap.state === 'normal_production' && !snap.ended && snap.scenario.type !== 'tour' && (
            <button className="btn btn-primary text-xs" disabled={busy} onClick={() => act('run_production')}>▶ 继续生产</button>
          )}
          {!snap.ended && <button className="btn text-xs" disabled={busy} onClick={async () => { if (confirm('放弃本局并进入复盘？')) { const r = await api.end(snap.session_id); setSnap(r.snapshot) } }}>结束本局</button>}
          {snap.ended && <button className="btn btn-primary text-xs" onClick={() => nav(`/train/review/${snap.session_id}`)}>查看复盘 →</button>}
        </div>
      </div>
      <StatusStrip snap={snap} metaLabel={meta?.mode_label} mode={meta?.mode} />
      <ErrorLine error={error} />
      {snap.ended && (
        <div className={`rounded border px-3 py-2 text-sm ${snap.terminated_by ? 'border-hmi-red bg-hmi-red/15 text-red-200' : snap.objective_met ? 'border-hmi-green bg-hmi-green/10 text-green-200' : 'border-hmi-amber bg-hmi-amber/10 text-amber-100'}`}>
          {snap.terminated_by ? `安全终止（${snap.terminated_by}）：本局判定失败。` : snap.objective_met ? '关卡目标完成。' : '本局结束。'}
          {snap.fail_reasons.length > 0 && ` ${snap.fail_reasons.join('；')}`}
          <button className="btn btn-primary ml-3 !py-0.5 text-xs" onClick={() => nav(`/train/review/${snap.session_id}`)}>查看复盘</button>
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_380px] 2xl:grid-cols-[minmax(0,1fr)_420px_420px]">
        <div className="flex min-h-0 flex-col gap-2">
          <div className="min-h-[220px] flex-[1.25] 2xl:flex-[3]">
            <Panorama station={station} snap={snap} onSelect={onHotspot} litTargets={litTargets} />
          </div>
          <div className="grid min-h-[250px] flex-1 grid-cols-1 gap-2 md:grid-cols-2 2xl:grid-cols-1">
            <Panel title="近景与观察" className="min-h-0 overflow-hidden">
              <Observation obs={obs} assetsBase={station.assets_base} sources={station.closeup_sources} />
            </Panel>
            <Panel title="对话" className="min-h-0 2xl:hidden">
              <ChatPanel snap={snap} mode={meta?.mode} busy={busy} feed={feed} onSay={say} onHint={hint} onAudio={sayAudio} />
            </Panel>
          </div>
        </div>
        <div className="flex min-h-0 flex-col gap-2 overflow-y-auto">
          <HMIPanel snap={snap} onInspect={(t) => act('inspect', { target: t })} />
          {snap.support && (
            <div className="panel px-3 py-2 text-sm">
              {snap.support.kind === 'andon' ? `Andon：${({ maintenance: '维修技术员', quality: '质量员', team_leader: '班组长' } as Record<string, string>)[snap.support.target]}` : '物料员'}
              {snap.support.reported ? `，预计 ${Math.ceil((snap.support.arrival_in || 0) / 60)} 分钟后到场（点“等待到场”）` : '，电话已接通，请在“Andon 电话”里汇报'}
            </div>
          )}
          {snap.checklist && <Panel title="开班点检记录表"><ChecklistCard snap={snap} onRecord={(item, result) => act('record_check', { item, result })} /></Panel>}
          {snap.tour && <Panel title="图鉴与工作循环"><TourCard snap={snap} /></Panel>}
          {snap.changeover?.active && <div className="panel px-3 py-2 text-sm text-hmi-amber">换型中：A → {snap.changeover.to_variant} 型{snap.changeover.verified ? '，首件已合格' : ''}{snap.changeover.quality_confirmed ? '，质量已确认' : ''}</div>}
          <Panel title="动作面板"><ActionPanel snap={snap} onAct={act} /></Panel>
          {snap.violations.length > 0 && (
            <Panel title="规则记录">
              <div className="space-y-1 p-2 text-xs">
                {snap.violations.map((v: Json, i: number) => <div key={i} className={v.severity === 'veto' ? 'text-hmi-red' : v.severity === 'major' ? 'text-hmi-amber' : 'text-hmi-dim'}>{v.message}</div>)}
              </div>
            </Panel>
          )}
        </div>
        <Panel title="对话" className="hidden min-h-0 2xl:flex">
          <ChatPanel snap={snap} mode={meta?.mode} busy={busy} feed={feed} onSay={say} onHint={hint} onAudio={sayAudio} />
        </Panel>
      </div>
    </div>
  )
}
