import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ReplaySource } from './replaySource'
import type { AgentEvent } from '../types/events'

function load(name: string): AgentEvent[] {
  return readFileSync(join(__dirname, '../../public/fixtures/', name), 'utf8')
    .split('\n').filter(Boolean).map((l) => JSON.parse(l) as AgentEvent)
}

const events = load('golden_clean.jsonl')

describe('cinematic pacing', () => {
  it('spaces events far enough apart to be readable', () => {
    vi.useFakeTimers()
    const seen: number[] = []
    const src = new ReplaySource(events, {
      onEvent: () => seen.push(Date.now()),
      onStatus: () => {},
    }, 'demo')
    src.start()
    // Advance 2 seconds of virtual time; with a 260ms floor only a handful
    // of events should have fired.
    vi.advanceTimersByTime(2000)
    const after2s = seen.length
    vi.useRealTimers()
    console.log(`events emitted in first 2s of demo pacing: ${after2s}`)
    expect(after2s).toBeLessThan(12)
    expect(after2s).toBeGreaterThan(1)
  })
})

describe('the approval hold asks exactly once', () => {
  // The backend re-emits approval.requested verbatim on every resume, and in a
  // recording that duplicate sits BEFORE the approval.resolved that settles it.
  // Holding on the duplicate meant the judge clicked Approve and was instantly
  // asked the identical question again — on stage that reads as a broken button.
  const conflict = load('golden_conflict.jsonl')

  const approvalIds = conflict
    .filter((e) => e.type === 'approval.requested')
    .map((e) => String((e.payload as { id?: string }).id ?? ''))

  it('the fixture really does contain a duplicated request', () => {
    expect(approvalIds.length).toBeGreaterThan(new Set(approvalIds).size)
  })

  it('holds once per approval id, however many times it is re-announced', () => {
    vi.useFakeTimers()
    const holds: string[] = []
    const src = new ReplaySource(conflict, {
      onEvent: () => {},
      onStatus: () => {},
      onAwaitApproval: (id) => {
        holds.push(id)
        // A judge decides promptly; release so playback continues.
        setTimeout(() => src.releaseHold(), 10)
      },
    }, 'demo')
    src.start()
    vi.advanceTimersByTime(120_000)
    vi.useRealTimers()

    expect(holds.length).toBe(new Set(approvalIds).size)
    expect(new Set(holds).size).toBe(holds.length)
  })

  it('plays to the end rather than stalling on the duplicate', () => {
    vi.useFakeTimers()
    let progress = 0
    const src = new ReplaySource(conflict, {
      onEvent: () => {},
      onStatus: () => {},
      onProgress: (i) => { progress = i },
      onAwaitApproval: () => { setTimeout(() => src.releaseHold(), 10) },
    }, 'demo')
    src.start()
    vi.advanceTimersByTime(120_000)
    vi.useRealTimers()

    expect(progress).toBe(conflict.length)
  })

  it('asks again if you scrub back before the decision', () => {
    vi.useFakeTimers()
    const holds: string[] = []
    const src = new ReplaySource(conflict, {
      onEvent: () => {},
      onStatus: () => {},
      onAwaitApproval: (id) => { holds.push(id); setTimeout(() => src.releaseHold(), 10) },
    }, 'demo')
    src.start()
    vi.advanceTimersByTime(120_000)
    const firstPass = holds.length

    // Rewind to the very start and play through again — the decision point is
    // in the future once more, so it must be offered again.
    src.seek(0)
    vi.advanceTimersByTime(120_000)
    vi.useRealTimers()

    expect(holds.length).toBe(firstPass * 2)
  })
})
