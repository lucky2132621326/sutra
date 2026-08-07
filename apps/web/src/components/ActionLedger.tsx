/**
 * "What it actually did", rendered from the backend's structured ledger.
 *
 * Deliberately NOT parsed out of the answer prose. The prose is written by a
 * language model and has, historically, claimed a registration succeeded on a
 * run where the human rejected it. These rows come from `run.finished.actions`,
 * which the approval gate writes as it resolves each action — so a row saying
 * DONE means a tool returned a receipt, and NOT DONE means nothing was written.
 */
import type { ActionOutcome, ActionRecord } from '../state/runReducer'

const STYLE: Record<ActionOutcome, { label: string; color: string; bg: string; glyph: string }> = {
  executed:     { label: 'Done',      color: 'var(--success)',  bg: 'var(--success-bg)',  glyph: '✓' },
  not_executed: { label: 'Not done',  color: 'var(--denied)',   bg: 'var(--denied-bg)',   glyph: '✕' },
  cancelled:    { label: 'Cancelled', color: 'var(--ink-400)',  bg: 'var(--surface-sunken)', glyph: '−' },
  failed:       { label: 'Failed',    color: 'var(--danger)',   bg: 'var(--danger-bg)',   glyph: '!' },
  skipped:      { label: 'Skipped',   color: 'var(--ink-400)',  bg: 'var(--surface-sunken)', glyph: '·' },
}

/**
 * Say why, in English.
 *
 * The backend's `error` field is written for a log, not a reader — a cancelled
 * step arrives as `depends on ['s2']`, which is a Python list repr leaking
 * onto a projector. Translate the shapes we know and pass anything else
 * through unchanged rather than swallowing it.
 */
function reason(a: ActionRecord): string {
  if (a.outcome === 'not_executed') {
    return a.error?.includes('already declined')
      ? 'You already declined this earlier, so it was not proposed again.'
      : 'You declined this, so nothing was written.'
  }
  if (a.outcome === 'cancelled') {
    const deps = [...(a.error ?? '').matchAll(/'([^']+)'/g)].map((m) => m[1])
    return deps.length
      ? `Skipped because ${deps.join(' and ')} did not go ahead.`
      : 'Skipped because a step it depended on did not go ahead.'
  }
  return a.error ?? ''
}

export function ActionLedger({ actions }: { actions: ActionRecord[] }) {
  const wrote = actions.filter((a) => a.outcome === 'executed').length

  return (
    <section
      aria-label="Actions taken"
      style={{
        marginTop: 14, border: '1px solid var(--line)', borderRadius: 'var(--r-card)',
        overflow: 'hidden', background: 'var(--surface)',
      }}
    >
      <header style={{
        display: 'flex', alignItems: 'baseline', gap: 8, padding: '8px 12px',
        background: 'var(--surface-sunken)', borderBottom: '1px solid var(--line)',
      }}>
        <span className="eyebrow">Actions taken</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--ink-400)' }}>
          {wrote === 0
            ? 'nothing was written'
            : `${wrote} write${wrote > 1 ? 's' : ''} committed`}
        </span>
      </header>

      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {actions.map((a, i) => {
          const st = STYLE[a.outcome] ?? STYLE.skipped
          return (
            <li
              key={a.approvalId ?? `${a.stepId}-${i}`}
              style={{
                display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 10,
                padding: '10px 12px',
                borderTop: i === 0 ? 'none' : '1px solid var(--line)',
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 20, height: 20, borderRadius: 'var(--r-pill)',
                  background: st.bg, color: st.color,
                  display: 'grid', placeItems: 'center',
                  fontSize: 11, fontWeight: 700, marginTop: 1,
                }}
              >
                {st.glyph}
              </span>

              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{
                    fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em',
                    textTransform: 'uppercase', color: st.color,
                  }}>
                    {st.label}
                  </span>
                  {a.agent && (
                    <span style={{ fontSize: 11, color: 'var(--ink-300)' }}>{a.agent} agent</span>
                  )}
                </div>

                <div style={{ fontSize: 12.5, lineHeight: '18px', color: 'var(--ink-900)', marginTop: 2 }}>
                  {a.description}
                </div>

                {a.receiptId && (
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 4 }}>
                    receipt {a.receiptId}
                  </div>
                )}
                {(a.error || a.outcome === 'not_executed') && !a.receiptId && (
                  <div style={{ fontSize: 11.5, color: 'var(--ink-400)', marginTop: 3 }}>
                    {reason(a)}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
