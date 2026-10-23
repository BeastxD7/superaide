'use client'

import { type NeedsYou } from '@/lib/api'
import { useSession } from '@/lib/session-context'
import { useResource } from '@/lib/use-resource'

// The Needs-you inbox. `enabled` is false while the backend has triage turned off, so callers render nothing
// and the app looks exactly as it did before. /app/me says so (`features.triage`), so the list is not even
// requested then (it would answer 404 by design and show as a failed request). An API that predates
// `features` is asked, and its 404 means the same thing. A failed first call (502 during a restart, offline)
// leaves `enabled` null and does not stop the polling, or the inbox would stay hidden for the whole session.
export function useNeedsYou(poll = 0) {
  const { me } = useSession()
  const off = me?.features?.triage === false
  const { data, error, enabled: asked, reload } = useResource<NeedsYou>(me === null || off ? null : '/needs-you', { poll, offOn404: true })
  return { enabled: off ? false : asked, data, error, reload }
}
