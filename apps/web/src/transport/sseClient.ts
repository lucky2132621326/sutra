/**
 * Live SSE client.
 *
 * Deliberately NOT EventSource, for two independent reasons verified against
 * the backend:
 *  1. Every frame is NAMED (`event: node.started`), so EventSource's bare
 *     `onmessage` receives nothing at all.
 *  2. The stream ends by socket close. EventSource auto-reconnects, and
 *     apps/api/bus.py replays the FULL history to every new subscriber — so
 *     it would loop forever, re-delivering the same run.
 *
 * Reconnection here is therefore manual and explicit; the reducer's event-id
 * dedupe makes a deliberate reconnect lossless.
 */
import type { AgentEvent } from '../types/events'
import type { InboxResponse } from '../types/inbox'
import type { Locale } from '../i18n'
import type { CalendarResponse } from '../types/calendar'
import type { EventTransport, TransportCallbacks } from './types'

export class SSEClient implements EventTransport {
  private controller: AbortController | null = null
  private stopped = false

  constructor(private runId: string, private cb: TransportCallbacks, private base = '') {}

  start() {
    this.stopped = false
    void this.run()
  }

  stop() {
    this.stopped = true
    this.controller?.abort()
    this.controller = null
  }

  private async run() {
    this.cb.onStatus('connecting')
    this.controller = new AbortController()
    try {
      const res = await fetch(`${this.base}/stream/${this.runId}`, {
        signal: this.controller.signal,
        headers: { Accept: 'text/event-stream' },
        cache: 'no-store',
      })
      if (!res.ok || !res.body) throw new Error(`stream ${res.status}`)
      this.cb.onStatus('streaming')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        // stream:true so a multi-byte character split across a chunk boundary
        // is handled rather than corrupted.
        buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')
        let split: number
        while ((split = buffer.indexOf('\n\n')) !== -1) {
          this.handleFrame(buffer.slice(0, split))
          buffer = buffer.slice(split + 2)
        }
      }
      if (buffer.trim()) this.handleFrame(buffer)
      this.cb.onStatus('closed')
    } catch (err) {
      if ((err as Error)?.name === 'AbortError' || this.stopped) return
      this.cb.onStatus('error')
    }
  }

  private handleFrame(frame: string) {
    const dataLines: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith(':')) continue           // comment / keepalive
      if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
      // `event:` is ignored on purpose — the JSON payload already carries `type`.
    }
    if (!dataLines.length) return
    try {
      this.cb.onEvent(JSON.parse(dataLines.join('\n')) as AgentEvent)
    } catch {
      // One malformed frame must never kill the stream.
    }
  }
}

/**
 * run_id and thread_id are different things: run_id scopes ONE execution (and
 * its event stream), thread_id scopes the CONVERSATION (checkpoint + memory).
 * Send the thread_id back on the next turn to keep context; each turn still
 * gets a fresh run_id and therefore a clean stream.
 */
export async function postChat(
  message: string, studentId: string, role: string,
  threadId: string | null = null, locale: Locale = 'en', base = '', signal?: AbortSignal,
): Promise<{ runId: string; threadId: string }> {
  const res = await fetch(`${base}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, student_id: studentId, role, thread_id: threadId, locale }),
    signal,
  })
  if (!res.ok) throw new Error(`chat ${res.status}`)
  const json = await res.json()
  return { runId: json.run_id as string, threadId: json.thread_id as string }
}

export async function postApprove(
  runId: string, threadId: string | null, approvalId: string,
  decision: 'approve' | 'reject' | 'edit',
  editedArgs: Record<string, unknown> | null = null,
  base = '',
): Promise<void> {
  const res = await fetch(`${base}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      run_id: runId, thread_id: threadId, approval_id: approvalId,
      decision, edited_args: editedArgs,
    }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function setChaos(service: string, mode: string, base = '') {
  const res = await fetch(`${base}/admin/chaos`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service, mode }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function resetChaos(base = '') {
  await fetch(`${base}/admin/chaos/reset`, { method: 'POST' })
}

export async function health(base = ''): Promise<boolean> {
  try {
    const res = await fetch(`${base}/health`, { cache: 'no-store' })
    return res.ok
  } catch {
    return false
  }
}

export async function getInbox(studentId: string, base = ''): Promise<InboxResponse> {
  const res = await fetch(`${base}/inbox/${encodeURIComponent(studentId)}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`inbox ${res.status}`)
  return res.json() as Promise<InboxResponse>
}

export async function getCalendar(
  studentId: string, start: string, end: string, base = '',
): Promise<CalendarResponse> {
  const query = new URLSearchParams({ start, end })
  const res = await fetch(
    `${base}/calendar/${encodeURIComponent(studentId)}?${query.toString()}`,
    { cache: 'no-store' },
  )
  if (!res.ok) throw new Error(`calendar ${res.status}`)
  return res.json() as Promise<CalendarResponse>
}
