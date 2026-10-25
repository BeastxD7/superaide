import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '07-approvals-policy',
  title: 'Approvals, policy, mandates and step-up',
  subtitle: 'The model can only propose. Code decides whether a proposal is refused, needs the owner, or may run. Everything is bounded by the kill switch and the spend cap.',
  height: 1190,
})
d.zone('flow', { x: 40, y: 120, w: 1840, h: 380, label: 'Life of a proposed action (policy/actions.py, policy/binding.py)', color: 'green', icon: 'lucide:gavel' })
d.zone('side', { x: 40, y: 530, w: 1100, h: 330, label: 'Ways to ask less, or to ask harder', color: 'violet', icon: 'lucide:key-round' })
d.zone('lim', { x: 1180, y: 530, w: 700, h: 330, label: 'Always-on limits', color: 'rose', icon: 'lucide:octagon-x' })
d.zone('ex', { x: 40, y: 890, w: 1840, h: 112, label: 'Worked example: an email send', color: 'amber', icon: 'brand:gmail' })

const xs = [62, 420, 778, 1136, 1494]
d.node('p1', { x: xs[0], y: 180, w: 330, h: 140, icon: 'lucide:brain', title: 'The model proposes', sub: 'A tool call or requested_action in TurnOutput. It cannot approve, change trust or settings: those kinds are never available through chat', color: 'green' })
d.node('p2', { x: xs[1], y: 180, w: 330, h: 140, icon: 'lucide:scale', title: 'decide()', sub: 'Refuses unknown or chat-forbidden kinds, kinds with no executor, an owner-only action asked for in a channel, a requester who is not a manager, or too little trust', color: 'green' })
d.node('p3', { x: xs[2], y: 180, w: 330, h: 140, icon: 'lucide:file-signature', title: 'Approval row is sealed', sub: 'bound_hash = sha256 of the exact payload; expiry from the owner\'s approval-expiry choice (default: never). Footprint guard and mandate shadow run here', color: 'green' })
d.node('p4', { x: xs[3], y: 180, w: 330, h: 140, icon: 'lucide:layout-dashboard', title: 'Owner decides in the app', sub: 'Dashboard, Approvals. A card from a voice call can only be approved in the app. Rejecting is accepted anywhere', color: 'green' })
d.node('p5', { x: xs[4], y: 180, w: 340, h: 140, icon: 'lucide:play', title: 'resolve() then executor', sub: 'Checks expiry, that the payload still matches, the circuit breaker. Runs the executor, writes the audit and action log, tells the owner in chat', color: 'green', accent: '#34a853' })
for (let i = 1; i < 5; i++) d.edge('p' + i, 'p' + (i + 1), { n: i })
d.node('no1', { x: xs[1], y: 380, w: 330, h: 80, icon: 'lucide:ban', title: 'Refused', sub: 'Reason shown to the requester; audited', color: 'rose', accent: '#dc2626' })
d.node('no2', { x: xs[3], y: 380, w: 330, h: 80, icon: 'lucide:ban', title: 'Not sent: the content changed', sub: 'Strict binding blocks a changed payload or draft', color: 'rose', accent: '#dc2626' })
d.edge('p2', 'no1', { from: 'b', to: 't', kind: 'deny' })
d.edge('p5', 'no2', { from: 'b', to: 'r', kind: 'deny', via: [[1664, 420]] })

d.node('m1', { x: 62, y: 580, w: 330, h: 150, icon: 'lucide:stamp', title: 'Mandates (signed cards)', sub: 'Macaroon-style token with caveats (tools, expiry, amount, domains, uses). Can be handed on only narrower; revoking one revokes its children. Never for send or spend', color: 'violet' })
d.node('m2', { x: 422, y: 580, w: 330, h: 150, icon: 'lucide:calendar', title: 'Skipping a card', sub: 'Only own-calendar events with no guests, above Infant, untainted turn, after 14 days of shadow data. Each skip is audited and shown to the owner', color: 'violet' })
d.node('m3', { x: 782, y: 580, w: 330, h: 150, icon: 'lucide:fingerprint-pattern', title: 'Step-up confirmation', sub: 'Sensitive changes (reading one-time codes) need a single-use server nonce, 2 minutes, tied to your session. STEPUP_MODE=password also asks for the password', color: 'violet' })
d.edge('m1', 'm2'); d.edge('m2', 'm3', { kind: 'dashed' })
d.note(62, 750, 1030, ['Mandates, shadow mode and skip are on by default, but with no mandate nothing is skipped. The rubber-stamp brake and circuit breaker are off by default (CIRCUIT_BREAKER_ENABLED, RUBBER_STAMP_BRAKE_ENABLED).'], { title: 'Defaults', color: 'violet' })

d.node('k1', { x: 1202, y: 580, w: 320, h: 120, icon: 'lucide:power', title: 'Kill switch', sub: 'Freeze one Ava or the whole platform. A frozen Ava makes no model call and sends no reply', color: 'rose' })
d.node('k2', { x: 1546, y: 580, w: 312, h: 120, icon: 'lucide:coins', title: 'BudgetGuard', sub: 'Reserves the worst-case cost before each call and settles from real usage. Lifetime, daily, monthly and per-purpose caps', color: 'rose' })
d.note(1202, 720, 656, ['Stages: Ava starts at Infant. The stage limits what the model can trigger on its own (triage ladder). Promotion is advisory: Ava never promotes herself.'], { title: 'Maturity', color: 'rose' })

d.note(62, 930, 1796, ['The model proposes gmail_send. decide() and the Gmail skill create a sealed card holding the draft hash. The owner opens Approvals, sees the recipient, subject and body, and taps approve. resolve() re-fetches the Gmail draft, compares its hash with the sealed one, and only then sends. If someone edited the draft in between, nothing is sent. The email carries the AI-assistant signature line unless the owner unticks it for that email.'], { color: 'amber' })
d.legend()
export default d
