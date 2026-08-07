import type { AgentEvent } from '../../types/events'

export const SCORE_LANES = [
  { id: 'orchestrator', label: 'Sūtradhāra', role: 'plans · arbitrates · gates' },
  { id: 'academic', label: 'Academic', role: 'schedule · attendance · veto' },
  { id: 'placement', label: 'Placement', role: 'eligibility · preparation' },
  { id: 'events', label: 'Events', role: 'discovery · registration' },
  { id: 'knowledge', label: 'Knowledge', role: 'policy · citations' },
  { id: 'services', label: 'Services', role: 'calendar · reminders · comms' },
] as const

export type ScoreLaneId = (typeof SCORE_LANES)[number]['id']
export type ScoreBlockStatus =
  | 'running' | 'done' | 'awaiting-approval' | 'degraded'
  | 'failed' | 'rejected' | 'denied' | 'cancelled'

export interface ScoreBlock {
  id: string
  stepId: string
  lane: ScoreLaneId
  task: string
  startTs: number
  endTs: number
  startIndex: number
  endIndex: number
  status: ScoreBlockStatus
  latencyMs: number | null
  tools: string[]
  retries: number
  fallback: boolean
  track: number
}

export type ScoreMarkerKind = 'plan' | 'replan' | 'conflict' | 'approval' | 'finish' | 'error' | 'fallback'

export interface ScoreMarker {
  id: string
  kind: ScoreMarkerKind
  label: string
  ts: number
  index: number
}

export interface ApprovalGate {
  id: string
  label: string
  startTs: number
  endTs: number
  startIndex: number
  endIndex: number
  decision: string | null
}

export interface RunScoreModel {
  startTs: number
  endTs: number
  cursorTs: number
  durationMs: number
  blocks: ScoreBlock[]
  markers: ScoreMarker[]
  gates: ApprovalGate[]
  peakConcurrency: number
  visibleCount: number
  totalCount: number
}

const STATUS: Record<string, ScoreBlockStatus> = {
  ok: 'done',
  pending_approval: 'awaiting-approval',
  degraded: 'degraded',
  error: 'failed',
  failed: 'failed',
  reject: 'rejected',
  rejected: 'rejected',
  permission_denied: 'denied',
  cancelled: 'cancelled',
}

function laneFor(agent: string | null): ScoreLaneId {
  if (agent === 'academic' || agent === 'placement' || agent === 'events' ||
      agent === 'knowledge' || agent === 'services') return agent
  return 'orchestrator'
}

function markerFor(e: AgentEvent, index: number, planVersion: number): ScoreMarker | null {
  const p = e.payload as Record<string, unknown>
  if (e.type === 'plan.created' && Array.isArray(p.steps)) {
    return { id: e.id, kind: 'plan', label: 'Plan v1', ts: e.ts, index }
  }
  if (e.type === 'plan.revised' && Array.isArray(p.steps)) {
    return { id: e.id, kind: 'replan', label: `Plan v${planVersion}`, ts: e.ts, index }
  }
  if (e.type === 'conflict.detected') {
    return { id: e.id, kind: 'conflict', label: 'Academic veto', ts: e.ts, index }
  }
  if (e.type === 'approval.requested') {
    return { id: e.id, kind: 'approval', label: 'Human gate', ts: e.ts, index }
  }
  if (e.type === 'tool.fallback') {
    return { id: e.id, kind: 'fallback', label: 'Fallback', ts: e.ts, index }
  }
  if (e.type === 'run.finished') {
    return { id: e.id, kind: 'finish', label: 'Answer ready', ts: e.ts, index }
  }
  if (e.type === 'run.error') {
    return { id: e.id, kind: 'error', label: 'Run failed', ts: e.ts, index }
  }
  return null
}

function assignTracks(blocks: ScoreBlock[]): ScoreBlock[] {
  const byLane = new Map<ScoreLaneId, ScoreBlock[]>()
  for (const block of blocks) {
    const lane = byLane.get(block.lane) ?? []
    lane.push(block)
    byLane.set(block.lane, lane)
  }

  const tracks = new Map<string, number>()
  for (const laneBlocks of byLane.values()) {
    const ends: number[] = []
    for (const block of [...laneBlocks].sort((a, b) => a.startTs - b.startTs || a.endTs - b.endTs)) {
      let track = ends.findIndex((end) => end <= block.startTs)
      if (track < 0) track = ends.length
      ends[track] = Math.max(block.endTs, block.startTs + 0.000001)
      tracks.set(block.id, track)
    }
  }
  return blocks.map((block) => ({ ...block, track: tracks.get(block.id) ?? 0 }))
}

function peakConcurrency(blocks: ScoreBlock[]): number {
  const points = blocks.flatMap((block) => [
    { ts: block.startTs, delta: 1 },
    { ts: Math.max(block.endTs, block.startTs + 0.000001), delta: -1 },
  ]).sort((a, b) => a.ts - b.ts || a.delta - b.delta)

  let active = 0
  let peak = 0
  for (const point of points) {
    active += point.delta
    peak = Math.max(peak, active)
  }
  return peak
}

/**
 * Convert the observable event history into a score. The full source controls
 * the stable time axis; visibleCount controls what the current replay frame is
 * allowed to reveal. No future block or marker is rendered before it occurs.
 */
export function buildRunScoreModel(events: AgentEvent[], visibleCount = events.length): RunScoreModel {
  const totalCount = events.length
  const count = Math.max(0, Math.min(visibleCount, totalCount))
  const visible = events.slice(0, count)
  const firstTs = events[0]?.ts ?? 0
  const lastTs = events.at(-1)?.ts ?? firstTs
  const cursorTs = visible.at(-1)?.ts ?? firstTs
  const planTasks = new Map<string, string>()
  const active = new Map<string, number>()
  const lastByStep = new Map<string, number>()
  const blocks: ScoreBlock[] = []
  const markers: ScoreMarker[] = []
  const gates = new Map<string, ApprovalGate>()
  const seenApprovalMarkers = new Set<string>()
  let planVersion = 0

  visible.forEach((event, zeroIndex) => {
    const index = zeroIndex + 1
    const payload = event.payload as Record<string, any>

    if ((event.type === 'plan.created' || event.type === 'plan.revised') && Array.isArray(payload.steps)) {
      planVersion += 1
      for (const step of payload.steps) planTasks.set(String(step.id), String(step.task ?? ''))
    }

    const marker = markerFor(event, index, planVersion)
    if (marker) {
      if (marker.kind !== 'approval') markers.push(marker)
      else {
        const approvalId = String(payload.id ?? '')
        if (!seenApprovalMarkers.has(approvalId)) {
          seenApprovalMarkers.add(approvalId)
          markers.push(marker)
        }
      }
    }

    if (event.type === 'node.started' && event.node_id) {
      const stepId = event.node_id
      const previous = active.get(stepId)
      if (previous != null) {
        blocks[previous].endTs = event.ts
        blocks[previous].endIndex = index
      }
      const block: ScoreBlock = {
        id: `${stepId}-${event.id}`,
        stepId,
        lane: laneFor(event.agent),
        task: String(payload.task ?? planTasks.get(stepId) ?? 'Agent work'),
        startTs: event.ts,
        endTs: event.ts,
        startIndex: index,
        endIndex: index,
        status: 'running',
        latencyMs: null,
        tools: [],
        retries: 0,
        fallback: false,
        track: 0,
      }
      blocks.push(block)
      active.set(stepId, blocks.length - 1)
      lastByStep.set(stepId, blocks.length - 1)
      return
    }

    if (event.type === 'tool.called' && event.node_id) {
      const blockIndex = active.get(event.node_id) ?? lastByStep.get(event.node_id)
      if (blockIndex != null) {
        const tool = String(payload.tool ?? '')
        if (tool && !blocks[blockIndex].tools.includes(tool)) blocks[blockIndex].tools.push(tool)
      }
      return
    }

    if ((event.type === 'tool.retry' || event.type === 'tool.fallback') && event.node_id) {
      const blockIndex = active.get(event.node_id) ?? lastByStep.get(event.node_id)
      if (blockIndex != null) {
        if (event.type === 'tool.retry') blocks[blockIndex].retries += 1
        else blocks[blockIndex].fallback = true
      }
      return
    }

    if (event.type === 'node.failed' && event.node_id) {
      const blockIndex = active.get(event.node_id) ?? lastByStep.get(event.node_id)
      if (blockIndex != null) blocks[blockIndex].status = 'failed'
      return
    }

    if (event.type === 'node.finished' && event.node_id) {
      const blockIndex = active.get(event.node_id)
      const fallbackIndex = lastByStep.get(event.node_id)
      const target = blockIndex ?? fallbackIndex
      if (target != null) {
        blocks[target].status = STATUS[String(payload.status ?? 'ok')] ?? 'done'
        blocks[target].latencyMs = event.latency_ms ?? blocks[target].latencyMs
        // A post-approval node.finished updates the verdict on the existing
        // block but must not imply the specialist worked throughout the human
        // wait. Only a block with a matching start owns this timestamp.
        if (blockIndex != null) {
          blocks[target].endTs = event.ts
          blocks[target].endIndex = index
          active.delete(event.node_id)
        }
      }
      return
    }

    if (event.type === 'approval.requested') {
      const id = String(payload.id ?? event.id)
      if (!gates.has(id)) {
        gates.set(id, {
          id,
          label: String(payload.description ?? 'Awaiting human decision'),
          startTs: event.ts,
          endTs: cursorTs,
          startIndex: index,
          endIndex: count,
          decision: null,
        })
      }
      return
    }

    if (event.type === 'approval.resolved') {
      const raw = String(payload.id ?? '')
      const ids = raw.includes(',') ? raw.split(',') : [raw]
      for (const id of ids) {
        const gate = gates.get(id)
        if (gate) gates.set(id, {
          ...gate,
          endTs: event.ts,
          endIndex: index,
          decision: String(payload.decision ?? 'resolved'),
        })
      }
    }
  })

  for (const blockIndex of active.values()) {
    blocks[blockIndex].endTs = Math.max(blocks[blockIndex].startTs, cursorTs)
    blocks[blockIndex].endIndex = count
  }
  for (const [id, gate] of gates) {
    if (!gate.decision) gates.set(id, { ...gate, endTs: Math.max(gate.startTs, cursorTs), endIndex: count })
  }

  const trackedBlocks = assignTracks(blocks)
  return {
    startTs: firstTs,
    endTs: lastTs,
    cursorTs,
    durationMs: Math.max(0, (lastTs - firstTs) * 1000),
    blocks: trackedBlocks,
    markers,
    gates: [...gates.values()],
    peakConcurrency: peakConcurrency(trackedBlocks),
    visibleCount: count,
    totalCount,
  }
}

/** Number of events that should have occurred at a chosen point on the axis. */
export function eventIndexForTime(events: AgentEvent[], ts: number): number {
  let low = 0
  let high = events.length
  while (low < high) {
    const mid = Math.floor((low + high) / 2)
    if (events[mid].ts <= ts) low = mid + 1
    else high = mid
  }
  return low
}

export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0ms'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`
  const roundedSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(roundedSeconds / 60)
  const seconds = roundedSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
