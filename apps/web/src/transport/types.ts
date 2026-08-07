import type { AgentEvent } from '../types/events'

export type TransportStatus = 'idle' | 'connecting' | 'streaming' | 'paused' | 'closed' | 'error'

/**
 * Live SSE and fixture replay both implement this, so the UI never knows (or
 * cares) which one is feeding it. That is what makes "switch to replay" a
 * one-line swap if a live demo stalls.
 */
export interface EventTransport {
  start(): void
  stop(): void
  /** Replay-only; live transports may no-op. */
  pause?(): void
  resume?(): void
  setSpeed?(multiplier: number): void
  seek?(index: number): void
  stepForward?(): void
  stepBack?(): void
}

export interface TransportCallbacks {
  onEvent: (e: AgentEvent) => void
  onStatus: (s: TransportStatus) => void
  /** Fired when replay reaches an approval it must hold at. */
  onAwaitApproval?: (approvalId: string) => void
  onProgress?: (index: number, total: number) => void
}
