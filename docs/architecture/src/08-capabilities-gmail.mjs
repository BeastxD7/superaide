import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '08-capabilities-gmail',
  title: 'Capabilities, connectors and the Gmail send flow',
  subtitle: 'Each capability is a registered skill with declared effects. Reads are free; anything that reaches another person goes through an approval card.',
  height: 1090,
})
d.zone('cap', { x: 40, y: 120, w: 1840, h: 440, label: 'Capabilities (Dashboard, Capabilities)', color: 'blue', icon: 'lucide:layers' })
d.zone('flow', { x: 40, y: 590, w: 1840, h: 390, label: 'Gmail: send with approval (integrations/gmail/skill.py)', color: 'green', icon: 'brand:gmail' })
const Yes = (t) => ({ t, bg: '#dcfce7', fg: '#166534', bold: true })
const Ask = (t) => ({ t, bg: '#fef9c3', fg: '#854d0e', bold: true })
d.table(62, 170, [
  { w: 230, label: 'Capability' }, { w: 560, label: 'What Ava can do' }, { w: 330, label: 'Needs your approval?' }, { w: 620, label: 'Where its settings live' },
], [
  ['Gmail', { t: 'Search and read threads, draft replies, send, compose', size: 12 }, Ask('Send and compose: always'), { t: 'Capabilities, Gmail: Reading, Drafts, Signature. Stored per owner on the installed skill', size: 12 }],
  ['Google Calendar', { t: 'Read events and free time; create, move, decline events', size: 12 }, Ask('Writes: yes, unless a mandate covers them'), { t: 'Capabilities, Google Calendar: Reading, writes on or off', size: 12 }],
  ['Web research', { t: 'Search (Brave or the model\'s own) and read pages, with citations', size: 12 }, Yes('No, but the turn is marked tainted'), { t: 'Capabilities, Web research: searches and pages per step. WEB_TOOLS_ENABLED', size: 12 }],
  ['Reminders', { t: 'Create and list reminders, delivered in the app and optionally Telegram or Slack', size: 12 }, Yes('No'), { t: 'Reminders page; auto-close days in Settings', size: 12 }],
  ['Voice', { t: 'Live voice calls and the spoken introduction (Deepgram when a key is set)', size: 12 }, Ask('Voice actions: confirm in the app'), { t: 'Connections (Deepgram key); VOICE_PROVIDER. Calls have their own cap', size: 12 }],
], { rowH: 56, headH: 40 })
d.note(62, 495, 1796, ['Credentials for every connector are verified against the service, encrypted in the vault, and shown only as a hint. Gmail and Calendar content is looked up live and not copied into memory; CONNECTOR_FACTS_ENABLED (off) can keep up to 3 short facts per item.'], { color: 'blue' })

const W = 270, ys = 640
const xs = [62, 392, 722, 1052, 1382]
d.node('g1', { x: xs[0], y: ys, w: W, h: 130, icon: 'lucide:brain', title: 'Model calls gmail_send or compose', sub: 'Recipients are chosen by code from the thread or an address you typed; noreply addresses are flagged', color: 'green' })
d.node('g2', { x: xs[1], y: ys, w: W, h: 130, icon: 'brand:gmail', title: 'A real draft is saved in Gmail', sub: 'With the signature on, two drafts: signed, and a plain alternative', color: 'green' })
d.node('g3', { x: xs[2], y: ys, w: W, h: 130, icon: 'lucide:file-signature', title: 'Approval card is sealed', sub: 'kind gmail_send; payload hash plus a hash of the draft\'s recipients, subject, text and html', color: 'green' })
d.node('g4', { x: xs[3], y: ys, w: W, h: 130, icon: 'lucide:layout-dashboard', title: 'You review it', sub: 'Dashboard, Approvals. You can untick the signature for this email', color: 'green' })
d.node('g5', { x: xs[4], y: ys, w: 450, h: 130, icon: 'lucide:send', title: 'send_approved()', sub: 'Re-fetches the draft and compares hashes. Changed: "Not sent". Same: send the draft, audit gmail_sent, delete the spare draft, record the card', color: 'green', accent: '#34a853' })
for (let i = 0; i < 4; i++) d.edge('g' + (i + 1), 'g' + (i + 2), { n: i + 1 })
d.node('n1', { x: xs[3], y: 830, w: 560, h: 90, icon: 'lucide:ban', title: 'Reject', sub: 'Both drafts are deleted. Nothing leaves your Gmail account', color: 'rose', accent: '#dc2626' })
d.edge('g4', 'n1', { from: 'b', to: 't', kind: 'deny', fa: 0.3, fb: 0.1 })
d.node('s1', { x: xs[2], y: 830, w: 270, h: 90, icon: 'lucide:eye', title: 'Reading is limited', sub: 'Credential mail hidden; code reads need step-up', color: 'amber' })
d.note(62, 830, 630, ['Example: Meera emails the owner asking to move a meeting. Ava reads the thread (read settings apply, the turn is tainted), drafts a reply, and the owner sees an approval card. Only on approve does it send, signed "Ava, personal assistant of the owner" with a link to a signed page.'], { title: 'Worked example', color: 'amber' })
d.node('tk', { x: 1052 + 590, y: 830, w: 190, h: 90, icon: 'lucide:key-round', title: 'Token in vault', sub: 'Refresh token, slot google', color: 'amber' })
d.legend()
export default d
