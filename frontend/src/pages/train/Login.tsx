import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Json } from '../../api'
import { ErrorLine, Panel } from '../../components/ui'
import { useApp } from '../../store'

/** 学员登录：选择或新建学员，本地无密码。 */
export default function Login() {
  const nav = useNavigate()
  const setTrainee = useApp((s) => s.setTrainee)
  const [list, setList] = useState<Json[]>([])
  const [name, setName] = useState('')
  const [team, setTeam] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.trainees().then(setList).catch((e) => setError(e.message))
  }, [])

  const choose = (t: Json) => {
    setTrainee(t.id, t.name)
    nav('/train/map')
  }
  const create = async () => {
    try {
      const t = await api.createTrainee(name.trim(), team.trim() || undefined)
      choose(t)
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <h1 className="text-xl font-semibold text-white">训练端 · 选择学员</h1>
      <ErrorLine error={error} />
      <Panel title="已有学员">
        <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3">
          {list.length === 0 && <div className="text-sm text-hmi-dim">还没有学员，在下面新建一个。</div>}
          {list.map((t) => (
            <button key={t.id} className="btn justify-between" onClick={() => choose(t)}>
              <span>{t.name}</span>
              <span className="text-xs text-hmi-dim">{t.team ? `${t.team} · ` : ''}{t.rank}</span>
            </button>
          ))}
        </div>
      </Panel>
      <Panel title="新建学员（本地，无密码）">
        <div className="flex flex-wrap gap-2 p-3">
          <input className="input max-w-xs" placeholder="姓名或代号" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="input max-w-xs" placeholder="班组（可选，用于班组排行）" value={team} onChange={(e) => setTeam(e.target.value)} />
          <button className="btn btn-primary" disabled={!name.trim()} onClick={create}>新建并进入</button>
        </div>
      </Panel>
    </div>
  )
}
