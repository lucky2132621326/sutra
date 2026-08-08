import {
  Bell, BookOpenCheck, CalendarCheck2, CalendarDays, ChevronLeft,
  ChevronRight, Clock3, ReceiptText, RefreshCw, ShieldCheck, X,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { CalendarItem, CalendarItemKind, CalendarResponse } from '../types/calendar'

type CalendarFilter = 'all' | 'course' | 'confirmed' | 'reminder'

const KIND_STYLE: Record<CalendarItemKind, { label: string; color: string; bg: string; icon: LucideIcon }> = {
  course: { label: 'Class', color: 'var(--agent-academic)', bg: 'var(--running-bg)', icon: BookOpenCheck },
  event: { label: 'Registered', color: 'var(--agent-events)', bg: 'var(--success-bg)', icon: CalendarCheck2 },
  calendar: { label: 'Confirmed', color: 'var(--agent-services)', bg: 'var(--approval-bg)', icon: CalendarDays },
  reminder: { label: 'Reminder', color: 'var(--agent-knowledge)', bg: 'var(--degraded-bg)', icon: Bell },
}

function localDate(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function iso(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function compactDateTime(date: string, time: string | null): string {
  return `${date.replaceAll('-', '')}T${(time ?? '00:00').replace(':', '')}00`
}

function googleCalendarUrl(item: CalendarItem): string {
  const start = compactDateTime(item.date, item.start_time)
  const end = compactDateTime(item.date, item.end_time ?? item.start_time)
  const query = new URLSearchParams({
    action: 'TEMPLATE', text: item.title, dates: `${start}/${end}`,
    details: `${item.source}${item.receipt_ids.length ? `\nSūtra receipts: ${item.receipt_ids.join(', ')}` : ''}`,
    ctz: 'Asia/Kolkata',
  })
  return `https://calendar.google.com/calendar/render?${query.toString()}`
}

function downloadIcs(item: CalendarItem) {
  const escape = (value: string) => value.replaceAll('\\', '\\\\').replaceAll(',', '\\,').replaceAll(';', '\\;').replaceAll('\n', '\\n')
  const lines = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Sutra//Campus Calendar//EN',
    'CALSCALE:GREGORIAN', 'BEGIN:VEVENT', `UID:${item.id}@sutra.local`,
    `DTSTART;TZID=Asia/Kolkata:${compactDateTime(item.date, item.start_time)}`,
    `DTEND;TZID=Asia/Kolkata:${compactDateTime(item.date, item.end_time ?? item.start_time)}`,
    `SUMMARY:${escape(item.title)}`, `DESCRIPTION:${escape(`${item.source}${item.receipt_ids.length ? ` · receipts ${item.receipt_ids.join(', ')}` : ''}`)}`,
    'END:VEVENT', 'END:VCALENDAR',
  ]
  const url = URL.createObjectURL(new Blob([lines.join('\r\n')], { type: 'text/calendar;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${item.title.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase() || 'sutra-event'}.ics`
  anchor.click()
  URL.revokeObjectURL(url)
}

function monthCells(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1)
  const mondayOffset = (first.getDay() + 6) % 7
  const start = new Date(first)
  start.setDate(first.getDate() - mondayOffset)
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start)
    day.setDate(start.getDate() + index)
    return day
  })
}

function matchesFilter(item: CalendarItem, filter: CalendarFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'confirmed') return item.kind === 'event' || item.kind === 'calendar'
  return item.kind === filter
}

export function CalendarPage({ data, loading, error, onClose, onRefresh }: {
  data: CalendarResponse | null
  loading: boolean
  error: boolean
  onClose: () => void
  onRefresh: () => void
}) {
  const [month, setMonth] = useState(() => new Date())
  const [filter, setFilter] = useState<CalendarFilter>('all')
  const [selected, setSelected] = useState<CalendarItem | null>(null)
  const cells = useMemo(() => monthCells(month), [month])
  const items = data?.items ?? []
  const visibleItems = items.filter((item) => {
    const itemDate = localDate(item.date)
    return itemDate.getFullYear() === month.getFullYear()
      && itemDate.getMonth() === month.getMonth()
      && matchesFilter(item, filter)
  })
  const itemsByDay = new Map<string, CalendarItem[]>()
  for (const item of visibleItems) {
    itemsByDay.set(item.date, [...(itemsByDay.get(item.date) ?? []), item])
  }
  const confirmed = items.filter((item) => item.kind === 'event' || item.kind === 'calendar').length
  const courseCount = visibleItems.filter((item) => item.kind === 'course').length
  const reminderCount = visibleItems.filter((item) => item.kind === 'reminder').length
  const today = iso(new Date())

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const moveMonth = (delta: number) => {
    setMonth((current) => new Date(current.getFullYear(), current.getMonth() + delta, 1))
    setSelected(null)
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Student calendar" className="calendar-page">
      <header className="calendar-topbar">
        <div className="calendar-brand">
          <span className="calendar-brand-icon"><CalendarDays size={20} /></span>
          <span>
            <span className="eyebrow" style={{ color: 'var(--accent)' }}>Sūtra calendar</span>
            <strong className="font-display">Your commitments, after approval</strong>
          </span>
        </div>
        <div className="calendar-live-proof">
          <ShieldCheck size={15} />
          Live campus records · rejected actions never appear
        </div>
        <button className="calendar-icon-button" onClick={onRefresh} aria-label="Refresh calendar" title="Refresh calendar">
          <RefreshCw size={16} className={loading ? 'calendar-spin' : ''} />
        </button>
        <button className="calendar-close" onClick={onClose}><X size={17} /> Close</button>
      </header>

      <div className="calendar-summary">
        <div>
          <div className="eyebrow">Student calendar</div>
          <h1 className="font-display">{data?.student_name ?? 'Your schedule'}</h1>
          <p>Classes provide context. Approved registrations and agent-created calendar entries carry execution receipts.</p>
        </div>
        <div className="calendar-metrics">
          <Metric value={String(confirmed)} label="approved commitments" tone="var(--success)" />
          <Metric value={String(courseCount)} label="classes this month" tone="var(--agent-academic)" />
          <Metric value={String(reminderCount)} label="active reminders" tone="var(--agent-knowledge)" />
        </div>
      </div>

      <div className="calendar-toolbar">
        <div className="calendar-month-nav">
          <button className="calendar-icon-button" onClick={() => moveMonth(-1)} aria-label="Previous month"><ChevronLeft size={17} /></button>
          <button className="calendar-today" onClick={() => setMonth(new Date())}>Today</button>
          <button className="calendar-icon-button" onClick={() => moveMonth(1)} aria-label="Next month"><ChevronRight size={17} /></button>
          <h2>{month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</h2>
        </div>
        <div className="calendar-filters" aria-label="Calendar filters">
          {([
            ['all', 'Everything'], ['course', 'Classes'], ['confirmed', 'Approved'], ['reminder', 'Reminders'],
          ] as [CalendarFilter, string][]).map(([value, label]) => (
            <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>
          ))}
        </div>
      </div>

      <main className="calendar-workspace">
        <section className="calendar-grid-shell" aria-label={`${month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })} calendar`}>
          {error && <div className="calendar-notice is-error">Calendar records are unavailable. The rest of Sūtra is unaffected.</div>}
          <div className="calendar-weekdays">
            {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => <span key={day}>{day}</span>)}
          </div>
          <div className="calendar-grid">
            {cells.map((day) => {
              const key = iso(day)
              const dayItems = itemsByDay.get(key) ?? []
              const inMonth = day.getMonth() === month.getMonth()
              return (
                <div key={key} className={`calendar-day${inMonth ? '' : ' is-outside'}${key === today ? ' is-today' : ''}`}>
                  <div className="calendar-date-line">
                    <span>{day.getDate()}</span>
                    {key === today && <small>today</small>}
                  </div>
                  <div className="calendar-day-items">
                    {dayItems.slice(0, 4).map((item) => <CalendarChip key={item.id} item={item} selected={selected?.id === item.id} onClick={() => setSelected(item)} />)}
                    {dayItems.length > 4 && <button className="calendar-more" onClick={() => setSelected(dayItems[4])}>+{dayItems.length - 4} more</button>}
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        <aside className="calendar-agenda">
          {selected ? (
            <CalendarDetail item={selected} onClose={() => setSelected(null)} />
          ) : (
            <>
              <div className="calendar-agenda-heading">
                <div><span className="eyebrow">Month agenda</span><strong>{visibleItems.length} scheduled items</strong></div>
                {loading && <RefreshCw size={14} className="calendar-spin" />}
              </div>
              <div className="calendar-agenda-list">
                {visibleItems.length ? visibleItems.map((item) => (
                  <button key={item.id} onClick={() => setSelected(item)}>
                    <span className="calendar-agenda-date"><b>{localDate(item.date).getDate()}</b>{localDate(item.date).toLocaleDateString(undefined, { weekday: 'short' })}</span>
                    <span className="calendar-agenda-copy"><strong>{item.title}</strong><small>{item.start_time ?? 'All day'} · {KIND_STYLE[item.kind].label}</small></span>
                    {item.receipt_ids.length > 0 && <ShieldCheck size={14} className="calendar-proof-icon" />}
                  </button>
                )) : (
                  <div className="calendar-empty"><CalendarDays size={28} /><strong>No items in this view</strong><span>Choose another filter or month.</span></div>
                )}
              </div>
            </>
          )}
        </aside>
      </main>
    </div>
  )
}

function CalendarChip({ item, selected, onClick }: { item: CalendarItem; selected: boolean; onClick: () => void }) {
  const style = KIND_STYLE[item.kind]
  return (
    <button
      className={`calendar-chip${selected ? ' is-selected' : ''}`}
      onClick={onClick}
      title={`${item.title} · ${item.start_time ?? style.label}`}
      style={{ '--calendar-color': style.color, '--calendar-bg': style.bg } as React.CSSProperties}
    >
      <span>{item.start_time}</span>{item.title}
      {item.receipt_ids.length > 0 && <ShieldCheck size={10} aria-label="Verified receipt" />}
    </button>
  )
}

function CalendarDetail({ item, onClose }: { item: CalendarItem; onClose: () => void }) {
  const style = KIND_STYLE[item.kind]
  const Icon = style.icon
  return (
    <div className="calendar-detail">
      <div className="calendar-detail-top">
        <span className="calendar-detail-icon" style={{ color: style.color, background: style.bg }}><Icon size={19} /></span>
        <button className="calendar-icon-button" onClick={onClose} aria-label="Close calendar detail"><X size={15} /></button>
      </div>
      <span className="eyebrow" style={{ color: style.color }}>{style.label} · {item.status}</span>
      <h3>{item.title}</h3>
      <div className="calendar-detail-row"><CalendarDays size={15} /><span>{localDate(item.date).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</span></div>
      <div className="calendar-detail-row"><Clock3 size={15} /><span>{item.start_time ?? 'No set time'}{item.end_time ? `–${item.end_time}` : ''}</span></div>
      <div className="calendar-source"><ShieldCheck size={15} /><span><b>Source of truth</b>{item.source}</span></div>
      {typeof item.metadata.description === 'string' && <p className="calendar-description">{item.metadata.description}</p>}
      {item.kind !== 'reminder' && (
        <div className="calendar-integrations">
          <a href={googleCalendarUrl(item)} target="_blank" rel="noreferrer"><CalendarCheck2 size={14} /> Open in Google Calendar</a>
          <button onClick={() => downloadIcs(item)}><CalendarDays size={14} /> Download .ics</button>
        </div>
      )}
      {item.receipt_ids.length ? (
        <div className="calendar-receipts">
          <span className="eyebrow"><ReceiptText size={13} /> Execution proof</span>
          {item.receipt_ids.map((receipt) => <code key={receipt}>{receipt}</code>)}
          <small>These IDs exist only after backend writes succeed.</small>
        </div>
      ) : (
        <div className="calendar-record-note"><BookOpenCheck size={15} /> Verified from the campus timetable database.</div>
      )}
    </div>
  )
}

function Metric({ value, label, tone }: { value: string; label: string; tone: string }) {
  return <div className="calendar-metric"><strong style={{ color: tone }}>{value}</strong><span>{label}</span></div>
}
