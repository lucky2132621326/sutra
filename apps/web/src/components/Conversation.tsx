/**
 * The conversation pane.
 *
 * Until this existed the app could only run one hardcoded query — impressive
 * to watch, impossible to interrogate. A judge's first instinct is to type
 * their own question, and "you can't" is the wrong answer to give them.
 *
 * The assistant turn is not a blob of text. The backend hands us three
 * separable things — prose, a "not completed" list, and a structured action
 * ledger — and each gets its own treatment, because conflating them is
 * exactly how a system ends up quietly claiming it did something it didn't.
 */
import { useEffect, useRef } from 'react'

import { useStore } from '../state/store'
import { ActionLedger } from './ActionLedger'
import { EvidenceCard } from './EvidenceCard'
import { VerdictCard, findEligibility } from './VerdictCard'

const SUGGESTIONS = [
  {
    label: 'The hero run',
    text:
      "I'm a third-year CSE student. Am I eligible for the Google internship? " +
      'If yes, register me for the placement workshop, add it to my calendar, ' +
      'and remind me an hour before.',
  },
  {
    label: 'Attendance rule',
    text: 'What attendance do I need to sit for exams, and am I currently short in anything?',
  },
  {
    label: 'Eligibility only',
    text: 'Am I eligible for the Google internship?',
  },
]

export function Conversation({ onSend }: { onSend: (text: string) => void }) {
  const turns = useStore((s) => s.turns)
  const draft = useStore((s) => s.draft)
  const setDraft = useStore((s) => s.setDraft)
  const sending = useStore((s) => s.sending)
  const mode = useStore((s) => s.mode)
  const backendUp = useStore((s) => s.backendUp)
  const composerFocusNonce = useStore((s) => s.composerFocusNonce)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const latestTurnText = turns.at(-1)?.text

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, latestTurnText])

  useEffect(() => {
    if (composerFocusNonce > 0) inputRef.current?.focus()
  }, [composerFocusNonce])

  const liveBlocked = mode === 'live' && !backendUp
  const canSend = draft.trim().length > 0 && !sending && !liveBlocked

  const submit = () => {
    if (!canSend) return
    const text = draft.trim()
    setDraft('')
    onSend(text)
  }

  return (
    <section
      aria-label="Conversation"
      style={{
        display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%',
        background: 'var(--surface)', borderRight: '1px solid var(--line)',
      }}
    >
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '18px 18px 8px' }}>
        {turns.length === 0 ? (
          <Welcome onPick={(t) => { setDraft(t); inputRef.current?.focus() }} />
        ) : (
          turns.map((t) => (t.role === 'user' ? <UserTurn key={t.id} text={t.text} /> : <AssistantTurn key={t.id} id={t.id} />))
        )}
        <div ref={endRef} />
      </div>

      <div style={{ borderTop: '1px solid var(--line)', padding: 12, background: 'var(--surface)' }}>
        {liveBlocked && (
          <div style={{
            fontSize: 12, color: 'var(--degraded)', background: 'var(--degraded-bg)',
            padding: '7px 10px', borderRadius: 'var(--r-chip)', marginBottom: 8,
          }}>
            Backend is not reachable. Switch to Replay to show a recorded run.
          </div>
        )}
        <div style={{
          display: 'flex', gap: 8, alignItems: 'flex-end',
          border: '1px solid var(--line-strong)', borderRadius: 'var(--r-card)',
          padding: 8, background: 'var(--surface)',
        }}>
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line — the convention
              // everyone already has muscle memory for.
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
            }}
            rows={2}
            placeholder={mode === 'replay'
              ? 'Ask anything — or press Play to replay a recorded run…'
              : 'Ask anything…'}
            aria-label="Ask a question"
            style={{
              flex: 1, resize: 'none', border: 'none', outline: 'none', background: 'transparent',
              font: 'inherit', fontSize: 14, lineHeight: '20px', color: 'var(--ink-900)',
              fontFamily: 'var(--font-body)', maxHeight: 120,
            }}
          />
          <button
            onClick={submit}
            disabled={!canSend}
            aria-label="Send"
            style={{
              border: 'none', borderRadius: 'var(--r-input)', cursor: canSend ? 'pointer' : 'not-allowed',
              background: canSend ? 'var(--accent)' : 'var(--surface-sunken)',
              color: canSend ? 'var(--accent-ink)' : 'var(--ink-300)',
              padding: '8px 14px', fontSize: 13, fontWeight: 700,
              fontFamily: 'var(--font-body)', transition: 'background var(--t-micro)',
            }}
          >
            {sending ? '…' : 'Send'}
          </button>
        </div>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginTop: 6, fontSize: 11, color: 'var(--ink-300)',
        }}>
          <span>Enter to send · Shift+Enter for a new line</span>
          {mode === 'replay' && <span>Replay mode — sending switches to live</span>}
        </div>
      </div>
    </section>
  )
}

function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div style={{ paddingTop: 8 }}>
      <h1 className="font-display" style={{ fontSize: 26, lineHeight: '32px', margin: '0 0 8px' }}>
        Ask the campus anything.
      </h1>
      <p style={{ fontSize: 14, lineHeight: '21px', color: 'var(--ink-600)', margin: '0 0 18px' }}>
        Five specialist agents plan together, check each other's work against real
        records, and stop for your approval before anything is written.
      </p>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Try one</div>
      <div style={{ display: 'grid', gap: 8 }}>
        {SUGGESTIONS.map((s) => (
          <button
            key={s.label}
            onClick={() => onPick(s.text)}
            style={{
              textAlign: 'left', padding: '10px 12px', cursor: 'pointer',
              border: '1px solid var(--line)', borderRadius: 'var(--r-card)',
              background: 'var(--surface-sunken)', fontFamily: 'var(--font-body)',
              transition: 'border-color var(--t-micro)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent)' }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--line)' }}
          >
            <div className="eyebrow" style={{ color: 'var(--accent)', marginBottom: 3 }}>{s.label}</div>
            <div style={{ fontSize: 12.5, lineHeight: '18px', color: 'var(--ink-600)' }}>{s.text}</div>
          </button>
        ))}
      </div>
    </div>
  )
}

function UserTurn({ text }: { text: string }) {
  return (
    <div style={{ marginBottom: 18, display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{
        maxWidth: '88%', background: 'var(--accent-weak)', color: 'var(--ink-900)',
        border: '1px solid var(--line)', borderRadius: 'var(--r-card)',
        padding: '10px 13px', fontSize: 13.5, lineHeight: '20px',
      }}>
        {text}
      </div>
    </div>
  )
}

/**
 * The live assistant turn. Reads straight from RunState rather than from the
 * stored turn text, so it fills in progressively as the run streams — the
 * status line, then the evidence, then the answer and its receipts.
 */
function AssistantTurn({ id }: { id: string }) {
  const run = useStore((s) => s.run)
  const turns = useStore((s) => s.turns)
  const turn = turns.find((t) => t.id === id)
  const isLatest = turns.filter((t) => t.role === 'assistant').at(-1)?.id === id

  // Older turns keep their frozen text; only the newest tracks live state.
  if (!isLatest) {
    return (
      <div style={{ marginBottom: 22 }}>
        <div style={{ fontSize: 14, lineHeight: '22px', color: 'var(--ink-900)' }}>{turn?.text}</div>
      </div>
    )
  }

  // A turn that has been resolved with its own text but produced no run —
  // the request never reached the backend — must show that text. Rendering
  // purely off live run state left it spinning on "Thinking" forever, which
  // is the one thing worse than an error: a UI that looks busy but is not.
  if (!turn?.pending && turn?.text && !run.answer) {
    return (
      <div style={{ marginBottom: 22 }}>
        <div style={{
          border: '1px solid var(--degraded)', background: 'var(--degraded-bg)',
          color: 'var(--degraded)', borderRadius: 'var(--r-card)',
          padding: 12, fontSize: 13, lineHeight: '19px',
        }}>
          {turn.text}
        </div>
      </div>
    )
  }

  const evidenced = run.conflicts.filter((c) => c.evidence)
  const eligibility = findEligibility(run)

  return (
    <div style={{ marginBottom: 22 }}>
      {!run.answer && !run.fatalError && <Working />}

      {eligibility && <VerdictCard result={eligibility} />}
      {evidenced.map((c, i) => <EvidenceCard key={i} conflict={c} />)}

      {run.fatalError && (
        <div style={{
          border: '1px solid var(--danger)', background: 'var(--danger-bg)',
          color: 'var(--danger)', borderRadius: 'var(--r-card)', padding: 12, fontSize: 13,
        }}>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Run failed</div>
          {run.fatalError}
        </div>
      )}

      {run.answer && (
        <div style={{ fontSize: 14, lineHeight: '22px', color: 'var(--ink-900)' }}>
          {run.answer}
        </div>
      )}

      {run.notCompleted.length > 0 && (
        <div style={{
          marginTop: 12, padding: '10px 12px', borderRadius: 'var(--r-card)',
          background: 'var(--degraded-bg)', color: 'var(--degraded)',
          fontSize: 12.5, lineHeight: '18px',
        }}>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Ran degraded</div>
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {run.notCompleted.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}

      {run.actions.length > 0 && <ActionLedger actions={run.actions} />}
    </div>
  )
}

/** A status line that reports what is actually happening, not a spinner. */
function Working() {
  const run = useStore((s) => s.run)
  const running = Object.values(run.steps).filter((s) => s.status === 'running')
  const awaiting = run.status === 'awaiting-approval' || run.approvalQueue.length > 0

  const label = awaiting
    ? 'Waiting for your decision'
    : running.length > 1
      ? `${running.length} agents working in parallel`
      : running.length === 1
        ? `${running[0].agent} agent — ${running[0].task}`
        : run.plan
          ? 'Planning'
          : 'Thinking'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
      fontSize: 13, color: awaiting ? 'var(--approval)' : 'var(--ink-400)',
    }}>
      <span className="pulse-dot" style={{ background: 'currentColor' }} />
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
    </div>
  )
}
