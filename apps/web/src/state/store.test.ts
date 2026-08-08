import { afterEach, describe, expect, it } from 'vitest'

import type { AgentEvent } from '../types/events'
import { useStore } from './store'

afterEach(() => {
  useStore.setState({
    centerView: 'missions', presentationMode: false, mode: 'replay', events: [], progress: { index: 0, total: 0 },
    turns: [], draft: '', sending: false, threadId: null, liveRunId: null,
  })
  useStore.getState().resetRun()
})

describe('cockpit view state', () => {
  it('defaults to missions and opens inspection deliberately', () => {
    expect(useStore.getState().centerView).toBe('missions')
    useStore.getState().setCenterView('score')
    expect(useStore.getState().centerView).toBe('score')
    useStore.getState().setCenterView('plan')
    expect(useStore.getState().centerView).toBe('plan')
    useStore.getState().setCenterView('missions')
    expect(useStore.getState().centerView).toBe('missions')
  })

  it('opens and closes the dedicated presentation surface independently', () => {
    expect(useStore.getState().presentationMode).toBe(false)
    useStore.getState().setPresentationMode(true)
    expect(useStore.getState().presentationMode).toBe(true)
    expect(useStore.getState().centerView).toBe('missions')
    useStore.getState().setPresentationMode(false)
    expect(useStore.getState().presentationMode).toBe(false)
  })

  it('buffers live events for the score without duplicating replay fixtures', () => {
    const event: AgentEvent = {
      id: 'live-1', run_id: 'live', ts: 1, type: 'run.started',
      node_id: null, agent: null, payload: {}, latency_ms: null, parent_id: null,
    }
    useStore.setState({ mode: 'live' })
    useStore.getState().ingest(event)
    expect(useStore.getState().events).toEqual([event])
    expect(useStore.getState().progress).toEqual({ index: 1, total: 1 })

    useStore.getState().resetRun()
    useStore.setState({ mode: 'replay', events: [event], progress: { index: 0, total: 1 } })
    useStore.getState().ingest(event)
    expect(useStore.getState().events).toEqual([event])
  })

  it('atomically releases the composer when a live run finishes', () => {
    const finished: AgentEvent = {
      id: 'finished-1', run_id: 'run-1', ts: 2, type: 'run.finished',
      node_id: null, agent: 'synthesizer',
      payload: { answer: 'Done', actions: [], citations: [] },
      latency_ms: null, parent_id: null,
    }
    useStore.setState({ mode: 'live', sending: true })
    useStore.getState().ingest(finished)

    expect(useStore.getState().run.answer).toBe('Done')
    expect(useStore.getState().sending).toBe(false)
  })

  it('makes new chat a complete conversation lifecycle reset', () => {
    useStore.setState({
      turns: [{ id: 't1', role: 'user', text: 'hello', runId: 'run-1', ts: 1 }],
      draft: 'my next question', sending: true, threadId: 'thread-1',
      liveRunId: 'run-1', approvalInFlight: true, status: 'streaming',
    })

    useStore.getState().clearConversation()

    expect(useStore.getState()).toMatchObject({
      turns: [], draft: '', sending: false, threadId: null,
      liveRunId: null, approvalInFlight: false, status: 'idle',
    })
  })
})
