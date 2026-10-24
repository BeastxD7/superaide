import { call, type Message } from '@/lib/api'
import { announceRemindersChanged } from '@/lib/reminders-sync'
import { toast, type ToastAction } from '@/lib/toast'

const TOOLS: Record<string, string> = {
  create_reminder: 'Reminder set',
  update_reminder: 'Reminder changed',
  cancel_reminder: 'Reminder cancelled',
}

// Ava's tool line says what she did, e.g. "Reminder set (id 3f2b…): Call Priya, tomorrow at 5:00 pm, arriving in
// the app." Turn it into the short toast text, and the id (for Undo).
export function describeReminderStep(name: string, detail: string): { message: string; id: string | null } | null {
  const head = TOOLS[name]
  if (!head) return null
  const id = /\(id ([0-9a-f-]{36})\)/i.exec(detail)?.[1] ?? null
  const rest = detail
    .replace(/^Reminder (?:set|moved|changed|updated|cancelled|marked done)(?: \(id [0-9a-f-]+\))?:?\s*/i, '')
    .replace(/,?\s*arriving .*$/i, '')
    .replace(/\.$/, '')
    .trim()
  return { message: rest ? `${head}: ${rest}` : head, id }
}

// A finished reminder tool call: tell every copy of the pill and the list to read again, and confirm in the moment.
export function reminderTool(step: { name: string; state: string; detail: string }): void {
  if (!(step.name in TOOLS) || step.state === 'running') return
  announceRemindersChanged()
  if (step.state !== 'done') return
  const info = describeReminderStep(step.name, step.detail)
  if (!info) return
  const actions: ToastAction[] = [{ label: 'Open', href: '/dashboard/reminders' }]
  if (step.name === 'create_reminder' && info.id) {
    actions.push({
      label: 'Undo',
      onClick: () => call(`/reminders/${info.id}/cancel`, { method: 'POST' }).finally(announceRemindersChanged),
    })
  }
  toast({ message: info.message, actions })
}

// After a turn: if the newest reply says Ava set, moved or cancelled a reminder, confirm it (the pill and the list
// read again at the same moment). Works for the model's tool call and for the code-only reminder reader alike,
// because both are saved on the reply's work log.
export function announceReminderSteps(messages: Message[]): void {
  const last = [...messages].reverse().find((m) => m.from === 'agent')
  if (!last || Date.now() - new Date(last.at).getTime() > 2 * 60_000) return // an old reply is not news
  for (const step of last.activity ?? []) {
    if (step.kind === 'tool' && step.name) reminderTool({ name: step.name, state: step.state ?? 'done', detail: step.detail ?? '' })
  }
}
