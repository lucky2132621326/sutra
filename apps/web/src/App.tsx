import { useCallback, useEffect, useRef } from 'react'

import { ApprovalModal } from './components/ApprovalModal'
import { Conversation } from './components/Conversation'
import { MissionGallery } from './components/MissionGallery'
import { NodeInspector } from './components/NodeInspector'
import { PlanCanvas } from './components/dag/PlanCanvas'
import { Citations, Memory, Telemetry, Timeline } from './components/Rail'
import { RunPresentation } from './components/RunPresentation'
import { RunScore } from './components/score/RunScore'
import { useStore } from './state/store'
import { ReplaySource, loadFixture } from './transport/replaySource'
import { SSEClient, health, postApprove, postChat } from './transport/sseClient'

const FIXTURES = [
  { file: 'golden_capabilities.jsonl', label: 'Full platform showcase · 24 tools' },
  { file: 'golden_conflict.jsonl', label: 'Conflict & arbitration' },
  { file: 'golden_clean.jsonl', label: 'Read-only question' },
  { file: 'golden_chaos.jsonl', label: 'Failure recovery' },
  { file: 'golden_reject.jsonl', label: 'Human rejects' },
]

const STUDENT = '1602-23-733-042'

export default function App() {
  const s = useStore()
  const replayRef = useRef<ReplaySource | null>(null)
  const sseRef = useRef<SSEClient | null>(null)
  const chatAbortRef = useRef<AbortController | null>(null)
  // Every transport callback captures the epoch it belongs to. Starting or
  // stopping a run advances it, so a late frame from an abandoned request can
  // never unlock, overwrite or otherwise interfere with the next turn.
  const transportEpochRef = useRef(0)

  useEffect(() => {
    // Precedence: what this user last chose here, then whatever the host page
    // already stamped on <html> (embedding hosts set data-theme to match the
    // viewer), then the OS preference. Defaulting straight to 'light' meant a
    // dark-mode viewer got a white flash and a toggle that appeared stuck.
    const saved = localStorage.getItem('sutra-theme') as 'light' | 'dark' | null
    const stamped = document.documentElement.getAttribute('data-theme') as 'light' | 'dark' | null
    const os = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    s.setTheme(saved ?? stamped ?? os)

    // An embedding host may flip data-theme at any time (its own theme
    // toggle). Mirror that into our state so the header button doesn't drift
    // out of sync with the page it is describing.
    const el = document.documentElement
    const observer = new MutationObserver(() => {
      const now = el.getAttribute('data-theme')
      if ((now === 'light' || now === 'dark') && now !== useStore.getState().theme) {
        useStore.getState().setTheme(now)
      }
    })
    observer.observe(el, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    let alive = true
    const poll = async () => { if (alive) useStore.getState().setBackendUp(await health()) }
    void poll()
    const t = setInterval(poll, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const stopAll = useCallback(() => {
    transportEpochRef.current += 1
    chatAbortRef.current?.abort()
    chatAbortRef.current = null
    replayRef.current?.stop()
    replayRef.current = null
    sseRef.current?.stop()
    sseRef.current = null
  }, [])

  const startReplay = useCallback(async (file: string) => {
    stopAll()
    const st = useStore.getState()
    st.setMode('replay')
    st.setSending(false)
    st.setLiveRunId(null)
    // Replays are standalone demonstrations. If they replace an in-flight
    // live turn, do not reuse that abandoned checkpoint on the next question.
    st.setThreadId(null)
    st.resetRun()
    const events = await loadFixture(file)
    st.loadEvents(events)
    // Playing a run is the explicit request to inspect it. The ordinary app
    // still opens on the mission gallery; this switches only after the user
    // presses Play.
    st.setCenterView('score')
    st.setPresentationMode(true)

    // Seed the transcript from the run itself, so replay and live produce the
    // same shape of conversation rather than two different-looking modes.
    const planned = events.find((e) => e.type === 'plan.created')
    const goal = (planned?.payload as { goal?: string } | undefined)?.goal
    st.addTurn({ role: 'user', text: goal || 'Recorded run', runId: st.run.runId })
    st.addTurn({ role: 'assistant', text: '', runId: st.run.runId, pending: true })

    const src = new ReplaySource(
      events,
      {
        onEvent: (e) => useStore.getState().ingest(e),
        onStatus: (status) => useStore.getState().setStatus(status),
        onProgress: (i, total) => useStore.getState().setProgress(i, total),
        onAwaitApproval: (id) => useStore.getState().setActiveApproval(id),
      },
      useStore.getState().pacing,
      (id) => useStore.getState().run.resolvedApprovalIds.includes(id),
    )
    src.setSpeed(useStore.getState().speed)
    replayRef.current = src
    src.start()
  }, [stopAll])

  /** The whole point of the composer: any question, live, no hardcoding. */
  const send = useCallback(async (text: string) => {
    stopAll()
    const epoch = transportEpochRef.current
    const controller = new AbortController()
    chatAbortRef.current = controller
    const st = useStore.getState()
    st.setMode('live')
    st.setSending(true)
    st.resetRun()
    st.addTurn({ role: 'user', text, runId: null })
    st.addTurn({ role: 'assistant', text: '', runId: null, pending: true })

    try {
      const { runId, threadId } = await postChat(
        text, STUDENT, 'student', st.threadId, '', controller.signal,
      )
      if (transportEpochRef.current !== epoch) return
      chatAbortRef.current = null
      useStore.getState().setLiveRunId(runId)
      useStore.getState().setThreadId(threadId)

      const client = new SSEClient(runId, {
        onEvent: (e) => {
          if (transportEpochRef.current !== epoch) return
          const store = useStore.getState()
          store.ingest(e)
          if (e.type === 'approval.requested') {
            const id = String((e.payload as { id?: string }).id ?? '')
            if (id && !store.run.resolvedApprovalIds.includes(id)) store.setActiveApproval(id)
          }
          if (e.type === 'run.finished') {
            const st2 = useStore.getState()
            const pending = st2.turns.filter((t) => t.role === 'assistant' && t.pending).at(-1)
            if (pending) st2.resolveTurn(pending.id, st2.run.answer ?? '')
            st2.setSending(false)
          }
          // A terminal graph error is just as final as run.finished. Benign
          // per-agent degradation notices include an agent/detail and must not
          // release the composer while the graph is still working.
          if (e.type === 'run.error' && e.agent == null && e.payload.detail === undefined) {
            const st2 = useStore.getState()
            const pending = st2.turns.filter((t) => t.role === 'assistant' && t.pending).at(-1)
            if (pending) st2.resolveTurn(pending.id, '')
            st2.setSending(false)
          }
        },
        onStatus: (status) => {
          if (transportEpochRef.current !== epoch) return
          const st2 = useStore.getState()
          st2.setStatus(status)
          if (status === 'closed' || status === 'error') {
            st2.setSending(false)
            // A healthy stream closes after run.finished. Anything else would
            // otherwise leave the newest assistant turn saying "Thinking"
            // forever even though there is no transport left to finish it.
            if (!st2.run.runComplete && !st2.run.fatalError) {
              const pending = st2.turns.filter((t) => t.role === 'assistant' && t.pending).at(-1)
              if (pending) {
                st2.resolveTurn(
                  pending.id,
                  status === 'error'
                    ? 'The live event stream disconnected before the run completed. Your request was not submitted again.'
                    : 'The run ended before a final answer arrived. You can safely try again.',
                )
              }
            }
          }
        },
      })
      sseRef.current = client
      client.start()
    } catch (error) {
      if ((error as Error)?.name === 'AbortError' || transportEpochRef.current !== epoch) return
      const st2 = useStore.getState()
      st2.setStatus('error')
      st2.setSending(false)
      const pending = st2.turns.filter((t) => t.role === 'assistant' && t.pending).at(-1)
      if (pending) {
        st2.resolveTurn(
          pending.id,
          "I couldn't reach the orchestrator, so nothing ran. Start it with " +
          '"python -m uvicorn apps.api.main:app --port 8000", or switch to ' +
          'Replay to show a recorded run.',
        )
      }
    } finally {
      if (chatAbortRef.current === controller) chatAbortRef.current = null
    }
  }, [stopAll])

  const stopLiveRun = useCallback(() => {
    stopAll()
    const st = useStore.getState()
    const pending = st.turns.filter((t) => t.role === 'assistant' && t.pending).at(-1)
    if (pending) st.resolveTurn(pending.id, 'Run stopped. Your draft is still here, so you can edit it or send it again.')
    st.setSending(false)
    st.setStatus('idle')
    st.setLiveRunId(null)
    // The backend has no cancellation endpoint, so any abandoned graph may
    // finish in the background. A fresh thread prevents that old checkpoint
    // from racing with the user's next question.
    st.setThreadId(null)
    st.setActiveApproval(null)
  }, [stopAll])

  const newChat = useCallback(() => {
    stopAll()
    const st = useStore.getState()
    st.clearConversation()
    st.resetRun()
    st.setStatus('idle')
    st.setCenterView('missions')
  }, [stopAll])

  const decide = useCallback(
    async (id: string, decision: 'approve' | 'reject' | 'edit', edited: Record<string, unknown> | null) => {
      const st = useStore.getState()
      st.setApprovalInFlight(true)
      st.setActiveApproval(null)
      try {
        if (st.mode === 'live' && st.liveRunId) {
          await postApprove(st.liveRunId, st.threadId, id, decision, edited)
        } else {
          replayRef.current?.releaseHold()
        }
      } finally {
        useStore.getState().setApprovalInFlight(false)
      }
    }, [])

  const seekReplay = useCallback((index: number) => {
    const st = useStore.getState()
    if (st.mode !== 'replay') return
    // Scrubbing is an inspection gesture, so freeze playback at the requested
    // frame instead of letting the next scheduled event immediately move it.
    replayRef.current?.pause()
    st.seekTo(index)
    replayRef.current?.seek(index)
  }, [])

  const toggleReplayPlayback = useCallback(() => {
    const st = useStore.getState()
    if (st.status === 'streaming') replayRef.current?.pause()
    else replayRef.current?.resume()
  }, [])

  const changeReplaySpeed = useCallback((speed: number) => {
    useStore.getState().setSpeed(speed)
    replayRef.current?.setSpeed(speed)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.code === 'Space') {
        e.preventDefault()
        const st = useStore.getState()
        if (st.status === 'streaming') replayRef.current?.pause()
        else replayRef.current?.resume()
      }
      if (e.key === 'ArrowRight') replayRef.current?.stepForward()
      if (e.key === 'ArrowLeft') replayRef.current?.stepBack()
      if (e.key === 'Escape') {
        const st = useStore.getState()
        if (st.inspectorOpen) st.closeInspector()
        else if (st.presentationMode && !document.fullscreenElement) st.setPresentationMode(false)
      }
      if (e.key.toLowerCase() === 'd') {
        const st = useStore.getState()
        st.setTheme(st.theme === 'light' ? 'dark' : 'light')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const RailBody = { timeline: Timeline, citations: Citations, memory: Memory, telemetry: Telemetry }[s.rail]

  if (s.presentationMode) {
    const label = FIXTURES.find((fixture) => fixture.file === s.fixture)?.label ?? 'Recorded run'
    return (
      <>
        <RunPresentation
          fixtureLabel={label}
          onSeek={seekReplay}
          onBack={() => s.setPresentationMode(false)}
          onTogglePlayback={toggleReplayPlayback}
          onRestart={() => void startReplay(s.fixture)}
          onSpeedChange={changeReplaySpeed}
        />
        <ApprovalModal onDecide={decide} />
      </>
    )
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--paper)' }}>
      <Header
        onReplay={() => void startReplay(s.fixture)}
        onNewChat={newChat}
        onSpeedChange={changeReplaySpeed}
      />

      <div className="cockpit" style={{ flex: 1, minHeight: 0 }}>
        <Conversation onSend={(t) => void send(t)} onCancel={stopLiveRun} />

        <main className="cockpit-canvas" style={{
          position: 'relative', minWidth: 0, overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          borderRight: '1px solid var(--line)',
        }}>
          {s.centerView === 'missions' ? (
            <MissionGallery />
          ) : (
            <>
              <CenterToolbar />
              <div style={{ flex: 1, minHeight: 0 }}>
                {s.centerView === 'score'
                  ? <RunScore onSeek={seekReplay} />
                  : <PlanCanvas />}
              </div>
            </>
          )}
          <NodeInspector />
        </main>

        <aside className="cockpit-rail" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, background: 'var(--surface)' }}>
          <div role="tablist" style={{ display: 'flex', borderBottom: '1px solid var(--line)' }}>
            {(['timeline', 'citations', 'memory', 'telemetry'] as const).map((r) => (
              <button key={r} role="tab" aria-selected={s.rail === r} onClick={() => s.setRail(r)}
                style={{
                  flex: 1, padding: '11px 6px', border: 'none', cursor: 'pointer',
                  background: s.rail === r ? 'var(--surface)' : 'var(--surface-sunken)',
                  borderBottom: s.rail === r ? '2px solid var(--accent)' : '2px solid transparent',
                  color: s.rail === r ? 'var(--ink-900)' : 'var(--ink-400)',
                  fontSize: 11.5, fontWeight: 700, letterSpacing: '0.06em',
                  textTransform: 'uppercase', fontFamily: 'var(--font-body)',
                }}>
                {r}
              </button>
            ))}
          </div>
          <div style={{ flex: 1, minHeight: 0 }}><RailBody /></div>
        </aside>
      </div>

      <ApprovalModal onDecide={decide} />
    </div>
  )
}

function CenterToolbar() {
  const view = useStore((s) => s.centerView)
  const setView = useStore((s) => s.setCenterView)
  return (
    <div style={{
      height: 42, flex: '0 0 42px', display: 'flex', alignItems: 'center', gap: 10,
      padding: '6px 12px', borderBottom: '1px solid var(--line)', background: 'var(--surface)',
    }}>
      <span className="eyebrow" style={{ fontSize: 10.5 }}>Run inspection</span>
      <div role="tablist" aria-label="Centre visualization" style={{
        display: 'flex', gap: 2, padding: 3, marginLeft: 'auto',
        borderRadius: 'var(--r-pill)', background: 'var(--surface-sunken)',
      }}>
        {(['score', 'plan'] as const).map((option) => (
          <button
            key={option}
            role="tab"
            aria-selected={view === option}
            onClick={() => setView(option)}
            style={{
              border: 'none', cursor: 'pointer', borderRadius: 'var(--r-pill)',
              padding: '4px 12px', background: view === option ? 'var(--surface)' : 'transparent',
              boxShadow: view === option ? 'var(--e1)' : 'none',
              color: view === option ? 'var(--ink-900)' : 'var(--ink-400)',
              fontSize: 11.5, fontWeight: 700, fontFamily: 'var(--font-body)',
              textTransform: 'capitalize',
            }}
          >
            {option === 'score' ? 'Run score' : 'Plan DAG'}
          </button>
        ))}
      </div>
    </div>
  )
}

function Header({
  onReplay,
  onNewChat,
  onSpeedChange,
}: {
  onReplay: () => void
  onNewChat: () => void
  onSpeedChange: (speed: number) => void
}) {
  const s = useStore()
  const pct = s.progress.total ? (s.progress.index / s.progress.total) * 100 : 0
  const inspecting = s.centerView !== 'missions'
  const inspectedEvents = s.mode === 'replay' ? s.progress.total : s.events.length

  return (
    <header style={{
      borderBottom: '2px solid var(--line)', background: 'var(--surface)',
      padding: '10px 18px', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span className="font-display" style={{ fontSize: 22 }}>Sūtra</span>
        <span className="eyebrow">Smart Campus Orchestrator</span>
      </div>

      <div style={{ display: 'flex', gap: 2, background: 'var(--surface-sunken)', borderRadius: 'var(--r-pill)', padding: 3 }}>
        {(['replay', 'live'] as const).map((m) => (
          <button key={m} onClick={() => s.setMode(m)}
            style={{
              padding: '5px 14px', borderRadius: 'var(--r-pill)', border: 'none', cursor: 'pointer',
              background: s.mode === m ? 'var(--surface)' : 'transparent',
              boxShadow: s.mode === m ? 'var(--e1)' : 'none',
              fontSize: 12.5, fontWeight: 700, color: s.mode === m ? 'var(--ink-900)' : 'var(--ink-400)',
              fontFamily: 'var(--font-body)', textTransform: 'capitalize',
            }}>{m}</button>
        ))}
      </div>

      {s.mode === 'replay' ? (
        <>
          <select value={s.fixture} onChange={(e) => s.setFixture(e.target.value)} style={selectStyle}
            aria-label="Recorded run">
            {FIXTURES.map((f) => <option key={f.file} value={f.file}>{f.label}</option>)}
          </select>
          <button onClick={onReplay} style={primaryBtn}>
            {s.fixture === 'golden_capabilities.jsonl' ? 'Play full showcase' : 'Play run'}
          </button>
          <select value={s.speed} onChange={(e) => onSpeedChange(Number(e.target.value))} style={selectStyle}
            aria-label="Replay speed">
            {[0.5, 1, 2, 4].map((v) => <option key={v} value={v}>{v}×</option>)}
          </select>
        </>
      ) : (
        <span style={{
          display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5,
          color: s.backendUp ? 'var(--success)' : 'var(--danger)',
        }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'currentColor' }} />
          {s.backendUp ? 'backend up' : 'backend down'}
        </span>
      )}

      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        {s.turns.length > 0 && (
          <button onClick={onNewChat} style={ghostBtn}>
            New chat
          </button>
        )}
        {s.centerView === 'plan' && (
          <>
            <span className="eyebrow tnum">{s.progress.index}/{s.progress.total}</span>
            <span style={{ width: 110, height: 4, background: 'var(--surface-sunken)', borderRadius: 2 }}>
              <span style={{
                display: 'block', height: '100%', width: `${pct}%`,
                background: 'var(--accent)', borderRadius: 2, transition: 'width var(--t-micro)',
              }} />
            </span>
          </>
        )}
        <button
          onClick={() => s.setCenterView(inspecting ? 'missions' : 'score')}
          aria-expanded={inspecting}
          style={inspecting ? ghostBtn : inspectBtn}
        >
          {inspecting ? 'Close inspection' : `Inspect run${inspectedEvents ? ` · ${inspectedEvents}` : ''}`}
        </button>
        <button onClick={() => s.setTheme(s.theme === 'light' ? 'dark' : 'light')} style={ghostBtn}>
          {s.theme === 'light' ? 'Dark' : 'Light'}
        </button>
      </div>
    </header>
  )
}

const selectStyle: React.CSSProperties = {
  fontSize: 12.5, padding: '6px 10px', borderRadius: 'var(--r-input)',
  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--ink-900)',
  fontFamily: 'var(--font-body)',
}
const primaryBtn: React.CSSProperties = {
  fontSize: 12.5, fontWeight: 700, padding: '7px 16px', borderRadius: 'var(--r-input)',
  border: '1px solid var(--accent)', background: 'var(--accent)', color: 'var(--accent-ink)',
  cursor: 'pointer', fontFamily: 'var(--font-body)',
}
const ghostBtn: React.CSSProperties = {
  fontSize: 12.5, fontWeight: 600, padding: '6px 12px', borderRadius: 'var(--r-input)',
  border: '1px solid var(--line)', background: 'transparent', color: 'var(--ink-600)',
  cursor: 'pointer', fontFamily: 'var(--font-body)',
}
const inspectBtn: React.CSSProperties = {
  ...ghostBtn,
  border: '1px solid var(--accent)',
  background: 'var(--accent-weak)',
  color: 'var(--accent)',
}
