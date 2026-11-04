import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmtMin, type Json } from '../../api'
import { Badge, ErrorLine, Panel, Stat } from '../../components/ui'
import { useApp } from '../../store'

function Radar({ dims, names }: { dims: Record<string, number>; names: Record<string, string> }) {
  const keys = Object.keys(dims)
  const R = 80
  const pt = (i: number, v: number) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / keys.length
    return [100 + Math.cos(a) * R * (v / 100), 100 + Math.sin(a) * R * (v / 100)]
  }
  return (
    <svg viewBox="-40 -12 280 224" className="h-52 w-64">
      {[25, 50, 75, 100].map((g) => (
        <polygon key={g} points={keys.map((_, i) => pt(i, g).join(',')).join(' ')} fill="none" stroke="#2e3843" />
      ))}
      <polygon points={keys.map((k, i) => pt(i, dims[k]).join(',')).join(' ')} fill="rgba(242,178,51,.25)" stroke="#f2b233" strokeWidth={2} />
      {keys.map((k, i) => {
        const [x, y] = pt(i, 118)
        return <text key={k} x={x} y={y} fill="#d9e1e8" fontSize={11} textAnchor="middle" dominantBaseline="middle">{names[k]} {Math.round(dims[k])}</text>
      })}
    </svg>
  )
}

/** 复盘：操作时间线、与标准路径对比、五维得分、AI 点评。分数由引擎算好，AI 只写点评。 */
export default function Review() {
  const { id } = useParams()
  const nav = useNavigate()
  const traineeId = useApp((s) => s.traineeId)
  const [rev, setRev] = useState<Json | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (id) api.review(id).then(setRev).catch((e) => setError(e.message))
  }, [id])

  const again = async () => {
    if (!rev || !traineeId) return
    const r = await api.createSession({ trainee_id: rev.trainee_id ?? traineeId, scenario_id: rev.scenario_id })
    nav(`/train/play/${r.id}`)
  }

  if (error) return <div className="p-6"><ErrorLine error={error} /><Link className="btn mt-3" to={`/train/play/${id}`}>返回本局</Link></div>
  if (!rev) return <div className="p-6 text-hmi-dim">生成复盘…（考官点评需要调用模型，降级模式下使用规则点评）</div>
  const sc = rev.scores

  return (
    <div className="mx-auto max-w-7xl space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/train/map" className="btn btn-ghost text-xs">← 关卡地图</Link>
        <h1 className="text-xl font-semibold text-white">复盘 · {rev.scenario_title}</h1>
        <Badge tone={rev.passed ? 'green' : 'red'}>{rev.passed ? '通过' : '未通过'}</Badge>
        {rev.terminated_by && <Badge tone="red">安全终止 {rev.terminated_by}</Badge>}
        {rev.root_cause && <Badge tone="amber">本局根因：{rev.root_cause.name}（{rev.root_cause.in_operator_scope ? '操作工可处理' : '需升级'}）</Badge>}
        <div className="ml-auto flex gap-2">
          <button className="btn btn-primary text-xs" onClick={again}>再来一局</button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Stat label="总分" value={sc.total} hint={`${'★'.repeat(sc.stars)}`} />
        <Stat label="停线时长" value={fmtMin(rev.downtime_s)} hint={rev.standard_downtime_s ? `标准路径 ${fmtMin(rev.standard_downtime_s)}` : undefined} />
        <Stat label="损失（示意）" value={`¥${rev.loss_yuan}`} />
        <Stat label="提示" value={rev.hints.length} unit="次" />
        <Stat label="与标准路径一致" value={`${Math.round(rev.path.match_rate * 100)}%`} />
        <Stat label="本局 AI 成本" value={rev.llm?.cost_usd ? `$${rev.llm.cost_usd.toFixed(4)}` : '$0'} hint={`模型调用 ${rev.llm?.model_calls ?? 0} 次 · 规则兜底 ${rev.llm?.fallback ?? 0} 次`} />
      </div>
      {rev.consequences.length > 0 && <div className="rounded border border-hmi-red/50 bg-hmi-red/10 px-3 py-2 text-sm text-red-200">{rev.consequences.join(' ')}</div>}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Panel title="五维得分（引擎按规则计算）">
          <div className="flex flex-col items-center p-2">
            <Radar dims={sc.dimensions} names={sc.dimension_names} />
            <div className="w-full space-y-0.5 text-xs text-hmi-dim">
              {Object.entries(sc.dimensions as Record<string, number>).map(([k, v]) => (
                <div key={k} className="flex justify-between"><span>{sc.dimension_names[k]}（权重 {Math.round(sc.weights[k] * 100)}%）</span><span className="tabular-nums text-hmi-text">{v}</span></div>
              ))}
              {rev.fail_reasons.length > 0 && <div className="pt-1 text-hmi-red">未通过原因：{rev.fail_reasons.join('；')}</div>}
            </div>
          </div>
        </Panel>
        <Panel title={`考官点评（${rev.examiner.mode === 'online' ? 'AI' : rev.examiner.mode === 'replay' ? 'AI·离线回放' : '规则点评'}）`} className="lg:col-span-2">
          <div className="space-y-2 p-3 text-sm">
            <p className="leading-relaxed">{rev.examiner.comment}</p>
            <div className="flex flex-wrap gap-1">
              {(rev.weakness_tags as string[]).map((t) => <Badge key={t} tone="amber">{t}</Badge>)}
              {(rev.examiner.ai_tags as string[]).filter((t) => !rev.weakness_tags.includes(t)).map((t) => <Badge key={t}>{t}（AI）</Badge>)}
            </div>
            {rev.violations.length > 0 && (
              <div className="space-y-1 pt-1 text-xs">
                {(rev.violations as Json[]).map((v, i) => (
                  <div key={i} className={v.severity === 'veto' ? 'text-hmi-red' : v.severity === 'major' ? 'text-hmi-amber' : 'text-hmi-dim'}>
                    {fmtMin(v.t)} · {v.message}（{v.dimension === 'safety' ? '安全' : v.dimension === 'quality' ? '质量' : v.dimension === 'efficiency' ? '效率' : '沟通'} −{v.deduction}）
                  </div>
                ))}
              </div>
            )}
            {rev.andon_reports.length > 0 && (
              <div className="text-xs text-hmi-dim">Andon 汇报：{(rev.andon_reports as Json[]).map((r) => `要素覆盖 ${Math.round(r.coverage * 100)}%（缺：${r.missing.join('、') || '无'}；追问 ${r.follow_ups} 轮；到场 ${Math.round(r.delay_s / 60)} 分钟）`).join('；')}</div>
            )}
          </div>
        </Panel>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Panel title="与标准路径对比">
          <ol className="space-y-1 p-3 text-sm">
            {(rev.path.rows as Json[]).map((r, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className={`w-5 text-center ${r.status === 'done' ? 'text-hmi-green' : 'text-hmi-red'}`}>{r.status === 'done' ? '✓' : '✗'}</span>
                <span className={r.status === 'done' ? '' : 'text-hmi-red'}>{r.label}</span>
                {r.t !== null && <span className="ml-auto text-xs tabular-nums text-hmi-dim">{fmtMin(r.t)}</span>}
                {r.status === 'missed' && r.tag && <Badge tone="red">{r.tag}</Badge>}
              </li>
            ))}
          </ol>
          {(rev.path.extras.length > 0 || rev.path.rejected.length > 0) && (
            <div className="border-t border-hmi-line p-3 text-xs text-hmi-dim">
              标准路径之外：{[...rev.path.extras.map((x: Json) => x.name), ...rev.path.rejected.map((x: Json) => `${x.name}（被拒绝）`)].join('、')}
            </div>
          )}
        </Panel>
        <Panel title="操作时间线">
          <div className="max-h-[480px] space-y-1 overflow-y-auto p-3 text-xs">
            {(rev.timeline as Json[]).filter((e) => e.actor !== 'system' || ['start_session', 'review'].includes(e.action)).map((e) => (
              <div key={e.seq} className="flex gap-2">
                <span className="w-12 shrink-0 tabular-nums text-hmi-dim">{fmtMin(e.t)}</span>
                <span className={`w-20 shrink-0 ${e.result === 'rejected' ? 'text-hmi-dim line-through' : e.violations.length ? 'text-hmi-amber' : 'text-hmi-cyan'}`}>{e.action}</span>
                <span className="min-w-0 flex-1">{e.message}{e.violations.length ? ` [${e.violations.join(',')}]` : ''}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  )
}
