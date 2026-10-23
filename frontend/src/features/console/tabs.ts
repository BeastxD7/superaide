import { Activity, FlaskConical, Gauge, MessagesSquare, Users, WalletCards, type LucideIcon } from 'lucide-react'

export type TabKey = 'overview' | 'people' | 'conversations' | 'log' | 'spend' | 'simulator'

// `about` is the line under each page's title (Overview writes its own heading).
export const TABS: { key: TabKey; label: string; about: string; icon: LucideIcon }[] = [
  { key: 'overview', label: 'Overview', about: '', icon: Gauge },
  { key: 'spend', label: 'Spend', about: 'What Claude has cost, against the cap.', icon: WalletCards },
  { key: 'people', label: 'People', about: 'Everyone Ava knows, and every account that has written in.', icon: Users },
  { key: 'conversations', label: 'Conversations', about: 'Every chat, and who could see each answer.', icon: MessagesSquare },
  { key: 'log', label: 'Safety log', about: 'Identity checks, refusals and controls, in plain words.', icon: Activity },
  { key: 'simulator', label: 'Try an impostor', about: 'Try to fool Ava without any risk.', icon: FlaskConical },
]

// Two groups: what the operator may always see, and an owner's content, which opens only with a reason (ADR 0009).
export const GROUPS: { label: string; keys: TabKey[] }[] = [
  { label: 'Monitor', keys: ['overview', 'spend'] },
  { label: "Owner's content (needs a reason)", keys: ['people', 'conversations', 'log', 'simulator'] },
]

