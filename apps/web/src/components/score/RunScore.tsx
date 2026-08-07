import { useMemo, type PointerEvent as ReactPointerEvent } from 'react'

import { useStore } from '../../state/store'
import {
  SCORE_LANES, buildRunScoreModel, eventIndexForTime, formatElapsed,
  type ScoreBlock, type ScoreBlockStatus, type ScoreMarker,
} from './runScoreModel'

const LABEL_W = 142
const AXIS_H = 42
const FOOT_H = 58
const TRACK_H = 44

const STATUS_STYLE: Record<ScoreBlockStatus, { label: string; color: string; bg: string }> = {
  running: { label: 'Running', color: 'var(--running)', bg: 'var(--running-bg)' },
  done: { label: 'Done', color: 'var(--success)', bg: 'var(--success-bg)' },
  'awaiting-approval': { label: 'Awaiting approval', color: 'var(--approval)', bg: 'var(--approval-bg)' },
  degraded: { label: 'Degraded', color: 'var(--degraded)', bg: 'var(--degraded-bg)' },
  failed: { label: 'Failed', color: 'var(--danger)', bg: 'var(--danger-bg)' },
  rejected: { label: 'Rejected', color: 'var(--denied)', bg: 'var(--denied-bg)' },
  denied: { label: 'Not permitted', color: 'var(--denied)', bg: 'var(--denied-bg)' },
  cancelled: { label: 'Cancelled', color: 'var(--ink-400)', bg: 'var(--surface-sunken)' },
}

const MARKER_STYLE: Record<ScoreMarker['kind'], { color: string; bg: string; glyph: string }> = {
  plan: { color: 'var(--accent)', bg: 'var(--accent-weak)', glyph: '◇' },
  replan: { color: 'var(--degraded)', bg: 'var(--degraded-bg)', glyph: '↻' },
  conflict: { color: 'var(--danger)', bg: 'var(--danger-bg)', glyph: '⚡' },
  approval: { color: 'var(--approval)', bg: 'var(--approval-bg)', glyph: '⏸' },
  finish: { color: 'var(--success)', bg: 'var(--success-bg)', glyph: '✓' },
  error: { color: 'var(--danger)', bg: 'var(--danger-bg)', glyph: '!' },
  fallback: { color: 'var(--degraded)', bg: 'var(--degraded-bg)', glyph: '⇢' },
}

function laneHeight(blocks: ScoreBlock[], orchestrator: boolean): number {
  const tracks = blocks.length ? Math.max(...blocks.map((block) => block.track)) + 1 : 1
  return Math.max(orchestrator ? 76 : 56, 12 + tracks * TRACK_H)
}

function position(ts: number, start: number, end: number): number {
  const span = Math.max(end - start, 0.001)
  return Math.max(0, Math.min(100, ((ts - start) / span) * 100))
}

function WorkBlock({ block, startTs, endTs, onInspect }: {
  block: ScoreBlock
  startTs: number
  endTs: number
  onInspect: (block: ScoreBlock) => void
}) {
  const actualLeft = position(block.startTs, startTs, endTs)
  const actualRight = position(block.endTs, startTs, endTs)
  // The fixtures are intentionally fast (single-digit milliseconds). Keep the
  // exact location, but widen short work enough to be selectable and readable.
  const left = Math.min(actualLeft, 92)
  const width = Math.max(0.35, actualRight - actualLeft)
  const style = STATUS_STYLE[block.status]
  const measured = block.latencyMs ?? Math.max(0, (block.endTs - block.startTs) * 1000)
  const tool = block.tools.join(' · ')
  const title = [
    block.task,
    `${style.label} · ${formatElapsed(measured)}`,
    tool ? `Tools: ${tool}` : 'No tool call',
    block.retries ? `${block.retries} retr${block.retries === 1 ? 'y' : 'ies'}` : '',
    block.fallback ? 'Fallback used' : '',
  ].filter(Boolean).join('\n')

  return (
    <button
      data-score-block
      type="button"
      title={title}
      aria-label={`${block.lane} agent: ${block.task}, ${style.label}, ${formatElapsed(measured)}`}
      onClick={(event) => { event.stopPropagation(); onInspect(block) }}
      style={{
        position: 'absolute', left: `${left}%`, top: 6 + block.track * TRACK_H,
        width: `max(${width}%, 82px)`, height: 36, minWidth: 0,
        border: `1px solid ${style.color}`, borderLeft: `4px solid ${style.color}`,
        borderRadius: 'var(--r-chip)', background: style.bg, color: 'var(--ink-900)',
        padding: '4px 8px', textAlign: 'left', cursor: 'pointer', overflow: 'hidden',
        boxShadow: block.status === 'running' ? '0 0 0 2px var(--running-bg)' : 'var(--e1)',
        zIndex: 3, fontFamily: 'var(--font-body)',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        {block.status === 'running' && <span className="pulse-dot" style={{ background: style.color, flex: '0 0 auto' }} />}
        <strong style={{
          minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          fontSize: 11.5, lineHeight: '14px', fontWeight: 700,
        }}>{block.task}</strong>
      </span>
      <span className="tnum" style={{
        display: 'block', fontSize: 10, lineHeight: '12px', color: style.color,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {tool || style.label} · {formatElapsed(measured)}
        {block.retries ? ` · retry ×${block.retries}` : ''}
      </span>
    </button>
  )
}

function OrchestratorMarker({ marker, startTs, endTs, order }: {
  marker: ScoreMarker
  startTs: number
  endTs: number
  order: number
}) {
  const style = MARKER_STYLE[marker.kind]
  const left = Math.min(position(marker.ts, startTs, endTs), 96)
  return (
    <span
      title={marker.label}
      style={{
        position: 'absolute', left: `${left}%`, top: order % 2 ? 39 : 6,
        transform: left > 88 ? 'translateX(-100%)' : left < 8 ? 'none' : 'translateX(-50%)',
        display: 'inline-flex', alignItems: 'center', gap: 4,
        maxWidth: 118, padding: '3px 7px', borderRadius: 'var(--r-pill)',
        border: `1px solid ${style.color}`, background: style.bg, color: style.color,
        fontSize: 10.5, lineHeight: '14px', fontWeight: 700, whiteSpace: 'nowrap',
        overflow: 'hidden', textOverflow: 'ellipsis', zIndex: 5,
      }}
    >
      <span aria-hidden>{style.glyph}</span>{marker.label}
    </span>
  )
}

export function RunScore({ onSeek }: { onSeek: (index: number) => void }) {
  const mode = useStore((s) => s.mode)
  const sourceEvents = useStore((s) => s.events)
  const progress = useStore((s) => s.progress)
  const run = useStore((s) => s.run)
  const openInspector = useStore((s) => s.openInspector)
  const closeInspector = useStore((s) => s.closeInspector)

  const events = mode === 'replay' ? sourceEvents : run.timeline
  const visibleCount = mode === 'replay' ? progress.index : events.length
  const model = useMemo(
    () => buildRunScoreModel(events, visibleCount),
    [events, visibleCount],
  )
  const axisStart = model.startTs
  const axisEnd = model.endTs
  const cursorLeft = position(model.cursorTs, axisStart, axisEnd)
  const elapsedMs = model.visibleCount > 0
    ? Math.max(0, (model.cursorTs - model.startTs) * 1000)
    : 0
  const ticks = Array.from({ length: 5 }, (_, i) => {
    const fraction = i / 4
    return {
      fraction,
      label: formatElapsed(model.durationMs * fraction),
    }
  })
  const blocksByLane = new Map(SCORE_LANES.map((lane) => [
    lane.id,
    model.blocks.filter((block) => block.lane === lane.id),
  ]))
  const heights = SCORE_LANES.map((lane) => laneHeight(blocksByLane.get(lane.id) ?? [], lane.id === 'orchestrator'))
  const bodyHeight = heights.reduce((sum, height) => sum + height, 0)
  const criticalMarkers = model.markers.filter((marker) => marker.kind === 'conflict' || marker.kind === 'approval')

  const inspect = (block: ScoreBlock) => {
    if (mode === 'replay') onSeek(block.endIndex)
    openInspector(block.stepId)
  }

  const seekFromPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (mode !== 'replay' || !events.length) return
    if ((event.target as HTMLElement).closest('[data-score-block]')) return
    const rect = event.currentTarget.getBoundingClientRect()
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
    const ts = axisStart + (axisEnd - axisStart) * fraction
    closeInspector()
    onSeek(eventIndexForTime(events, ts))
  }

  return (
    <section aria-label="Agent collaboration score" style={{
      height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column',
      background: 'var(--paper)', overflow: 'hidden',
    }}>
      <header style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
        borderBottom: '1px solid var(--line)', background: 'var(--surface)',
      }}>
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow" style={{ color: 'var(--accent)' }}>Agent collaboration score</div>
          <div style={{ fontSize: 12, color: 'var(--ink-400)' }}>
            {mode === 'replay' ? 'Recorded run timing' : 'Live wall time'} · short work is widened for readability
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 16, alignItems: 'baseline' }}>
          <Metric label="Elapsed" value={`${formatElapsed(elapsedMs)} / ${formatElapsed(model.durationMs)}`} />
          <Metric label="Peak parallel" value={`${model.peakConcurrency || 0} agents`} />
          <Metric label="Plan" value={`v${Math.max(1, run.planVersion)}`} />
        </div>
      </header>

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        <div style={{ minWidth: 620, position: 'relative' }}>
          <div style={{
            height: AXIS_H, display: 'grid', gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)`,
            borderBottom: '1px solid var(--line)', background: 'var(--surface-sunken)',
          }}>
            <div className="eyebrow" style={{ padding: '12px 12px 0' }}>Agent lanes</div>
            <div style={{ position: 'relative', marginRight: 16 }}>
              {ticks.map((tick) => (
                <span key={tick.fraction} className="tnum" style={{
                  position: 'absolute', left: `${tick.fraction * 100}%`, top: 10,
                  transform: tick.fraction === 0 ? 'none' : tick.fraction === 1 ? 'translateX(-100%)' : 'translateX(-50%)',
                  fontSize: 10.5, color: 'var(--ink-400)',
                }}>{tick.label}</span>
              ))}
            </div>
          </div>

          <div style={{ position: 'relative' }}>
            <div
              aria-label={mode === 'replay' ? 'Click the score to inspect that moment' : undefined}
              onPointerDown={seekFromPointer}
              style={{ cursor: mode === 'replay' ? 'crosshair' : 'default' }}
            >
              {SCORE_LANES.map((lane, laneIndex) => {
                const laneBlocks = blocksByLane.get(lane.id) ?? []
                const height = heights[laneIndex]
                return (
                  <div key={lane.id} style={{
                    height, display: 'grid', gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)`,
                    borderBottom: '1px solid var(--line)', background: laneIndex % 2 ? 'var(--surface)' : 'var(--paper)',
                  }}>
                    <div style={{
                      padding: '10px 10px 8px 14px', borderRight: '1px solid var(--line)',
                      display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0,
                    }}>
                      <span className="eyebrow" style={{ color: `var(--agent-${lane.id})`, fontSize: 10.5 }}>
                        {lane.label}
                      </span>
                      <span style={{ fontSize: 10.5, lineHeight: '14px', color: 'var(--ink-400)' }}>{lane.role}</span>
                    </div>
                    <div style={{ position: 'relative', minWidth: 0, marginRight: 16, overflow: 'hidden' }}>
                      <div aria-hidden style={{
                        position: 'absolute', left: 0, right: 0, top: '50%', height: 1,
                        background: 'var(--line)',
                      }} />
                      {lane.id === 'orchestrator' && model.markers.map((marker, index) => (
                        <OrchestratorMarker key={marker.id} marker={marker} startTs={axisStart} endTs={axisEnd} order={index} />
                      ))}
                      {laneBlocks.map((block) => (
                        <WorkBlock key={block.id} block={block} startTs={axisStart} endTs={axisEnd} onInspect={inspect} />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>

            <div aria-hidden style={{
              position: 'absolute', left: LABEL_W, right: 16, top: 0, height: bodyHeight,
              pointerEvents: 'none', overflow: 'hidden',
            }}>
              {model.gates.map((gate) => {
                const left = position(gate.startTs, axisStart, axisEnd)
                const right = position(gate.endTs, axisStart, axisEnd)
                return (
                  <div key={gate.id} style={{
                    position: 'absolute', left: `${left}%`, top: 0,
                    width: `${Math.max(0.5, right - left)}%`, minWidth: 5, height: '100%',
                    background: 'color-mix(in srgb, var(--approval) 9%, transparent)',
                    borderLeft: '2px solid var(--approval)', borderRight: gate.decision ? '1px solid var(--approval)' : 'none',
                  }} />
                )
              })}
              {criticalMarkers.map((marker) => {
                const style = MARKER_STYLE[marker.kind]
                return (
                  <div key={`line-${marker.id}`} style={{
                    position: 'absolute', left: `${position(marker.ts, axisStart, axisEnd)}%`,
                    top: 0, height: '100%', borderLeft: `2px solid ${style.color}`,
                    opacity: 0.7,
                  }} />
                )
              })}
              {model.visibleCount > 0 && (
                <div style={{
                  position: 'absolute', left: `${cursorLeft}%`, top: 0, height: '100%',
                  borderLeft: '1px solid var(--accent)', zIndex: 8,
                }}>
                  <span style={{
                    position: 'absolute', top: 0, left: -4, width: 8, height: 8,
                    borderRadius: 999, background: 'var(--accent)', boxShadow: '0 0 0 3px var(--accent-weak)',
                  }} />
                </div>
              )}
            </div>
          </div>

          <footer style={{
            height: FOOT_H, display: 'grid', gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)`,
            background: 'var(--surface)', borderBottom: '1px solid var(--line)',
          }}>
            <div style={{ padding: '10px 12px', borderRight: '1px solid var(--line)' }}>
              <div className="eyebrow" style={{ fontSize: 10.5 }}>Run position</div>
              <div className="tnum" style={{ fontSize: 11.5, color: 'var(--ink-600)' }}>
                {model.visibleCount} / {model.totalCount} events
              </div>
            </div>
            <div style={{ padding: '10px 16px 8px 0' }}>
              {mode === 'replay' ? (
                <input
                  aria-label="Scrub recorded run"
                  type="range"
                  min={0}
                  max={Math.max(0, events.length)}
                  value={Math.min(progress.index, events.length)}
                  onChange={(event) => { closeInspector(); onSeek(Number(event.target.value)) }}
                  style={{ width: '100%', accentColor: 'var(--accent)', cursor: 'ew-resize' }}
                />
              ) : (
                <div style={{
                  height: 4, marginTop: 10, borderRadius: 'var(--r-pill)',
                  background: 'linear-gradient(90deg, var(--accent), var(--running))',
                }} />
              )}
              <div style={{ fontSize: 10.5, color: 'var(--ink-400)', marginTop: 2 }}>
                {mode === 'replay'
                  ? 'Drag to pause and inspect any point. Click a work block for its tools, evidence and result.'
                  : 'The cursor advances with the live event stream.'}
              </div>
            </div>
          </footer>

          {!events.length && (
            <div style={{
              position: 'absolute', left: LABEL_W, right: 16, top: AXIS_H + 88,
              textAlign: 'center', pointerEvents: 'none',
            }}>
              <div className="font-display" style={{ fontSize: 20, color: 'var(--ink-600)' }}>The score is waiting</div>
              <div style={{ fontSize: 13, color: 'var(--ink-400)', marginTop: 5 }}>
                Ask a question or play a recorded run. Overlapping work will appear on these permanent agent lanes.
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ textAlign: 'right' }}>
      <div className="eyebrow" style={{ fontSize: 9.5, lineHeight: '12px' }}>{label}</div>
      <div className="tnum" style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink-900)', whiteSpace: 'nowrap' }}>
        {value}
      </div>
    </div>
  )
}
