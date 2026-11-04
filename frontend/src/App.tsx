import { useEffect } from 'react'
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import EvalPage from './pages/EvalPage'
import History from './pages/train/History'
import Login from './pages/train/Login'
import MapPage from './pages/train/MapPage'
import Play from './pages/train/Play'
import Review from './pages/train/Review'
import DraftPage from './pages/workshop/DraftPage'
import ScenarioVersions from './pages/workshop/ScenarioVersions'
import WorkshopHome from './pages/workshop/WorkshopHome'
import { ModeBadge } from './components/ui'
import { useApp } from './store'

function TopBar() {
  const { meta, traineeName } = useApp()
  const link = ({ isActive }: { isActive: boolean }) => `px-3 py-1 rounded text-sm ${isActive ? 'bg-hmi-panel2 text-white' : 'text-hmi-dim hover:text-white'}`
  return (
    <header className="flex h-12 items-center gap-2 border-b border-hmi-line bg-hmi-panel px-3">
      <Link to="/" className="mr-2 flex items-center gap-2 font-semibold text-white">
        <span className="flex flex-col gap-[2px]"><span className="h-1.5 w-3 rounded-sm bg-hmi-red" /><span className="h-1.5 w-3 rounded-sm bg-hmi-amber" /><span className="h-1.5 w-3 rounded-sm bg-hmi-green" /></span>
        佳境
      </Link>
      <NavLink to="/train" className={link}>训练端</NavLink>
      <NavLink to="/workshop" className={link}>关卡工坊</NavLink>
      <NavLink to="/dashboard" className={link}>管理看板</NavLink>
      <NavLink to="/eval" className={link}>评测台</NavLink>
      <div className="ml-auto flex items-center gap-2 text-xs text-hmi-dim">
        {traineeName && <span>学员：{traineeName}</span>}
        <span>虚构示例 · 示例转向系统总成装配车间</span>
        {meta ? <ModeBadge mode={meta.mode} label={meta.mode_label} /> : <ModeBadge mode="degraded" label="连接中" />}
      </div>
    </header>
  )
}

function Home() {
  const meta = useApp((s) => s.meta)
  const cards = [
    { to: '/train', title: '训练端', desc: '全景工位 + HMI + 动作面板 + 自然语言行动；带班师傅三级提示、Andon 电话汇报、复盘五维得分。' },
    { to: '/workshop', title: '关卡工坊', desc: '师傅口述 → AI 生成剧本 → 格式/引用/引擎回放校验 → 设备·质量·EHS 三方审核 → 发布与版本回滚。' },
    { to: '/dashboard', title: '管理看板', desc: '学员进度、关卡完成率、平均处置时长、提示使用率、安全违规、错误分布与学员画像。' },
    { to: '/eval', title: '评测台', desc: '按 7 类场景、8 个指标评测候选模型；历史评测对比、bad case 浏览。命令行一条命令复现。' },
  ]
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">佳境 · 产线排障情景训练系统</h1>
        <p className="mt-2 text-sm text-hmi-dim">OP30 电机总成螺栓拧紧工位（虚构示例）。设备反应、违规判定、得分和维修到场时间全部由规则状态机计算；大模型只负责口语理解、陪练提示、电话追问、复盘点评和关卡起草。</p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {cards.map((c) => (
          <Link key={c.to} to={c.to} className="panel block p-4 transition-colors hover:border-hmi-amber">
            <div className="text-lg font-semibold text-white">{c.title}</div>
            <div className="mt-1 text-sm text-hmi-dim">{c.desc}</div>
          </Link>
        ))}
      </div>
      {meta && (
        <div className="panel p-4 text-xs text-hmi-dim">
          <div>AI 模式：<ModeBadge mode={meta.mode} label={meta.mode_label} />（配置 {meta.configured_mode}{meta.has_api_key ? '，已配置 API Key' : '，未配置 API Key'}）· 录制文件 {meta.recordings.total} 条</div>
          <div className="mt-1">本体：{meta.content.scenarios} 个关卡、{meta.content.rules} 条规则、{meta.content.actions} 个动作、{meta.content.components} 个部件。模型配置：{meta.models_status}</div>
          <div className="mt-1">角色 → 模型：{Object.entries(meta.roles).map(([k, v]) => `${k}=${v ?? '规则'}`).join('，')}</div>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const loadMeta = useApp((s) => s.loadMeta)
  useEffect(() => {
    loadMeta()
    const t = setInterval(loadMeta, 30000)
    return () => clearInterval(t)
  }, [loadMeta])
  return (
    <BrowserRouter>
      <TopBar />
      <main className="min-h-[calc(100vh-48px)]">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/train" element={<Login />} />
          <Route path="/train/map" element={<MapPage />} />
          <Route path="/train/history" element={<History />} />
          <Route path="/train/play/:id" element={<Play />} />
          <Route path="/train/review/:id" element={<Review />} />
          <Route path="/workshop" element={<WorkshopHome />} />
          <Route path="/workshop/drafts/:id" element={<DraftPage />} />
          <Route path="/workshop/scenarios/:id" element={<ScenarioVersions />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/eval" element={<EvalPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
