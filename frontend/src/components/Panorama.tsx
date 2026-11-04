import { useEffect, useRef, useState } from 'react'
import { Viewer } from '@photo-sphere-viewer/core'
import { MarkersPlugin } from '@photo-sphere-viewer/markers-plugin'
import '@photo-sphere-viewer/core/index.css'
import '@photo-sphere-viewer/markers-plugin/index.css'
import type { Hotspot, Snapshot, StationInfo } from '../api'

const TOWER = { green: '#3fbf6b', yellow: '#f2b233', red: '#e5484d', off: '#3a4048' } as Record<string, string>

function overlayHtml(kind: string | null, snap: Snapshot | null, lit: boolean): string {
  const ind = snap?.indicators
  const ring = lit ? 'box-shadow:0 0 0 3px rgba(63,191,107,.7);' : ''
  if (!kind || !ind) return `<div class="hs" style="${ring}"></div>`
  switch (kind) {
    case 'tower_light': {
      const c = TOWER[ind.tower_light] || TOWER.off
      const blink = ind.tower_light === 'red' ? 'animation:psvblink 1s steps(2) infinite;' : ''
      return `<div class="hs-overlay" style="display:flex;flex-direction:column;gap:2px;padding:3px;background:#0009;border-radius:4px;${ring}">
        ${['red', 'yellow', 'green'].map((k) => `<span style="width:16px;height:9px;border-radius:2px;background:${ind.tower_light === k ? c : '#2a2f36'};${ind.tower_light === k ? blink : ''}"></span>`).join('')}</div>`
    }
    case 'gauge': {
      const col = ind.pressure_zone === '绿区' ? '#3fbf6b' : ind.pressure_zone === '黄区' ? '#f2b233' : '#e5484d'
      return `<div class="hs-overlay" style="padding:2px 6px;background:#000c;border:2px solid ${col};border-radius:12px;color:#fff;font:600 12px system-ui;${ring}">${ind.pressure.toFixed(2)} MPa</div>`
    }
    case 'estop': {
      const pressed = ind.estop === 'pressed'
      return `<div class="hs-overlay" style="padding:2px 6px;background:#000c;border:2px solid ${pressed ? '#e5484d' : '#f2b233'};border-radius:12px;color:${pressed ? '#ff8a8a' : '#fff'};font:600 12px system-ui;${ring}">${pressed ? '急停按下' : '急停'}</div>`
    }
    case 'curtain': {
      const bad = ind.curtain !== 'ok'
      return `<div class="hs-overlay" style="padding:2px 6px;background:#000c;border:2px solid ${bad ? '#e5484d' : '#3fbf6b'};border-radius:12px;color:#fff;font:600 12px system-ui;${ring}">${bad ? '光栅中断' : '光栅'}</div>`
    }
    case 'controller': {
      const ok = ind.controller_link === 'ok'
      return `<div class="hs-overlay" style="display:flex;align-items:center;gap:4px;padding:2px 6px;background:#000c;border-radius:12px;color:#fff;font:600 12px system-ui;${ring}"><span style="width:10px;height:10px;border-radius:50%;background:${ok ? '#3fbf6b' : '#e5484d'}"></span>控制器</div>`
    }
    case 'hmi': {
      const alarm = snap?.hmi?.alarm
      return `<div class="hs-overlay" style="padding:3px 8px;background:#06121a;border:2px solid ${alarm ? '#e5484d' : '#36b3c9'};border-radius:4px;color:${alarm ? '#ff8a8a' : '#9fe6f2'};font:600 12px system-ui;${ring}">${alarm ? alarm : 'HMI'}</div>`
    }
    case 'nav':
      return `<div class="hs-nav">⇄</div>`
    default:
      return `<div class="hs" style="${ring}"></div>`
  }
}

/** 浏览器能不能用 WebGL（360° 全景需要；Chrome 关闭“图形加速”时不可用）。 */
function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}

// 平面模式显示的俯仰范围（弧度）：覆盖全部热点
const FLAT_TOP = 0.6
const FLAT_BOTTOM = -1.0

/** 平面全景：不需要 WebGL。等距柱状图按俯仰带裁切，可左右拖动，热点按偏航 / 俯仰换算到平面坐标。 */
function FlatPanorama({ station, vp, snap, litTargets, onSelect, onGoto }: {
  station: StationInfo; vp: string; snap: Snapshot | null; litTargets: string[]; onSelect: (h: Hotspot) => void; onGoto: (vp: string) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [h, setH] = useState(0)
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null)
  const p = station.hotspots.panoramas[vp]
  const band = (FLAT_TOP - FLAT_BOTTOM) / Math.PI
  const width = h ? (h * 2) / band : 0
  const xOf = (yaw: number) => { let f = 0.5 + yaw / (2 * Math.PI); f -= Math.floor(f); return f * width }
  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(() => setH(el.clientHeight))
    ro.observe(el)
    setH(el.clientHeight)
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    const el = box.current
    if (el && width && p) el.scrollLeft = xOf(p.default_yaw) - el.clientWidth / 2
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vp, width])
  const list = station.hotspots.hotspots.filter((x) => x.viewpoint === vp)
  return (
    <div ref={box} className="absolute inset-0 cursor-grab overflow-x-auto overflow-y-hidden bg-black"
      onPointerDown={(e) => { if (box.current) drag.current = { x: e.clientX, left: box.current.scrollLeft, moved: false } }}
      onPointerMove={(e) => { const d = drag.current; if (d && box.current) { if (Math.abs(e.clientX - d.x) > 4) d.moved = true; box.current.scrollLeft = d.left - (e.clientX - d.x) } }}
      onPointerUp={() => { setTimeout(() => { drag.current = null }, 0) }}
      onPointerLeave={() => { drag.current = null }}>
      {p && width > 0 && (
        <div className="relative h-full select-none" style={{ width, backgroundImage: `url(${station.assets_base}/${p.file})`, backgroundSize: `${width}px ${width / 2}px`,
          backgroundPosition: `0px ${-(0.5 - FLAT_TOP / Math.PI) * (width / 2)}px`, backgroundRepeat: 'no-repeat' }}>
          {list.map((hs) => (
            <div key={hs.id} title={hs.label} className="absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: xOf(hs.yaw), top: ((FLAT_TOP - hs.pitch) / (FLAT_TOP - FLAT_BOTTOM)) * h }}
              onClick={() => { if (drag.current?.moved) return; if (hs.target.startsWith('@goto:')) onGoto(hs.target.slice(6)); else onSelect(hs) }}
              dangerouslySetInnerHTML={{ __html: overlayHtml(hs.overlay, snap, litTargets.includes(hs.target)) }} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function Panorama({ station, snap, onSelect, litTargets = [] }: { station: StationInfo; snap: Snapshot | null; onSelect: (h: Hotspot) => void; litTargets?: string[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<Viewer | null>(null)
  const [vp, setVp] = useState<string>('operator')
  const [ready, setReady] = useState(0)
  const [mode, setMode] = useState<'sphere' | 'flat'>(() => {
    try { if (new URLSearchParams(location.search).get('pano') === 'flat' || localStorage.getItem('pano_mode') === 'flat') return 'flat' } catch { /* 无存储时按能力判断 */ }
    return webglAvailable() ? 'sphere' : 'flat'
  })
  const chooseMode = (m: 'sphere' | 'flat') => {
    try { localStorage.setItem('pano_mode', m) } catch { /* 忽略 */ }
    setReady(0)
    setMode(m)
  }
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const panos = station.hotspots.panoramas

  // 初始化（360° 模式）。创建失败或 8 秒内没加载出来，自动切换为平面模式
  useEffect(() => {
    if (mode !== 'sphere' || !ref.current) return
    const p = panos[vp] || Object.values(panos)[0]
    if (!p) return
    let viewer: Viewer
    let loaded = false
    const timer = window.setTimeout(() => { if (!loaded) setMode('flat') }, 8000)
    try {
      viewer = new Viewer({
      container: ref.current,
      panorama: `${station.assets_base}/${p.file}`,
      defaultYaw: p.default_yaw,
      defaultPitch: p.default_pitch,
      defaultZoomLvl: 30,
      navbar: ['zoom', 'move', 'fullscreen'],
      loadingTxt: '加载全景…',
      lang: { zoom: '缩放', zoomOut: '缩小', zoomIn: '放大', moveUp: '上', moveDown: '下', moveLeft: '左', moveRight: '右', fullscreen: '全屏', loadError: '全景加载失败' } as any,
      plugins: [[MarkersPlugin, { markers: [] }]],
    })
    } catch {
      window.clearTimeout(timer)
      setMode('flat')
      return
    }
    viewerRef.current = viewer
    viewer.addEventListener('ready', () => { loaded = true; window.clearTimeout(timer); setReady((x) => x + 1) }, { once: true })
    viewer.addEventListener('panorama-loaded', () => setReady((x) => x + 1))
    const markers = viewer.getPlugin(MarkersPlugin) as MarkersPlugin
    markers.addEventListener('select-marker', (e: any) => {
      const h = e.marker?.data as Hotspot | undefined
      if (!h) return
      if (h.target.startsWith('@goto:')) {
        setVp(h.target.slice(6))
        return
      }
      onSelectRef.current(h)
    })
    return () => {
      window.clearTimeout(timer)
      try { viewer.destroy() } catch { /* 已失效的 WebGL 上下文 */ }
      viewerRef.current = null
    }
    // 只在模式变化时初始化；切换点位走下面的 effect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // 切换点位
  useEffect(() => {
    const viewer = viewerRef.current
    const p = panos[vp]
    if (!viewer || !p) return
    viewer.setPanorama(`${station.assets_base}/${p.file}`, { position: { yaw: p.default_yaw, pitch: p.default_pitch }, transition: { speed: 600 } }).catch(() => undefined)
  }, [vp, panos, station.assets_base])

  // 热点与动态叠加层
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer || !ready) return
    const markers = viewer.getPlugin(MarkersPlugin) as MarkersPlugin
    const list = station.hotspots.hotspots.filter((h) => h.viewpoint === vp)
    markers.setMarkers(
      list.map((h) => ({
        id: h.id,
        position: { yaw: h.yaw, pitch: h.pitch },
        html: overlayHtml(h.overlay, snap, litTargets.includes(h.target)),
        anchor: 'center center',
        tooltip: { content: h.label, position: 'top center' },
        data: h,
      })) as any,
    )
  }, [vp, snap, station, litTargets, ready])

  return (
    <div className="relative h-full w-full overflow-hidden rounded-md">
      <style>{'@keyframes psvblink{50%{opacity:.25}}'}</style>
      {mode === 'sphere' ? <div ref={ref} className="absolute inset-0" /> : (
        <FlatPanorama station={station} vp={vp} snap={snap} litTargets={litTargets} onSelect={(h) => onSelectRef.current(h)} onGoto={setVp} />
      )}
      <div className="pointer-events-none absolute left-2 top-2 flex gap-2">
        <span className="tag border-hmi-line bg-black/60 text-hmi-dim">{panos[vp]?.name}</span>
        <span className="tag border-hmi-line bg-black/60 text-hmi-dim">示意全景（程序生成占位）</span>
        {mode === 'flat' && <span className="tag border-hmi-amber/60 bg-black/70 text-hmi-amber" title="浏览器没有开启图形加速，360° 视图不可用。打开 Chrome 设置 → 系统 → “使用图形加速功能”，重启浏览器即可恢复 360° 视图">平面模式：可左右拖动，部件照常点击</span>}
      </div>
      <div className="absolute right-2 top-2 z-10 flex gap-1">
        {Object.entries(panos).map(([k, p]) => (
          <button key={k} className={`btn text-xs ${vp === k ? 'border-hmi-amber text-hmi-amber' : ''}`} onClick={() => setVp(k)}>{p.name.split('（')[0]}</button>
        ))}
        <button className="btn text-xs" title="360° 全景需要浏览器开启图形加速；加载不出来时切换为平面模式" onClick={() => chooseMode(mode === 'sphere' ? 'flat' : 'sphere')}>
          {mode === 'sphere' ? '平面' : '360°'}
        </button>
      </div>
    </div>
  )
}
