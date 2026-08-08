import { useMemo, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { useStore } from '../../state/store'
import {
  SCORE_LANES, buildRunScoreModel, eventIndexForTime, formatElapsed,
  type ScoreBlock, type ScoreBlockStatus, type ScoreMarker,
} from './runScoreModel'
import { layoutScoreBlocks, layoutScoreMarkers, type LaidOutBlock, type LaidOutMarker } from './scoreLayout'

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

function laneHeight(blocks: LaidOutBlock[], orchestrator: boolean, trackHeight: number, presentation: boolean, markerTracks = 1): number {
  const tracks = blocks.length ? Math.max(...blocks.map((block) => block.visualTrack)) + 1 : 1
  return Math.max(
    orchestrator ? Math.max(presentation ? 94 : 76, 12 + markerTracks * (presentation ? 34 : 28)) : (presentation ? 72 : 56),
    12 + tracks * trackHeight,
  )
}

function position(ts: number, start: number, end: number): number {
  const span = Math.max(end - start, 0.001)
  return Math.max(0, Math.min(100, ((ts - start) / span) * 100))
}

function WorkBlock({ block, selected, onInspect, presentation, trackHeight }: {
  block: LaidOutBlock
  selected: boolean
  onInspect: (block: ScoreBlock) => void
  presentation: boolean
  trackHeight: number
}) {
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
  const showLabel = block.widthPct >= (presentation ? 7 : 9)

  return (
    <button
      data-score-block
      type="button"
      title={title}
      aria-label={`${block.lane} agent: ${block.task}, ${style.label}, ${formatElapsed(measured)}`}
      onClick={(event) => { event.stopPropagation(); onInspect(block) }}
      style={{
        position: 'absolute', left: `${block.leftPct}%`, top: 6 + block.visualTrack * trackHeight,
        width: `${block.widthPct}%`,
        height: presentation ? 48 : 36, minWidth: 0,
        border: `1px solid ${style.color}`, borderLeft: `4px solid ${style.color}`,
        borderRadius: 'var(--r-chip)', background: style.bg, color: 'var(--ink-900)',
        padding: showLabel ? (presentation ? '7px 11px' : '4px 8px') : 0,
        textAlign: 'left', cursor: 'pointer', overflow: 'hidden',
        boxShadow: selected ? `0 0 0 3px ${style.bg}, 0 0 0 4px ${style.color}`
          : block.status === 'running' ? '0 0 0 2px var(--running-bg)' : 'var(--e1)',
        zIndex: selected ? 5 : 3, fontFamily: 'var(--font-body)',
      }}
    >
      {showLabel ? <>
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
      </> : <span aria-hidden style={{ display: 'block', width: '100%', height: '100%', background: style.color, opacity: .78 }} />}
    </button>
  )
}

function OrchestratorMarker({ marker, selected, onSelect, presentation }: {
  marker: LaidOutMarker
  selected: boolean
  onSelect: (marker: ScoreMarker) => void
  presentation: boolean
}) {
  const style = MARKER_STYLE[marker.kind]
  return (
    <button
      data-score-marker
      type="button"
      title={marker.label}
      aria-label={`${marker.label}, orchestration marker`}
      onClick={(event) => { event.stopPropagation(); onSelect(marker) }}
      style={{
        position: 'absolute', left: `${marker.leftPct}%`, top: 6 + marker.visualTrack * (presentation ? 34 : 28),
        transform: marker.leftPct > 97 ? 'translateX(-100%)' : 'translateX(-50%)',
        width: presentation ? 30 : 24, height: presentation ? 30 : 24,
        display: 'inline-grid', placeItems: 'center', padding: 0, borderRadius: '50%',
        border: `1px solid ${style.color}`, background: style.bg, color: style.color,
        fontSize: presentation ? 14 : 11, fontWeight: 800, cursor: 'pointer',
        boxShadow: selected ? `0 0 0 3px ${style.bg}, 0 0 0 4px ${style.color}` : 'var(--e1)',
        zIndex: selected ? 7 : 5,
      }}
    >
      <span aria-hidden>{style.glyph}</span>
    </button>
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
  const [selection, setSelection] = useState<{ kind: 'block' | 'marker'; id: string } | null>(null)

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
  const laidOutBlocks = useMemo(
    () => layoutScoreBlocks(model.blocks, axisStart, axisEnd, presentation ? 1 : 1.4),
    [model.blocks, axisStart, axisEnd, presentation],
  )
  const laidOutMarkers = useMemo(
    () => layoutScoreMarkers(model.markers, axisStart, axisEnd, presentation ? 2.3 : 3.2),
    [model.markers, axisStart, axisEnd, presentation],
  )
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
    laidOutBlocks.filter((block) => block.lane === lane.id),
  ]))
  const markerTrackCount = laidOutMarkers.length
    ? Math.max(...laidOutMarkers.map((marker) => marker.visualTrack)) + 1 : 1
  const heights = SCORE_LANES.map((lane) => laneHeight(
    blocksByLane.get(lane.id) ?? [], lane.id === 'orchestrator', trackHeight, presentation,
    lane.id === 'orchestrator' ? markerTrackCount : 1,
  ))
  const bodyHeight = heights.reduce((sum, height) => sum + height, 0)
  const criticalMarkers = model.markers.filter((marker) => marker.kind === 'conflict' || marker.kind === 'approval')
  const observedTools = new Set(model.blocks.flatMap((block) => block.tools))
  const selectedBlock = selection?.kind === 'block'
    ? laidOutBlocks.find((block) => block.id === selection.id) ?? null : null
  const selectedMarker = selection?.kind === 'marker'
    ? laidOutMarkers.find((marker) => marker.id === selection.id) ?? null : null

  const inspect = (block: ScoreBlock) => {
    if (mode === 'replay') onSeek(block.endIndex)
    setSelection({ kind: 'block', id: block.id })
  }

  const selectMarker = (marker: ScoreMarker) => {
    if (mode === 'replay') onSeek(marker.index)
    setSelection({ kind: 'marker', id: marker.id })
  }

  const seekFromPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (mode !== 'replay' || !events.length) return
    if ((event.target as HTMLElement).closest('[data-score-block], [data-score-marker]')) return
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
              {mode === 'replay' ? 'Recorded backend events' : 'Live wall time'} · width shows latency · click to expand
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

      {(selectedBlock || selectedMarker) && (
        <ScoreSelection
          block={selectedBlock}
          marker={selectedMarker}
          startTs={axisStart}
          presentation={presentation}
          onClose={() => setSelection(null)}
          onFullTrace={selectedBlock ? () => openInspector(selectedBlock.stepId) : null}
        />
      )}

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
                      {lane.id === 'orchestrator' && laidOutMarkers.map((marker) => (
                        <OrchestratorMarker
                          key={marker.id} marker={marker}
                          selected={selection?.kind === 'marker' && selection.id === marker.id}
                          onSelect={selectMarker} presentation={presentation}
                        />
                      ))}
                      {laneBlocks.map((block) => (
                        <WorkBlock
                          key={block.id} block={block}
                          selected={selection?.kind === 'block' && selection.id === block.id}
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

function ScoreSelection({ block, marker, startTs, presentation, onClose, onFullTrace }: {
  block: LaidOutBlock | null
  marker: LaidOutMarker | null
  startTs: number
  presentation: boolean
  onClose: () => void
  onFullTrace: (() => void) | null
}) {
  if (!block && !marker) return null
  const blockStyle = block ? STATUS_STYLE[block.status] : null
  const markerStyle = marker ? MARKER_STYLE[marker.kind] : null
  const accent = blockStyle?.color ?? markerStyle?.color ?? 'var(--accent)'
  const measured = block
    ? block.latencyMs ?? Math.max(0, (block.endTs - block.startTs) * 1000)
    : Math.max(0, ((marker?.ts ?? startTs) - startTs) * 1000)
  return (
    <div role="status" aria-live="polite" style={{
      minHeight: presentation ? 76 : 64, padding: presentation ? '11px 22px' : '8px 14px',
      display: 'flex', alignItems: 'center', gap: presentation ? 18 : 12,
      borderBottom: '1px solid var(--line)', background: 'var(--surface-raised)',
      boxShadow: 'var(--e1)',
    }}>
      <span aria-hidden style={{ width: 4, alignSelf: 'stretch', borderRadius: 4, background: accent }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="eyebrow" style={{ color: accent, fontSize: presentation ? 11.5 : 9.5 }}>
          {block ? `${block.lane} · ${blockStyle?.label}` : `Orchestration · ${marker?.kind}`}
        </div>
        <div style={{
          marginTop: 1, fontSize: presentation ? 16 : 13, fontWeight: 700,
          lineHeight: presentation ? '21px' : '17px', color: 'var(--ink-900)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {block?.task ?? marker?.label}
        </div>
        <div style={{ marginTop: 2, fontSize: presentation ? 12.5 : 10.5, color: 'var(--ink-400)' }}>
          {block
            ? `${block.tools.length ? block.tools.join(' · ') : 'No tool call'} · latency ${formatElapsed(measured)}${block.retries ? ` · ${block.retries} retries` : ''}${block.fallback ? ' · fallback used' : ''}`
            : `Occurred at ${formatElapsed(measured)} from run start`}
        </div>
      </div>
      {onFullTrace && (
        <button onClick={onFullTrace} style={{
          border: '1px solid var(--accent)', background: 'var(--accent-weak)', color: 'var(--accent)',
          borderRadius: 'var(--r-input)', padding: presentation ? '7px 12px' : '5px 9px',
          font: `700 ${presentation ? 12.5 : 10.5}px var(--font-body)`, cursor: 'pointer', whiteSpace: 'nowrap',
        }}>Full trace</button>
      )}
      <button onClick={onClose} aria-label="Close expanded score item" style={{
        width: presentation ? 34 : 28, height: presentation ? 34 : 28, border: '1px solid var(--line)',
        borderRadius: 'var(--r-input)', background: 'transparent', color: 'var(--ink-600)',
        fontSize: presentation ? 20 : 17, lineHeight: 1, cursor: 'pointer',
      }}>×</button>
    </div>
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
