export type CalendarItemKind = 'course' | 'event' | 'calendar' | 'reminder'

export interface CalendarItem {
  id: string
  kind: CalendarItemKind
  title: string
  date: string
  start_time: string | null
  end_time: string | null
  status: 'scheduled' | 'registered' | 'confirmed' | 'reminder'
  source: string
  receipt_ids: string[]
  metadata: Record<string, unknown>
}

export interface CalendarResponse {
  student_id: string
  student_name: string
  range_start: string
  range_end: string
  generated_at: string
  items: CalendarItem[]
}
