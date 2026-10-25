import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '04-identity-linking',
  title: 'Identity and channel linking',
  subtitle: 'People are found only by immutable platform ids, never by display names. Authority comes from the database, never from message text.',
  height: 1150,
})
d.zone('model', { x: 40, y: 120, w: 560, h: 520, label: 'Who is who (database)', color: 'blue', icon: 'lucide:users' })
d.zone('link', { x: 640, y: 120, w: 1240, h: 520, label: 'Linking a new channel to the owner', color: 'green', icon: 'lucide:link' })
d.zone('trust', { x: 40, y: 680, w: 880, h: 330, label: 'Trust tiers (identity/trust.py)', color: 'violet', icon: 'lucide:shield' })
d.zone('claim', { x: 960, y: 680, w: 920, h: 330, label: 'A stranger says "I\'m Dana" (claimed names)', color: 'rose', icon: 'lucide:ghost' })

d.node('person', { x: 62, y: 170, w: 516, h: 84, icon: 'lucide:user-round', title: 'Person', sub: 'relation: manager, colleague, external or contact. Set by the app or operator, never by what a message says', color: 'blue' })
d.node('ciw', { x: 62, y: 330, w: 516, h: 70, icon: 'lucide:monitor', title: 'ChannelIdentity: web', sub: 'stable_key = the person id; the session cookie is the proof', color: 'blue' })
d.node('cit', { x: 62, y: 430, w: 516, h: 70, icon: 'brand:telegram', title: 'ChannelIdentity: Telegram', sub: 'stable_key = Telegram user id; endpoint_key = which bot', color: 'blue' })
d.node('cis', { x: 62, y: 530, w: 516, h: 70, icon: 'slack:', title: 'ChannelIdentity: Slack', sub: 'stable_key = Slack user id; endpoint_key = which app', color: 'blue' })
for (const t of ['ciw', 'cit', 'cis']) d.edge('person', t, { from: 'l', to: 'l', via: [[50, 212], [50, t === 'ciw' ? 365 : t === 'cit' ? 465 : 565]] })

const s1 = { y: 190, h: 110, w: 270 }
d.node('l1', { x: 662, ...s1, icon: 'lucide:layout-dashboard', title: 'Owner opens Connections', sub: 'The signed-in owner asks the app to link Telegram or Slack', color: 'green' })
d.node('l2', { x: 962, ...s1, icon: 'lucide:key-round', title: 'App mints an invite', sub: 'ChannelInvite: a one-time code the owner types into the other channel', color: 'green' })
d.node('l3', { x: 1262, ...s1, icon: 'lucide:send', title: 'Code goes to the bot', sub: 'Telegram: /start code. Slack: "link code" in a DM only; never honoured in a channel', color: 'green' })
d.node('l4', { x: 1562, ...s1, icon: 'lucide:badge-check', title: 'Pipeline redeems it', sub: 'Identity gets person_id, trust LINKED, endpoint_key; IdentityLinkEvent and audit written', color: 'green' })
d.edge('l1', 'l2', { n: 1 }); d.edge('l2', 'l3', { n: 2 }); d.edge('l3', 'l4', { n: 3 })
d.node('res', { x: 662, y: 380, w: 570, h: 100, icon: 'lucide:fingerprint-pattern', title: 'Every later message: resolve()', sub: 'Looks up (provider, workspace, stable_key). Person is set only for an active identity of an active person. An identity flagged external never resolves to a person', color: 'green' })
d.node('unc', { x: 1262, y: 380, w: 570, h: 100, icon: 'lucide:rotate-ccw', title: 'Unconfirmed: fixed reply, no model, no memory', sub: 'Same person id but a different bot or app than the one linked, or back after a long silence: the owner is told and must re-confirm', color: 'rose', accent: '#dc2626' })
d.edge('l4', 'res', { from: 'b', to: 't', n: 4, via: [[1697, 340], [947, 340]] })
d.edge('res', 'unc', { from: 'r', to: 'l', kind: 'deny', label: 'endpoint changed' })
d.note(662, 520, 1170, ['Nothing is merged automatically (ADR 0008): one owner is one Person with up to three ChannelIdentity rows, each linked by an app-issued code. Roles (manager, colleague) come from Person.relation. The web chat is trusted because it carries the session cookie; the pipeline builds its message with provider "web" and the person id.'], { title: 'Rule', color: 'green' })

d.table(62, 730, [{ w: 150, label: 'Tier' }, { w: 110, label: 'Value' }, { w: 560, label: 'Meaning' }], [
  ['HINT', { t: '0', bg: '#fef2f2' }, { t: 'A name or handle claim; never grants anything', size: 12 }],
  ['AUTHENTICATED', { t: '1', bg: '#fff7ed' }, { t: 'Verified platform id but not linked: a stranger', size: 12 }],
  ['LINKED', { t: '2', bg: '#f0fdf4' }, { t: 'Linked through a one-time code on an already trusted channel', size: 12 }],
  ['DIRECTORY', { t: '3', bg: '#f1f5f9' }, { t: 'Planned: a workspace directory vouches for the person', size: 12 }],
  ['STEPPED_UP', { t: '4', bg: '#f0fdf4' }, { t: 'Needed for the most sensitive actions (grant_access, delete_memory)', size: 12 }],
], { rowH: 40 })

d.node('c1', { x: 982, y: 740, w: 260, h: 100, icon: 'lucide:quote', title: 'Claim detected', sub: 'A message like "I\'m Dana" from an unknown account', color: 'rose' })
d.node('c2', { x: 1282, y: 740, w: 280, h: 100, icon: 'lucide:send', title: 'Code to a trusted channel', sub: 'If Dana exists, a code goes to her already-linked channel. The reply is identical whether or not she exists', color: 'rose' })
d.node('c3', { x: 1602, y: 740, w: 258, h: 100, icon: 'lucide:user-check', title: 'Verify or review', sub: 'Right code links her. With IDENTITY_CLAIM_REVIEW the owner decides: link, new person, stranger, block', color: 'rose' })
d.edge('c1', 'c2', { n: 5 }); d.edge('c2', 'c3', { n: 6 })
d.note(982, 880, 878, ['Example: Meera writes to the owner\'s Telegram bot from an unknown account. She is a stranger (tier 1), whatever name she gives. Dana, a colleague who is already linked on Slack, can add Telegram by redeeming a code the app sent her, never by typing her name.'], { title: 'Worked example', color: 'amber' })
d.legend()
export default d
