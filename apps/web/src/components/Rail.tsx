import { useEffect, useRef } from 'react'

import { useStore } from '../state/store'
import type { AgentEvent } from '../types/events'

const AGENT_COLOR: Record<string, string> = {
  academic: 'var(--agent-academic)', placement: 'var(--agent-placement)',
  events: 'var(--agent-events)', knowledge: 'var(--agent-knowledge)',
  services: 'var(--agent-services)',
}

const GLYPH: Record<string, string> = {
  'plan.created': '◫', 'plan.revised': '↻', 'node.started': '▸', 'node.finished': '✓',
  'node.failed': '✕', 'agent.thinking': '⋯', 'tool.called': '⌘', 'tool.result': '←',
  'tool.retry': '↻', 'tool.fallback': '⇢', 'rag.retrieved': '❝', 'conflict.detected': '⚔',
  'schedule.checked': '⌖', 'attendance.impact.calculated': '%',
  'conflict.resolved': '✓', 'approval.requested': '⏸', 'approval.resolved': '▶',
  'memory.recall': '◔', 'memory.write': '◉', 'run.finished': '★', 'run.error': '!',
}

function summarize(e: AgentEvent): string {
  const p = e.payload as Record<string, any>
  switch (e.type) {
    case 'plan.created': return `Plan created — ${(p.steps ?? []).length} steps`
    case 'plan.revised': return Array.isArray(p.steps)
      ? `Plan revised — ${p.steps.length} steps` : `Critic: ${p.feedback || 'plan rejected'}`
    case 'node.started': return String(p.task ?? '').slice(0, 80)
    case 'node.finished': return `finished · ${p.status}`
    case 'node.failed': return String(p.error ?? '').slice(0, 80)
    case 'agent.thinking': return p.detail ? String(p.detail).slice(0, 80) : 'thinking…'
    case 'tool.called': return `${p.tool}(${Object.keys(p.args ?? {}).join(', ')})`
    case 'tool.result': return `${p.tool} → ${p.status}${p.error ? ` · ${p.error}` : ''}`
    case 'tool.retry': return `${p.tool} retry ${p.attempt}`
    case 'tool.fallback': return `${p.tool} → ${p.to} (${p.reason})`.slice(0, 90)
    case 'schedule.checked': return p.has_conflict
      ? `schedule clash · ${p.detail}`.slice(0, 90)
      : `schedule clear · ${p.event_title ?? 'proposed event'}`
    case 'attendance.impact.calculated': return `${p.course_name ?? p.course_id}: ${p.current_pct}% → ${p.projected_pct}%`
    case 'rag.retrieved': return p.abstained ? 'no relevant clause — abstained' : `${p.chunks} clauses retrieved`
    case 'conflict.detected': return `${(p.conflicts ?? []).length} conflict(s) — ${p.rationale ?? ''}`.slice(0, 110)
    case 'conflict.resolved': return 'arbiter pass — no conflicts'
    case 'approval.requested': return `awaiting human: ${p.description ?? ''}`.slice(0, 90)
    case 'approval.resolved': return `human decision: ${p.decision}`
    case 'memory.recall': return `recalled ${(p.profile_facts ?? []).length} facts, ${(p.recalled ?? []).length} summaries`
    case 'memory.write': return `wrote ${(p.facts_written ?? []).length} durable facts`
    case 'run.finished': return 'answer ready'
    case 'run.error': return `${p.detail ?? p.error}`.slice(0, 100)
    default: return e.type
  }
}

export function Timeline() {
  const run = useStore((s) => s.run)
  const selectStep = useStore((s) => s.openInspector)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [run.timeline.length])

  if (!run.timeline.length) {
    return <Empty label="No events yet" hint="Events stream here as agents work." />
  }

  return (
    <div style={{ overflowY: 'auto', height: '100%', padding: '4px 0' }}>
      {run.timeline.map((e) => {
        const offset = run.t0 ? e.ts - run.t0 : 0
        const ink = AGENT_COLOR[e.agent ?? ''] ?? 'var(--ink-400)'
        const isConflict = e.type === 'conflict.detected'
        const isApproval = e.type.startsWith('approval')
        return (
          <button key={e.id} onClick={() => e.node_id && selectStep(e.node_id)}
            style={{
              display: 'grid', gridTemplateColumns: '52px 18px 1fr', gap: 8,
              width: '100%', textAlign: 'left', padding: '6px 12px',
              background: isConflict ? 'var(--danger-bg)' : isApproval ? 'var(--approval-bg)' : 'transparent',
              border: 'none', borderBottom: '1px solid var(--line)', cursor: e.node_id ? 'pointer' : 'default',
              fontFamily: 'var(--font-body)',
            }}>
            <span className="tnum" style={{ fontSize: 11.5, color: 'var(--ink-400)' }}>
              +{offset.toFixed(2)}s
            </span>
            <span style={{ color: ink, fontSize: 13 }}>{GLYPH[e.type] ?? '·'}</span>
            <span style={{ minWidth: 0 }}>
              <span className="eyebrow" style={{ color: ink, fontSize: 10.5, marginRight: 6 }}>
                {e.agent ?? 'system'}
              </span>
              <span style={{ fontSize: 12.5, color: 'var(--ink-900)' }}>{summarize(e)}</span>
            </span>
          </button>
        )
      })}
      <div ref={endRef} />
    </div>
  )
}

export function Citations() {
  const citations = useStore((s) => s.run.citations)
  if (!citations.length) {
    return <Empty label="No citations yet" hint="The Knowledge Agent's retrieved clauses appear here, with document and clause numbers." />
  }
  return (
    <div style={{ overflowY: 'auto', height: '100%', padding: 12, display: 'grid', gap: 10 }}>
      {citations.map((c, i) => (
        <div key={i} className="panel" style={{ padding: 12 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
            <span className="eyebrow" style={{ color: 'var(--accent)' }}>[doc:{i}]</span>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{c.doc_title}</span>
          </div>
          <div className="mono" style={{ fontSize: 11.5, color: 'var(--ink-400)', marginBottom: 8 }}>
            {c.doc_number} · clause {c.clause} · p.{c.page} · score {c.score.toFixed(3)}
          </div>
          <div style={{ fontSize: 12.5, lineHeight: '18px', color: 'var(--ink-600)' }}>{c.text}</div>
        </div>
      ))}
    </div>
  )
}

export function Memory() {
  const memory = useStore((s) => s.run.memory)
  const has = memory.facts.length || memory.recalled.length || memory.written.length
  if (!has) return <Empty label="No memory yet" hint="Durable facts and recalled conversation summaries appear here." />

  return (
    <div style={{ overflowY: 'auto', height: '100%', padding: 12, display: 'grid', gap: 14 }}>
      {memory.facts.length > 0 && (
        <section>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Recalled profile facts</div>
          {memory.facts.map((f) => (
            <div key={f.key} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-600)' }}>{f.key}</span>
                <span style={{ fontSize: 13 }}>{f.value}</span>
              </div>
              <div style={{ height: 3, background: 'var(--surface-sunken)', borderRadius: 2, marginTop: 4 }}>
                <div style={{ height: '100%', width: `${f.confidence * 100}%`, background: 'var(--accent)', borderRadius: 2 }} />
              </div>
            </div>
          ))}
        </section>
      )}
      {memory.recalled.length > 0 && (
        <section>
          <div className="eyebrow" style={{ marginBottom: 8 }}>From earlier conversations</div>
          {memory.recalled.map((r) => (
            <div key={r.id} className="panel" style={{ padding: 10, marginBottom: 8 }}>
              <div style={{ fontSize: 12.5, lineHeight: '18px' }}>{r.summary}</div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 6 }}>
                score {r.score.toFixed(2)} · thread {r.thread_id || '—'}
              </div>
            </div>
          ))}
        </section>
      )}
      {memory.written.length > 0 && (
        <section>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Written this turn</div>
          <div style={{ fontSize: 11.5, color: 'var(--ink-400)', marginBottom: 8 }}>
            Recorded after the answer — deliberately off the critical path.
          </div>
          {memory.written.map((w, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12.5, marginBottom: 4 }}>
              <span className="mono" style={{ color: 'var(--ink-600)' }}>{w.key}</span>
              <span>{w.value}</span>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}

export function Telemetry() {
  const run = useStore((s) => s.run)
  const t = run.telemetry
  const elapsed = run.t0 && run.lastTs ? run.lastTs - run.t0 : 0
  const done = Object.values(run.steps).filter((s) => s.status === 'done').length
  const latencies = Object.values(run.steps).map((s) => s.latencyMs).filter((x): x is number => x != null)
  const p50 = latencies.length
    ? [...latencies].sort((a, b) => a - b)[Math.floor(latencies.length / 2)] : 0

  const rows: [string, string][] = [
    ['Elapsed', `${elapsed.toFixed(2)}s`],
    ['Steps complete', `${done} / ${Object.keys(run.steps).length}`],
    ['Peak parallelism', `${t.peakConcurrency} agents`],
    ['Agents engaged', `${t.agentsUsed.length}`],
    ['Tool calls', `${t.toolCalls}`],
    ['Retries', `${t.retries}`],
    ['Fallbacks', `${t.fallbacks}`],
    ['Degraded results', `${t.degraded}`],
    ['Median step latency', `${(p50 / 1000).toFixed(2)}s`],
    ['Plan revisions', `${Math.max(0, run.planVersion - 1)}`],
    ['Arbiter passes', `${run.arbiterPasses.length}`],
  ]

  return (
    <div style={{ overflowY: 'auto', height: '100%', padding: 12 }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{
          display: 'flex', justifyContent: 'space-between', padding: '7px 0',
          borderBottom: '1px solid var(--line)', fontSize: 13,
        }}>
          <span style={{ color: 'var(--ink-600)' }}>{k}</span>
          <span className="tnum" style={{ fontWeight: 600 }}>{v}</span>
        </div>
      ))}
      {run.notices.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="eyebrow" style={{ marginBottom: 6, color: 'var(--degraded)' }}>
            Degradation notices ({run.notices.length})
          </div>
          {run.notices.map((n, i) => (
            <div key={i} style={{
              fontSize: 12, padding: 8, marginBottom: 6,
              background: 'var(--degraded-bg)', color: 'var(--degraded)',
              borderRadius: 'var(--r-chip)',
            }}>
              <strong>{n.agent}</strong> — {n.detail}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Empty({ label, hint }: { label: string; hint: string }) {
  return (
    <div style={{ display: 'grid', placeItems: 'center', height: '100%', padding: 24, textAlign: 'center' }}>
      <div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-600)', marginBottom: 6 }}>{label}</div>
        <div style={{ fontSize: 12.5, color: 'var(--ink-400)', maxWidth: 260 }}>{hint}</div>
      </div>
    </div>
  )
}
