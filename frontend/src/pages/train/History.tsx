import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtMin, type Json } from '../../api'
import { Badge, Empty, ErrorLine, Panel } from '../../components/ui'
import { useApp } from '../../store'

/** 历史与重玩：查看过往对局与复盘。 */
export default function History() {
  const nav = useNavigate()
  const { traineeId, traineeName } = useApp()
  const [rows, setRows] = useState<Json[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!traineeId) nav('/train')
    else api.sessions(traineeId).then(setRows).catch((e) => setError(e.message))
  }, [traineeId, nav])

  const replay = async (scenario_id: string) => {
    const r = await api.createSession({ trainee_id: traineeId, scenario_id })
    nav(`/train/play/${r.id}`)
  }

  return (
    <div className="mx-auto max-w-5xl space-y-3 p-4">
      <div className="flex items-center gap-2">
        <Link to="/train/map" className="btn btn-ghost text-xs">← 关卡地图</Link>
        <h1 className="text-xl font-semibold text-white">{traineeName} · 历史对局</h1>
      </div>
      <ErrorLine error={error} />
      <Panel>
        {rows.length === 0 ? <Empty>还没有对局。</Empty> : (
          <table className="w-full text-sm">
            <thead className="text-xs text-hmi-dim"><tr className="border-b border-hmi-line"><th className="p-2 text-left">时间</th><th className="text-left">关卡</th><th>结果</th><th>得分</th><th>停线</th><th>提示</th><th>AI 模式</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-hmi-line/50">
                  <td className="p-2 text-xs text-hmi-dim">{new Date(r.started_at).toLocaleString()}</td>
                  <td>{r.title}</td>
                  <td className="text-center">{r.ended_at ? <Badge tone={r.passed ? 'green' : 'red'}>{r.passed ? '通过' : '未通过'}</Badge> : <Badge>进行中</Badge>}</td>
                  <td className="text-center tabular-nums">{r.total ?? '—'}</td>
                  <td className="text-center tabular-nums">{fmtMin(r.downtime_s)}</td>
                  <td className="text-center">{r.hints}</td>
                  <td className="text-center text-xs text-hmi-dim">{r.ai_mode}</td>
                  <td className="space-x-1 p-2 text-right">
                    {r.ended_at ? <Link className="btn !py-0.5 text-xs" to={`/train/review/${r.id}`}>复盘</Link> : <Link className="btn !py-0.5 text-xs" to={`/train/play/${r.id}`}>继续</Link>}
                    <button className="btn !py-0.5 text-xs" onClick={() => replay(r.scenario_id)}>重玩</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
