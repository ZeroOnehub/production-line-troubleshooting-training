import { useState } from 'react'
import type { ActionDef, Json, Snapshot } from '../api'
import { TARGET_NAMES } from '../api'

const PANEL_ORDER = ['设备', '处置', '呼叫', 'HMI', '换型', '点检', '观察', '认识工位']

/** 动作面板：按分组列出引擎允许的动作。带参数的动作弹出选择。 */
export default function ActionPanel({ snap, onAct }: { snap: Snapshot; onAct: (action: string, params?: Json) => void }) {
  const [pending, setPending] = useState<ActionDef | null>(null)
  const groups = new Map<string, ActionDef[]>()
  for (const a of snap.actions) {
    if (a.id === 'inspect') continue
    if (!groups.has(a.panel)) groups.set(a.panel, [])
    groups.get(a.panel)!.push(a)
  }
  const ordered = [...groups.entries()].sort((a, b) => PANEL_ORDER.indexOf(a[0]) - PANEL_ORDER.indexOf(b[0]))

  const click = (a: ActionDef) => {
    const needs = Object.entries(a.params).filter(([k, p]) => p.required && k !== 'item' && k !== 'result')
    if (needs.length) setPending(a)
    else onAct(a.id)
  }

  return (
    <div className="space-y-2 p-2">
      {ordered.map(([panel, acts]) => (
        <div key={panel}>
          <div className="mb-1 text-[11px] text-hmi-dim">{panel}</div>
          <div className="flex flex-wrap gap-1">
            {acts.map((a) => (
              <button key={a.id} className={`btn !px-2 !py-1 text-xs ${a.id === 'emergency_stop' ? 'btn-danger' : ''} ${a.id === 'pull_andon' ? '!border-hmi-blue !text-blue-200' : ''}`}
                disabled={!a.enabled} title={a.requires_pause ? '要求设备处于暂停或急停' : undefined} onClick={() => click(a)}>
                {a.name}{a.requires_pause ? ' ⏸' : ''}
              </button>
            ))}
          </div>
        </div>
      ))}
      {pending && (
        <div className="rounded border border-hmi-amber/60 bg-hmi-bg p-2 text-xs">
          <div className="mb-1">{pending.name}：选择参数</div>
          {Object.entries(pending.params).map(([k, p]) => (
            <div key={k} className="flex flex-wrap gap-1">
              {(p.values || []).map((v) => (
                <button key={v} className="btn !px-2 !py-1 text-xs" onClick={() => { onAct(pending.id, { [k]: v }); setPending(null) }}>
                  {k === 'target' ? TARGET_NAMES[v] || v : k === 'variant' ? `${v} 型` : v}
                </button>
              ))}
            </div>
          ))}
          <button className="btn btn-ghost mt-1 !px-2 !py-0.5 text-xs" onClick={() => setPending(null)}>取消</button>
        </div>
      )}
      <div className="pt-1 text-[11px] text-hmi-dim">⏸ = 要求设备先暂停或急停。越权操作（电气柜、参数、传感器等）请直接用说的，系统会判断。</div>
    </div>
  )
}
