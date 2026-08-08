import { useEffect, useState } from 'react'

import { getInbox } from '../transport/sseClient'
import type { InboxResponse } from '../types/inbox'

export function useInbox(studentId: string) {
  const [data, setData] = useState<InboxResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    getInbox(studentId)
      .then((result) => { if (alive) { setData(result); setError(false) } })
      .catch(() => { if (alive) setError(true) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [studentId, nonce])

  return { data, loading, error, refresh: () => setNonce((n) => n + 1) }
}
