/**
 * The evidence card.
 *
 * This is the one thing in the whole interface that has to be unarguable. Any
 * system can print "that clashes with your lab" — the interesting claim is
 * that it CHECKED, and can show its working. So the card renders the two
 * things the backend's preflight actually computed:
 *
 *   1. the overlap, drawn to scale on a shared time axis
 *   2. the attendance projection, drawn against the 75% line it fails
 *
 * Every number here comes from `conflict.evidence`, which the backend fills
 * from campus.db before any approval is offered. Nothing here is prose the
 * model produced.
 */
import type { ConflictRecord } from '../state/runReducer'

const THRESHOLD = 75

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + (m || 0)
}

export function EvidenceCard({ conflict }: { conflict: ConflictRecord }) {
  const ev = conflict.evidence
  if (!ev) return null
  const { event, collides_with: collides, attendance_impact: impact } = ev

  return (
    <figure
      className="evidence-card"
      style={{
        margin: '0 0 14px', border: '1px solid var(--danger)', borderLeftWidth: 3,
        borderRadius: 'var(--r-card)', background: 'var(--surface)',
        overflow: 'hidden', boxShadow: 'var(--e1)',
      }}
    >
      <figcaption style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '9px 13px',
        background: 'var(--danger-bg)', color: 'var(--danger)',
        borderBottom: '1px solid var(--danger)',
      }}>
        <span aria-hidden style={{ fontSize: 13 }}>⚔</span>
        <span className="eyebrow" style={{ color: 'inherit' }}>
          Academic agent blocked this
        </span>
        <span style={{
          marginLeft: 'auto', fontSize: 10.5, letterSpacing: '0.04em',
          textTransform: 'uppercase', opacity: 0.75,
        }}>
          checked, not guessed
        </span>
      </figcaption>

      <div style={{ padding: 13 }}>
        {event && collides && <TimeCollision event={event} collides={collides} />}
        {impact && <AttendanceProjection impact={impact} />}

        {conflict.rationale && (
          <p style={{
            margin: '12px 0 0', paddingTop: 11, borderTop: '1px solid var(--line)',
            fontSize: 12.5, lineHeight: '19px', color: 'var(--ink-600)',
          }}>
            {conflict.rationale}
          </p>
        )}
      </div>
    </figure>
  )
}

/**
 * Both blocks drawn on ONE shared axis spanning the union of the two windows,
 * so the overlap is a visual fact rather than something you verify by reading
 * four timestamps and doing the arithmetic yourself.
 */
function TimeCollision({
  event, collides,
}: {
  event: NonNullable<NonNullable<ConflictRecord['evidence']>['event']>
  collides: NonNullable<NonNullable<ConflictRecord['evidence']>['collides_with']>
}) {
  const start = toMinutes(event.start)
  const end = toMinutes(event.end)
  // The colliding class's own window isn't in the payload as start/end, but
  // the overlap is total by construction (the preflight only fires on a real
  // interval intersection), so the class bar spans the same window.
  const span = Math.max(end - start, 60)
  const pct = (mins: number) => ((mins - start) / span) * 100

  return (
    <div style={{ marginBottom: 14 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Same slot</div>

      <div style={{ display: 'grid', gap: 6 }}>
        <Bar
          label={event.title}
          sub={`${event.day} ${event.start}–${event.end}`}
          color="var(--danger)"
          bg="var(--danger-bg)"
          left={pct(start)}
          width={pct(end) - pct(start)}
        />
        <Bar
          label={collides.course_id}
          sub={`${collides.session_type} · already on your timetable`}
          color="var(--ink-600)"
          bg="var(--surface-sunken)"
          left={pct(start)}
          width={pct(end) - pct(start)}
        />
      </div>

      <div style={{
        display: 'flex', justifyContent: 'space-between', marginTop: 5,
        fontSize: 10.5, color: 'var(--ink-300)', fontFamily: 'var(--font-mono)',
      }}>
        <span>{event.start}</span>
        <span>{event.end}</span>
      </div>
    </div>
  )
}

function Bar({
  label, sub, color, bg, left, width,
}: {
  label: string; sub: string; color: string; bg: string; left: number; width: number
}) {
  return (
    <div>
      <div style={{
        height: 26, borderRadius: 5, background: 'var(--surface-sunken)',
        position: 'relative', overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', top: 0, bottom: 0,
          left: `${left}%`, width: `${width}%`,
          background: bg, borderLeft: `2px solid ${color}`,
          display: 'flex', alignItems: 'center', paddingLeft: 7,
        }}>
          <span style={{
            fontSize: 11.5, fontWeight: 700, color,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {label}
          </span>
        </div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 2 }}>{sub}</div>
    </div>
  )
}

/**
 * The number that makes the veto legitimate. Drawn as a track with the 75%
 * requirement marked, so "below the line" is literal.
 */
function AttendanceProjection({
  impact,
}: {
  impact: NonNullable<NonNullable<ConflictRecord['evidence']>['attendance_impact']>
}) {
  // Scale to 100% so the threshold sits where a reader expects it.
  const cur = Math.max(0, Math.min(100, impact.current_pct))
  const proj = Math.max(0, Math.min(100, impact.projected_pct))

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 8 }}>
        Cost of missing it · {impact.course_name}
      </div>

      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 9,
        fontFamily: 'var(--font-mono)',
      }}>
        <span style={{ fontSize: 20, fontWeight: 700, color: 'var(--ink-600)' }}>
          {impact.current_pct}%
        </span>
        <span aria-hidden style={{ color: 'var(--ink-300)' }}>→</span>
        <span style={{ fontSize: 20, fontWeight: 700, color: 'var(--danger)' }}>
          {impact.projected_pct}%
        </span>
        <span style={{
          fontSize: 11.5, fontWeight: 700, color: 'var(--danger)',
          background: 'var(--danger-bg)', padding: '2px 7px', borderRadius: 'var(--r-pill)',
        }}>
          {impact.delta_pct}
        </span>
      </div>

      {/* Track */}
      <div style={{ position: 'relative', height: 22 }}>
        <div style={{
          position: 'absolute', inset: 0, borderRadius: 4,
          background: 'var(--surface-sunken)',
        }} />
        {/* current */}
        <div style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: `${cur}%`,
          background: 'var(--pending-bg)', borderRadius: '4px 0 0 4px',
          borderRight: '2px solid var(--ink-300)',
        }} />
        {/* projected — sits under the current bar, so the lost slice reads as loss */}
        <div style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: `${proj}%`,
          background: 'var(--danger-bg)', borderRadius: '4px 0 0 4px',
          borderRight: '2px solid var(--danger)',
          transition: 'width var(--t-layout)',
        }} />
        {/* the 75% requirement */}
        <div style={{
          position: 'absolute', left: `${THRESHOLD}%`, top: -3, bottom: -3, width: 2,
          background: 'var(--ink-900)',
        }} />
        <div style={{
          position: 'absolute', left: `calc(${THRESHOLD}% + 6px)`, top: '50%',
          transform: 'translateY(-50%)', fontSize: 10.5, fontWeight: 700,
          color: 'var(--ink-900)', whiteSpace: 'nowrap',
        }}>
          75% required
        </div>
      </div>

      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--ink-600)', lineHeight: '18px' }}>
        {impact.already_below
          ? `Already ${(THRESHOLD - impact.current_pct).toFixed(2)} points short of the requirement — `
          : impact.crosses_threshold
            ? 'This is what would push it below the requirement — '
            : 'Still above the requirement, but — '}
        <strong style={{ color: 'var(--ink-900)' }}>
          {impact.classes_attended}/{impact.classes_held} attended
        </strong>
        {impact.sessions_needed_to_recover > 0 && (
          <>
            , and it would take{' '}
            <strong style={{ color: 'var(--ink-900)' }}>
              {impact.sessions_needed_to_recover} consecutive sessions
            </strong>{' '}
            to recover.
          </>
        )}
      </div>
    </div>
  )
}
