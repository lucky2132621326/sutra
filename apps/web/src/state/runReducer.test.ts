/**
 * Folds the REAL recorded fixtures through the reducer and asserts the
 * resulting state matches the known demo scenario. Headless, no browser, no
 * backend — this is where reducer correctness is actually settled, before any
 * UI depends on it.
 *
 * Run: npm test
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import type { AgentEvent } from '../types/events'
import { initialRunState, reduce, reduceAll } from './runReducer'

const FIXTURE_DIR = join(__dirname, '../../public/fixtures')

function load(name: string): AgentEvent[] {
  return readFileSync(join(FIXTURE_DIR, name), 'utf8')
    .split('\n').filter((l) => l.trim())
    .map((l) => JSON.parse(l) as AgentEvent)
}

const ALL = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.jsonl'))

describe('every fixture folds without throwing', () => {
  it.each(ALL)('%s', (name) => {
    const events = load(name)
    expect(events.length).toBeGreaterThan(0)
    const state = reduceAll(events)
    expect(state.runId).toBeTruthy()
    expect(state.plan).not.toBeNull()
    expect(state.answer).toBeTruthy()
    expect(state.fatalError).toBeNull()
  })
})

// golden_clean is a READ-ONLY run. It used to be the registration path, but the
// Thursday clash is now detected against the real timetable, so any goal that
// registers for the workshop genuinely conflicts — "clean" has to mean "asked
// for nothing that writes". The approval and conflict assertions therefore live
// on golden_conflict, which is the run that actually has them.
describe('golden_clean — a read-only run', () => {
  const state = reduceAll(load('golden_clean.jsonl'))

  it('builds the plan and completes every step', () => {
    expect(state.plan!.steps.length).toBeGreaterThanOrEqual(2)
    expect(state.status).toBe('finished')
    expect(state.runComplete).toBe(true)
    const statuses = Object.values(state.steps).map((s) => s.status)
    expect(statuses).not.toContain('pending')
  })

  it('records genuinely parallel execution', () => {
    expect(state.telemetry.peakConcurrency).toBeGreaterThanOrEqual(2)
  })

  it('asks for no approvals, because nothing writes', () => {
    expect(Object.keys(state.approvals)).toHaveLength(0)
    expect(state.approvalQueue).toHaveLength(0)
  })

  it('does not treat conflict.resolved as a conflict', () => {
    expect(state.conflicts).toHaveLength(0)
    expect(state.arbiterPasses.some((p) => p.verdict === 'clear')).toBe(true)
  })

  it('carries retrieved clauses the answer can cite', () => {
    expect(state.citations.length).toBeGreaterThan(0)
    expect(state.citations[0].clause).toBeTruthy()
    expect(state.citations[0].doc_title).toBeTruthy()
  })

  it('every [doc:N] marker in the answer resolves to a real citation', () => {
    const markers = [...state.answer!.matchAll(/\[doc:(\d+)\]/g)].map((m) => Number(m[1]))
    expect(markers.length).toBeGreaterThan(0)
    for (const n of markers) expect(state.citations[n]).toBeDefined()
  })
})

describe('golden_conflict — the Academic Agent veto', () => {
  const events = load('golden_conflict.jsonl')
  const state = reduceAll(events)

  it('detects conflicts and captures the arbiter rationale', () => {
    expect(state.conflicts.length).toBeGreaterThan(0)
    expect(state.conflicts[0].type).toBe('SCHEDULE_COLLISION')
    // The rationale is now derived from the attendance projection rather than
    // generated, so assert on the numbers it must contain — a stronger check
    // than matching prose, and one that fails loudly if the arbiter silently
    // reverts to an LLM opinion.
    expect(state.conflicts[0].rationale).toMatch(/70\.27/)
    expect(state.conflicts[0].rationale).toMatch(/68\.42/)
    expect(state.conflicts[0].rationale).toMatch(/4\.2/)
  })

  it('carries the evidence the rationale is built from', () => {
    const evidence = (state.conflicts[0] as any).evidence
    expect(evidence?.collides_with?.course_id).toBe('CS301L')
    expect(evidence?.attendance_impact?.current_pct).toBeCloseTo(70.27, 1)
  })

  it('records EVERY committed write, gated or not', () => {
    // Was 1: the ledger only carried approval-gated actions, so "Actions taken"
    // reported the registration and stayed silent about the calendar entry and
    // reminder that followed — writes the student would find in their account
    // with no record of who made them.
    const executed = state.actions.filter((a) => a.outcome === 'executed')
    expect(executed.length).toBeGreaterThanOrEqual(3)
    expect(executed.every((a) => a.receiptId)).toBe(true)
    expect(executed.map((a) => a.tool)).toEqual(
      expect.arrayContaining(['register_event', 'add_to_calendar', 'create_reminder']),
    )
  })

  it('every receipt on the wire appears in the ledger, and vice versa', () => {
    const onWire = new Set(
      events
        .filter((e) => e.type === 'tool.result')
        .map((e) => (e.payload as any))
        .filter((p) => p.status === 'ok' && p.data?.receipt_id)
        .map((p) => p.data.receipt_id as string),
    )
    const inLedger = new Set(state.actions.map((a) => a.receiptId).filter(Boolean) as string[])
    expect(inLedger).toEqual(onWire)
  })

  it('the gated registration is the one that went through the human', () => {
    const gated = state.actions.find((a) => a.tool === 'register_event')
    expect(gated?.approvalId).toBeTruthy()
    expect(gated?.args.event_id).toBe('evt_workshop_sat')
  })

  it('dedupes repeated approval.requested by payload id', () => {
    const raw = load('golden_conflict.jsonl').filter((e) => e.type === 'approval.requested')
    const uniqueIds = new Set(raw.map((e) => (e.payload as any).id))
    // The backend re-emits pending approvals on every resume.
    expect(raw.length).toBeGreaterThan(uniqueIds.size)
    expect(Object.keys(state.approvals).length).toBe(uniqueIds.size)
  })

  it('resolves every approval it opened', () => {
    expect(state.approvalQueue).toHaveLength(0)
    for (const a of Object.values(state.approvals)) expect(a.status).not.toBe('pending')
  })

  it('only ever asks the human to approve the SAFE alternative', () => {
    for (const a of Object.values(state.approvals)) {
      expect((a.args as any)?.event_id).not.toBe('evt_workshop_thu')
    }
  })

  it('links the conflict back to a real step in the DAG', () => {
    const withSteps = state.conflicts.filter((c) => c.stepIds.length > 0)
    expect(withSteps.length).toBeGreaterThan(0)
    for (const c of withSteps) for (const id of c.stepIds) expect(state.steps[id]).toBeDefined()
  })

  it('revises the plan and RE-EXECUTES it', () => {
    expect(state.planVersion).toBeGreaterThan(1)
    expect(state.planHistory.length).toBeGreaterThan(1)
    // Guards the merge-reducer bug where a replan produced a new plan but
    // dispatched nothing, leaving the DAG frozen at pending.
    const firstRevision = events.findIndex((e) => e.type === 'plan.revised')
    const startsAfter = events.slice(firstRevision).filter((e) => e.type === 'node.started')
    expect(startsAfter.length).toBeGreaterThan(0)
  })

  it('ends on the revised plan, not the original', () => {
    const last = state.planHistory.at(-1)!
    expect(last.plan.goal).toMatch(/saturday/i)
    expect(Object.keys(state.steps).sort()).toEqual(last.plan.steps.map((s) => s.id).sort())
  })
})

describe('golden_chaos — failure recovery', () => {
  const state = reduceAll(load('golden_chaos.jsonl'))

  it('records retries and a fallback', () => {
    expect(state.telemetry.retries).toBeGreaterThanOrEqual(2)
    expect(state.telemetry.fallbacks).toBeGreaterThanOrEqual(1)
  })

  it('attributes the retry to the node that owns the tool', () => {
    // resilience.py's module-global buffer can emit these against the wrong
    // node; the reducer re-routes by tool owner.
    const withRetries = Object.values(state.steps).filter((s) => s.tools.some((t) => t.retries.length))
    expect(withRetries.length).toBeGreaterThan(0)
    for (const step of withRetries) {
      const tool = step.tools.find((t) => t.retries.length)!
      expect(state.toolOwner[tool.tool]).toBe(step.id)
    }
  })

  it('still produces an answer despite the injected failure', () => {
    expect(state.answer).toBeTruthy()
    expect(state.status).toBe('finished')
  })
})

describe('golden_reject — human veto', () => {
  const state = reduceAll(load('golden_reject.jsonl'))

  it('marks approvals rejected', () => {
    const decisions = Object.values(state.approvals).map((a) => a.status)
    expect(decisions).toContain('reject')
  })

  it('still completes rather than crashing', () => {
    expect(state.status).toBe('finished')
    expect(state.answer).toBeTruthy()
  })

  it('captures the structured ledger, not just the rendered text', () => {
    expect(state.actions.length).toBeGreaterThan(0)
    expect(state.actions.some((a) => a.outcome === 'not_executed')).toBe(true)
    expect(state.actions.some((a) => a.outcome === 'cancelled')).toBe(true)
    expect(state.actions.every((a) => a.receiptId === null)).toBe(true)
  })

  it('keeps the rendered blocks OUT of the answer prose', () => {
    expect(state.answer).not.toContain('Actions taken:')
    expect(state.answer).not.toContain('Not completed:')
    expect(state.answer).not.toContain('NOT DONE')
  })
})

describe('the answer prose is split from its appended blocks', () => {
  // synthesize_node appends "Not completed:" and then "Actions taken:".
  // Splitting on the first marker alone made notCompleted swallow the ledger.
  const base = load('golden_reject.jsonl')
  const finished = base.find((e) => e.type === 'run.finished')!

  function withAnswer(answer: string) {
    const events = base.map((e) =>
      e === finished ? { ...e, payload: { ...(e.payload as object), answer } } : e)
    return reduceAll(events)
  }

  it('handles both blocks present without mixing them', () => {
    const s = withAnswer(
      'You are eligible.' +
      '\n\nNot completed:\n- placement returned degraded data.' +
      '\n\nActions taken:\n- NOT DONE — Register …: you declined this.')
    expect(s.answer).toBe('You are eligible.')
    expect(s.notCompleted).toEqual(['placement returned degraded data.'])
    expect(s.notCompleted.join(' ')).not.toContain('NOT DONE')
  })

  it('handles only the actions block', () => {
    const s = withAnswer('You are eligible.\n\nActions taken:\n- DONE — Register … (receipt abc).')
    expect(s.answer).toBe('You are eligible.')
    expect(s.notCompleted).toEqual([])
  })

  it('handles neither block', () => {
    const s = withAnswer('You are eligible.')
    expect(s.answer).toBe('You are eligible.')
    expect(s.notCompleted).toEqual([])
  })
})

describe('approval gating is visible in state', () => {
  // golden_conflict is the run with writes in it; golden_clean has none.
  const events = load('golden_conflict.jsonl')
  const state = reduceAll(events)

  it('no dependent write is recorded before an approval resolves', () => {
    const firstResolved = events.findIndex((e) => e.type === 'approval.resolved')
    const calendar = events.findIndex(
      (e) => e.type === 'tool.called' && (e.payload as any).tool === 'add_to_calendar')
    expect(firstResolved).toBeGreaterThanOrEqual(0)
    if (calendar >= 0) expect(calendar).toBeGreaterThan(firstResolved)
  })

  it('captures the structured tool result for evidence cards', () => {
    const withData = Object.values(state.steps)
      .flatMap((s) => s.tools)
      .filter((t) => t.data && Object.keys(t.data).length)
    expect(withData.length).toBeGreaterThan(0)
    const eligibility = withData.find((t) => t.tool === 'check_placement_eligibility')
    expect(eligibility?.data).toHaveProperty('is_eligible')
    expect(eligibility?.data).toHaveProperty('breakdown')
  })

  it('an approved write ends up carrying a receipt', () => {
    const approved = Object.values(state.steps)
      .flatMap((s) => s.tools)
      .filter((t) => t.approvalId && t.status === 'ok')
    expect(approved.length).toBeGreaterThan(0)
    expect(approved.some((t) => (t.data as any)?.receipt_id)).toBe(true)
  })
})

describe('golden_reject — downstream writes are cancelled', () => {
  const events = load('golden_reject.jsonl')
  const state = reduceAll(events)

  it('never calls the dependent write', () => {
    const calendar = events.filter(
      (e) => e.type === 'tool.called' && (e.payload as any).tool === 'add_to_calendar')
    expect(calendar).toHaveLength(0)
  })

  it('reports the rejection outcome explicitly', () => {
    const resolved = events.filter((e) => e.type === 'approval.resolved')
    expect(resolved.length).toBeGreaterThan(0)
    for (const e of resolved) {
      expect((e.payload as any).decision).toBe('reject')
      expect((e.payload as any).outcome).toBe('not_executed')
      expect((e.payload as any).step_id).toBeTruthy()
    }
  })

  it('still reaches an answer', () => {
    expect(state.status).toBe('finished')
    expect(state.answer).toBeTruthy()
  })
})

describe('robustness guards', () => {
  it('ignores a duplicate event id (bus history replay)', () => {
    const events = load('golden_clean.jsonl')
    const once = reduceAll(events)
    const twice = reduceAll([...events, ...events])
    expect(twice.timeline.length).toBe(once.timeline.length)
    expect(twice.telemetry.toolCalls).toBe(once.telemetry.toolCalls)
  })

  it('ignores events from a different run', () => {
    const events = load('golden_clean.jsonl')
    const foreign = { ...events[0], id: 'zzz', run_id: 'someone-else' }
    expect(reduceAll([...events, foreign]).timeline.length).toBe(reduceAll(events).timeline.length)
  })

  it('scrubbing to N equals folding the first N events', () => {
    const events = load('golden_conflict.jsonl')
    const half = Math.floor(events.length / 2)
    expect(reduceAll(events.slice(0, half))).toEqual(reduceAll(events.slice(0, half)))
  })

  it('treats a benign run.error as a notice, not a fatal', () => {
    const benign = {
      id: 'n1', run_id: 'r1', ts: 1, type: 'run.error', node_id: null, agent: 'critic',
      payload: { error: 'boom', detail: 'critic LLM call failed; treating plan as satisfied' },
      latency_ms: null, parent_id: null,
    } as AgentEvent
    const state = reduce(initialRunState(), benign)
    expect(state.fatalError).toBeNull()
    expect(state.notices).toHaveLength(1)
    expect(state.status).not.toBe('error')
  })

  it('treats main.py run.error (no agent, no detail) as fatal', () => {
    const fatal = {
      id: 'f1', run_id: 'r1', ts: 1, type: 'run.error', node_id: null, agent: null,
      payload: { error: 'graph blew up' }, latency_ms: null, parent_id: null,
    } as AgentEvent
    const state = reduce(initialRunState(), fatal)
    expect(state.fatalError).toBe('graph blew up')
    expect(state.status).toBe('error')
  })

  it('survives a conflicts[] containing raw strings', () => {
    const weird = {
      id: 'c1', run_id: 'r1', ts: 1, type: 'conflict.detected', node_id: null, agent: 'conflict_arbiter',
      payload: { conflicts: ['just a bare string', { type: 'X' }], rationale: 'r' },
      latency_ms: null, parent_id: null,
    } as AgentEvent
    const state = reduce(initialRunState(), weird)
    expect(state.conflicts).toHaveLength(2)
    expect(state.conflicts[0].detail).toBe('just a bare string')
  })

  it('does not end the run on run.finished — memory.write still follows', () => {
    const events = load('golden_clean.jsonl')
    const idx = events.findIndex((e) => e.type === 'run.finished')
    expect(events.slice(idx + 1).some((e) => e.type === 'memory.write')).toBe(true)
    const state = reduceAll(events)
    expect(state.runComplete).toBe(true)
    expect(state.streamClosed).toBe(false)
    expect(state.memory.written.length).toBeGreaterThan(0)
  })
})
