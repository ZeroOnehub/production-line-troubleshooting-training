import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Json } from '../../api'
import { Badge, ErrorLine, Panel } from '../../components/ui'

const STAGES = ['collecting', 'ai_draft', 'validating', 'pending_review', 'published']
const STAGE_LABEL: Record<string, string> = { collecting: '采集中', ai_draft: 'AI 草稿', validating: '结构校验', pending_review: '待审核', published: '已发布', needs_recheck: '待复核', retired: '已下线' }
const REVIEWERS = [
  { role: 'equipment', name: '设备工程师', focus: '设备行为是否合理：报警触发、部件状态、维修处置' },
  { role: 'quality', name: '质量工程师', focus: '质量后果是否正确：NG 隔离、首件、批次处理' },
  { role: 'ehs', name: 'EHS 工程师', focus: '安全规则是否完整：停机再动手、越权、安全装置' },
]

function ValidationView({ v }: { v: Json }) {
  return (
    <div className="space-y-2 p-3 text-xs">
      <div className={`text-sm font-semibold ${v.ok ? 'text-hmi-green' : 'text-hmi-red'}`}>{v.ok ? '✓ ' : '✗ '}{v.summary}</div>
      {v.format_errors?.length > 0 && <div><div className="text-hmi-red">格式错误</div>{v.format_errors.map((e: string, i: number) => <div key={i} className="font-mono">· {e}</div>)}</div>}
      {v.reference_errors?.length > 0 && <div><div className="text-hmi-red">引用错误（只能引用已有的部件、动作、规则）</div>{v.reference_errors.map((e: string, i: number) => <div key={i}>· {e}</div>)}</div>}
      {v.warnings?.length > 0 && <div><div className="text-hmi-amber">提示</div>{v.warnings.map((e: string, i: number) => <div key={i}>· {e}</div>)}</div>}
      {v.replay?.length > 0 && (
        <div>
          <div className="mb-1 text-hmi-dim">引擎自动回放每条正确路径</div>
          {v.replay.map((r: Json, i: number) => (
            <div key={i} className="mb-1 rounded bg-hmi-bg p-1.5">
              <span className={r.passed ? 'text-hmi-green' : 'text-hmi-red'}>{r.passed ? '✓ 通关' : '✗ 未通关'}</span> {r.variant}
              <span className="ml-2 text-hmi-dim">{r.steps} 步 · 停线 {Math.round(r.downtime_s)} 秒</span>
              {!r.passed && <div className="text-hmi-red">{r.error || [...(r.violations || []), ...(r.rejected || [])].join('；') || r.state}</div>}
              {r.path && <div className="mt-0.5 break-all font-mono text-[10px] text-hmi-dim">{r.path.join(' → ')}</div>}
            </div>
          ))}
        </div>
      )}
      {v.negative?.length > 0 && (
        <div>
          <div className="mb-1 text-hmi-dim">错误路径用例</div>
          {v.negative.map((n: Json, i: number) => <div key={i} className={n.passed ? 'text-hmi-green' : 'text-hmi-red'}>{n.passed ? '✓' : '✗'} {n.name}：期望 {JSON.stringify(n.expected)}，实际 {JSON.stringify(n.got_violations)}</div>)}
        </div>
      )}
    </div>
  )
}

export default function DraftPage() {
  const { id } = useParams()
  const [d, setD] = useState<Json | null>(null)
  const [yaml, setYaml] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rv, setRv] = useState<Record<string, { name: string; comments: string }>>({})

  const apply = (x: Json) => {
    setD(x)
    setYaml(x.yaml || '')
  }
  useEffect(() => {
    if (id) api.draft(id).then(apply).catch((e) => setError(e.message))
  }, [id])

  const run = async (label: string, fn: () => Promise<Json>) => {
    setBusy(label)
    setError(null)
    try {
      apply(await fn())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  if (!d) return <div className="p-6 text-hmi-dim">加载草稿…<ErrorLine error={error} /></div>
  const dirty = yaml !== (d.yaml || '')
  const cur = STAGES.indexOf(d.status)

  return (
    <div className="mx-auto max-w-7xl space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/workshop" className="btn btn-ghost text-xs">← 工坊</Link>
        <h1 className="text-xl font-semibold text-white">{d.title}</h1>
        <span className="font-mono text-xs text-hmi-dim">{d.id} · 草稿 v{d.version}</span>
        <Badge tone="cyan">{d.status_label}</Badge>
        <span className="ml-auto text-xs text-hmi-dim">制作计时 {Math.round(d.elapsed_s / 60)} 分钟{d.published_at ? '（已停止）' : ''}</span>
      </div>
      <div className="flex flex-wrap gap-1 text-xs">
        {STAGES.map((s, i) => <span key={s} className={`tag ${i <= cur ? 'border-hmi-amber text-hmi-amber' : 'border-hmi-line text-hmi-dim'}`}>{i + 1}. {STAGE_LABEL[s]}</span>)}
        {d.status === 'needs_recheck' && <Badge tone="red">待复核</Badge>}
      </div>
      <ErrorLine error={error} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <div className="space-y-3">
          <Panel title="① 师傅口述">
            <div className="space-y-2 p-3">
              <textarea className="input h-32 text-xs" value={d.source_text} readOnly={d.status === 'published'} onChange={(e) => setD({ ...d, source_text: e.target.value })}
                onBlur={() => d.status !== 'published' && api.patchDraft(d.id, { source_text: d.source_text }).then(apply)} />
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-primary" disabled={!!busy || d.status === 'published'} onClick={() => run('gen', () => api.generate(d.id))}>{busy === 'gen' ? '生成中…（编剧调用最强档模型）' : '② AI 生成剧本草稿'}</button>
                <button className="btn" disabled={!!busy || !d.validation || d.validation.ok || d.status === 'published'} onClick={() => run('gen', () => api.generate(d.id, true))}>按校验意见重新生成</button>
              </div>
              {d.gen_mode && <div className="text-xs text-hmi-dim">生成方式：{d.gen_mode}{d.gen_model ? `（${d.gen_model}）` : ''}</div>}
              {d.explanation && <div className="rounded bg-hmi-bg p-2 text-xs leading-relaxed">{d.explanation}</div>}
            </div>
          </Panel>
          <Panel title="③ 剧本 YAML（可手工修改）" right={dirty ? <button className="btn !py-0.5 text-xs" onClick={() => run('save', () => api.patchDraft(d.id, { yaml }))}>保存修改</button> : null}>
            <textarea className="input h-[420px] font-mono text-[11px] leading-snug" spellCheck={false} value={yaml} readOnly={d.status === 'published'} onChange={(e) => setYaml(e.target.value)} placeholder="生成后的 YAML 显示在这里" />
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title="④ 自动校验：格式 → 引用 → 引擎回放" right={<button className="btn !py-0.5 text-xs" disabled={!!busy || !yaml.trim() || dirty || d.status === 'published'} onClick={() => run('val', () => api.validate(d.id))}>{busy === 'val' ? '回放中…' : dirty ? '先保存修改' : '提交校验'}</button>}>
            {d.validation ? <ValidationView v={d.validation} /> : <div className="p-3 text-xs text-hmi-dim">生成或录入 YAML 后提交校验。校验通过才进入三方审核。</div>}
          </Panel>
          <Panel title="⑤ 三方审核（全部通过才能发布）">
            <div className="grid grid-cols-1 gap-2 p-3 md:grid-cols-3">
              {REVIEWERS.map((r) => {
                const done = (d.reviews as Json[]).filter((x) => x.role === r.role && x.version === d.version)
                const last = done[done.length - 1]
                return (
                  <div key={r.role} className="rounded border border-hmi-line p-2 text-xs">
                    <div className="font-semibold text-white">{r.name}</div>
                    <div className="mb-1 text-hmi-dim">{r.focus}</div>
                    {last ? <Badge tone={last.verdict === 'pass' ? 'green' : 'red'}>{last.verdict === 'pass' ? '通过' : '不通过'} · {last.name || '—'}</Badge> : (
                      <div className="space-y-1">
                        <input className="input !py-1 text-xs" placeholder="审核人" value={rv[r.role]?.name || ''} onChange={(e) => setRv({ ...rv, [r.role]: { ...rv[r.role], name: e.target.value } })} />
                        <input className="input !py-1 text-xs" placeholder="意见" value={rv[r.role]?.comments || ''} onChange={(e) => setRv({ ...rv, [r.role]: { ...rv[r.role], comments: e.target.value } })} />
                        <div className="flex gap-1">
                          <button className="btn !py-0.5 text-xs" disabled={d.status !== 'pending_review'} onClick={() => run('rv', () => api.reviewDraft(d.id, { role: r.role, verdict: 'pass', ...rv[r.role] }))}>通过</button>
                          <button className="btn btn-danger !py-0.5 text-xs" disabled={d.status !== 'pending_review'} onClick={() => run('rv', () => api.reviewDraft(d.id, { role: r.role, verdict: 'reject', ...rv[r.role] }))}>不通过</button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </Panel>
          <Panel title="⑥ 发布与版本">
            <div className="space-y-2 p-3 text-sm">
              <button className="btn btn-primary" disabled={!d.can_publish || !!busy} onClick={() => run('pub', () => api.publish(d.id))}>发布为新版本</button>
              {d.published_scenario_id && <div>已发布为 <Link className="text-hmi-amber underline" to={`/workshop/scenarios/${d.published_scenario_id}`}>{d.published_scenario_id} v{d.published_version}</Link>，用时 {Math.round(d.elapsed_s / 60)} 分钟。训练端关卡地图即可看到。</div>}
              <div className="text-xs text-hmi-dim">发布会把 YAML 写入 content/scenarios/（旧版本归档到 _versions/），引擎立即加载新版本。</div>
            </div>
          </Panel>
          <Panel title="过程记录">
            <div className="max-h-48 space-y-0.5 overflow-y-auto p-3 text-xs">
              {(d.timeline as Json[]).map((t, i) => <div key={i}><span className="text-hmi-dim">{new Date(t.at).toLocaleTimeString()} · {STAGE_LABEL[t.stage] || t.stage} · v{t.version}</span> {t.note}</div>)}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
