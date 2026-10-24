import { useEffect } from 'react'

// Reminders are shown in the sidebar pill, the phone tab bar, Home and the Reminders page, each with its own data.
// When one of them changes something, or Ava sets one in chat, tell every other copy to read again so the pill and
// the list update within a moment instead of at the next poll (the same pattern as approvals-sync).
const EVENT = 'superaide:reminders-changed'

export function announceRemindersChanged(): void {
  window.dispatchEvent(new Event(EVENT))
}

// Runs `reload` when a reminder changed elsewhere in the app, and when the tab comes back into view.
export function useRemindersChanged(reload: () => unknown): void {
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
