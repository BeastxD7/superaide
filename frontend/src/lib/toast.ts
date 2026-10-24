// One small message that can appear on any dashboard page: "Reminder set: call Priya". `toast()` can be called from
// anywhere; one <ToastHost /> (lib/toast-host.tsx) in the shell shows them.
export type ToastAction = { label: string; href?: string; onClick?: () => unknown }
export type ToastOptions = { message: string; actions?: ToastAction[]; ms?: number }
export type ToastItem = ToastOptions & { id: number }

export const TOAST_EVENT = 'superaide:toast'
let nextId = 1

export function toast(options: ToastOptions): void {
  window.dispatchEvent(new CustomEvent<ToastItem>(TOAST_EVENT, { detail: { ...options, id: nextId++ } }))
}
