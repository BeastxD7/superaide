import { useEffect } from 'react'

// "Waiting for your OK" and "Requests from your link" both show approvals, each with its own data. When one
// decides something, tell the other (and any other copy) to read again so nothing is left showing a stale button.
const EVENT = 'superaide:approvals-changed'

export function announceApprovalsChanged(): void {
  window.dispatchEvent(new Event(EVENT))
}

// Runs `reload` when another card changed an approval, and when the tab comes back into view.
export function useApprovalsChanged(reload: () => unknown): void {
  useEffect(() => {
    const run = () => void reload()
    const onVisible = () => {
      if (document.visibilityState === 'visible') run()
    }
    window.addEventListener(EVENT, run)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener(EVENT, run)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [reload])
}
