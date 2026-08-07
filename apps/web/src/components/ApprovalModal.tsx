import { useEffect, useMemo, useState } from 'react'

import { useStore } from '../state/store'

interface Props {
  onDecide: (
    approvalId: string,
    decision: 'approve' | 'reject' | 'edit',
    editedArgs: Record<string, unknown> | null,
  ) => void
}

/**
 * The human-in-the-loop gate. This is the moment the judge is handed the mouse,
 * so it shows exactly what is about to happen — the real payload, not a summary.
 */
export function ApprovalModal({ onDecide }: Props) {
  const run = useStore((s) => s.run)
  const activeId = useStore((s) => s.activeApprovalId)
  const inFlight = useStore((s) => s.approvalInFlight)
  const mode = useStore((s) => s.mode)
  const events = useStore((s) => s.events)
  const approval = activeId ? run.approvals[activeId] : null

  // In replay the decision is already in the recording; find it so the modal
  // can report it instead of pretending to ask.
  const replaying = mode === 'replay'
  const recorded = replaying && activeId
    ? (events.find(
        (e) => e.type === 'approval.resolved'
          && (e.payload as { id?: string }).id === activeId,
      )?.payload as { decision?: string } | undefined)?.decision ?? null
    : null

  const [args, setArgs] = useState<Record<string, unknown>>({})
  const [shake, setShake] = useState(false)

  useEffect(() => {
    if (approval) setArgs({ ...approval.args })
  }, [approval?.id])

  const dirty = useMemo(
    () => approval ? JSON.stringify(args) !== JSON.stringify(approval.args) : false,
    [args, approval],
  )

  useEffect(() => {
    if (!approval) return
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        // The graph is genuinely blocked here — letting Esc dismiss would
        // imply a decision the user never made.
        ev.preventDefault()
        setShake(true)
        setTimeout(() => setShake(false), 400)
      }
      if (ev.key === 'Enter' && !inFlight) {
        onDecide(approval.id, (replaying ? recorded ?? 'approve' : 'approve') as 'approve' | 'reject' | 'edit', null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [approval, inFlight, onDecide, replaying, recorded])

  if (!approval || approval.status !== 'pending') return null

  const waited = run.lastTs && approval.requestedTs
    ? Math.max(0, run.lastTs - approval.requestedTs) : 0
  const queuePos = run.approvalQueue.indexOf(approval.id) + 1

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'rgb(22 24 29 / 0.12)', backdropFilter: 'blur(2px)',
      display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div className={shake ? 'shake' : undefined} style={{
        width: 560, maxWidth: '100%', maxHeight: '86vh', overflow: 'auto',
        background: 'var(--surface)', borderRadius: 'var(--r-card)',
        border: '1px solid var(--line)', boxShadow: 'var(--e3)',
      }}>
        <div style={{ padding: '18px 22px', borderBottom: '2px solid var(--line)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span className="eyebrow" style={{ color: 'var(--approval)' }}>Approval required</span>
            <span style={{
              fontSize: 10.5, fontWeight: 700, padding: '2px 7px', borderRadius: 'var(--r-pill)',
              background: approval.risk === 'medium' ? 'var(--degraded-bg)' : 'var(--pending-bg)',
              color: approval.risk === 'medium' ? 'var(--degraded)' : 'var(--ink-600)',
            }}>{approval.risk.toUpperCase()} RISK</span>
            <span style={{
              fontSize: 10.5, fontWeight: 700, padding: '2px 7px', borderRadius: 'var(--r-pill)',
              background: approval.reversible ? 'var(--success-bg)' : 'var(--danger-bg)',
              color: approval.reversible ? 'var(--success)' : 'var(--danger)',
            }}>{approval.reversible ? 'REVERSIBLE' : 'IRREVERSIBLE'}</span>
            {run.approvalQueue.length > 1 && (
              <span className="eyebrow" style={{ marginLeft: 'auto' }}>
                {queuePos} of {run.approvalQueue.length}
              </span>
            )}
          </div>
          <div className="font-display" style={{ fontSize: 19, lineHeight: '25px' }}>
            {approval.description}
          </div>
        </div>

        {approval.preview && (
          <div style={{ padding: '14px 22px', borderBottom: '1px solid var(--line)' }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>What will happen</div>
            <pre className="mono" style={{
              margin: 0, whiteSpace: 'pre-wrap', fontSize: 12.5, lineHeight: '18px',
              background: 'var(--surface-sunken)', padding: 12, borderRadius: 'var(--r-chip)',
              color: 'var(--ink-900)',
            }}>{approval.preview}</pre>
          </div>
        )}

        <div style={{ padding: '14px 22px' }}>
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            Arguments · {approval.tool} · agent {approval.agent}
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <tbody>
              {Object.entries(args).map(([k, v]) => {
                const changed = JSON.stringify(v) !== JSON.stringify(approval.args[k])
                return (
                  <tr key={k}>
                    <td className="mono" style={{
                      padding: '6px 8px 6px 0', color: 'var(--ink-600)', width: 130, verticalAlign: 'top',
                    }}>{k}</td>
                    <td style={{ padding: '6px 0' }}>
                      <input
                        className="mono"
                        value={String(v)}
                        onChange={(ev) => setArgs({ ...args, [k]: ev.target.value })}
                        style={{
                          width: '100%', fontSize: 12.5, padding: '6px 8px',
                          border: `1px solid ${changed ? 'var(--accent)' : 'var(--line)'}`,
                          borderRadius: 'var(--r-input)', background: 'var(--surface)',
                          color: 'var(--ink-900)',
                        }}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {/* Only existing keys are editable: the backend's _validated_edited_args
              rejects unknown fields loudly, so the UI must not invite them. */}
          <div style={{ fontSize: 12, color: 'var(--ink-400)', marginTop: 8 }}>
            Values may be edited; new fields are rejected by the server.
          </div>
        </div>

        <div style={{
          padding: '14px 22px', borderTop: '1px solid var(--line)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span className="tnum" style={{ fontSize: 12, color: 'var(--ink-400)' }}>
            agent blocked {waited.toFixed(1)}s
          </span>
          {replaying ? (
            // A recorded run already has its decision. Offering Approve /
            // Reject / Edit here would be theatre: whichever the judge picks,
            // playback continues with the choice the recording made. Show what
            // was decided, and say plainly that it is a recording.
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 12, color: 'var(--ink-400)' }}>
                Recorded decision — replay
              </span>
              <span style={{
                fontSize: 12.5, fontWeight: 700, padding: '6px 14px',
                borderRadius: 'var(--r-input)',
                color: recorded === 'reject' ? 'var(--denied)' : 'var(--success)',
                background: recorded === 'reject' ? 'var(--denied-bg)' : 'var(--success-bg)',
                border: `1px solid ${recorded === 'reject' ? 'var(--denied)' : 'var(--success)'}`,
              }}>
                {recorded === 'reject' ? 'Rejected' : recorded === 'edit' ? 'Edited & approved' : 'Approved'}
              </span>
              <button onClick={() => onDecide(approval.id, recorded ?? 'approve', null)}
                style={btn('primary')}>Continue</button>
            </div>
          ) : (
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <button disabled={inFlight} onClick={() => onDecide(approval.id, 'reject', null)}
                style={btn('ghost')}>Reject</button>
              <button disabled={inFlight || !dirty} onClick={() => onDecide(approval.id, 'edit', args)}
                style={btn(dirty ? 'secondary' : 'disabled')}>Edit &amp; Approve</button>
              <button disabled={inFlight} onClick={() => onDecide(approval.id, 'approve', null)}
                style={btn('primary')}>Approve</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function btn(kind: 'primary' | 'secondary' | 'ghost' | 'disabled'): React.CSSProperties {
  const base: React.CSSProperties = {
    fontSize: 13.5, fontWeight: 600, padding: '8px 16px',
    borderRadius: 'var(--r-input)', cursor: kind === 'disabled' ? 'not-allowed' : 'pointer',
    transition: 'background var(--t-micro), border-color var(--t-micro)',
    fontFamily: 'var(--font-body)',
  }
  if (kind === 'primary') return { ...base, background: 'var(--accent)', color: 'var(--accent-ink)', border: '1px solid var(--accent)' }
  if (kind === 'secondary') return { ...base, background: 'var(--surface)', color: 'var(--accent)', border: '1px solid var(--accent)' }
  if (kind === 'disabled') return { ...base, background: 'var(--surface)', color: 'var(--ink-300)', border: '1px solid var(--line)', opacity: 0.7 }
  return { ...base, background: 'transparent', color: 'var(--ink-600)', border: '1px solid var(--line)' }
}
