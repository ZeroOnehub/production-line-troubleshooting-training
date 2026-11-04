import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Json } from '../../api'
import { Badge, ErrorLine } from '../../components/ui'
import { useApp } from '../../store'

const TYPE: Record<string, string> = { tour: '认识工位', checklist: '点检', changeover: '换型', alarm: '异常', shift: '考核' }

/** 关卡地图与段位：四层展示，显示解锁状态和当前段位。 */
export default function MapPage() {
  const nav = useNavigate()
  const { traineeId, traineeName } = useApp()
  const [prog, setProg] = useState<Json | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    if (traineeId) api.progress(traineeId).then(setProg).catch((e) => setError(e.message))
  }, [traineeId])
  useEffect(() => {
    if (!traineeId) nav('/train')
    else load()
  }, [traineeId, nav, load])

  const start = async (scenario_id: string) => {
    if (!traineeId) return
    setBusy(true)
    try {
      const r = await api.createSession({ trainee_id: traineeId, scenario_id })
      nav(`/train/play/${r.id}`)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!prog) return <div className="p-6 text-hmi-dim">加载中…<ErrorLine error={error} /></div>
  const rankIdx = prog.rank.index as number

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-white">{traineeName} 的关卡地图</h1>
        <div className="flex items-center gap-1">
          {(prog.ranks as string[]).map((r, i) => (
            <span key={r} className={`tag ${i <= rankIdx ? 'border-hmi-amber text-hmi-amber' : 'border-hmi-line text-hmi-dim'}`}>{i === rankIdx ? '★ ' : ''}{r}</span>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <Link className="btn text-xs" to="/train/history">历史与重玩</Link>
          <label className="flex items-center gap-1 text-xs text-hmi-dim" title="不按段位限制，直接打任意关卡">
            <input type="checkbox" checked={!!prog.trainee.unlock_all} onChange={async (e) => { await api.setUnlock(traineeId!, e.target.checked); load() }} />
            解锁全部关卡
          </label>
          <button className="btn btn-ghost text-xs" onClick={() => nav('/train')}>切换学员</button>
        </div>
      </div>
      {prog.next_rank && <div className="text-xs text-hmi-dim">下一个段位：{prog.next_rank.name}。{prog.next_rank.id === 'independent' ? '通过认识工位、开班点检、换型。' : prog.next_rank.id === 'level1' ? '通过 10 个异常关卡中的至少 8 个。' : '通过“上一个班”考核。'}</div>}
      <ErrorLine error={error} />
      {(prog.layers as Json[]).map((layer) => (
        <section key={layer.layer} className={`panel p-3 ${layer.unlocked ? '' : 'opacity-60'}`}>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-semibold text-white">第 {layer.layer} 层 · {layer.name}</span>
            {!layer.unlocked && <Badge>未解锁</Badge>}
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {(layer.scenarios as Json[]).map((s) => (
              <button key={s.id} disabled={!s.unlocked || busy} onClick={() => start(s.id)}
                className={`rounded-md border p-3 text-left transition-colors ${s.passed ? 'border-hmi-green/60 bg-hmi-green/5' : 'border-hmi-line bg-hmi-panel2'} ${s.unlocked ? 'hover:border-hmi-amber' : 'cursor-not-allowed'}`}>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-hmi-dim">{s.alarm || TYPE[s.type]}</span>
                  <span className="text-hmi-amber">{'★'.repeat(s.stars)}{'☆'.repeat(Math.max(0, 3 - s.stars))}</span>
                </div>
                <div className="mt-1 font-semibold text-white">{s.title}</div>
                <div className="mt-1 line-clamp-2 text-[11px] text-hmi-dim">{s.summary}</div>
                <div className="mt-2 text-[11px] text-hmi-dim">{s.attempts ? `已打 ${s.attempts} 局 · 最好 ${s.best ?? '—'} 分` : '未挑战'} · v{s.version}</div>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
