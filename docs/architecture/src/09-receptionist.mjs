import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '09-receptionist',
  title: 'Receptionist: strangers, limits, taken messages and notifications',
  subtitle: 'A stranger never reaches memory or tools. The receptionist is a short, tool-free turn on public facts; what it learns becomes a message for the owner.',
  height: 1180,
})
d.zone('st', { x: 40, y: 120, w: 330, h: 800, label: 'Stranger side', color: 'blue', icon: 'lucide:ghost' })
d.zone('rc', { x: 410, y: 120, w: 920, h: 800, label: 'Receptionist (reception/service.py)', color: 'green', icon: 'lucide:headset' })
d.zone('ow', { x: 1370, y: 120, w: 510, h: 800, label: 'Owner side', color: 'violet', icon: 'lucide:user-check' })

d.node('m', { x: 62, y: 170, w: 286, h: 100, icon: 'brand:telegram', title: 'Meera DMs the bot', sub: 'Telegram or Slack DM from an account Ava does not know', color: 'blue' })
d.node('r', { x: 62, y: 330, w: 286, h: 110, icon: 'lucide:message-square', title: 'She gets a reply', sub: 'Introduces Ava as an AI assistant; answers only from what the owner made public', color: 'blue' })
d.node('f', { x: 62, y: 710, w: 286, h: 110, icon: 'lucide:rotate-ccw', title: 'Over the limit', sub: 'One polite note per hour, then silence', color: 'rose', accent: '#dc2626' })

const x1 = 432, x2 = 880, w = 410
d.node('c1', { x: x1, y: 170, w, h: 90, icon: 'lucide:fingerprint-pattern', title: 'Resolved as a stranger', sub: 'No linked person for this platform id (trust 1)', color: 'green' })
d.node('c2', { x: x2, y: 170, w, h: 90, icon: 'lucide:toggle-right', title: 'Receptionist on, and a DM?', sub: 'On by default. Off, or in a channel: a fixed reply only', color: 'green' })
d.node('c3', { x: x2, y: 310, w, h: 90, icon: 'lucide:timer', title: 'Within limits?', sub: 'Default 10 per hour and 30 per day per sender; its own daily budget bucket', color: 'green' })
d.node('c4', { x: x2, y: 450, w, h: 90, icon: 'lucide:brain', title: 'Model ready and budget left?', sub: 'If not: a fixed reply, and the message is still passed on', color: 'green' })
d.node('c5', { x: x2, y: 590, w, h: 150, icon: 'lucide:shield-check', title: 'One model turn, no tools', sub: 'Sees: the persona, the public profile, public notes, share labels, an optional away line, and this sender\'s own chat. Never sees the owner brief or private notes', color: 'green', accent: '#34a853' })
d.node('c6', { x: x2, y: 780, w, h: 120, icon: 'lucide:inbox', title: 'take_message', sub: 'The only output besides the reply. Stored as data in reception_message: name, contact, topic, message, deadline, claimed urgency', color: 'green' })
d.edge('c1', 'c2', { n: 1 }); d.edge('c2', 'c3', { from: 'b', to: 't', n: 2 }); d.edge('c3', 'c4', { from: 'b', to: 't', n: 3 }); d.edge('c4', 'c5', { from: 'b', to: 't', n: 4 })
d.edge('c5', 'c6', { from: 'b', to: 't', n: 5 })
d.edge('m', 'c1', { from: 'r', to: 'l', via: [[390, 220], [390, 215]] })
d.edge('c5', 'r', { from: 'l', to: 'b', kind: 'dashed', via: [[640, 665], [205, 665]], label: 'reply' })
d.edge('c3', 'f', { from: 'l', to: 'r', kind: 'deny', via: [[860, 355], [380, 355], [380, 765]], label: 'over limit' })

d.node('o1', { x: 1392, y: 170, w: 466, h: 100, icon: 'lucide:bell', title: 'Owner is told, with fixed text', sub: 'Stranger text never reaches the owner\'s model. App chat first; then push, Telegram or Slack by your interrupt-channel choice', color: 'violet' })
d.node('o2', { x: 1392, y: 320, w: 466, h: 100, icon: 'lucide:list-checks', title: 'People, taken messages', sub: 'Dashboard, People: new, done, dismissed or spam. Settings: on or off, show first name, public profile', color: 'violet' })
d.node('o3', { x: 1392, y: 470, w: 466, h: 110, icon: 'lucide:route', title: 'Triage and escalation (TRIAGE_ENABLED, off by default)', sub: 'handle, hold, interrupt, defer or verify. An interrupt climbs a ladder: app at once, a channel after 3 minutes, the other after 5, until you acknowledge. Seen is not acknowledged', color: 'violet' })
d.node('o4', { x: 1392, y: 630, w: 466, h: 90, icon: 'lucide:moon', title: 'Quiet hours and digests', sub: 'Default 22:00 to 07:00; VIPs may break through. Digests are built in code, no model call', color: 'violet' })
d.edge('c6', 'o1', { from: 'r', to: 'l', n: 6, via: [[1350, 840], [1350, 220]] })
d.edge('o1', 'o2', { from: 'b', to: 't' }); d.edge('o2', 'o3', { from: 'b', to: 't', kind: 'dashed' }); d.edge('o3', 'o4', { from: 'b', to: 't', kind: 'dashed' })
d.note(62, 950, 880, ['Meera writes: "Can Dana call me about the invoice? It is urgent." The receptionist says the owner is busy and records name, topic and the claimed urgency. The owner sees a card saying a stranger left a message. Urgency claimed by a stranger is never trusted: rules and the owner decide.'], { title: 'Worked example', color: 'amber' })
d.note(980, 950, 878, ['Strangers cannot link an account by typing a name: "I\'m Dana" starts a code challenge sent to Dana\'s already-linked channel, and the reply is identical whether or not Dana exists.'], { title: 'Impostors', color: 'rose' })
d.legend()
export default d
