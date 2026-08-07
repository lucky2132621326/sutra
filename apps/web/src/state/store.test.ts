import { afterEach, describe, expect, it } from 'vitest'

import type { AgentEvent } from '../types/events'
import { useStore } from './store'

afterEach(() => {
  useStore.setState({ centerView: 'score', mode: 'replay', events: [], progress: { index: 0, total: 0 } })
  useStore.getState().resetRun()
})

describe('cockpit view state', () => {
  it('defaults to the run score and preserves a deliberate Plan DAG switch', () => {
    expect(useStore.getState().centerView).toBe('score')
    useStore.getState().setCenterView('plan')
    expect(useStore.getState().centerView).toBe('plan')
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
})
