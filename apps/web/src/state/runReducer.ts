/**
 * Folds a stream of AgentEvents into RunState.
 *
 * PURE by design — no async, no Date.now(), no side effects. That purity is
 * what makes scrubbing free: jumping to event N is just a re-fold of
 * events.slice(0, N), and it lets the whole thing be unit-tested headless
 * against the recorded fixtures with no browser and no backend.
 *
 * Every non-obvious branch below exists because of something the backend
 * actually does — see the comment on each.
 */
import type {
  AgentEvent, Citation, PendingAction, Plan, ProfileFact, RecalledMemory, Step,
} from '../types/events'

export type StepStatus =
  | 'pending' | 'running' | 'done' | 'degraded'
  | 'failed' | 'awaiting-approval' | 'rejected' | 'denied' | 'cancelled'

export interface ToolCall {
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'ok' | 'error' | 'degraded' | 'pending_approval' | 'permission_denied'
  error?: string
  degradationReason?: string
  requiredRole?: string
  heldRole?: string
  /** The structured tool result. Absent until the tool actually ran — a
   *  pending_approval result carries no data and no receipt. */
  data?: Record<string, unknown>
  approvalId?: string
  retries: { attempt: number; error: string }[]
  fallbackTo?: string
  fallbackReason?: string
}

export interface StepState {
  id: string
  agent: string
  task: string
  dependsOn: string[]
  requiresApproval: boolean
  expectedOutput: string
  status: StepStatus
  thinking: boolean
  startedTs?: number
  finishedTs?: number
  latencyMs?: number
  tools: ToolCall[]
  errors: string[]
  conflicted: boolean
  awaitingApprovalId?: string
  ragChunks?: number
}

export interface ApprovalState extends PendingAction {
  status: 'pending' | 'approve' | 'reject' | 'edit'
  requestedTs: number
  resolvedTs?: number
  /** How many times the backend re-emitted this request (see dedupe note). */
  requestCount: number
}

export interface ConflictRecord {
  type: string
  detail: string
  stepIds: string[]
  rationale: string
  ts: number
  pass: number
  /** Which plan this was raised against. Step ids are REUSED across revisions
   *  for different tasks, so a conflict about v1's "s3" must not highlight
   *  v3's "s3" — which is a completely different action. */
  planVersion: number
  /** The deterministic preflight's working: the event, the class it collides
   *  with, and the attendance projection. Present when the conflict came from
   *  the real timetable check rather than an LLM judgement — which is what
   *  lets the UI show a verifiable evidence card instead of a claim. */
  evidence?: ConflictEvidence
}

export interface ConflictEvidence {
  event?: { id: string; title: string; day: string; start: string; end: string; seats_remaining: number }
  collides_with?: { course_id: string; session_type: string; detail: string }
  attendance_impact?: {
    course_id: string
    course_name: string
    current_pct: number
    projected_pct: number
    delta_pct: number
    classes_attended: number
    classes_held: number
    crosses_threshold: boolean
    already_below: boolean
    sessions_needed_to_recover: number
  }
}

export interface Notice {
  agent: string
  error: string
  detail: string
  ts: number
}

export type ActionOutcome = 'executed' | 'not_executed' | 'cancelled' | 'failed' | 'skipped'

export interface ActionRecord {
  approvalId: string | null
  stepId: string | null
  agent: string
  tool: string | null
  args: Record<string, unknown>
  description: string
  decision: string | null
  outcome: ActionOutcome
  /** Only ever present on `executed`. Proof the write happened. */
  receiptId: string | null
  error: string | null
}

export interface RunState {
  runId: string | null
  status: 'idle' | 'running' | 'awaiting-approval' | 'finished' | 'error'
  t0: number | null
  lastTs: number | null

  planVersion: number
  plan: Plan | null
  planHistory: { version: number; plan: Plan; ts: number }[]

  steps: Record<string, StepState>
  stepOrder: string[]

  timeline: AgentEvent[]
  approvals: Record<string, ApprovalState>
  approvalQueue: string[]
  resolvedApprovalIds: string[]

  conflicts: ConflictRecord[]
  arbiterPasses: { pass: number; verdict: 'clear' | 'detected'; count: number }[]
  criticVerdict: { satisfied: boolean; feedback: string } | null

  memory: {
    facts: ProfileFact[]
    recalled: RecalledMemory[]
    written: { key: string; value: string; confidence: number; stored: boolean }[]
    summary: string
  }
  citations: Citation[]

  answer: string | null
  notCompleted: string[]
  /** The backend's authoritative record of every gated action it resolved.
   *  Render "what did it actually do" from THIS, never from the answer prose —
   *  the prose is model-written, this is not. */
  actions: ActionRecord[]
  notices: Notice[]
  fatalError: string | null

  telemetry: {
    toolCalls: number
    retries: number
    fallbacks: number
    degraded: number
    peakConcurrency: number
    agentsUsed: string[]
  }

  runComplete: boolean
  streamClosed: boolean
  seenEventIds: string[]
  /** tool name -> node that issued it; corrects mis-attributed retry events. */
  toolOwner: Record<string, string>
}

export function initialRunState(): RunState {
  return {
    runId: null, status: 'idle', t0: null, lastTs: null,
    planVersion: 0, plan: null, planHistory: [],
    steps: {}, stepOrder: [],
    timeline: [], approvals: {}, approvalQueue: [], resolvedApprovalIds: [],
    conflicts: [], arbiterPasses: [], criticVerdict: null,
    memory: { facts: [], recalled: [], written: [], summary: '' },
    citations: [],
    answer: null, notCompleted: [], actions: [], notices: [], fatalError: null,
    telemetry: { toolCalls: 0, retries: 0, fallbacks: 0, degraded: 0, peakConcurrency: 0, agentsUsed: [] },
    runComplete: false, streamClosed: false, seenEventIds: [], toolOwner: {},
  }
}

const STATUS_MAP: Record<string, StepStatus> = {
  ok: 'done',
  error: 'failed',
  degraded: 'degraded',
  rejected: 'rejected',
  permission_denied: 'denied',
  // Emitted when a step is dropped because something it depends on was
  // rejected or not permitted — the write never happened.
  cancelled: 'cancelled',
  pending_approval: 'awaiting-approval',
}

function makeStep(s: Step): StepState {
  return {
    id: s.id, agent: s.agent, task: s.task,
    dependsOn: s.depends_on ?? [], requiresApproval: !!s.requires_approval,
    expectedOutput: s.expected_output ?? '',
    status: 'pending', thinking: false, tools: [], errors: [], conflicted: false,
  }
}

/** conflicts[] is unvalidated LLM output — entries may be plain strings. */
function normalizeConflict(
  raw: unknown, knownStepIds: string[],
): Omit<ConflictRecord, 'rationale' | 'ts' | 'pass' | 'planVersion'> {
  if (typeof raw === 'string') return { type: 'conflict', detail: raw, stepIds: [] }
  const c = (raw ?? {}) as Record<string, unknown>
  const detail = String(c.detail ?? c.type ?? JSON.stringify(c))
  // LLM-authored conflicts carry no step_id; recover ids mentioned in the prose
  // so the DAG can still highlight what the arbiter was talking about.
  const mentioned = (detail.match(/\bs\d+\b/g) ?? []) as string[]
  const ids = [String(c.step_id ?? ''), ...mentioned].filter((id) => id && knownStepIds.includes(id))
  const evidence = c.evidence as ConflictEvidence | undefined
  return {
    type: String(c.type ?? 'conflict'),
    detail,
    stepIds: [...new Set(ids)],
    ...(evidence && Object.keys(evidence).length ? { evidence } : {}),
  }
}

function concurrency(steps: Record<string, StepState>): number {
  return Object.values(steps).filter((s) => s.status === 'running').length
}

export function reduce(state: RunState, e: AgentEvent): RunState {
  // Guard 1 — absorbs the bus replaying full history to a reconnecting
  // subscriber (apps/api/bus.py subscribe()). Without it a reconnect would
  // double-count everything.
  if (state.seenEventIds.includes(e.id)) return state
  // Guard 2 — never mix runs.
  if (state.runId && e.run_id !== state.runId) return state

  const s: RunState = {
    ...state,
    runId: state.runId ?? e.run_id,
    t0: state.t0 ?? e.ts,
    lastTs: e.ts,
    seenEventIds: [...state.seenEventIds, e.id],
    timeline: [...state.timeline, e],
  }
  const p = e.payload as Record<string, any>

  switch (e.type) {
    case 'plan.created':
    case 'plan.revised': {
      // Two incompatible payloads share this type: the planner sends a full
      // Plan, the critic sends {satisfied, feedback}. Branch on shape rather
      // than agent alone — more robust if agent labelling ever changes.
      const isFullPlan = Array.isArray(p.steps)
      if (!isFullPlan) {
        s.criticVerdict = { satisfied: !!p.satisfied, feedback: String(p.feedback ?? '') }
        return s
      }
      const plan: Plan = { goal: p.goal ?? '', reasoning: p.reasoning ?? '', steps: p.steps ?? [] }
      s.planVersion = state.planVersion + 1
      s.plan = plan
      s.planHistory = [...state.planHistory, { version: s.planVersion, plan, ts: e.ts }]

      // Preserve state for steps whose definition is unchanged, so a revision
      // doesn't visually reset work that already completed.
      const next: Record<string, StepState> = {}
      for (const step of plan.steps) {
        const prev = state.steps[step.id]
        const same =
          prev && prev.task === step.task && prev.agent === step.agent &&
          JSON.stringify(prev.dependsOn) === JSON.stringify(step.depends_on ?? [])
        // Conflict flags never carry over: they were raised against the plan
        // being replaced, and this step id may now mean something else.
        next[step.id] = same
          ? { ...prev, requiresApproval: !!step.requires_approval, conflicted: false }
          : makeStep(step)
      }
      s.steps = next
      s.stepOrder = plan.steps.map((x) => x.id)
      s.status = 'running'
      return s
    }

    case 'node.started': {
      const id = e.node_id
      if (!id) return s
      const prev = s.steps[id] ?? makeStep({
        id, agent: e.agent ?? 'unknown', task: String(p.task ?? ''),
        depends_on: [], expected_output: '', requires_approval: false,
      })
      s.steps = {
        ...s.steps,
        [id]: { ...prev, status: 'running', startedTs: e.ts, thinking: false, task: String(p.task ?? prev.task) },
      }
      if (!s.stepOrder.includes(id)) s.stepOrder = [...s.stepOrder, id]
      s.telemetry = {
        ...s.telemetry,
        peakConcurrency: Math.max(s.telemetry.peakConcurrency, concurrency(s.steps)),
        agentsUsed: e.agent && !s.telemetry.agentsUsed.includes(e.agent)
          ? [...s.telemetry.agentsUsed, e.agent] : s.telemetry.agentsUsed,
      }
      return s
    }

    case 'agent.thinking': {
      // The critic reuses this type for its own skip notices.
      if (e.agent === 'critic') return s
      const id = e.node_id
      if (!id || !s.steps[id]) return s
      s.steps = { ...s.steps, [id]: { ...s.steps[id], thinking: true } }
      return s
    }

    case 'node.finished': {
      const id = e.node_id
      if (!id || !s.steps[id]) return s
      const status = STATUS_MAP[String(p.status)] ?? 'done'
      s.steps = {
        ...s.steps,
        [id]: { ...s.steps[id], status, thinking: false, finishedTs: e.ts, latencyMs: e.latency_ms ?? undefined },
      }
      if (status === 'degraded') s.telemetry = { ...s.telemetry, degraded: s.telemetry.degraded + 1 }
      return s
    }

    case 'node.failed': {
      // Always followed by node.finished(status=error) — record the message
      // here but let node.finished own the status so it isn't double-counted.
      const id = e.node_id
      if (!id || !s.steps[id]) return s
      s.steps = { ...s.steps, [id]: { ...s.steps[id], errors: [...s.steps[id].errors, String(p.error ?? '')] } }
      return s
    }

    case 'tool.called': {
      const id = e.node_id
      if (!id || !s.steps[id]) return s
      const call: ToolCall = { tool: String(p.tool), args: p.args ?? {}, status: 'running', retries: [] }
      s.steps = { ...s.steps, [id]: { ...s.steps[id], tools: [...s.steps[id].tools, call] } }
      s.toolOwner = { ...s.toolOwner, [String(p.tool)]: id }
      s.telemetry = { ...s.telemetry, toolCalls: s.telemetry.toolCalls + 1 }
      return s
    }

    case 'tool.result': {
      const id = e.node_id
      if (!id || !s.steps[id]) return s
      const tools = [...s.steps[id].tools]
      // Prefer the call still awaiting a result. Falling back to the last call
      // of that name matters for the POST-APPROVAL tool.result: by then the
      // original call has already resolved to `pending_approval`, so there is
      // no 'running' entry left to match — but that later event is the one
      // carrying the real data and receipt.
      const running = tools.findLastIndex(
        (t) => t.tool === String(p.tool) && t.status === 'running')
      const target = running >= 0 ? running : tools.map((t) => t.tool).lastIndexOf(String(p.tool))
      if (target >= 0) {
        tools[target] = {
          ...tools[target],
          status: (p.status as ToolCall['status']) ?? 'ok',
          error: p.error ? String(p.error) : undefined,
          degradationReason: p.degradation_reason ? String(p.degradation_reason) : undefined,
          requiredRole: p.required_role ? String(p.required_role) : undefined,
          heldRole: p.held_role ? String(p.held_role) : undefined,
          data: (p.data as Record<string, unknown>) ?? tools[target].data,
          approvalId: p.approval_id ? String(p.approval_id) : tools[target].approvalId,
        }
      }
      s.steps = { ...s.steps, [id]: { ...s.steps[id], tools } }
      return s
    }

    case 'tool.retry':
    case 'tool.fallback': {
      // resilience.py buffers these in a MODULE-GLOBAL list drained per node,
      // so with parallel steps they can be emitted against the wrong node_id.
      // Route by which node actually issued the tool.
      const owner = s.toolOwner[String(p.tool)] ?? e.node_id
      if (!owner || !s.steps[owner]) return s
      const tools = [...s.steps[owner].tools]
      const idx = tools.map((t) => t.tool).lastIndexOf(String(p.tool))
      if (idx >= 0) {
        if (e.type === 'tool.retry') {
          tools[idx] = {
            ...tools[idx],
            retries: [...tools[idx].retries, { attempt: Number(p.attempt ?? 0), error: String(p.error ?? '') }],
          }
        } else {
          tools[idx] = { ...tools[idx], fallbackTo: String(p.to ?? ''), fallbackReason: String(p.reason ?? '') }
        }
      }
      s.steps = { ...s.steps, [owner]: { ...s.steps[owner], tools } }
      s.telemetry = e.type === 'tool.retry'
        ? { ...s.telemetry, retries: s.telemetry.retries + 1 }
        : { ...s.telemetry, fallbacks: s.telemetry.fallbacks + 1 }
      return s
    }

    case 'rag.retrieved': {
      const id = e.node_id
      const cites = (p.citations ?? []) as Citation[]
      if (id && s.steps[id]) {
        s.steps = { ...s.steps, [id]: { ...s.steps[id], ragChunks: Number(p.chunks ?? 0) } }
      }
      if (cites.length) {
        const seen = new Set(s.citations.map((c) => `${c.doc_number}#${c.clause}`))
        s.citations = [...s.citations, ...cites.filter((c) => !seen.has(`${c.doc_number}#${c.clause}`))]
      }
      return s
    }

    case 'conflict.detected': {
      const known = Object.keys(s.steps)
      const rationale = String(p.rationale ?? '')
      const pass = s.arbiterPasses.length + 1
      const list = (p.conflicts ?? []) as unknown[]
      const records = list.map((c) => ({
        ...normalizeConflict(c, known), rationale, ts: e.ts, pass, planVersion: s.planVersion,
      }))
      s.conflicts = [...s.conflicts, ...records]
      s.arbiterPasses = [...s.arbiterPasses, { pass, verdict: 'detected', count: records.length }]
      const flagged = new Set(records.flatMap((r) => r.stepIds))
      if (flagged.size) {
        const steps = { ...s.steps }
        for (const id of flagged) if (steps[id]) steps[id] = { ...steps[id], conflicted: true }
        s.steps = steps
      }
      return s
    }

    case 'conflict.resolved': {
      // Empty payload, and it fires on EVERY clean pass — it means "checked,
      // none found", not "a conflict was resolved". Must not clear conflicts.
      s.arbiterPasses = [...s.arbiterPasses, { pass: s.arbiterPasses.length + 1, verdict: 'clear', count: 0 }]
      return s
    }

    case 'approval.requested': {
      const action = p as unknown as PendingAction
      // The gate replays from the top on every resume, so the same approval is
      // re-emitted with a NEW event id but the SAME payload.id. Observed 5
      // emissions for 3 approvals. Dedupe on payload.id, and never re-open one
      // already resolved.
      if (s.resolvedApprovalIds.includes(action.id)) return s
      const existing = s.approvals[action.id]
      if (existing) {
        s.approvals = { ...s.approvals, [action.id]: { ...existing, requestCount: existing.requestCount + 1 } }
        return s
      }
      s.approvals = {
        ...s.approvals,
        [action.id]: { ...action, status: 'pending', requestedTs: e.ts, requestCount: 1 },
      }
      s.approvalQueue = [...s.approvalQueue, action.id]
      if (action.step_id && s.steps[action.step_id]) {
        s.steps = {
          ...s.steps,
          [action.step_id]: { ...s.steps[action.step_id], awaitingApprovalId: action.id, status: 'awaiting-approval' },
        }
      }
      s.status = 'awaiting-approval'
      return s
    }

    case 'approval.resolved': {
      const id = String(p.id ?? '')
      const decision = String(p.decision ?? 'reject') as ApprovalState['status']
      // A superseded batch reports comma-joined ids.
      const ids = id.includes(',') ? id.split(',') : [id]
      const approvals = { ...s.approvals }
      for (const one of ids) {
        if (approvals[one]) approvals[one] = { ...approvals[one], status: decision, resolvedTs: e.ts }
      }
      s.approvals = approvals
      s.resolvedApprovalIds = [...new Set([...s.resolvedApprovalIds, ...ids])]
      s.approvalQueue = s.approvalQueue.filter((q) => !ids.includes(q))
      if (!s.approvalQueue.length && s.status === 'awaiting-approval') s.status = 'running'
      return s
    }

    case 'memory.recall': {
      s.memory = {
        ...s.memory,
        facts: (p.profile_facts ?? []) as ProfileFact[],
        recalled: (p.recalled ?? []) as RecalledMemory[],
      }
      return s
    }

    case 'memory.write': {
      s.memory = {
        ...s.memory,
        written: (p.facts_written ?? []) as RunState['memory']['written'],
        summary: String(p.summary ?? ''),
      }
      return s
    }

    case 'run.finished': {
      const raw = String(p.answer ?? '')
      // synthesize_node deterministically appends up to TWO blocks, in this
      // order: "Not completed:" then "Actions taken:". Splitting on the first
      // marker alone made the not-completed list swallow the whole ledger, so
      // cut at whichever appears first and parse each section separately.
      const NOT_DONE = '\n\nNot completed:\n'
      const TAKEN = '\n\nActions taken:\n'
      const cuts = [raw.indexOf(NOT_DONE), raw.indexOf(TAKEN)].filter((i) => i >= 0)
      s.answer = cuts.length ? raw.slice(0, Math.min(...cuts)) : raw

      const section = (marker: string): string[] => {
        const at = raw.indexOf(marker)
        if (at < 0) return []
        const rest = raw.slice(at + marker.length)
        const end = rest.indexOf('\n\n')
        return (end >= 0 ? rest.slice(0, end) : rest)
          .split('\n').map((l) => l.replace(/^-\s*/, '').trim()).filter(Boolean)
      }
      s.notCompleted = section(NOT_DONE)

      // The ledger, structured. Preferred over the rendered text above for
      // anything the UI needs to reason about (receipts, outcomes, per-step
      // links) — the text is for reading, this is for rendering.
      s.actions = ((p.actions ?? []) as Record<string, unknown>[]).map((a) => ({
        approvalId: (a.approval_id as string) ?? null,
        stepId: (a.step_id as string) ?? null,
        agent: String(a.agent ?? ''),
        tool: (a.tool as string) ?? null,
        args: (a.args as Record<string, unknown>) ?? {},
        description: String(a.description ?? a.tool ?? 'action'),
        decision: (a.decision as string) ?? null,
        outcome: (a.outcome as ActionOutcome) ?? 'skipped',
        receiptId: (a.receipt_id as string) ?? null,
        error: (a.error as string) ?? null,
      }))

      const cites = (p.citations ?? []) as Citation[]
      if (Array.isArray(cites) && cites.length && typeof cites[0] === 'object') {
        const seen = new Set(s.citations.map((c) => `${c.doc_number}#${c.clause}`))
        s.citations = [...s.citations, ...cites.filter((c) => !seen.has(`${c.doc_number}#${c.clause}`))]
      }
      s.runComplete = true
      s.status = 'finished'
      return s
    }

    case 'run.error': {
      // Only main.py's emit is terminal; the five graph-node emits are benign
      // degradation notices that always carry BOTH `agent` and `detail`.
      const terminal = e.agent == null && p.detail === undefined
      if (terminal) {
        s.fatalError = String(p.error ?? 'unknown error')
        s.status = 'error'
      } else {
        s.notices = [...s.notices, {
          agent: e.agent ?? 'system', error: String(p.error ?? ''), detail: String(p.detail ?? ''), ts: e.ts,
        }]
      }
      return s
    }

    default:
      return s
  }
}

export function reduceAll(events: AgentEvent[], from: RunState = initialRunState()): RunState {
  return events.reduce(reduce, from)
}
