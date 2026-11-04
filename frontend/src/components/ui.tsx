import type { ReactNode } from 'react'

export function Panel({ title, right, children, className = '' }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel flex flex-col min-h-0 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between px-3 pt-2 pb-1">
          <div className="text-xs font-semibold tracking-wider text-hmi-dim">{title}</div>
          <div className="text-xs">{right}</div>
        </div>
      )}
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  )
}

export function Badge({ children, tone = 'dim' }: { children: ReactNode; tone?: 'dim' | 'green' | 'red' | 'amber' | 'blue' | 'cyan' }) {
  const map = {
    dim: 'border-hmi-line text-hmi-dim',
    green: 'border-hmi-green/60 text-hmi-green',
    red: 'border-hmi-red/60 text-hmi-red',
    amber: 'border-hmi-amber/60 text-hmi-amber',
    blue: 'border-hmi-blue/60 text-hmi-blue',
    cyan: 'border-hmi-cyan/60 text-hmi-cyan',
  }
  return <span className={`tag ${map[tone]}`}>{children}</span>
}

export function DemoBadge() {
  return <span className="tag border-fuchsia-400/60 text-fuchsia-300">演示数据</span>
}

export function LayerTag({ layer }: { layer: 1 | 2 | 3 }) {
  const t = { 1: '人工记录', 2: '实测数据', 3: '演示数据' }[layer]
  return <span className={`tag ${layer === 2 ? 'border-hmi-cyan/60 text-hmi-cyan' : layer === 3 ? 'border-fuchsia-400/60 text-fuchsia-300' : 'border-hmi-amber/60 text-hmi-amber'}`}>{t}</span>
}

export function Stat({ label, value, unit, hint }: { label: string; value: ReactNode; unit?: string; hint?: string }) {
  return (
    <div className="panel px-3 py-2">
      <div className="text-xs text-hmi-dim">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-white">
        {value ?? '—'}
        {unit && <span className="ml-1 text-sm text-hmi-dim">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-hmi-dim">{hint}</div>}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-6 text-center text-sm text-hmi-dim">{children}</div>
}

/** 简单横向条形图（SVG），数值直接标在条末，不依赖图表库。 */
export function Bars({ data, max, unit = '', color = '#36b3c9', format }: { data: { label: string; value: number | null }[]; max?: number; unit?: string; color?: string; format?: (v: number) => string }) {
  const m = max ?? Math.max(1, ...data.map((d) => d.value ?? 0))
  return (
    <div className="space-y-1.5">
      {data.map((d) => (
        <div key={d.label} className="flex items-center gap-2 text-xs">
          <div className="w-28 shrink-0 truncate text-hmi-dim" title={d.label}>{d.label}</div>
          <div className="relative h-4 flex-1 rounded-sm bg-hmi-bg">
            <div className="h-4 rounded-sm" style={{ width: `${((d.value ?? 0) / m) * 100}%`, background: color }} />
          </div>
          <div className="w-16 shrink-0 text-right tabular-nums">{d.value === null ? '—' : format ? format(d.value) : `${d.value}${unit}`}</div>
        </div>
      ))}
    </div>
  )
}

export function ModeBadge({ mode, label }: { mode?: string; label?: string }) {
  const tone = mode === 'online' ? 'green' : mode === 'offline' ? 'amber' : 'red'
  return <Badge tone={tone}>{label ?? mode}</Badge>
}

export function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null
  return <div className="rounded border border-hmi-red/50 bg-hmi-red/10 px-3 py-2 text-sm text-hmi-red">{error}</div>
}
