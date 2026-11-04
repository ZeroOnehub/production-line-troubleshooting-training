import { useEffect, useState } from 'react'
import { api, type Json } from '../api'
import { Bars, DemoBadge, Empty, ErrorLine, LayerTag, Panel, Stat } from '../components/ui'
import { safeStorage } from '../api'

const pct = (v: number | null | undefined) => (v === null || v === undefined ? null : Math.round(v * 1000) / 10)

/** 管理看板：总览、错误分布、学员画像、班组排行。演示数据开关打开时叠加合成学员数据并显示标识。 */
export default function Dashboard() {
  const [demo, setDemo] = useState(safeStorage('dash_demo') !== 'false')
  const [ov, setOv] = useState<Json | null>(null)
  const [er, setEr] = useState<Json | null>(null)
  const [teams, setTeams] = useState<Json | null>(null)
  const [llm, setLlm] = useState<Json | null>(null)
  const [sel, setSel] = useState<number | null>(null)
  const [profile, setProfile] = useState<Json | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    safeStorage('dash_demo', String(demo))
    Promise.all([api.overview(demo), api.errors(demo), api.teams(demo), api.llmStats()])
      .then(([a, b, c, d]) => { setOv(a); setEr(b); setTeams(c); setLlm(d) })
      .catch((e) => setError(e.message))
  }, [demo])
  useEffect(() => {
    if (sel !== null) api.traineeStats(sel).then(setProfile).catch((e) => setError(e.message))
  }, [sel])

  return (
    <div className="mx-auto max-w-7xl space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-white">管理看板</h1>
        <label className="flex items-center gap-2 rounded border border-fuchsia-400/50 px-2 py-1 text-sm">
          <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} /> 叠加演示数据
        </label>
        {demo ? <DemoBadge /> : <span className="text-xs text-hmi-dim">只显示真实训练局（本机实际打过的局）</span>}
        <span className="ml-auto text-xs text-hmi-dim">演示数据由脚本生成，只用于展示看板功能，不代表真实培训效果</span>
      </div>
      <ErrorLine error={error} />
      {!ov ? <Empty>加载中…</Empty> : (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
            <Stat label="学员" value={ov.trainees} unit="人" />
            <Stat label="训练局" value={ov.ended_sessions} unit="局" />
            <Stat label="学员平均进度" value={pct(ov.progress_avg)} unit="%" hint="已通过关卡 / 14" />
            <Stat label="关卡完成率" value={pct(ov.completion_rate)} unit="%" hint="通过的 学员×关卡 / 全部" />
            <Stat label="平均处置时长" value={ov.avg_handling_min} unit="分钟" hint="通过的异常关卡停线时长" />
            <Stat label="提示使用率" value={pct(ov.hint_usage_rate)} unit="%" hint={`每局 ${ov.hints_per_session ?? '—'} 次`} />
            <Stat label="安全违规" value={ov.safety_violations} unit="次" hint={`安全终止 ${ov.safety_terminations} · 越权尝试 ${ov.authority_attempts}`} />
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <Panel title="错误分布 · 按规则类型" right={demo ? <DemoBadge /> : null}>
              <div className="p-3">{er && <Bars data={er.by_type.map((t: Json) => ({ label: t.type, value: t.count }))} unit=" 次" color="#e5484d" />}</div>
            </Panel>
            <Panel title="高频违规规则（共性薄弱环节）">
              <div className="space-y-1 p-3 text-xs">
                {er?.top_rules.length ? er.top_rules.slice(0, 8).map((r: Json) => (
                  <div key={r.rule_id} className="flex gap-2"><span className="w-20 font-mono text-hmi-amber">{r.rule_id}</span><span className="flex-1 truncate" title={r.quote}>{r.quote}</span><span className="tabular-nums">{r.count}</span></div>
                )) : <Empty>暂无违规记录</Empty>}
              </div>
            </Panel>
            <Panel title="段位分布">
              <div className="p-3"><Bars data={ov.rank_distribution.map((r: Json) => ({ label: r.rank, value: r.count }))} unit=" 人" color="#f2b233" /></div>
            </Panel>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel title="关卡通过率">
              <div className="p-3"><Bars data={ov.by_scenario.map((s: Json) => ({ label: s.title, value: s.pass_rate === null ? null : Math.round(s.pass_rate * 100) }))} max={100} unit="%" color="#3fbf6b" /></div>
            </Panel>
            <Panel title="错误分布 · 关卡 × 规则类型">
              <div className="overflow-x-auto p-3 text-xs">
                {er && er.matrix.length ? (
                  <table className="w-full">
                    <thead className="text-hmi-dim"><tr><th className="text-left">关卡</th>{er.types.map((t: string) => <th key={t}>{t}</th>)}</tr></thead>
                    <tbody>{er.matrix.map((m: Json) => (
                      <tr key={m.scenario_id} className="border-t border-hmi-line/40"><td className="py-1">{m.title}</td>{er.types.map((t: string) => <td key={t} className="text-center tabular-nums" style={{ background: m[t] ? `rgba(229,72,77,${Math.min(0.7, m[t] / 10)})` : undefined }}>{m[t] || ''}</td>)}</tr>
                    ))}</tbody>
                  </table>
                ) : <Empty>暂无</Empty>}
              </div>
            </Panel>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1.2fr]">
            <Panel title="学员进度（点击查看画像）">
              <div className="max-h-96 overflow-y-auto p-2 text-xs">
                {ov.trainee_progress.map((t: Json) => (
                  <button key={t.id} onClick={() => setSel(t.id)} className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-hmi-panel2 ${sel === t.id ? 'bg-hmi-panel2' : ''}`}>
                    <span className="w-28 truncate">{t.name}</span><span className="w-24 text-hmi-amber">{t.rank}</span>
                    <span className="h-2 flex-1 rounded bg-hmi-bg"><span className="block h-2 rounded bg-hmi-cyan" style={{ width: `${(t.passed / 14) * 100}%` }} /></span>
                    <span className="w-10 text-right tabular-nums">{t.passed}/14</span>
                    {t.is_demo && <span className="text-[10px] text-fuchsia-300">演示</span>}
                  </button>
                ))}
              </div>
            </Panel>
            <Panel title="学员画像：薄弱点与建议关卡" right={profile?.is_demo ? <DemoBadge /> : null}>
              {!profile ? <Empty>在左侧选择一名学员</Empty> : (
                <div className="space-y-2 p-3 text-sm">
                  <div><b className="text-white">{profile.trainee.name}</b> · {profile.rank.name} · 打了 {profile.sessions} 局，通过 {profile.passed_scenarios} 关，平均 {profile.avg_score ?? '—'} 分</div>
                  <div>
                    <div className="mb-1 text-xs text-hmi-dim">薄弱点（按违规标签与复盘标签统计）</div>
                    {profile.weak_tags.length ? <Bars data={profile.weak_tags.map((t: Json) => ({ label: t.tag, value: t.score }))} color="#f2b233" /> : <div className="text-xs text-hmi-dim">暂无明显薄弱点</div>}
                  </div>
                  <div>
                    <div className="mb-1 text-xs text-hmi-dim">建议关卡</div>
                    {profile.suggested.length ? profile.suggested.map((s: Json) => <div key={s.scenario_id} className="text-xs">· {s.title}（针对“{s.tag}”，最好成绩 {s.best ?? '未通过'}）</div>) : <div className="text-xs text-hmi-dim">暂无</div>}
                  </div>
                </div>
              )}
            </Panel>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel title="班组排行">
              <div className="p-3 text-sm">
                {teams?.teams.length ? teams.teams.map((t: Json, i: number) => (
                  <div key={t.team} className="flex items-center gap-2 py-0.5"><span className="w-6 text-hmi-amber">{i + 1}</span><span className="flex-1">{t.team}</span><span className="text-xs text-hmi-dim">{t.members} 人 · 人均通过 {t.avg_passed} 关 · 工位能手 {t.masters} 人</span></div>
                )) : <Empty>暂无班组（新建学员时填写班组）</Empty>}
              </div>
            </Panel>
            <Panel title="大模型调用（真实调用日志）" right={<LayerTag layer={2} />}>
              <div className="p-3 text-xs">
                {llm && Object.keys(llm.by_role).length ? (
                  <table className="w-full">
                    <thead className="text-hmi-dim"><tr><th className="text-left">角色</th><th>调用</th><th>成功</th><th>P50</th><th>P95</th><th>成本</th><th className="text-left">模式</th></tr></thead>
                    <tbody>{Object.entries(llm.by_role as Record<string, Json>).map(([k, v]) => (
                      <tr key={k} className="border-t border-hmi-line/40"><td className="py-1">{k}</td><td className="text-center">{v.calls}</td><td className="text-center">{v.ok}</td><td className="text-center">{v.p50_ms ? `${Math.round(v.p50_ms)}ms` : '—'}</td><td className="text-center">{v.p95_ms ? `${Math.round(v.p95_ms)}ms` : '—'}</td><td className="text-center">${v.cost_usd.toFixed(4)}</td><td className="text-hmi-dim">{Object.entries(v.modes).map(([m, n]) => `${m}:${n}`).join(' ')}</td></tr>
                    ))}</tbody>
                  </table>
                ) : <Empty>还没有调用记录</Empty>}
                <div className="mt-2 text-hmi-dim">单局平均成本：{llm?.avg_cost_per_session_usd !== null && llm?.avg_cost_per_session_usd !== undefined ? `$${llm.avg_cost_per_session_usd}` : '—（尚无在线对局）'}。{llm?.note}</div>
              </div>
            </Panel>
          </div>
        </>
      )}
    </div>
  )
}
