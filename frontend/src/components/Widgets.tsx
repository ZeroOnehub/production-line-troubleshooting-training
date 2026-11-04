import { useEffect, useState } from 'react'
import { fmtMin, type Json, type Snapshot } from '../api'
import { Badge } from './ui'

const LIGHT: Record<string, string> = { green: 'bg-hmi-green', yellow: 'bg-hmi-amber', red: 'bg-hmi-red', off: 'bg-zinc-700' }

/** HMI 触摸屏：网页组件实现，内容全部来自引擎状态。 */
export function HMIPanel({ snap, onInspect }: { snap: Snapshot; onInspect: (target: string) => void }) {
  const h = snap.hmi
  const alarm = h.alarm
  return (
    <div className="rounded-md border-2 border-zinc-700 bg-[#071219] p-2 font-mono text-[13px] text-cyan-100 shadow-inner">
      <div className="mb-1 flex items-center justify-between text-[11px] text-cyan-300/70">
        <span>OP30 电机总成螺栓拧紧 · HMI</span>
        <span>{snap.mode_label}</span>
      </div>
      <button className={`mb-2 w-full rounded px-2 py-1.5 text-left ${alarm ? 'animate-pulse bg-red-900/70 text-red-100' : 'bg-cyan-950/60 text-cyan-200'}`} onClick={() => onInspect('hmi')}>
        {alarm ? `报警 ${alarm} ${h.alarm_text}` : '无报警'}
      </button>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        <span className="text-cyan-300/70">当前型号</span><span>{h.variant_name}</span>
        <span className="text-cyan-300/70">程序号</span><span>{h.program}</span>
        <span className="text-cyan-300/70">扫码型号</span><span>{h.scanned_variant ? `${h.scanned_variant} 型` : '—'}</span>
        <span className="text-cyan-300/70">扭矩</span><span className={String(h.torque_result).startsWith('NG') ? 'text-red-300' : ''}>{h.torque_result}</span>
        <span className="text-cyan-300/70">角度</span><span className={String(h.angle_result).startsWith('NG') ? 'text-red-300' : ''}>{h.angle_result}</span>
        <span className="text-cyan-300/70">计数</span><span>OK {h.ok_count} / NG {h.ng_count}</span>
        <span className="text-cyan-300/70">循环</span><span className="truncate">{h.cycle_step}</span>
      </div>
      <div className="mt-2 flex gap-1">
        <button className="btn !px-2 !py-0.5 text-[11px]" onClick={() => onInspect('tighten_curve')}>拧紧曲线</button>
        <button className="btn !px-2 !py-0.5 text-[11px]" onClick={() => onInspect('bit_counter')}>批头计数</button>
        <button className="btn !px-2 !py-0.5 text-[11px]" onClick={() => onInspect('hmi')}>报警详情</button>
      </div>
    </div>
  )
}

/** 停线计时与损失金额：异常发生即开始计时（引擎时钟 + 本地走秒）。 */
export function StatusStrip({ snap, metaLabel, mode }: { snap: Snapshot; metaLabel?: string; mode?: string }) {
  const [, tick] = useState(0)
  const [since, setSince] = useState(Date.now())
  useEffect(() => setSince(Date.now()), [snap.downtime, snap.clock])
  useEffect(() => {
    if (!snap.downtime_running) return
    const t = setInterval(() => tick((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [snap.downtime_running])
  const local = snap.downtime_running ? Math.min(30, (Date.now() - since) / 1000) : 0
  const dt = snap.downtime + local
  const loss = Math.round((dt / 60) * snap.loss_per_min)
  const light = snap.indicators.tower_light
  return (
    <div className="panel flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className={`h-3 w-3 rounded-full ${LIGHT[light]} ${light === 'red' ? 'animate-pulse' : ''}`} />
        <span className="font-semibold text-white">{snap.state_label}</span>
        <Badge>{snap.mode_label}</Badge>
      </div>
      <div className={`tabular-nums ${snap.downtime_running ? 'text-hmi-red' : 'text-hmi-dim'}`}>
        停线 <span className="text-lg font-semibold">{fmtMin(dt)}</span>
      </div>
      <div className={`tabular-nums ${snap.downtime_running ? 'text-hmi-red' : 'text-hmi-dim'}`}>损失 ¥<span className="text-lg font-semibold">{loss.toLocaleString()}</span><span className="ml-1 text-[11px]">（示意 {snap.loss_per_min} 元/分钟）</span></div>
      <div className="text-hmi-dim">训练时钟 {fmtMin(snap.clock)}</div>
      {snap.target_pieces ? <div className="text-hmi-dim">产量 {snap.produced_ok}/{snap.target_pieces}</div> : <div className="text-hmi-dim">合格 {snap.produced_ok} 件</div>}
      <div className="ml-auto flex items-center gap-2 text-xs text-hmi-dim">
        <span>提示 {snap.hints_used} 次</span>
        {metaLabel && <Badge tone={mode === 'online' ? 'green' : mode === 'offline' ? 'amber' : 'red'}>AI：{metaLabel}</Badge>}
      </div>
    </div>
  )
}

/** 最近一次观察（近景图 + 引擎返回的事实）。 */
export function Observation({ obs, assetsBase, sources = {} }: { obs: Json | null; assetsBase: string; sources?: Record<string, string> }) {
  if (!obs) return <div className="flex h-full items-center justify-center p-4 text-sm text-hmi-dim">点击全景里的热点，或在下方说“看看批头”，近景会显示在这里。</div>
  return (
    <div className="flex h-full gap-3 overflow-y-auto p-3">
      {obs.closeup ? (
        <div className="relative w-1/2 shrink-0">
          <img src={`${assetsBase}/${obs.closeup}`} alt={obs.name} className="max-h-full w-full rounded object-contain bg-black/40" />
          <span className="absolute bottom-1 left-1 tag border-hmi-line bg-black/60 text-[10px] text-hmi-dim">{sources[obs.closeup] === 'ai' ? 'AI 生成素材' : '示意图'}</span>
        </div>
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-white">{obs.name}</div>
        <div className="mt-1 text-sm leading-relaxed">{obs.text}</div>
        {obs.codex && (
          <div className="mt-2 rounded border border-hmi-green/50 bg-hmi-green/10 p-2 text-xs">
            <div className="font-semibold text-hmi-green">图鉴点亮：{obs.codex.name}</div>
            <div className="mt-1 text-hmi-text">{obs.codex.desc}</div>
          </div>
        )}
      </div>
    </div>
  )
}

export function ChecklistCard({ snap, onRecord }: { snap: Snapshot; onRecord: (item: string, result: 'ok' | 'ng') => void }) {
  if (!snap.checklist) return null
  return (
    <div className="space-y-1 p-2 text-xs">
      {snap.checklist.items.map((it) => (
        <div key={it.id} className="flex items-center gap-2 rounded bg-hmi-bg px-2 py-1">
          <span className={`h-2 w-2 rounded-full ${it.checked ? 'bg-hmi-cyan' : 'bg-zinc-600'}`} title={it.checked ? '已检查' : '未检查'} />
          <span className="w-12 text-hmi-dim">{it.id}</span>
          <span className="flex-1" title={it.standard}>{it.name}</span>
          {it.sign_item ? (
            <span className={it.recorded ? 'text-hmi-green' : 'text-hmi-dim'}>{it.recorded ? '已签字' : '未签字'}</span>
          ) : it.recorded ? (
            <span className={it.recorded === 'ok' ? 'text-hmi-green' : 'text-hmi-amber'}>{it.recorded === 'ok' ? '正常' : '异常已处理'}</span>
          ) : (
            <span className="flex gap-1">
              <button className="btn !px-1.5 !py-0 text-[11px]" disabled={snap.ended} onClick={() => onRecord(it.id, 'ok')}>记正常</button>
              <button className="btn !px-1.5 !py-0 text-[11px]" disabled={snap.ended} onClick={() => onRecord(it.id, 'ng')}>记异常</button>
            </span>
          )}
        </div>
      ))}
      <div className="px-1 pt-1 text-[11px] text-hmi-dim">蓝点 = 已做过对应的查看或测试。没查过的项不能打勾。</div>
    </div>
  )
}

export function TourCard({ snap }: { snap: Snapshot }) {
  if (!snap.tour) return null
  const lit = snap.tour.codex.filter((c) => c.lit).length
  return (
    <div className="space-y-2 p-2 text-xs">
      <div className="text-hmi-dim">图鉴 {lit}/{snap.tour.codex.length} · 工作循环 {snap.tour.cycle_step_index}/{snap.tour.cycle_steps.length}</div>
      <div className="grid grid-cols-2 gap-1">
        {snap.tour.codex.map((c) => (
          <div key={c.id} className={`rounded px-2 py-1 ${c.lit ? 'bg-hmi-green/15 text-hmi-green' : 'bg-hmi-bg text-hmi-dim'}`} title={c.lit ? c.desc : '在全景里找到它并点击'}>{c.lit ? '●' : '○'} {c.name}</div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {snap.tour.cycle_steps.map((c, i) => (
          <span key={c.id} className={`tag ${i < snap.tour!.cycle_step_index ? 'border-hmi-green/60 text-hmi-green' : 'border-hmi-line text-hmi-dim'}`}>{i + 1}. {c.name}</span>
        ))}
      </div>
    </div>
  )
}
