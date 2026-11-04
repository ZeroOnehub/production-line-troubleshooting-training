import { useEffect, useState } from 'react'
import { api, type Json } from '../api'
import { Badge, Empty, ErrorLine, LayerTag, Panel } from '../components/ui'

const METRIC_ORDER = ['intent_accuracy', 'safety_block_rate', 'leak_rate', 'faithfulness', 'report_f1', 'script_first_pass', 'latency_p50_ms', 'latency_p95_ms', 'cost_per_session_usd']

function fmt(m: Json | undefined) {
  if (!m || m.value === null || m.value === undefined) return '—'
  if (m.unit === 'ms') return `${Math.round(m.value)} ms`
  if (m.unit === 'usd') return `$${m.value.toFixed(5)}`
  if (m.unit === 'ratio') return `${(m.value * 100).toFixed(1)}%`
  return String(m.value)
}

/** 评测台网页：历史评测对比、bad case 浏览。数字全部读 eval/reports/<run>/results.json。 */
export default function EvalPage() {
  const [runs, setRuns] = useState<Json[]>([])
  const [run, setRun] = useState<string | null>(null)
  const [data, setData] = useState<Json | null>(null)
  const [bad, setBad] = useState<Json[]>([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.evalRuns().then((r) => { setRuns(r); if (r.length) setRun(r[0].run) }).catch((e) => setError(e.message))
    api.badcases().then(setBad).catch(() => undefined)
  }, [])
  useEffect(() => {
    if (run) api.evalRun(run).then(setData).catch((e) => setError(e.message))
  }, [run])

  const models = data ? Object.keys(data.models) : []
  const cases: Json[] = data ? models.flatMap((m) => (data.models[m].bad_cases || []).map((b: Json) => ({ ...b, model: m }))) : []

  return (
    <div className="mx-auto max-w-7xl space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-white">评测台</h1>
        <LayerTag layer={2} />
        <span className="text-xs text-hmi-dim">命令行运行：<code className="text-hmi-amber">python -m eval.run --models qwen3.6-27b,deepseek-v4-flash --roles narrator,coach --split test</code></span>
      </div>
      <ErrorLine error={error} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[260px_1fr]">
        <Panel title="历次评测">
          <div className="space-y-1 p-2 text-xs">
            {runs.length === 0 && <Empty>还没有评测结果</Empty>}
            {runs.map((r) => (
              <button key={r.run} className={`w-full rounded p-2 text-left ${run === r.run ? 'bg-hmi-panel2' : 'hover:bg-hmi-panel2'}`} onClick={() => setRun(r.run)}>
                <div className="font-mono">{r.run}</div>
                <div className="text-hmi-dim">{r.split} · {r.models.join(', ')}{r.iteration ? ` · 第 ${r.iteration} 轮` : ''}</div>
                {r.note && <div className="text-hmi-amber">{r.note}</div>}
              </button>
            ))}
          </div>
        </Panel>
        <div className="space-y-3">
          {data && (
            <Panel title={`指标（${data.split} 集 · 用例集 ${data.case_set?.version} · ${data.case_set?.review_status}）`}>
              <div className="overflow-x-auto p-3 text-sm">
                <table className="w-full">
                  <thead className="text-xs text-hmi-dim"><tr><th className="text-left">指标</th><th>门槛</th>{models.map((m) => <th key={m}>{m}{data.models[m].kind === 'rules' && <div className="text-[10px] text-hmi-amber">规则降级（非大模型）</div>}</th>)}</tr></thead>
                  <tbody>
                    {METRIC_ORDER.filter((k) => models.some((m) => data.models[m].metrics[k])).map((k) => {
                      const any = models.map((m) => data.models[m].metrics[k]).find(Boolean)
                      return (
                        <tr key={k} className="border-t border-hmi-line/40">
                          <td className="py-1">{any?.name || k}</td>
                          <td className="text-center text-xs text-hmi-dim">{any?.threshold_text || '—'}</td>
                          {models.map((m) => {
                            const v = data.models[m].metrics[k]
                            return <td key={m} className={`text-center tabular-nums ${v?.pass === false ? 'text-hmi-red' : v?.pass ? 'text-hmi-green' : ''}`}>{fmt(v)}{v?.n ? <span className="ml-1 text-[10px] text-hmi-dim">n={v.n}</span> : null}</td>
                          })}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                {data.selection && (
                  <div className="mt-3 rounded bg-hmi-bg p-2 text-xs">
                    <div className="mb-1 font-semibold">选型结论（先过安全门槛，再按准确率 50% / 响应 30% / 成本 20% 加权）</div>
                    {Object.entries(data.selection as Record<string, Json>).map(([role, s]) => <div key={role}>{role} → <b className="text-hmi-amber">{s.model || '无合格模型'}</b>{s.reason ? `（${s.reason}）` : ''}</div>)}
                  </div>
                )}
                {data.judge && <div className="mt-2 text-xs text-hmi-dim">裁判模型：{data.judge.model || '无'}；人工抽检：{data.judge.human_review || '待进行'}</div>}
                {data.notes?.length > 0 && <div className="mt-2 space-y-0.5 text-xs text-hmi-amber">{data.notes.map((n: string, i: number) => <div key={i}>· {n}</div>)}</div>}
              </div>
            </Panel>
          )}
          <Panel title="Bad case 浏览" right={<input className="input !w-48 !py-0.5 text-xs" placeholder="按类别 / 输入过滤" value={filter} onChange={(e) => setFilter(e.target.value)} />}>
            <div className="max-h-[520px] space-y-1 overflow-y-auto p-2 text-xs">
              {([...cases, ...bad.map((b) => ({ ...b, model: b.model || '库' }))] as Json[]).filter((c) => !filter || JSON.stringify(c).includes(filter)).slice(0, 200).map((c, i) => (
                <div key={i} className="rounded bg-hmi-bg p-2">
                  <div className="flex gap-2"><Badge tone="red">{c.category}</Badge><span className="text-hmi-dim">{c.model}</span><span className="font-mono text-hmi-dim">{c.case_id}</span>{c.status && <Badge>{c.status}</Badge>}</div>
                  <div className="mt-1">输入：{typeof c.input === 'string' ? c.input : JSON.stringify(c.input)}</div>
                  <div className="text-hmi-green">期望：{JSON.stringify(c.expected)}</div>
                  <div className="text-hmi-red">实际：{JSON.stringify(c.got)}</div>
                  {c.attribution && <div className="text-hmi-amber">归因：{c.attribution}</div>}
                </div>
              ))}
              {cases.length + bad.length === 0 && <Empty>暂无 bad case</Empty>}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
