import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Json } from '../../api'
import { Badge, ErrorLine, Panel } from '../../components/ui'

/** 关卡版本：版本列表、差异对比、回滚（回滚会生成一个新版本，历史保持线性）。 */
export default function ScenarioVersions() {
  const { id } = useParams()
  const [versions, setVersions] = useState<Json[]>([])
  const [yaml, setYaml] = useState('')
  const [diff, setDiff] = useState<Json | null>(null)
  const [a, setA] = useState<number | null>(null)
  const [b, setB] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    if (!id) return
    api.versions(id).then((v) => {
      setVersions(v)
      if (v.length >= 2) {
        setA(v[v.length - 2].version)
        setB(v[v.length - 1].version)
      }
    }).catch((e) => setError(e.message))
    api.scenarioYaml(id).then(setYaml).catch(() => undefined)
  }
  useEffect(load, [id])
  useEffect(() => {
    if (id && a !== null && b !== null && a !== b) api.diff(id, a, b).then(setDiff).catch(() => setDiff(null))
  }, [id, a, b])

  return (
    <div className="mx-auto max-w-7xl space-y-3 p-4">
      <div className="flex items-center gap-2">
        <Link to="/workshop" className="btn btn-ghost text-xs">← 工坊</Link>
        <h1 className="text-xl font-semibold text-white">{id} · 版本</h1>
      </div>
      <ErrorLine error={error} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[360px_1fr]">
        <Panel title="版本列表">
          <div className="space-y-1 p-2 text-xs">
            {versions.map((v) => (
              <div key={v.version} className="flex items-center gap-2 rounded bg-hmi-bg p-2">
                <span className="font-semibold">v{v.version}</span>
                <Badge tone={v.status === 'active' ? 'green' : 'dim'}>{v.status === 'active' ? '当前' : v.status === 'archived' ? '已归档' : '已下线'}</Badge>
                {v.needs_recheck && <Badge tone="red">待复核</Badge>}
                <span className="flex-1 truncate text-hmi-dim" title={v.source}>{v.source}</span>
                {v.status !== 'active' && <button className="btn !py-0 text-[11px]" onClick={async () => { try { await api.rollback(id!, v.version); load() } catch (e: any) { setError(e.message) } }}>回滚到此</button>}
              </div>
            ))}
          </div>
        </Panel>
        <div className="space-y-3">
          {versions.length >= 2 && (
            <Panel title="版本对比" right={
              <span className="flex gap-1">
                <select className="input !w-20 !py-0.5 text-xs" value={a ?? ''} onChange={(e) => setA(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</select>
                <span>→</span>
                <select className="input !w-20 !py-0.5 text-xs" value={b ?? ''} onChange={(e) => setB(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</select>
              </span>}>
              <pre className="max-h-96 overflow-auto p-3 text-[11px] leading-snug">
                {diff?.diff.map((l: string, i: number) => <div key={i} className={l.startsWith('+') ? 'text-hmi-green' : l.startsWith('-') ? 'text-hmi-red' : 'text-hmi-dim'}>{l}</div>)}
                {diff && diff.diff.length === 0 && '两个版本内容相同'}
              </pre>
            </Panel>
          )}
          <Panel title="当前版本 YAML（content/scenarios/）">
            <pre className="max-h-[560px] overflow-auto p-3 text-[11px] leading-snug">{yaml}</pre>
          </Panel>
        </div>
      </div>
    </div>
  )
}
