import { useCallback, useEffect, useMemo, useState } from 'react'

import { getCalendar } from '../transport/sseClient'
import type { CalendarResponse } from '../types/calendar'

function iso(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function useCalendar(studentId: string) {
  const range = useMemo(() => {
    const now = new Date()
    const start = new Date(now.getFullYear(), now.getMonth() - 1, 1)
    const end = new Date(now.getFullYear(), now.getMonth() + 12, 0)
    return { start: iso(start), end: iso(end) }
  }, [])
  const [data, setData] = useState<CalendarResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [nonce, setNonce] = useState(0)

  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    getCalendar(studentId, range.start, range.end)
      .then((result) => { if (alive) { setData(result); setError(false) } })
      .catch(() => { if (alive) setError(true) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [studentId, range.start, range.end, nonce])

  return { data, loading, error, refresh }
}
