import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Json } from '../../api'
import { Badge, DemoBadge, Empty, ErrorLine, LayerTag, Panel } from '../../components/ui'

export const SAMPLE_ORAL = `OP30 报 T-104 夹具未到位，我带徒弟时最常见的两种：
一是夹具定位面上掉了铁屑，工件被垫起来没贴合，要先暂停，把工件拿出来用气枪把碎屑吹干净再放回去，复位就好；
二是到位传感器坏了，工件明明放好了，传感器指示灯就是不亮，这种别自己去掰传感器，直接拉 Andon 叫维修，换完传感器要打个首件。
新人常犯的错是没暂停就伸手去抠铁屑。`

const STATUS_TONE: Record<string, 'dim' | 'amber' | 'cyan' | 'green' | 'red'> = {
  collecting: 'dim', ai_draft: 'amber', validating: 'cyan', pending_review: 'cyan', published: 'green', needs_recheck: 'red', retired: 'dim',
}

export default function WorkshopHome() {
  const nav = useNavigate()
  const [drafts, setDrafts] = useState<Json[]>([])
  const [topics, setTopics] = useState<Json | null>(null)
  const [recheck, setRecheck] = useState<Json[]>([])
  const [prod, setProd] = useState<Json | null>(null)
  const [scns, setScns] = useState<Json[]>([])
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('示例师傅')
  const [oral, setOral] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    api.drafts().then(setDrafts).catch((e) => setError(e.message))
    api.topics().then(setTopics).catch(() => undefined)
    api.recheck().then(setRecheck).catch(() => undefined)
    api.productionTime().then(setProd).catch(() => undefined)
    api.scenarios().then(setScns).catch(() => undefined)
  }
  useEffect(load, [])

  const create = async () => {
    try {
      const d = await api.createDraft({ title: title || '未命名关卡', author, source_text: oral })
      nav(`/workshop/drafts/${d.id}`)
    } catch (e: any) {
      setError(e.message)
    }
  }
  const upload = async (f: File | null) => {
    if (!f) return
    try {
      const d = await api.uploadDraftAudio(title || '录音口述', author, f)
      nav(`/workshop/drafts/${d.id}`)
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-3 p-4">
      <h1 className="text-xl font-semibold text-white">关卡工坊 · 师傅口述 → 关卡发布</h1>
      <ErrorLine error={error} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.2fr_1fr]">
        <Panel title="新建关卡草稿（采集口述）">
          <div className="space-y-2 p-3">
            <div className="flex gap-2">
              <input className="input" placeholder="关卡标题，如：夹具未到位（新版）" value={title} onChange={(e) => setTitle(e.target.value)} />
              <input className="input max-w-[160px]" placeholder="口述人" value={author} onChange={(e) => setAuthor(e.target.value)} />
            </div>
            <textarea className="input h-36 font-mono text-xs" placeholder="粘贴师傅口述的故障经验：什么报警、常见的几种原因、怎么看出来、怎么处理、什么情况必须叫人、新人常犯什么错" value={oral} onChange={(e) => setOral(e.target.value)} />
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" disabled={!oral.trim()} onClick={create}>创建草稿并开始计时</button>
              <button className="btn" onClick={() => { setOral(SAMPLE_ORAL); setTitle('夹具未到位（工坊示例）') }}>填入示例口述（虚构）</button>
              <label className="btn cursor-pointer">上传录音转写<input type="file" accept="audio/*" className="hidden" onChange={(e) => upload(e.target.files?.[0] || null)} /></label>
            </div>
            <div className="text-[11px] text-hmi-dim">从创建草稿到发布的耗时会被记录。录音转写需要在线模式。</div>
          </div>
        </Panel>
        <Panel title="关卡制作耗时（实测）" right={<LayerTag layer={2} />}>
          <div className="p-3 text-sm">
            {prod && prod.count > 0 ? (
              <div className="space-y-1">
                <div>已发布 <b>{prod.count}</b> 个，平均 <b>{prod.avg_minutes}</b> 分钟，中位数 <b>{prod.median_minutes}</b> 分钟</div>
                {(prod.items as Json[]).slice(0, 6).map((i) => (
                  <div key={i.draft_id} className="text-xs text-hmi-dim">{i.title} → {i.scenario_id} v{i.version}：{i.minutes} 分钟（{i.gen_mode}；首次校验{i.first_validation_ok ? '通过' : '未通过'}）</div>
                ))}
              </div>
            ) : <Empty>还没有从工坊发布的关卡。发布第一个后这里会显示真实耗时。</Empty>}
          </div>
        </Panel>
      </div>
      <Panel title="草稿">
        {drafts.length === 0 ? <Empty>还没有草稿。</Empty> : (
          <table className="w-full text-sm">
            <thead className="text-xs text-hmi-dim"><tr className="border-b border-hmi-line"><th className="p-2 text-left">草稿</th><th className="text-left">标题</th><th>状态</th><th>版本</th><th>生成方式</th><th>审核</th><th>耗时</th></tr></thead>
            <tbody>
              {drafts.map((d) => (
                <tr key={d.id} className="cursor-pointer border-b border-hmi-line/50 hover:bg-hmi-panel2" onClick={() => nav(`/workshop/drafts/${d.id}`)}>
                  <td className="p-2 font-mono text-xs">{d.id}</td>
                  <td>{d.title}{d.is_demo && <span className="ml-1"><DemoBadge /></span>}</td>
                  <td className="text-center"><Badge tone={STATUS_TONE[d.status]}>{d.status_label}</Badge></td>
                  <td className="text-center">v{d.version}</td>
                  <td className="text-center text-xs text-hmi-dim">{d.gen_mode || '—'}</td>
                  <td className="text-center text-xs">{d.reviews_passed.length}/3</td>
                  <td className="text-center tabular-nums text-xs">{Math.round(d.elapsed_s / 60)} 分</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Panel title="停机记录选题（按频次 × 时长推荐）" right={<span className="flex items-center gap-2"><DemoBadge /><button className="btn !py-0.5 text-xs" onClick={async () => { await api.importDowntime(); load() }}>导入合成停机记录</button></span>}>
          <div className="max-h-80 overflow-y-auto p-2 text-xs">
            {topics && topics.topics.length ? (
              <table className="w-full">
                <thead className="text-hmi-dim"><tr><th className="text-left">工位·报警</th><th>次数</th><th>分钟</th><th className="text-left">覆盖</th></tr></thead>
                <tbody>
                  {(topics.topics as Json[]).map((t) => (
                    <tr key={t.station + t.alarm_code} className="border-t border-hmi-line/40" title={t.samples.join('；')}>
                      <td className="py-1">{t.station} {t.alarm_code}</td><td className="text-center">{t.count}</td><td className="text-center">{t.minutes}</td>
                      <td className={t.coverage === '未覆盖' ? 'text-hmi-amber' : 'text-hmi-dim'}>{t.coverage === '未覆盖' ? `★ ${t.recommendation}` : t.coverage}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty>还没有导入停机记录。</Empty>}
          </div>
        </Panel>
        <Panel title="待复核（规则变更自动标记）">
          <div className="p-2 text-sm">
            {recheck.length === 0 ? <Empty>没有待复核的关卡。修改 content/ontology/rules.yaml 中被引用的规则后，这里会自动列出受影响关卡。</Empty> : recheck.map((r) => (
              <div key={r.scenario_id} className="flex items-center gap-2 border-b border-hmi-line/50 py-1">
                <span className="font-mono text-xs">{r.scenario_id} v{r.version}</span><span className="flex-1 text-xs text-hmi-amber">{r.reason}</span>
                <button className="btn !py-0.5 text-xs" onClick={async () => { await api.recheckConfirm(r.scenario_id); load() }}>复核通过</button>
                <button className="btn !py-0.5 text-xs" onClick={async () => { await api.recheckConfirm(r.scenario_id, true); load() }}>下线</button>
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <Panel title="关卡库（版本、对比与回滚）">
        <div className="grid grid-cols-2 gap-1 p-2 text-xs md:grid-cols-4 xl:grid-cols-7">
          {scns.map((s) => <Link key={s.id} to={`/workshop/scenarios/${s.id}`} className="btn justify-between !text-xs"><span>{s.title}</span><span className="text-hmi-dim">v{s.version}</span></Link>)}
        </div>
      </Panel>
    </div>
  )
}
