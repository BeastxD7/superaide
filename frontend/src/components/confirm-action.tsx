'use client'

import { useRef, useState, type ComponentProps } from 'react'
import { Button } from '@/components/ui/button'

// A button for something that can't be undone: the first press asks "Are you sure?" in place, and only the
// second press runs it. Focus moves to the "Keep" choice when it opens, and back to the button when it closes.
export function ConfirmAction({
  label,
  confirmLabel,
  question = 'Are you sure?',
  onConfirm,
  disabled,
  variant = 'outline',
}: {
  label: string
  confirmLabel: string
  question?: string
  onConfirm: () => void | Promise<void>
  disabled?: boolean
  variant?: ComponentProps<typeof Button>['variant']
}) {
  const [asking, setAsking] = useState(false)
  const opener = useRef<HTMLButtonElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)

  if (!asking)
    return (
      <Button
        ref={opener}
        type="button"
        variant={variant}
        disabled={disabled}
        onClick={() => {
          setAsking(true)
          requestAnimationFrame(() => cancel.current?.focus())
        }}
      >
        {label}
      </Button>
    )
  return (
    <span className="inline-flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      <span className="text-sm text-muted">{question}</span>
      <Button
        type="button"
        variant="destructive"
        disabled={disabled}
        onClick={() => {
          setAsking(false)
          void onConfirm()
        }}
      >
        {confirmLabel}
      </Button>
      <Button
        ref={cancel}
        type="button"
        variant="ghost"
        onClick={() => {
          setAsking(false)
        }}
      >
        Keep it
      </Button>
    </span>
  )
}
