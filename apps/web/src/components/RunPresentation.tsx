import { ArrowLeft, Maximize2, Minimize2, Pause, Play, RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'

import { useStore } from '../state/store'
import { NodeInspector } from './NodeInspector'
import { RunScore } from './score/RunScore'

interface RunPresentationProps {
  fixtureLabel: string
  onSeek: (index: number) => void
  onBack: () => void
  onTogglePlayback: () => void
  onRestart: () => void
  onSpeedChange: (speed: number) => void
}

/**
 * Projector-first replay surface.
 *
 * This intentionally replaces the cockpit instead of opening a popup: the
 * replay, approval queue, reducer state and node inspector remain the exact
 * same objects while the score gets the entire viewport.
 */
export function RunPresentation({
  fixtureLabel,
  onSeek,
  onBack,
  onTogglePlayback,
  onRestart,
  onSpeedChange,
}: RunPresentationProps) {
  const status = useStore((s) => s.status)
  const progress = useStore((s) => s.progress)
  const speed = useStore((s) => s.speed)
  const theme = useStore((s) => s.theme)
  const activeApprovalId = useStore((s) => s.activeApprovalId)
  const setTheme = useStore((s) => s.setTheme)
  const [nativeFullscreen, setNativeFullscreen] = useState(false)

  useEffect(() => {
    const sync = () => setNativeFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', sync)
    sync()
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [])

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await document.documentElement.requestFullscreen()
    } catch {
      // Fullscreen can be denied by an embedding host. Presentation mode still
      // owns the viewport, so this optional enhancement may fail silently.
    }
  }

  const leavePresentation = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
    } finally {
      onBack()
    }
  }

  const complete = progress.total > 0 && progress.index >= progress.total
  const playing = status === 'streaming'
  const playbackLabel = activeApprovalId
    ? 'Decision required'
    : complete || status === 'closed'
      ? 'Replay again'
      : playing
        ? 'Pause'
        : 'Continue'
  const PlaybackIcon = complete || status === 'closed' ? RotateCcw : playing ? Pause : Play
  const pct = progress.total ? Math.min(100, (progress.index / progress.total) * 100) : 0

  return (
    <div style={{
      width: '100vw', height: '100vh', overflow: 'hidden',
      display: 'flex', flexDirection: 'column', background: 'var(--paper)',
    }}>
      <header style={{
        minHeight: 72, padding: '10px 22px', display: 'flex', alignItems: 'center', gap: 18,
        borderBottom: '2px solid var(--line)', background: 'var(--surface)', flexWrap: 'wrap',
      }}>
        <button onClick={() => void leavePresentation()} style={secondaryButton}>
          <ArrowLeft size={17} aria-hidden />
          Back to cockpit
        </button>

        <div style={{ minWidth: 230 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span className="font-display" style={{ fontSize: 25 }}>Sūtra</span>
            <span className="eyebrow" style={{ color: 'var(--accent)' }}>Presentation mode</span>
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-600)', whiteSpace: 'nowrap' }}>
            {fixtureLabel}
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 210, maxWidth: 520 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
            <span className="eyebrow" style={{ fontSize: 10.5 }}>
              Recorded proof · 5 agents
            </span>
            <span className="tnum" style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink-600)' }}>
              {progress.index} / {progress.total} events
            </span>
          </div>
          <div style={{ height: 5, background: 'var(--surface-sunken)', borderRadius: 'var(--r-pill)', overflow: 'hidden' }}>
            <span style={{
              display: 'block', width: `${pct}%`, height: '100%', borderRadius: 'inherit',
              background: complete ? 'var(--success)' : 'var(--accent)', transition: 'width var(--t-micro)',
            }} />
          </div>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            onClick={complete || status === 'closed' ? onRestart : onTogglePlayback}
            disabled={Boolean(activeApprovalId)}
            style={{
              ...primaryButton,
              opacity: activeApprovalId ? 0.55 : 1,
              cursor: activeApprovalId ? 'not-allowed' : 'pointer',
            }}
          >
            <PlaybackIcon size={16} aria-hidden />
            {playbackLabel}
          </button>

          <label className="eyebrow" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10.5 }}>
            Speed
            <select
              aria-label="Presentation replay speed"
              value={speed}
              onChange={(event) => onSpeedChange(Number(event.target.value))}
              style={selectStyle}
            >
              {[0.5, 1, 2, 4].map((value) => <option key={value} value={value}>{value}×</option>)}
            </select>
          </label>

          <button onClick={() => void toggleFullscreen()} style={iconButton}
            aria-label={nativeFullscreen ? 'Exit browser full screen' : 'Enter browser full screen'}
            title={nativeFullscreen ? 'Exit browser full screen' : 'Enter browser full screen'}>
            {nativeFullscreen ? <Minimize2 size={18} aria-hidden /> : <Maximize2 size={18} aria-hidden />}
          </button>
          <button onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} style={secondaryButton}>
            {theme === 'light' ? 'Dark' : 'Light'}
          </button>
        </div>
      </header>

      <main style={{ flex: 1, minHeight: 0, position: 'relative', overflow: 'hidden' }}>
        <RunScore onSeek={onSeek} presentation />
        <NodeInspector />
      </main>
    </div>
  )
}

const primaryButton: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 7,
  minHeight: 38, padding: '8px 14px', borderRadius: 'var(--r-input)',
  border: '1px solid var(--accent)', background: 'var(--accent)', color: 'var(--accent-ink)',
  fontSize: 13, fontWeight: 700, fontFamily: 'var(--font-body)',
}

const secondaryButton: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 7, cursor: 'pointer',
  minHeight: 38, padding: '8px 12px', borderRadius: 'var(--r-input)',
  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--ink-600)',
  fontSize: 13, fontWeight: 700, fontFamily: 'var(--font-body)', whiteSpace: 'nowrap',
}

const iconButton: React.CSSProperties = {
  ...secondaryButton, width: 38, padding: 0, justifyContent: 'center',
}

const selectStyle: React.CSSProperties = {
  minHeight: 34, padding: '5px 8px', borderRadius: 'var(--r-input)',
  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--ink-900)',
  fontSize: 12.5, fontFamily: 'var(--font-body)',
}
