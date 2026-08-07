import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, BackgroundVariant, ReactFlow, ReactFlowProvider,
  ViewportPortal, useReactFlow, type Edge, type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { NODE_H, NODE_W, layerLayout } from '../../layout/layerLayout'
import { useStore } from '../../state/store'
import { StepNode } from './StepNode'

const nodeTypes = { step: StepNode }

function Canvas() {
  const run = useStore((s) => s.run)
  const selectStep = useStore((s) => s.selectStep)
  const openInspector = useStore((s) => s.openInspector)
  const closeInspector = useStore((s) => s.closeInspector)
  const selectedStepId = useStore((s) => s.selectedStepId)
  const { fitView } = useReactFlow()
  const [layoutReady, setLayoutReady] = useState(false)
  const lastPlanVersion = useRef(0)

  const { positions, columns } = useMemo(
    () => layerLayout(run.steps, run.stepOrder),
    [run.steps, run.stepOrder],
  )

  const nodes: Node[] = useMemo(
    () => positions.map((p) => ({
      id: p.id,
      type: 'step',
      position: { x: p.x, y: p.y },
      data: { step: run.steps[p.id], selected: selectedStepId === p.id },
      draggable: false,
    })),
    [positions, run.steps, selectedStepId],
  )

  const edges: Edge[] = useMemo(() => {
    const out: Edge[] = []
    for (const id of run.stepOrder) {
      const step = run.steps[id]
      if (!step) continue
      for (const dep of step.dependsOn) {
        if (!run.steps[dep]) continue
        const active = run.steps[dep].status !== 'pending'
        out.push({
          id: `${dep}->${id}`, source: dep, target: id, type: 'smoothstep',
          animated: run.steps[id].status === 'running',
          style: {
            stroke: active ? 'var(--line-strong)' : 'var(--line)',
            strokeWidth: 1.5,
          },
        })
      }
    }
    // A conflict implicating two steps gets a direct edge between them — the
    // literal picture of two agents disagreeing.
    for (const c of run.conflicts) {
      if (c.stepIds.length >= 2) {
        const [a, b] = c.stepIds
        out.push({
          id: `conflict-${a}-${b}-${c.pass}`, source: a, target: b, type: 'straight',
          label: c.type, animated: true,
          style: { stroke: 'var(--danger)', strokeWidth: 2.5, strokeDasharray: '6 4' },
          labelStyle: { fill: 'var(--danger)', fontSize: 11, fontWeight: 700 },
          labelBgStyle: { fill: 'var(--danger-bg)' },
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 6,
        })
      }
    }
    return out
  }, [run.steps, run.stepOrder, run.conflicts])

  // Keep the whole graph inside the pane, always.
  //
  // Fitting only on plan.revised was not enough: steps appear one at a time as
  // they start, so a graph that fitted at two columns silently overflowed at
  // three, and the first and last nodes were clipped by the pane edges. Refit
  // whenever the SET of nodes changes, and whenever the pane itself resizes —
  // the centre column is flexible, so every window resize changes the budget.
  const shape = run.stepOrder.join(',')
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!nodes.length) return
    // A revision re-lays-out the whole graph, so it earns a slower, visible
    // settle; a node simply appearing should not yank the viewport around.
    const revised = run.planVersion !== lastPlanVersion.current
    lastPlanVersion.current = run.planVersion
    const t = setTimeout(
      () => fitView({ duration: revised ? 500 : 260, padding: 0.16, maxZoom: 1 }),
      revised ? 80 : 40,
    )
    return () => clearTimeout(t)
  }, [shape, run.planVersion, nodes.length, fitView])

  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let t: ReturnType<typeof setTimeout>
    const ro = new ResizeObserver(() => {
      clearTimeout(t)
      // Debounced: a drag-resize fires continuously, and refitting on every
      // frame fights the user's own gesture.
      t = setTimeout(() => fitView({ duration: 200, padding: 0.16, maxZoom: 1 }), 120)
    })
    ro.observe(el)
    return () => { clearTimeout(t); ro.disconnect() }
  }, [fitView])

  // Gate the reposition transition until after first paint, else every node
  // animates in from (0,0) on mount.
  useEffect(() => {
    const t = setTimeout(() => setLayoutReady(true), 60)
    return () => clearTimeout(t)
  }, [])

  const runningCount = Object.values(run.steps).filter((s) => s.status === 'running').length

  if (!nodes.length) {
    return (
      <div style={{
        height: '100%', display: 'grid', placeItems: 'center',
        color: 'var(--ink-400)', textAlign: 'center', padding: 32,
      }}>
        <div>
          <div className="font-display" style={{ fontSize: 20, color: 'var(--ink-600)', marginBottom: 8 }}>
            No plan yet
          </div>
          <div style={{ fontSize: 14, maxWidth: 380 }}>
            Ask a question, or press play to replay a recorded run. The orchestrator's plan
            will appear here as a live graph.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div ref={wrapRef} className={layoutReady ? 'dag-ready' : undefined}
      style={{ height: '100%', width: '100%', position: 'relative', overflow: 'hidden' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => openInspector(n.id)}
        onPaneClick={() => { selectStep(null); closeInspector() }}
        proOptions={{ hideAttribution: true }}
        fitView
        fitViewOptions={{ padding: 0.16, maxZoom: 1 }}
        // Low floor on purpose: a four-column revised plan in a narrow centre
        // pane needs to zoom well out, and clipping the graph is worse than
        // small nodes — the judge can zoom in, but cannot see what is missing.
        minZoom={0.2}
        maxZoom={1.6}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--line)" />

        {/* Column bands live in viewport space so they pan and zoom WITH the
            graph. They make "these ran together" unambiguous even when paused. */}
        <ViewportPortal>
          {columns.map((col) => {
            const running = col.ids.filter((id) => run.steps[id]?.status === 'running').length
            const parallel = col.ids.length > 1
            if (!parallel) return null
            return (
              <div key={col.depth} style={{
                position: 'absolute',
                left: col.x - 18, top: -14,
                width: NODE_W + 36,
                height: col.ids.length * (NODE_H + 28) + 44,
                borderRadius: 14,
                background: running > 1 ? 'var(--running-bg)' : 'var(--surface-sunken)',
                opacity: running > 1 ? 0.75 : 0.45,
                border: `1px dashed ${running > 1 ? 'var(--running)' : 'var(--line-strong)'}`,
                transition: 'opacity var(--t-state), background var(--t-state)',
                pointerEvents: 'none',
                zIndex: -1,
              }}>
                <div className="eyebrow" style={{
                  position: 'absolute', top: 8, left: 12, fontSize: 10.5,
                  color: running > 1 ? 'var(--running)' : 'var(--ink-400)',
                }}>
                  Wave {col.depth + 1} · {col.ids.length} agents in parallel
                </div>
              </div>
            )
          })}
        </ViewportPortal>
      </ReactFlow>

      {runningCount > 1 && (
        <div style={{
          position: 'absolute', top: 12, left: 12, zIndex: 5,
          background: 'var(--running-bg)', color: 'var(--running)',
          border: '1px solid var(--running)', borderRadius: 'var(--r-pill)',
          padding: '5px 12px', fontSize: 12.5, fontWeight: 700,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span className="pulse-dot" style={{ background: 'var(--running)' }} />
          {runningCount} agents running concurrently
        </div>
      )}

      {run.planVersion > 1 && (
        <div style={{
          position: 'absolute', top: 12, right: 12, zIndex: 5,
          background: 'var(--degraded-bg)', color: 'var(--degraded)',
          border: '1px solid var(--degraded)', borderRadius: 'var(--r-pill)',
          padding: '5px 12px', fontSize: 12.5, fontWeight: 700,
        }}>
          Plan v{run.planVersion} · replanned after arbitration
        </div>
      )}
    </div>
  )
}

export function PlanCanvas() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  )
}
