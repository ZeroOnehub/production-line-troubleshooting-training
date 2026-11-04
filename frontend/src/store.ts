import { create } from 'zustand'
import { api, safeStorage, type Meta, type StationInfo } from './api'

interface AppState {
  meta: Meta | null
  station: StationInfo | null
  traineeId: number | null
  traineeName: string | null
  loadMeta: () => Promise<void>
  loadStation: () => Promise<StationInfo>
  setTrainee: (id: number | null, name: string | null) => void
}

const savedId = safeStorage('trainee_id')

export const useApp = create<AppState>((set, get) => ({
  meta: null,
  station: null,
  traineeId: savedId ? Number(savedId) : null,
  traineeName: safeStorage('trainee_name'),
  loadMeta: async () => {
    try {
      set({ meta: await api.meta() })
    } catch {
      /* 后端未就绪时保持空 */
    }
  },
  loadStation: async () => {
    const cur = get().station
    if (cur) return cur
    const st = await api.station()
    set({ station: st })
    return st
  },
  setTrainee: (id, name) => {
    safeStorage('trainee_id', id === null ? '' : String(id))
    safeStorage('trainee_name', name ?? '')
    set({ traineeId: id, traineeName: name })
  },
}))
