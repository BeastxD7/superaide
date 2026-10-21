import type { AuditRow } from '@/features/console/api'

export const usd = (n: number, digits = 4) => `US$${n.toFixed(digits)}`

export function ago(iso: string | null): string {
  if (!iso) return 'never'
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export const TRUST_NAMES = ['Hint', 'Authenticated', 'Linked', 'Directory', 'Stepped up'] as const

export const TRUST_HELP = [
  'A name or claim only. Unlocks nothing.',
  'A verified platform account that is not linked to a person yet.',
  'Linked to a person through a code sent to a channel already trusted.',
  'Bound to a company directory account.',
  'Freshly re-verified for a sensitive action.',
] as const

export const AUDIENCE_NAMES: Record<string, string> = {
  private_1to1: 'Private, one to one',
  team: 'Group, members unverified',
  mixed_external: 'Includes someone outside',
}

export const METHOD_NAMES: Record<string, string> = {
  operator_bootstrap: 'Added by you at the terminal',
  code_via_trusted_channel: 'Code sent to a trusted channel',
  operator_dashboard: 'Linked by you in the dashboard',
  unlinked: 'Unlinked',
  '': 'Not linked',
}

export type AuditKind = 'identity' | 'refusal' | 'model' | 'memory' | 'control'

const str = (v: unknown) => (v === undefined || v === null ? '' : String(v))

// Turns an audit row into one plain sentence and a category.
export function describeAudit(row: AuditRow): { kind: AuditKind; text: string } {
  const d = row.detail
  switch (row.action) {
    case 'new_identity':
      return { kind: 'identity', text: `A new ${str(d.provider)} account wrote in for the first time.` }
    case 'claim_seen':
      return { kind: 'identity', text: `An unlinked account claimed to be "${str(d.claimed)}".` }
    case 'link_challenge_sent':
      return { kind: 'identity', text: 'Sent a one-time code to the real person on a channel already trusted.' }
    case 'linked':
      return { kind: 'identity', text: 'Linked a new channel after the right code came back.' }
    case 'link_code_wrong':
      return { kind: 'refusal', text: `Wrong link code (attempt ${str(d.attempts)}).` }
    case 'link_code_expired':
      return { kind: 'refusal', text: 'A link code expired before it was used.' }
    case 'link_code_locked':
      return { kind: 'refusal', text: 'Locked a link code after too many wrong tries.' }
    case 'link_refused':
      return { kind: 'refusal', text: 'Refused to link an account from another company.' }
    case 'link_rate_limited':
      return { kind: 'refusal', text: 'Stopped sending codes to an account that asked too often.' }
    case 'link_no_trusted_channel':
      return { kind: 'identity', text: 'Could not send a code: that person has no trusted channel.' }
    case 'linked_by_operator':
      return { kind: 'identity', text: 'You linked an account to a person from the dashboard.' }
    case 'unlinked':
      return { kind: 'identity', text: `Unlinked a channel (by ${row.actor}).` }
    case 'person_bootstrapped':
      return { kind: 'identity', text: `Added a ${str(d.relation)} with ${str(d.channels)} channel(s).` }
    case 'action_refused':
      return { kind: 'refusal', text: `Refused "${str(d.kind)}" for ${str(d.requester) || 'someone'}: ${str(d.reason)}.` }
    case 'approval_requested':
      return { kind: 'control', text: `Asked you to approve "${str(d.kind)}".` }
    case 'llm_call_refused':
      return {
        kind: 'refusal',
        text: d.reason === 'frozen' ? 'Blocked a model call: Ava is paused.' : 'Blocked a model call that could pass the spend cap.',
      }
    case 'llm_call_settled':
      return { kind: 'model', text: `Model call for ${str(d.purpose)} on ${str(d.model)}: US$${Number(d.cost_usd ?? 0).toFixed(5)}.` }
    case 'llm_call_failed':
      return { kind: 'model', text: `A model call failed and cost nothing: ${str(d.error).slice(0, 120)}` }
    case 'reservation_exceeded':
      return { kind: 'model', text: 'A call cost more than its reservation. Check the estimate.' }
    case 'prediction':
      return {
        kind: 'model',
        text: `Predicted: ${str(d.expectation)} (confidence ${Number(d.confidence ?? 0).toFixed(2)}).`,
      }
    case 'remembered':
      return { kind: 'memory', text: 'Remembered something, visible only to the people in that private chat.' }
    case 'rejected':
      return { kind: 'refusal', text: `Rejected a ${str(d.provider)} webhook: ${str(d.reason)}.` }
    case 'dropped_unverified':
      return { kind: 'refusal', text: 'Dropped a message that was not verified.' }
    case 'kill_switch':
      return { kind: 'control', text: d.on ? `Paused by ${row.actor}.` : `Resumed by ${row.actor}.` }
    case 'frozen_no_reply':
      return { kind: 'control', text: 'Logged a message but stayed silent: Ava is paused.' }
    case 'budget_cap_reached':
      return { kind: 'refusal', text: 'Reached the spend cap and paused replies.' }
    case 'simulated_message':
      return { kind: 'control', text: `Ran a simulated ${str(d.provider)} message (${str(d.mode)}).` }
    default:
      return { kind: 'control', text: row.action.replaceAll('_', ' ') }
  }
}
