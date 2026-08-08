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
  safety: { color: 'var(--running)', bg: 'var(--running-bg)', glyph: '⌖' },
  conflict: { color: 'var(--danger)', bg: 'var(--danger-bg)', glyph: '⚡' },
  approval: { color: 'var(--approval)', bg: 'var(--approval-bg)', glyph: '⏸' },
  finish: { color: 'var(--success)', bg: 'var(--success-bg)', glyph: '✓' },
  error: { color: 'var(--danger)', bg: 'var(--danger-bg)', glyph: '!' },
  fallback: { color: 'var(--degraded)', bg: 'var(--degraded-bg)', glyph: '⇢' },
}

function laneHeight(blocks: ScoreBlock[], orchestrator: boolean, trackHeight: number, presentation: boolean): number {
  const tracks = blocks.length ? Math.max(...blocks.map((block) => block.track)) + 1 : 1
  return Math.max(
    orchestrator ? (presentation ? 94 : 76) : (presentation ? 72 : 56),
    12 + tracks * trackHeight,
  )
}

function position(ts: number, start: number, end: number): number {
  const span = Math.max(end - start, 0.001)
  return Math.max(0, Math.min(100, ((ts - start) / span) * 100))
}

function WorkBlock({ block, startTs, endTs, onInspect, presentation, trackHeight }: {
  block: ScoreBlock
  startTs: number
  endTs: number
  onInspect: (block: ScoreBlock) => void
  presentation: boolean
  trackHeight: number
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
        position: 'absolute', left: `${left}%`, top: 6 + block.track * trackHeight,
        width: `max(${width}%, ${presentation ? 126 : 82}px)`,
        height: presentation ? 48 : 36, minWidth: 0,
        border: `1px solid ${style.color}`, borderLeft: `4px solid ${style.color}`,
        borderRadius: 'var(--r-chip)', background: style.bg, color: 'var(--ink-900)',
        padding: presentation ? '7px 11px' : '4px 8px',
        textAlign: 'left', cursor: 'pointer', overflow: 'hidden',
        boxShadow: block.status === 'running' ? '0 0 0 2px var(--running-bg)' : 'var(--e1)',
        zIndex: 3, fontFamily: 'var(--font-body)',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        {block.status === 'running' && <span className="pulse-dot" style={{ background: style.color, flex: '0 0 auto' }} />}
        <strong style={{
          minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          fontSize: presentation ? 14.5 : 11.5,
          lineHeight: presentation ? '18px' : '14px', fontWeight: 700,
        }}>{block.task}</strong>
      </span>
      <span className="tnum" style={{
        display: 'block', fontSize: presentation ? 12 : 10,
        lineHeight: presentation ? '15px' : '12px', color: style.color,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {tool || style.label} · {formatElapsed(measured)}
        {block.retries ? ` · retry ×${block.retries}` : ''}
      </span>
    </button>
  )
}

function OrchestratorMarker({ marker, startTs, endTs, order, presentation }: {
  marker: ScoreMarker
  startTs: number
  endTs: number
  order: number
  presentation: boolean
}) {
  const style = MARKER_STYLE[marker.kind]
  const left = Math.min(position(marker.ts, startTs, endTs), 96)
  return (
    <span
      title={marker.label}
      style={{
        position: 'absolute', left: `${left}%`, top: order % 2 ? (presentation ? 49 : 39) : 6,
        transform: left > 88 ? 'translateX(-100%)' : left < 8 ? 'none' : 'translateX(-50%)',
        display: 'inline-flex', alignItems: 'center', gap: 4,
        maxWidth: presentation ? 170 : 118,
        padding: presentation ? '5px 10px' : '3px 7px', borderRadius: 'var(--r-pill)',
        border: `1px solid ${style.color}`, background: style.bg, color: style.color,
        fontSize: presentation ? 13 : 10.5,
        lineHeight: presentation ? '17px' : '14px', fontWeight: 700, whiteSpace: 'nowrap',
        overflow: 'hidden', textOverflow: 'ellipsis', zIndex: 5,
      }}
    >
      <span aria-hidden>{style.glyph}</span>{marker.label}
    </span>
  )
}

export function RunScore({ onSeek, presentation = false }: {
  onSeek: (index: number) => void
  presentation?: boolean
}) {
  const mode = useStore((s) => s.mode)
  const sourceEvents = useStore((s) => s.events)
  const progress = useStore((s) => s.progress)
  const run = useStore((s) => s.run)
  const openInspector = useStore((s) => s.openInspector)
  const closeInspector = useStore((s) => s.closeInspector)

  const events = mode === 'replay' ? sourceEvents : run.timeline
  const labelWidth = presentation ? 188 : LABEL_W
  const axisHeight = presentation ? 52 : AXIS_H
  const footerHeight = presentation ? 70 : FOOT_H
  const trackHeight = presentation ? 58 : TRACK_H
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
  const heights = SCORE_LANES.map((lane) => laneHeight(
    blocksByLane.get(lane.id) ?? [], lane.id === 'orchestrator', trackHeight, presentation,
  ))
  const bodyHeight = heights.reduce((sum, height) => sum + height, 0)
  const criticalMarkers = model.markers.filter((marker) => marker.kind === 'conflict' || marker.kind === 'approval')
  const observedTools = new Set(model.blocks.flatMap((block) => block.tools))

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
        display: 'flex', flexDirection: 'column', gap: presentation ? 12 : 8,
        padding: presentation ? '15px 22px' : '10px 14px',
        borderBottom: '1px solid var(--line)', background: 'var(--surface)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow" style={{
              color: 'var(--accent)', fontSize: presentation ? 14 : undefined,
            }}>Agent collaboration score</div>
            <div style={{ fontSize: presentation ? 14 : 12, color: 'var(--ink-400)' }}>
              {mode === 'replay' ? 'Recorded backend events' : 'Live wall time'} · short work is widened for readability
            </div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: presentation ? 28 : 16, alignItems: 'baseline' }}>
            <Metric presentation={presentation} label="Elapsed" value={`${formatElapsed(elapsedMs)} / ${formatElapsed(model.durationMs)}`} />
            <Metric presentation={presentation} label="Peak parallel" value={`${model.peakConcurrency || 0} agents`} />
            <Metric presentation={presentation} label="Tools verified" value={`${observedTools.size} / 24`} />
          </div>
        </div>
        <CapabilityCoverage blocks={model.blocks} presentation={presentation} />
      </header>

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        <div style={{ minWidth: presentation ? 980 : 620, position: 'relative' }}>
          <div style={{
            height: axisHeight, display: 'grid', gridTemplateColumns: `${labelWidth}px minmax(0, 1fr)`,
            borderBottom: '1px solid var(--line)', background: 'var(--surface-sunken)',
          }}>
            <div className="eyebrow" style={{
              padding: presentation ? '15px 18px 0' : '12px 12px 0',
              fontSize: presentation ? 13 : undefined,
            }}>Agent lanes</div>
            <div style={{ position: 'relative', marginRight: presentation ? 24 : 16 }}>
              {ticks.map((tick) => (
                <span key={tick.fraction} className="tnum" style={{
                  position: 'absolute', left: `${tick.fraction * 100}%`, top: presentation ? 15 : 10,
                  transform: tick.fraction === 0 ? 'none' : tick.fraction === 1 ? 'translateX(-100%)' : 'translateX(-50%)',
                  fontSize: presentation ? 12.5 : 10.5, color: 'var(--ink-400)',
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
                    height, display: 'grid', gridTemplateColumns: `${labelWidth}px minmax(0, 1fr)`,
                    borderBottom: '1px solid var(--line)', background: laneIndex % 2 ? 'var(--surface)' : 'var(--paper)',
                  }}>
                    <div style={{
                      padding: presentation ? '12px 14px 10px 20px' : '10px 10px 8px 14px',
                      borderRight: '1px solid var(--line)',
                      display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0,
                    }}>
                      <span className="eyebrow" style={{
                        color: lane.id === 'orchestrator'
                          ? 'var(--agent-orchestration)'
                          : `var(--agent-${lane.id})`,
                        fontSize: presentation ? 13 : 10.5,
                      }}>
                        {lane.label}
                      </span>
                      <span style={{
                        fontSize: presentation ? 12.5 : 10.5,
                        lineHeight: presentation ? '17px' : '14px', color: 'var(--ink-400)',
                      }}>{lane.role}</span>
                    </div>
                    <div style={{
                      position: 'relative', minWidth: 0,
                      marginRight: presentation ? 24 : 16, overflow: 'hidden',
                    }}>
                      <div aria-hidden style={{
                        position: 'absolute', left: 0, right: 0, top: '50%', height: 1,
                        background: 'var(--line)',
                      }} />
                      {lane.id === 'orchestrator' && model.markers.map((marker, index) => (
                        <OrchestratorMarker
                          key={marker.id} marker={marker} startTs={axisStart} endTs={axisEnd}
                          order={index} presentation={presentation}
                        />
                      ))}
                      {laneBlocks.map((block) => (
                        <WorkBlock
                          key={block.id} block={block} startTs={axisStart} endTs={axisEnd}
                          onInspect={inspect} presentation={presentation} trackHeight={trackHeight}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>

            <div aria-hidden style={{
              position: 'absolute', left: labelWidth, right: presentation ? 24 : 16,
              top: 0, height: bodyHeight,
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
            height: footerHeight, display: 'grid', gridTemplateColumns: `${labelWidth}px minmax(0, 1fr)`,
            background: 'var(--surface)', borderBottom: '1px solid var(--line)',
          }}>
            <div style={{
              padding: presentation ? '13px 18px' : '10px 12px', borderRight: '1px solid var(--line)',
            }}>
              <div className="eyebrow" style={{ fontSize: presentation ? 12 : 10.5 }}>Run position</div>
              <div className="tnum" style={{ fontSize: presentation ? 14 : 11.5, color: 'var(--ink-600)' }}>
                {model.visibleCount} / {model.totalCount} events
              </div>
            </div>
            <div style={{ padding: presentation ? '13px 24px 10px 0' : '10px 16px 8px 0' }}>
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
              <div style={{ fontSize: presentation ? 12.5 : 10.5, color: 'var(--ink-400)', marginTop: 2 }}>
                {mode === 'replay'
                  ? 'Drag to pause and inspect any point. Click a work block for its tools, evidence and result.'
                  : 'The cursor advances with the live event stream.'}
              </div>
            </div>
          </footer>

          {!events.length && (
            <div style={{
              position: 'absolute', left: labelWidth, right: presentation ? 24 : 16,
              top: axisHeight + (presentation ? 110 : 88),
              textAlign: 'center', pointerEvents: 'none',
            }}>
              <div className="font-display" style={{
                fontSize: presentation ? 28 : 20, color: 'var(--ink-600)',
              }}>The score is waiting</div>
              <div style={{ fontSize: presentation ? 16 : 13, color: 'var(--ink-400)', marginTop: 5 }}>
                Ask a question or play a recorded run. Overlapping work will appear on these permanent agent lanes.
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function Metric({ label, value, presentation }: { label: string; value: string; presentation: boolean }) {
  return (
    <div style={{ textAlign: 'right' }}>
      <div className="eyebrow" style={{
        fontSize: presentation ? 11.5 : 9.5, lineHeight: presentation ? '15px' : '12px',
      }}>{label}</div>
      <div className="tnum" style={{
        fontSize: presentation ? 16 : 12.5, fontWeight: 700,
        color: 'var(--ink-900)', whiteSpace: 'nowrap',
      }}>
        {value}
      </div>
    </div>
  )
}

const TOOL_TOTALS: Record<string, number> = {
  academic: 5,
  placement: 4,
  events: 4,
  knowledge: 2,
  services: 9,
}

function CapabilityCoverage({ blocks, presentation }: { blocks: ScoreBlock[]; presentation: boolean }) {
  const byAgent = new Map<string, Set<string>>()
  for (const block of blocks) {
    const tools = byAgent.get(block.lane) ?? new Set<string>()
    block.tools.forEach((tool) => tools.add(tool))
    byAgent.set(block.lane, tools)
  }

  return (
    <div aria-label="Backend capability coverage" style={{
      display: 'grid', gridTemplateColumns: 'repeat(5, minmax(90px, 1fr))',
      gap: presentation ? 10 : 6,
    }}>
      {SCORE_LANES.filter((lane) => lane.id !== 'orchestrator').map((lane) => {
        const count = byAgent.get(lane.id)?.size ?? 0
        const total = TOOL_TOTALS[lane.id]
        const complete = count === total
        return (
          <div key={lane.id} title={`${count} of ${total} ${lane.label} tools observed`} style={{
            minWidth: 0, padding: presentation ? '8px 10px' : '5px 7px', border: '1px solid var(--line)',
            borderRadius: 'var(--r-chip)', background: complete ? 'var(--success-bg)' : 'var(--surface-sunken)',
          }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
              <span className="eyebrow" style={{
                minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
                color: complete ? 'var(--success)' : `var(--agent-${lane.id})`,
                fontSize: presentation ? 11.5 : 9,
              }}>{lane.label}</span>
              <span className="tnum" style={{
                marginLeft: 'auto', color: complete ? 'var(--success)' : 'var(--ink-400)',
                fontSize: presentation ? 13 : 10.5, fontWeight: 700,
              }}>{count}/{total}</span>
            </div>
            <span style={{
              display: 'block', height: presentation ? 4 : 2,
              marginTop: presentation ? 5 : 3, borderRadius: 2,
              background: 'var(--line)', overflow: 'hidden',
            }}>
              <span style={{
                display: 'block', height: '100%', width: `${(count / total) * 100}%`,
                background: complete ? 'var(--success)' : `var(--agent-${lane.id})`,
                transition: 'width var(--t-state)',
              }} />
            </span>
          </div>
        )
      })}
    </div>
  )
}
