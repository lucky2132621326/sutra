import { describe, expect, it } from 'vitest'

import type { AgentEvent, EventType } from '../../types/events'
import { buildRunScoreModel, eventIndexForTime, formatElapsed } from './runScoreModel'

let seq = 0
function event(
  type: EventType,
  ts: number,
  options: {
    node?: string
    agent?: string
    payload?: Record<string, unknown>
    latency?: number | null
  } = {},
): AgentEvent {
  return {
    id: `e${++seq}`,
    run_id: 'run-score-test',
    ts,
    type,
    node_id: options.node ?? null,
    agent: options.agent ?? null,
    payload: options.payload ?? {},
    latency_ms: options.latency ?? null,
    parent_id: null,
  }
}

function plan(ts = 0): AgentEvent {
  return event('plan.created', ts, {
    agent: 'planner',
    payload: {
      goal: 'test', reasoning: 'parallel where independent',
      steps: [
        { id: 's1', agent: 'academic', task: 'Check attendance', depends_on: [], expected_output: '', requires_approval: false },
        { id: 's2', agent: 'events', task: 'Find event', depends_on: [], expected_output: '', requires_approval: false },
      ],
    },
  })
}

describe('run score model', () => {
  it('renders overlapping specialist work as true concurrency', () => {
    const events = [
      plan(),
      event('node.started', 1, { node: 's1', agent: 'academic', payload: { task: 'Check attendance' } }),
      event('node.started', 1.2, { node: 's2', agent: 'events', payload: { task: 'Find event' } }),
      event('node.finished', 2.4, { node: 's1', agent: 'academic', payload: { status: 'ok' }, latency: 1400 }),
      event('node.finished', 2.8, { node: 's2', agent: 'events', payload: { status: 'ok' }, latency: 1600 }),
    ]
    const model = buildRunScoreModel(events)
    expect(model.blocks).toHaveLength(2)
    expect(model.peakConcurrency).toBe(2)
    expect(model.blocks.map((block) => block.lane)).toEqual(['academic', 'events'])
  })

  it('preserves sequential work without inventing overlap', () => {
    const events = [
      plan(),
      event('node.started', 1, { node: 's1', agent: 'academic' }),
      event('node.finished', 2, { node: 's1', agent: 'academic', payload: { status: 'ok' } }),
      event('node.started', 2, { node: 's2', agent: 'events' }),
      event('node.finished', 3, { node: 's2', agent: 'events', payload: { status: 'ok' } }),
    ]
    const model = buildRunScoreModel(events)
    expect(model.peakConcurrency).toBe(1)
    expect(model.blocks[1].startTs).toBeGreaterThanOrEqual(model.blocks[0].endTs)
  })

  it('surfaces conflict and replan as orchestration markers', () => {
    const events = [
      plan(),
      event('conflict.detected', 1.5, { agent: 'conflict_arbiter', payload: { conflicts: [], rationale: 'schedule clash' } }),
      event('plan.revised', 2, { agent: 'planner', payload: { goal: 'test', reasoning: 'use Saturday', steps: [] } }),
    ]
    const model = buildRunScoreModel(events)
    expect(model.markers.map((marker) => marker.kind)).toEqual(['plan', 'conflict', 'replan'])
    expect(model.markers.find((marker) => marker.kind === 'conflict')?.label).toBe('Academic veto')
  })

  it('turns duplicate approval requests into one bounded human gate', () => {
    const events = [
      plan(),
      event('approval.requested', 2, { agent: 'approval_gate', payload: { id: 'a1', description: 'Register' } }),
      event('approval.requested', 2.2, { agent: 'approval_gate', payload: { id: 'a1', description: 'Register' } }),
      event('approval.resolved', 5, { agent: 'approval_gate', payload: { id: 'a1', decision: 'approve' } }),
    ]
    const model = buildRunScoreModel(events)
    expect(model.markers.filter((marker) => marker.kind === 'approval')).toHaveLength(1)
    expect(model.gates).toEqual([expect.objectContaining({ startTs: 2, endTs: 5, decision: 'approve' })])
  })

  it('keeps the specialist block short when approval later updates its verdict', () => {
    const events = [
      plan(),
      event('node.started', 1, { node: 's2', agent: 'events', payload: { task: 'Register' } }),
      event('node.finished', 2, { node: 's2', agent: 'events', payload: { status: 'pending_approval' }, latency: 1000 }),
      event('approval.requested', 2.1, { agent: 'approval_gate', payload: { id: 'a1' } }),
      event('approval.resolved', 8, { agent: 'approval_gate', payload: { id: 'a1', decision: 'approve' } }),
      event('node.finished', 8.1, { node: 's2', agent: 'events', payload: { status: 'ok' } }),
    ]
    const [block] = buildRunScoreModel(events).blocks
    expect(block.status).toBe('done')
    expect(block.endTs).toBe(2)
  })

  it('represents failure, degradation, retry and fallback honestly', () => {
    const events = [
      plan(),
      event('node.started', 1, { node: 's1', agent: 'academic' }),
      event('tool.called', 1.1, { node: 's1', agent: 'academic', payload: { tool: 'get_attendance' } }),
      event('tool.retry', 1.2, { node: 's1', agent: 'academic', payload: { tool: 'get_attendance', attempt: 1 } }),
      event('tool.fallback', 1.3, { node: 's1', agent: 'academic', payload: { tool: 'get_attendance' } }),
      event('node.finished', 2, { node: 's1', agent: 'academic', payload: { status: 'degraded' } }),
      event('node.started', 2.2, { node: 's2', agent: 'events' }),
      event('node.failed', 2.5, { node: 's2', agent: 'events', payload: { error: 'down' } }),
      event('node.finished', 2.6, { node: 's2', agent: 'events', payload: { status: 'error' } }),
    ]
    const model = buildRunScoreModel(events)
    expect(model.blocks[0]).toEqual(expect.objectContaining({ status: 'degraded', retries: 1, fallback: true }))
    expect(model.blocks[1].status).toBe('failed')
    expect(model.markers.some((marker) => marker.kind === 'fallback')).toBe(true)
  })

  it('supports event-accurate seeking and hides future work', () => {
    const events = [
      plan(),
      event('node.started', 1, { node: 's1', agent: 'academic' }),
      event('node.finished', 2, { node: 's1', agent: 'academic', payload: { status: 'ok' } }),
      event('run.finished', 3, { agent: 'synthesizer' }),
    ]
    expect(eventIndexForTime(events, 1.5)).toBe(2)
    expect(buildRunScoreModel(events, 2).markers.some((marker) => marker.kind === 'finish')).toBe(false)
    expect(buildRunScoreModel(events, 2).blocks[0].status).toBe('running')
  })

  it('has a stable empty state and readable adaptive duration labels', () => {
    const model = buildRunScoreModel([])
    expect(model.blocks).toEqual([])
    expect(model.totalCount).toBe(0)
    expect(formatElapsed(9.8)).toBe('10ms')
    expect(formatElapsed(259)).toBe('259ms')
    expect(formatElapsed(137_000)).toBe('2:17')
    expect(formatElapsed(119_600)).toBe('2:00')
  })
})
