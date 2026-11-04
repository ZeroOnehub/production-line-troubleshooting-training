// 后端接口封装。所有判定都在后端，前端只负责展示与收集输入。

export type Json = Record<string, any>

export interface Meta {
  mode: 'online' | 'offline' | 'degraded'
  mode_label: string
  configured_mode: string
  has_api_key: boolean
  record_calls: boolean
  roles: Record<string, string | null>
  models_status: string
  prompt_versions: Record<string, string>
  content: { scenarios: number; rules: number; actions: number; components: number; factory: string }
  recordings: { total: number; by_role: Record<string, number>; files: string[] }
  demo_unlock_all: boolean
}

export interface ActionDef {
  id: string
  name: string
  panel: string
  params: Record<string, { type: string; values?: string[]; required?: boolean }>
  enabled: boolean
  requires_pause: boolean
}

export interface LogEntry {
  seq: number
  t: number
  action: string
  params: Json
  actor: string
  source: string
  result: string
  message: string
  violations: string[]
  state_before: string
  state_after: string
  observation?: Json | null
}

export interface Snapshot {
  session_id: string
  trainee_id: number
  ai_mode: string
  scenario: { id: string; version: number; type: string; title: string; story: string; objective: string; layer: number }
  state: string
  state_label: string
  mode: string
  mode_label: string
  clock: number
  downtime: number
  downtime_running: boolean
  loss_yuan: number
  loss_per_min: number
  hmi: Json
  indicators: { tower_light: string; pressure: number; pressure_zone: string; curtain: string; estop: string; controller_link: string }
  piece: Json | null
  order_variant: string
  produced_ok: number
  target_pieces: number | null
  ng_bin_count: number
  changeover: Json
  support: null | { kind: string; target: string; reported: boolean; arrival_in: number | null; transcript: { role: string; content: string }[] }
  messages: { t: number; from: string; text: string }[]
  actions: ActionDef[]
  log: LogEntry[]
  violations: Json[]
  hints_used: number
  ended: boolean
  objective_met: boolean
  terminated_by: string | null
  fail_reasons: string[]
  checklist?: { items: { id: string; name: string; standard: string; checked: boolean; recorded: string | null; sign_item: boolean }[]; signed: boolean }
  tour?: { codex: { id: string; name: string; desc: string; targets: string[]; lit: boolean }[]; cycle_steps: { id: string; name: string; text: string }[]; cycle_step_index: number }
}

export interface Hotspot {
  id: string
  viewpoint: string
  target: string
  label: string
  yaw: number
  pitch: number
  overlay: string | null
}

export interface StationInfo {
  factory: string
  station: Json
  variants: Record<string, Json>
  components: Record<string, { name: string; equipment: string; hidden: boolean; states: Record<string, { label: string; closeup: string }> }>
  targets: Record<string, string>
  hotspots: { panoramas: Record<string, { name: string; file: string; default_yaw: number; default_pitch: number }>; hotspots: Hotspot[] }
  andon_targets: Record<string, { name: string }>
  assets_base: string
  closeup_sources?: Record<string, string>
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `${r.status}`
    try {
      const j = await r.json()
      msg = j.detail || msg
    } catch {
      /* 非 JSON 响应 */
    }
    throw new Error(msg)
  }
  const ct = r.headers.get('content-type') || ''
  return (ct.includes('application/json') ? r.json() : r.text()) as Promise<T>
}

export const api = {
  meta: () => req<Meta>('GET', '/api/meta'),
  station: () => req<StationInfo>('GET', '/api/content/station'),
  trainees: (demo = false) => req<Json[]>('GET', `/api/trainees?include_demo=${demo}`),
  createTrainee: (name: string, team?: string) => req<Json>('POST', '/api/trainees', { name, team }),
  progress: (id: number) => req<Json>('GET', `/api/trainees/${id}/progress`),
  setUnlock: (id: number, unlock_all: boolean) => req<Json>('PATCH', `/api/trainees/${id}`, { unlock_all }),
  createSession: (body: Json) => req<{ id: string; snapshot: Snapshot }>('POST', '/api/sessions', body),
  sessions: (traineeId?: number) => req<Json[]>('GET', `/api/sessions${traineeId ? `?trainee_id=${traineeId}` : ''}`),
  session: (id: string) => req<Snapshot>('GET', `/api/sessions/${id}`),
  act: (id: string, action: string, params: Json = {}) => req<{ result: Json; snapshot: Snapshot }>('POST', `/api/sessions/${id}/act`, { action, params }),
  say: (id: string, text: string) => req<{ kind: string; ai: Json; snapshot: Snapshot }>('POST', `/api/sessions/${id}/say`, { text }),
  sayAudio: (id: string, blob: Blob) => {
    const fd = new FormData()
    fd.append('audio', blob, 'speech.webm')
    return req<{ kind: string; ai?: Json; snapshot: Snapshot; transcript?: Json; error?: string }>('POST', `/api/sessions/${id}/say`, fd)
  },
  hint: (id: string, question?: string) => req<{ hint: Json; snapshot: Snapshot }>('POST', `/api/sessions/${id}/hint`, { question }),
  andon: (id: string, text: string) => req<{ kind: string; ai: Json; snapshot: Snapshot }>('POST', `/api/sessions/${id}/andon`, { text }),
  end: (id: string) => req<{ snapshot: Snapshot }>('POST', `/api/sessions/${id}/end`),
  review: (id: string) => req<Json>('GET', `/api/sessions/${id}/review`),
  tts: async (text: string, speaker: string): Promise<Blob | null> => {
    const r = await fetch('/api/voice/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, speaker }) })
    if (r.status !== 200) return null
    return r.blob()
  },
  // 工坊
  drafts: () => req<Json[]>('GET', '/api/drafts'),
  draft: (id: string) => req<Json>('GET', `/api/drafts/${id}`),
  createDraft: (body: Json) => req<Json>('POST', '/api/drafts', body),
  uploadDraftAudio: (title: string, author: string, blob: Blob) => {
    const fd = new FormData()
    fd.append('title', title)
    fd.append('author', author)
    fd.append('audio', blob, 'oral.webm')
    return req<Json>('POST', '/api/drafts/upload', fd)
  },
  patchDraft: (id: string, body: Json) => req<Json>('PATCH', `/api/drafts/${id}`, body),
  generate: (id: string, use_feedback = false) => req<Json>('POST', `/api/drafts/${id}/generate`, { use_feedback }),
  validate: (id: string) => req<Json>('POST', `/api/drafts/${id}/validate`),
  reviewDraft: (id: string, body: Json) => req<Json>('POST', `/api/drafts/${id}/reviews`, body),
  publish: (id: string) => req<Json>('POST', `/api/drafts/${id}/publish`),
  catalog: () => req<Json>('GET', '/api/workshop/catalog'),
  scenarioYaml: (id: string) => req<string>('GET', `/api/workshop/scenarios/${id}/yaml`),
  versions: (id: string) => req<Json[]>('GET', `/api/scenarios/${id}/versions`),
  diff: (id: string, a: number, b: number) => req<Json>('GET', `/api/scenarios/${id}/diff?a=${a}&b=${b}`),
  rollback: (id: string, version: number) => req<Json>('POST', `/api/scenarios/${id}/rollback`, { version }),
  recheck: () => req<Json[]>('GET', '/api/workshop/recheck'),
  recheckConfirm: (id: string, retire = false) => req<Json>('POST', `/api/workshop/recheck/${id}`, { retire }),
  importDowntime: () => req<Json>('POST', '/api/workshop/downtime/import'),
  topics: () => req<Json>('GET', '/api/workshop/topics'),
  productionTime: () => req<Json>('GET', '/api/workshop/production-time'),
  scenarios: () => req<Json[]>('GET', '/api/scenarios'),
  // 看板
  overview: (demo: boolean) => req<Json>('GET', `/api/stats/overview?demo=${demo}`),
  errors: (demo: boolean) => req<Json>('GET', `/api/stats/errors?demo=${demo}`),
  traineeStats: (id: number) => req<Json>('GET', `/api/stats/trainees/${id}`),
  teams: (demo: boolean) => req<Json>('GET', `/api/stats/teams?demo=${demo}`),
  llmStats: () => req<Json>('GET', '/api/stats/llm'),
  // 评测
  evalRuns: () => req<Json[]>('GET', '/api/eval/runs'),
  evalRun: (run: string) => req<Json>('GET', `/api/eval/runs/${run}`),
  badcases: () => req<Json[]>('GET', '/api/eval/badcases'),
}

export function fmtMin(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${r.toString().padStart(2, '0')}`
}

export const TARGET_NAMES: Record<string, string> = { maintenance: '维修技术员', quality: '质量员', team_leader: '班组长' }

export function safeStorage(key: string, value?: string): string | null {
  try {
    if (value === undefined) return window.localStorage.getItem(key)
    window.localStorage.setItem(key, value)
    return value
  } catch {
    return null
  }
}
