/**
 * Fixture replay with cinematic pacing.
 *
 * The recorded traces fire events ~0.01s apart (they are MOCK_LLM runs), so
 * true 1x playback is an unreadable blur — the whole DAG completes in under a
 * second. Replay therefore preserves ORDER and CONCURRENCY but enforces a
 * readable minimum gap, and gives specific narrative beats extra air so a
 * judge can actually follow what happened.
 *
 * Realtime mode is still available for honesty when someone asks.
 */
import type { AgentEvent, EventType } from '../types/events'
import type { EventTransport, TransportCallbacks } from './types'

const MIN_GAP = 260
const MAX_GAP = 1600

/** Moments that need room to land, in ms. */
const BEAT_AFTER: Partial<Record<EventType, number>> = {
  'plan.created': 900,       // let the DAG lay out and be read
  'plan.revised': 900,
  'conflict.detected': 1200, // the money shot — judges must read it
  'run.finished': 800,       // before memory.write arrives
}
const BEAT_BEFORE: Partial<Record<EventType, number>> = {
  'conflict.detected': 700,
  'approval.requested': 500,
}

export type PacingMode = 'demo' | 'realtime'

export class ReplaySource implements EventTransport {
  private timer: ReturnType<typeof setTimeout> | null = null
  private index = 0
  private speed = 1
  private paused = false
  private stopped = false
  private holding = false
  /**
   * Approval ids this replay has already stopped for.
   *
   * The backend re-emits `approval.requested` verbatim on every resume, and in
   * a recording that duplicate sits BEFORE the `approval.resolved` that would
   * mark it settled. Consulting only resolved-ids therefore held a second time
   * on the same decision: the judge clicked Approve and was immediately asked
   * the identical question again, which reads as the button not working.
   *
   * A duplicate is a re-announcement, never a second ask — so one hold per id.
   */
  private heldIds = new Set<string>()

  constructor(
    private events: AgentEvent[],
    private cb: TransportCallbacks,
    private mode: PacingMode = 'demo',
    /** Ids already resolved, so a re-emitted approval doesn't re-hold. */
    private isApprovalResolved: (id: string) => boolean = () => false,
  ) {}

  start() {
    this.stopped = false
    this.paused = false
    this.cb.onStatus('streaming')
    this.scheduleNext(0)
  }

  stop() {
    this.stopped = true
    this.clear()
    this.cb.onStatus('closed')
  }

  pause() {
    this.paused = true
    this.clear()
    this.cb.onStatus('paused')
  }

  resume() {
    if (this.stopped) return
    this.paused = false
    this.holding = false
    this.cb.onStatus('streaming')
    this.scheduleNext(120)
  }

  setSpeed(multiplier: number) {
    this.speed = multiplier
  }

  /** Scrub. Cheap because the reducer is pure — the caller re-folds 0..index. */
  seek(index: number) {
    this.clear()
    this.index = Math.max(0, Math.min(index, this.events.length))
    // Rebuild the held set for the new position: approvals now in the past
    // count as already presented, approvals scrubbed back BEFORE do not — so
    // rewinding past the decision point lets it be made again, which is the
    // whole reason someone scrubs backwards during a demo.
    this.heldIds = new Set(
      this.events.slice(0, this.index)
        .filter((e) => e.type === 'approval.requested')
        .map((e) => String((e.payload as { id?: string }).id ?? ''))
        .filter(Boolean),
    )
    this.holding = false
    this.cb.onProgress?.(this.index, this.events.length)
    if (!this.paused && !this.stopped) this.scheduleNext(120)
  }

  stepForward() {
    this.paused = true
    this.clear()
    if (this.index < this.events.length) this.emitCurrent()
  }

  stepBack() {
    this.paused = true
    this.clear()
    this.seek(Math.max(0, this.index - 1))
  }

  get total() {
    return this.events.length
  }

  private clear() {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
  }

  private delayFor(i: number): number {
    if (i === 0) return 0
    const prev = this.events[i - 1]
    const cur = this.events[i]

    if (this.mode === 'realtime') {
      return Math.max(0, (cur.ts - prev.ts) * 1000) / this.speed
    }

    const rawGap = cur.ts - prev.ts
    // log1p keeps genuinely long real pauses feeling longer than instant ones,
    // without letting a 6-second recorded stall dominate the demo.
    const base = rawGap <= 0.25 ? MIN_GAP : MIN_GAP + Math.log1p(rawGap) * 900
    const beat = (BEAT_AFTER[prev.type] ?? 0) + (BEAT_BEFORE[cur.type] ?? 0)
    return (Math.min(Math.max(base, MIN_GAP), MAX_GAP) + beat) / this.speed
  }

  private scheduleNext(overrideDelay?: number) {
    if (this.stopped || this.paused || this.holding) return
    if (this.index >= this.events.length) {
      this.cb.onStatus('closed')
      return
    }
    const delay = overrideDelay ?? this.delayFor(this.index)
    this.timer = setTimeout(() => this.emitCurrent(), delay)
  }

  private emitCurrent() {
    if (this.stopped) return
    const e = this.events[this.index]
    this.index += 1
    this.cb.onEvent(e)
    this.cb.onProgress?.(this.index, this.events.length)

    // Hold at a genuine approval so the modal is a real decision point rather
    // than something that flashes past. Skip approvals already resolved — the
    // backend re-emits pending ones on every resume.
    if (e.type === 'approval.requested') {
      const id = String((e.payload as { id?: string }).id ?? '')
      if (id && !this.heldIds.has(id) && !this.isApprovalResolved(id)) {
        this.heldIds.add(id)
        this.holding = true
        this.cb.onStatus('paused')
        this.cb.onAwaitApproval?.(id)
        return
      }
    }
    this.scheduleNext()
  }

  /** Called once the user decides, to let playback continue. */
  releaseHold() {
    this.holding = false
    this.scheduleNext(200)
  }
}

/**
 * Recorded runs, from wherever they happen to live.
 *
 * Normally fetched from /fixtures/. The standalone build inlines them onto
 * `window.__SUTRA_FIXTURES__` instead, so the whole cockpit runs from a single
 * HTML file with no server — which is the demo's last line of defence if the
 * venue's network, the laptop, or the backend gives out.
 */
declare global {
  interface Window { __SUTRA_FIXTURES__?: Record<string, string> }
}

export async function loadFixture(name: string): Promise<AgentEvent[]> {
  const inlined = typeof window !== 'undefined' ? window.__SUTRA_FIXTURES__?.[name] : undefined
  const text = inlined ?? await (async () => {
    const res = await fetch(`/fixtures/${name}`)
    if (!res.ok) throw new Error(`fixture ${name}: ${res.status}`)
    return res.text()
  })()
  return text.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l) as AgentEvent)
}
