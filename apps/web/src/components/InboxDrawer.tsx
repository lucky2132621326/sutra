import { AlertTriangle, Bell, BookOpen, BriefcaseBusiness, CalendarDays, CheckCircle2, RefreshCw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { useStore } from '../state/store'
import type { InboxItem, InboxResponse } from '../types/inbox'

const READ_KEY = 'sutra-inbox-read'

export function InboxDrawer({
  data, loading, error, onClose, onRefresh,
}: {
  data: InboxResponse | null
  loading: boolean
  error: boolean
  onClose: () => void
  onRefresh: () => void
}) {
  const setDraft = useStore((s) => s.setDraft)
  const requestComposerFocus = useStore((s) => s.requestComposerFocus)
  const [filter, setFilter] = useState<'all' | 'attention'>('all')
  const [read, setRead] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) ?? '[]') as string[]) }
    catch { return new Set() }
  })
  const items = useMemo(() => (
    data?.items.filter((item) => filter === 'all' || item.severity === 'urgent' || item.severity === 'warning') ?? []
  ), [data, filter])

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [onClose])

  const markRead = (id: string) => {
    const next = new Set(read).add(id)
    setRead(next)
    localStorage.setItem(READ_KEY, JSON.stringify([...next]))
  }
  const markAllRead = () => {
    const next = new Set([...(data?.items.map((item) => item.id) ?? []), ...read])
    setRead(next)
    localStorage.setItem(READ_KEY, JSON.stringify([...next]))
  }
  const act = (item: InboxItem) => {
    markRead(item.id)
    if (item.action_prompt) {
      setDraft(item.action_prompt)
      requestComposerFocus()
      onClose()
    }
  }

  return (
    <div className="inbox-layer" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <aside className="inbox-drawer" role="dialog" aria-modal="true" aria-label="Campus inbox">
        <div className="inbox-head">
          <div>
            <div className="eyebrow" style={{ color: 'var(--accent)', marginBottom: 3 }}>Personal attention queue</div>
            <h2 className="font-display" style={{ margin: 0, fontSize: 25 }}>Inbox</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close inbox"><X size={19} /></button>
        </div>

        <div className="inbox-summary">
          <div><strong>{data?.attention_count ?? 0}</strong><span>need attention</span></div>
          <div><strong>{data?.items.length ?? 0}</strong><span>campus updates</span></div>
          <button className="icon-button" onClick={onRefresh} disabled={loading} aria-label="Refresh inbox">
            <RefreshCw size={16} className={loading ? 'spin' : ''} />
          </button>
        </div>

        <div className="inbox-controls">
          <div className="inbox-tabs" role="tablist">
            {(['all', 'attention'] as const).map((value) => (
              <button key={value} role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>
                {value === 'all' ? 'All updates' : 'Needs attention'}
              </button>
            ))}
          </div>
          <button className="text-button" onClick={markAllRead}>Mark all read</button>
        </div>

        <div className="inbox-list">
          {loading && !data && <InboxEmpty title="Checking campus systems…" detail="Attendance, events, placements and services are being verified." />}
          {error && !data && <InboxEmpty title="Inbox unavailable" detail="The backend is not reachable. Your campus records were not changed." />}
          {!loading && !error && items.length === 0 && <InboxEmpty title="You’re all caught up" detail="No alerts match this view." />}
          {items.map((item) => (
            <InboxRow key={item.id} item={item} read={read.has(item.id)} onRead={() => markRead(item.id)} onAct={() => act(item)} />
          ))}
        </div>

        <div className="inbox-foot">
          <span><span className="live-dot" /> Live campus data</span>
          <span>{data ? `Updated ${new Date(data.generated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}</span>
        </div>
      </aside>
    </div>
  )
}

function InboxRow({ item, read, onRead, onAct }: { item: InboxItem; read: boolean; onRead: () => void; onAct: () => void }) {
  const Icon = item.kind === 'attendance' ? AlertTriangle
    : item.kind === 'placement' ? BriefcaseBusiness
      : item.kind === 'library' ? BookOpen
        : item.severity === 'success' ? CheckCircle2 : CalendarDays
  return (
    <article className={`inbox-row severity-${item.severity}${read ? ' is-read' : ''}`} onClick={onRead}>
      <div className="inbox-row-icon"><Icon size={18} /></div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="inbox-row-meta">
          <span>{item.source}</span>
          {!read && <span className="unread-dot" aria-label="Unread" />}
        </div>
        <h3>{item.title}</h3>
        <p>{item.detail}</p>
        {item.action_prompt && <button onClick={(e) => { e.stopPropagation(); onAct() }}>{item.action_label} <span>→</span></button>}
      </div>
    </article>
  )
}

function InboxEmpty({ title, detail }: { title: string; detail: string }) {
  return <div className="inbox-empty"><Bell size={25} /><strong>{title}</strong><span>{detail}</span></div>
}
