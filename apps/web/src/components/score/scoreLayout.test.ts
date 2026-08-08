import { describe, expect, it } from 'vitest'

import type { ScoreBlock, ScoreMarker } from './runScoreModel'
import { layoutScoreBlocks, layoutScoreMarkers } from './scoreLayout'

function block(id: string, startTs: number, endTs: number): ScoreBlock {
  return {
    id, stepId: id, lane: 'academic', task: id, startTs, endTs,
    startIndex: 1, endIndex: 2, status: 'done', latencyMs: (endTs - startTs) * 1000,
    tools: [], retries: 0, fallback: false, track: 0,
  }
}

describe('score visual layout', () => {
  it('moves visually colliding short work onto sub-lanes', () => {
    const result = layoutScoreBlocks([
      block('a', 1, 1.01), block('b', 1.02, 1.03), block('c', 2, 2.5),
    ], 0, 10, 2, 0.25)
    expect(result[0].visualTrack).toBe(0)
    expect(result[1].visualTrack).toBe(1)
    expect(result[2].visualTrack).toBe(0)
  })

  it('keeps width proportional to latency once it exceeds the click target', () => {
    const [result] = layoutScoreBlocks([block('a', 2, 5)], 0, 10, 1)
    expect(result.leftPct).toBe(20)
    expect(result.widthPct).toBe(30)
  })

  it('packs point markers that occur at nearly the same time', () => {
    const markers: ScoreMarker[] = [
      { id: 'm1', kind: 'plan', label: 'Plan', ts: 1, index: 1 },
      { id: 'm2', kind: 'safety', label: 'Safety', ts: 1.01, index: 2 },
      { id: 'm3', kind: 'finish', label: 'Done', ts: 5, index: 3 },
    ]
    const result = layoutScoreMarkers(markers, 0, 10, 3)
    expect(result.map((marker) => marker.visualTrack)).toEqual([0, 1, 0])
  })
})
