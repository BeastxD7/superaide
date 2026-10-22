'use client'

import { Download, Share, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { isIOS, isStandalone } from '@/features/pwa/display'
import { PWA_ENABLED } from '@/lib/flags'

type InstallPrompt = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

const DISMISSED = 'ava-install-hint-dismissed'

function dismissed(): boolean {
  try {
    return localStorage.getItem(DISMISSED) === '1'
  } catch {
    return false
  }
}

// "Install Ava" on phones: Android gets the browser's own install prompt behind our button;
// iOS Safari gets a one-time hint (Share, then Add to Home Screen). Hidden once installed,
// once dismissed, on desktop, and entirely unless NEXT_PUBLIC_PWA=1.
export function InstallHint() {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null)
  const [ios, setIos] = useState(false)
  const [hidden, setHidden] = useState(true)

  useEffect(() => {
    if (!PWA_ENABLED || isStandalone() || dismissed()) return
    const onPrompt = (e: Event) => {
      e.preventDefault()
      setPrompt(e as InstallPrompt)
      setHidden(false)
    }
    window.addEventListener('beforeinstallprompt', onPrompt)
    // After this render (Safari never fires beforeinstallprompt; the hint is ours to show).
    const later = isIOS()
      ? setTimeout(() => {
          setIos(true)
          setHidden(false)
        }, 0)
      : undefined
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      clearTimeout(later)
    }
  }, [])

  if (!PWA_ENABLED || hidden || (!prompt && !ios)) return null

  function close() {
    setHidden(true)
    try {
      localStorage.setItem(DISMISSED, '1')
    } catch {
      // Private mode: the hint just comes back next time.
    }
  }

  return (
    <div role="region" aria-label="Install Ava" className="flex items-center gap-3 border-b border-line/70 bg-lilac-50 px-4 py-2.5 text-sm md:hidden">
      {prompt ? (
        <>
          <p className="min-w-0 flex-1 text-body">Put Ava on your home screen.</p>
          <Button
            size="sm"
            onClick={() => {
              void prompt.prompt()
              void prompt.userChoice.finally(() => setHidden(true))
            }}
          >
            <Download aria-hidden /> Install Ava
          </Button>
        </>
      ) : (
        <p className="min-w-0 flex-1 text-body">
          To install Ava, tap <Share className="inline size-4 align-text-bottom" aria-label="Share" /> then{' '}
          <span className="font-medium text-ink">Add to Home Screen</span>.
        </p>
      )}
      <button type="button" onClick={close} className="rounded-md p-1 text-muted hover:text-ink" aria-label="Dismiss">
        <X className="size-4" aria-hidden />
      </button>
    </div>
  )
}
