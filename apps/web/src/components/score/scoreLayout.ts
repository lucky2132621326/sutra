import type { ScoreBlock, ScoreLaneId, ScoreMarker } from './runScoreModel'

export interface LaidOutBlock extends ScoreBlock {
  leftPct: number
  widthPct: number
  visualTrack: number
}

export interface LaidOutMarker extends ScoreMarker {
  leftPct: number
  visualTrack: number
}

function pct(ts: number, start: number, end: number): number {
  const span = Math.max(end - start, 0.001)
  return Math.max(0, Math.min(100, ((ts - start) / span) * 100))
}

/**
 * Pack blocks using their visible footprint, not only their timestamp range.
 * A very fast step still gets a small click target; if that target would
 * overlap the next step, the next step moves to a sub-lane instead of being
 * painted on top of it.
 */
export function layoutScoreBlocks(
  blocks: ScoreBlock[], startTs: number, endTs: number,
  minWidthPct = 1.4, gapPct = 0.35,
): LaidOutBlock[] {
  const byLane = new Map<ScoreLaneId, ScoreBlock[]>()
  for (const block of blocks) {
    const lane = byLane.get(block.lane) ?? []
    lane.push(block)
    byLane.set(block.lane, lane)
  }

  const laidOut = new Map<string, LaidOutBlock>()
  for (const laneBlocks of byLane.values()) {
    const trackEnds: number[] = []
    for (const block of [...laneBlocks].sort((a, b) => a.startTs - b.startTs || a.endTs - b.endTs)) {
      const leftPct = Math.min(pct(block.startTs, startTs, endTs), 99)
      const actualWidth = Math.max(0, pct(block.endTs, startTs, endTs) - leftPct)
      const widthPct = Math.min(100 - leftPct, Math.max(minWidthPct, actualWidth))
      let visualTrack = trackEnds.findIndex((end) => end + gapPct <= leftPct)
      if (visualTrack < 0) visualTrack = trackEnds.length
      trackEnds[visualTrack] = leftPct + widthPct
      laidOut.set(block.id, { ...block, leftPct, widthPct, visualTrack })
    }
  }
  return blocks.map((block) => laidOut.get(block.id)!)
}

/** Compact point markers need collision packing too. */
export function layoutScoreMarkers(
  markers: ScoreMarker[], startTs: number, endTs: number, minGapPct = 3.2,
): LaidOutMarker[] {
  const trackPositions: number[] = []
  return [...markers]
    .sort((a, b) => a.ts - b.ts || a.index - b.index)
    .map((marker) => {
      const leftPct = Math.min(pct(marker.ts, startTs, endTs), 99)
      let visualTrack = trackPositions.findIndex((last) => leftPct - last >= minGapPct)
      if (visualTrack < 0) visualTrack = trackPositions.length
      trackPositions[visualTrack] = leftPct
      return { ...marker, leftPct, visualTrack }
    })
}
