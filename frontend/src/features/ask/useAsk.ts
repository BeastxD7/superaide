'use client'

import { useResource } from '@/lib/use-resource'

// One owner endpoint of the "Ask my Ava" link. `enabled` is false while the backend has the feature
// off (it answers 404), so callers render nothing and the dashboard looks as it did before.
export function useAsk<T>(path: string) {
  return useResource<T>(path, { offOn404: true })
}
