import { create } from 'zustand'

import type { AgentEvent } from '../types/events'
import type { PacingMode } from '../transport/replaySource'
import type { TransportStatus } from '../transport/types'
import { initialRunState, reduce, reduceAll, type RunState } from './runReducer'

export type Mode = 'replay' | 'live'

/**
 * One exchange in the conversation. Deliberately NOT part of RunState: a
 * RunState is one turn and gets wiped when the next run starts, but the
 * transcript is the thing a judge scrolls back through. Kept in UI state so
 * it survives resetRun().
 */
export interface Turn {
  id: string
  role: 'user' | 'assistant'
  text: string
  /** Which run produced this — lets a turn re-open its own DAG and evidence. */
  runId: string | null
  ts: number
  pending?: boolean
}

interface UIState {
  mode: Mode
  theme: 'light' | 'dark'
  fixture: string
  pacing: PacingMode
  speed: number
  status: TransportStatus
  progress: { index: number; total: number }
  selectedStepId: string | null
  activeApprovalId: string | null
  rail: 'timeline' | 'citations' | 'memory' | 'telemetry'
  /** Buffered so scrubbing can re-fold without re-fetching. */
  events: AgentEvent[]
  run: RunState
  backendUp: boolean
  liveRunId: string | null
  /** Stable across turns; sent back on each /chat to keep conversation state. */
  threadId: string | null
  /** Guards the single-flight approval rule (POST /approve is not id-scoped). */
  approvalInFlight: boolean
  /** The transcript. Survives resetRun() — see Turn. */
  turns: Turn[]
  /** What the composer currently holds. */
  draft: string
  /** Set while a live turn is in flight so the composer can lock. */
  sending: boolean
  inspectorOpen: boolean
  /** Bumped to ask the composer to take focus. A counter rather than a boolean
   *  so picking the SAME mission twice still re-focuses. */
  composerFocusNonce: number
}

interface Actions {
  ingest: (e: AgentEvent) => void
  resetRun: () => void
  loadEvents: (events: AgentEvent[]) => void
  seekTo: (index: number) => void
  setMode: (m: Mode) => void
  setTheme: (t: 'light' | 'dark') => void
  setFixture: (f: string) => void
  setPacing: (p: PacingMode) => void
  setSpeed: (n: number) => void
  setStatus: (s: TransportStatus) => void
  setProgress: (index: number, total: number) => void
  selectStep: (id: string | null) => void
  setActiveApproval: (id: string | null) => void
  setRail: (r: UIState['rail']) => void
  setBackendUp: (up: boolean) => void
  setLiveRunId: (id: string | null) => void
  setThreadId: (id: string | null) => void
  setApprovalInFlight: (b: boolean) => void
  addTurn: (turn: Omit<Turn, 'id' | 'ts'>) => string
  resolveTurn: (id: string, text: string) => void
  setDraft: (d: string) => void
  setSending: (b: boolean) => void
  clearConversation: () => void
  openInspector: (stepId: string) => void
  closeInspector: () => void
  requestComposerFocus: () => void
}

let turnSeq = 0

export const useStore = create<UIState & Actions>((set, get) => ({
  mode: 'replay',
  theme: 'light',
  fixture: 'golden_conflict.jsonl',
  pacing: 'demo',
  speed: 1,
  status: 'idle',
  progress: { index: 0, total: 0 },
  selectedStepId: null,
  activeApprovalId: null,
  rail: 'timeline',
  events: [],
  run: initialRunState(),
  backendUp: false,
  liveRunId: null,
  threadId: null,
  approvalInFlight: false,
  turns: [],
  draft: '',
  sending: false,
  inspectorOpen: false,
  composerFocusNonce: 0,

  ingest: (e) => set((s) => ({ run: reduce(s.run, e) })),
  resetRun: () => set({ run: initialRunState(), selectedStepId: null, activeApprovalId: null }),
  loadEvents: (events) => set({ events, run: initialRunState(), progress: { index: 0, total: events.length } }),

  // Scrub = re-fold from zero. Cheap because the reducer is pure, and it keeps
  // scrubbed state byte-identical to played state.
  seekTo: (index) => {
    const { events } = get()
    set({ run: reduceAll(events.slice(0, index)), progress: { index, total: events.length } })
  },

  setMode: (mode) => set({ mode }),
  setTheme: (theme) => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('sutra-theme', theme)
    set({ theme })
  },
  setFixture: (fixture) => set({ fixture }),
  setPacing: (pacing) => set({ pacing }),
  setSpeed: (speed) => set({ speed }),
  setStatus: (status) => set({ status }),
  setProgress: (index, total) => set({ progress: { index, total } }),
  selectStep: (selectedStepId) => set({ selectedStepId }),
  setActiveApproval: (activeApprovalId) => set({ activeApprovalId }),
  setRail: (rail) => set({ rail }),
  setBackendUp: (backendUp) => set({ backendUp }),
  setLiveRunId: (liveRunId) => set({ liveRunId }),
  setThreadId: (threadId) => set({ threadId }),
  setApprovalInFlight: (approvalInFlight) => set({ approvalInFlight }),

  addTurn: (turn) => {
    const id = `t${++turnSeq}`
    set((s) => ({ turns: [...s.turns, { ...turn, id, ts: Date.now() }] }))
    return id
  },
  resolveTurn: (id, text) => set((s) => ({
    turns: s.turns.map((t) => (t.id === id ? { ...t, text, pending: false } : t)),
  })),
  setDraft: (draft) => set({ draft }),
  setSending: (sending) => set({ sending }),
  clearConversation: () => set({ turns: [], threadId: null }),

  openInspector: (selectedStepId) => set({ selectedStepId, inspectorOpen: true }),
  closeInspector: () => set({ inspectorOpen: false }),
  requestComposerFocus: () => set((s) => ({ composerFocusNonce: s.composerFocusNonce + 1 })),
}))
