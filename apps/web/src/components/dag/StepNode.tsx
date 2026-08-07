import { Handle, Position, type NodeProps } from '@xyflow/react'

import type { StepState, StepStatus } from '../../state/runReducer'
import { NODE_H, NODE_W } from '../../layout/layerLayout'

/**
 * The primary status signal is a 4px colored LEFT RAIL, not a border tint or
 * a filled background — it reads far better at 3m on a projector. Every state
 * also carries a WORD, so nothing depends on colour alone (projector washout
 * and colour-blind safety).
 */
const STATUS_STYLE: Record<StepStatus, { rail: string; label: string; chipBg: string; chipInk: string }> = {
  pending: { rail: 'var(--line-strong)', label: 'PENDING', chipBg: 'var(--pending-bg)', chipInk: 'var(--ink-400)' },
  running: { rail: 'var(--running)', label: 'RUNNING', chipBg: 'var(--running-bg)', chipInk: 'var(--running)' },
  done: { rail: 'var(--success)', label: 'DONE', chipBg: 'var(--success-bg)', chipInk: 'var(--success)' },
  degraded: { rail: 'var(--degraded)', label: 'DEGRADED', chipBg: 'var(--degraded-bg)', chipInk: 'var(--degraded)' },
  failed: { rail: 'var(--danger)', label: 'FAILED', chipBg: 'var(--danger-bg)', chipInk: 'var(--danger)' },
  'awaiting-approval': { rail: 'var(--approval)', label: 'NEEDS APPROVAL', chipBg: 'var(--approval-bg)', chipInk: 'var(--approval)' },
  rejected: { rail: 'var(--ink-300)', label: 'REJECTED BY HUMAN', chipBg: 'var(--pending-bg)', chipInk: 'var(--ink-400)' },
  denied: { rail: 'var(--denied)', label: 'NEEDS HIGHER ROLE', chipBg: 'var(--denied-bg)', chipInk: 'var(--denied)' },
  // Never ran: something it depended on was rejected or not permitted.
  cancelled: { rail: 'var(--ink-300)', label: 'CANCELLED', chipBg: 'var(--pending-bg)', chipInk: 'var(--ink-400)' },
}

const AGENT_COLOR: Record<string, string> = {
  academic: 'var(--agent-academic)',
  placement: 'var(--agent-placement)',
  events: 'var(--agent-events)',
  knowledge: 'var(--agent-knowledge)',
  services: 'var(--agent-services)',
}

export type StepNodeData = { step: StepState; selected: boolean }

export function StepNode({ data }: NodeProps) {
  const { step } = data as unknown as StepNodeData
  const style = STATUS_STYLE[step.status]
  const isRunning = step.status === 'running'
  const agentInk = AGENT_COLOR[step.agent] ?? 'var(--agent-orchestration)'

  return (
    <div
      className="step-node"
      data-status={step.status}
      data-conflicted={step.conflicted || undefined}
      style={{
        width: NODE_W, height: NODE_H,
        background: 'var(--surface)',
        border: `1px solid ${step.conflicted ? 'var(--danger)' : 'var(--line)'}`,
        borderLeft: `4px solid ${style.rail}`,
        borderRadius: 'var(--r-card)',
        boxShadow: isRunning ? '0 0 0 2px var(--running-bg), var(--e1)' : 'var(--e1)',
        padding: '12px 14px',
        display: 'flex', flexDirection: 'column', gap: 6,
        borderStyle: step.status === 'pending' ? 'dashed' : 'solid',
        opacity: step.status === 'rejected' ? 0.62 : 1,
        transition: 'box-shadow var(--t-state), border-color var(--t-state), opacity var(--t-state)',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0, width: 1, height: 1 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: agentInk, flexShrink: 0 }} />
        <span className="eyebrow" style={{ color: agentInk, fontSize: 11.5 }}>{step.agent}</span>
        <span className="eyebrow mono" style={{ fontSize: 11 }}>{step.id}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          {step.latencyMs != null && (
            <span className="tnum" style={{ fontSize: 11.5, color: 'var(--ink-400)' }}>
              {(step.latencyMs / 1000).toFixed(2)}s
            </span>
          )}
          {isRunning && <span className="pulse-dot" style={{ background: 'var(--running)' }} />}
        </span>
      </div>

      <div style={{
        fontSize: 13.5, lineHeight: '18px', color: 'var(--ink-900)',
        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
        textDecoration: step.status === 'rejected' ? 'line-through' : 'none',
      }}>
        {step.task}
      </div>

      <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 10.5, fontWeight: 700, letterSpacing: '0.07em',
          padding: '2px 7px', borderRadius: 'var(--r-pill)',
          background: style.chipBg, color: style.chipInk,
        }}>
          {style.label}
        </span>
        {step.tools.map((t, i) => (
          <span key={i} className="mono" title={t.error ?? t.degradationReason ?? t.tool}
            style={{
              fontSize: 10.5, padding: '2px 6px', borderRadius: 'var(--r-chip)',
              border: '1px solid var(--line)',
              background: t.status === 'running' ? 'var(--surface-sunken)' : 'transparent',
              color: t.retries.length || t.fallbackTo ? 'var(--degraded)' : 'var(--ink-600)',
            }}>
            {t.tool}
            {t.retries.length > 0 && ` ↻${t.retries.length}`}
            {t.fallbackTo && ' ⇢'}
          </span>
        ))}
        {step.ragChunks != null && (
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-400)' }}>
            {step.ragChunks} clauses
          </span>
        )}
      </div>

      <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 1, height: 1 }} />
    </div>
  )
}
