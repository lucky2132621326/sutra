/**
 * Deterministic left-to-right layering for the plan DAG.
 *
 * depth(step) = 1 + max(depth(deps)) — so every step at the same dependency
 * depth shares a column. That single property is what makes parallelism
 * visible at a glance, and it's why this is hand-rolled rather than delegated
 * to dagre: dagre's network-simplex ranking can shuffle nodes between ranks
 * and break exactly the reading we depend on.
 *
 * Column order within a depth follows the plan's own step order, so a
 * re-layout after plan.revised never reshuffles unrelated nodes.
 */
import type { StepState } from '../state/runReducer'

export const NODE_W = 264
export const NODE_H = 116
export const COL_GAP = 104
export const ROW_GAP = 28
const PAD_X = 48
const PAD_Y = 40

export interface PositionedStep {
  id: string
  depth: number
  x: number
  y: number
}

export function computeDepths(steps: Record<string, StepState>, order: string[]): Map<string, number> {
  const depth = new Map<string, number>()
  const visiting = new Set<string>()

  const resolve = (id: string): number => {
    if (depth.has(id)) return depth.get(id)!
    // Cycle guard: a malformed depends_on must not hang the UI.
    if (visiting.has(id)) return 0
    visiting.add(id)
    const step = steps[id]
    const deps = (step?.dependsOn ?? []).filter((d) => d in steps)
    const d = deps.length ? 1 + Math.max(...deps.map(resolve)) : 0
    visiting.delete(id)
    depth.set(id, d)
    return d
  }

  for (const id of order) resolve(id)
  return depth
}

export function layerLayout(
  steps: Record<string, StepState>,
  order: string[],
): { positions: PositionedStep[]; columns: { depth: number; ids: string[]; x: number }[] } {
  const depths = computeDepths(steps, order)

  const byDepth = new Map<number, string[]>()
  for (const id of order) {
    const d = depths.get(id) ?? 0
    if (!byDepth.has(d)) byDepth.set(d, [])
    byDepth.get(d)!.push(id)
  }

  const tallest = Math.max(1, ...[...byDepth.values()].map((ids) => ids.length))
  const canvasH = PAD_Y * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP
  const midY = canvasH / 2

  const positions: PositionedStep[] = []
  const columns: { depth: number; ids: string[]; x: number }[] = []

  for (const [d, ids] of [...byDepth.entries()].sort((a, b) => a[0] - b[0])) {
    const x = PAD_X + d * (NODE_W + COL_GAP)
    columns.push({ depth: d, ids, x })
    const blockH = ids.length * NODE_H + (ids.length - 1) * ROW_GAP
    const startY = midY - blockH / 2
    ids.forEach((id, i) => {
      positions.push({ id, depth: d, x, y: startY + i * (NODE_H + ROW_GAP) })
    })
  }

  return { positions, columns }
}
