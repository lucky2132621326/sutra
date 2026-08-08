export type InboxKind = 'attendance' | 'event' | 'placement' | 'library' | 'reminder' | 'calendar'
export type InboxSeverity = 'urgent' | 'warning' | 'info' | 'success'

export interface InboxItem {
  id: string
  kind: InboxKind
  severity: InboxSeverity
  title: string
  detail: string
  due_at: string | null
  source: string
  action_label: string | null
  action_prompt: string | null
  metadata: Record<string, unknown>
}

export interface InboxResponse {
  student_id: string
  student_name: string
  generated_at: string
  attention_count: number
  items: InboxItem[]
}
