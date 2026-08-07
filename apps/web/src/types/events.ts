/**
 * Mirrors packages/contracts/events.py.
 *
 * Only the 17 event types the backend ACTUALLY emits are modelled as live.
 * `run.started`, `a2a.message`, `token.usage` and `proactive.alert` are
 * declared in the Python enum but never emitted anywhere in apps/api — the UI
 * must not reserve space for data that never arrives.
 */

export type EventType =
  | 'plan.created'
  | 'plan.revised'
  | 'node.started'
  | 'node.finished'
  | 'node.failed'
  | 'agent.thinking'
  | 'conflict.detected'
  | 'conflict.resolved'
  | 'tool.called'
  | 'tool.result'
  | 'tool.retry'
  | 'tool.fallback'
  | 'rag.retrieved'
  | 'memory.write'
  | 'memory.recall'
  | 'approval.requested'
  | 'approval.resolved'
  | 'run.finished'
  | 'run.error'
  // declared but never emitted; kept so an unexpected one doesn't crash parsing
  | 'run.started'
  | 'a2a.message'
  | 'token.usage'
  | 'proactive.alert'

/** All 9 keys are always present — Python's asdict() emits nulls too. */
export interface AgentEvent {
  id: string
  run_id: string
  /** epoch SECONDS (float), not milliseconds. */
  ts: number
  type: EventType
  node_id: string | null
  agent: string | null
  payload: Record<string, unknown>
  /** float ms; non-null only on node.finished. */
  latency_ms: number | null
  /** always null — nothing in the backend sets it. */
  parent_id: string | null
}

export const AGENT_NAMES = ['academic', 'placement', 'events', 'knowledge', 'services'] as const
export type AgentName = (typeof AGENT_NAMES)[number]

/** Orchestration roles that appear as `agent` but are not specialists. */
export const ORCHESTRATION_ROLES = [
  'intake', 'planner', 'conflict_arbiter', 'critic', 'approval_gate', 'synthesizer', 'memory',
] as const

export interface Step {
  id: string
  agent: string
  task: string
  depends_on: string[]
  expected_output: string
  requires_approval: boolean
}

export interface Plan {
  goal: string
  reasoning: string
  steps: Step[]
}

export interface PendingAction {
  id: string
  step_id: string | null
  agent: string
  tool: string
  args: Record<string, unknown>
  description: string
  risk: string
  reversible: boolean
  preview: string
}

export interface Citation {
  text: string
  doc_title: string
  doc_number: string
  clause: string
  page: number
  score: number
}

export interface ProfileFact {
  key: string
  value: string
  confidence: number
  evidence_turn?: string
  updated_at?: number
}

export interface RecalledMemory {
  id: string
  summary: string
  score: number
  thread_id: string
  ts?: number
}
