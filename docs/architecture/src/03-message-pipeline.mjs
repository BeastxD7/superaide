import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '03-message-pipeline',
  title: 'The message pipeline',
  subtitle: 'backend/src/superaide/pipeline.py, Agent.handle. Every channel becomes one InboundMessage and takes the same path; the model is only called at step 4.',
  height: 1170,
})
d.zone('in', { x: 40, y: 120, w: 270, h: 380, label: 'Channels', color: 'blue', icon: 'lucide:message-square' })
d.zone('pipe', { x: 350, y: 120, w: 1550, h: 380, label: 'Agent.handle (one database session per message)', color: 'green', icon: 'lucide:workflow' })
d.zone('out', { x: 350, y: 540, w: 1550, h: 260, label: 'What a step can lead to', color: 'rose', icon: 'lucide:octagon-x', right: true })
d.zone('ex', { x: 40, y: 840, w: 1840, h: 190, label: 'Worked examples', color: 'amber', icon: 'lucide:book-open' })

d.node('tg', { x: 62, y: 170, w: 226, h: 70, icon: 'brand:telegram', title: 'Telegram', sub: 'DM or group', color: 'blue' })
d.node('sl', { x: 62, y: 270, w: 226, h: 70, icon: 'slack:', title: 'Slack', sub: 'DM, @-mention in a channel', color: 'blue' })
d.node('wb', { x: 62, y: 370, w: 226, h: 70, icon: 'lucide:monitor', title: 'Web chat', sub: 'Session cookie, always a DM', color: 'blue' })

const xs = [372, 590, 808, 1026, 1244, 1462, 1680]
const steps = [
  ['verify', 'lucide:shield-check', '1 Verify', 'Webhook secret or Slack signature; unverified is dropped'],
  ['resolve', 'lucide:fingerprint-pattern', '2 Resolve identity', 'Immutable platform id to ChannelIdentity and Person'],
  ['aud', 'lucide:eye', '3 Audience gate', 'audience_for(): who may see this conversation'],
  ['safe', 'lucide:power', 'Safeguards', 'Duplicate check, halt word, kill switch'],
  ['think', 'lucide:brain', '4 Think', 'Prompt from the charter, brief and visible memories; tool loop (max 6 steps)'],
  ['pol', 'lucide:gavel', '5 Policy', 'decide(): refuse, or create an approval card'],
  ['rep', 'lucide:send', '6 Reply', 'Messenger sends; event logged; citations stripped outside DMs'],
]
steps.forEach(([id, icon, title, sub], i) => d.node(id, { x: xs[i], y: 190, w: 200, h: 150, icon, title, sub, color: 'green', accent: id === 'think' ? '#34a853' : undefined }))

d.edge('tg', 'verify', { via: [[330, 205], [330, 265]] })
d.edge('sl', 'verify', { via: [[330, 305], [330, 265]] })
d.edge('wb', 'verify', { via: [[330, 405], [330, 265]] })
for (let i = 0; i < 6; i++) d.edge(steps[i][0], steps[i + 1][0], { n: i === 3 ? undefined : undefined })

d.node('drop', { x: 372, y: 600, w: 200, h: 120, icon: 'lucide:ban', title: 'Dropped', sub: 'Audit event dropped_unverified; nothing else runs', color: 'rose', accent: '#dc2626' })
d.node('str', { x: 590, y: 600, w: 200, h: 150, icon: 'lucide:ghost', title: 'Stranger mode', sub: 'Unknown sender: receptionist if on, else a fixed reply. No memory, no tools', color: 'rose', accent: '#dc2626' })
d.node('dm', { x: 808, y: 600, w: 200, h: 150, icon: 'lucide:filter', title: 'DM vs shared', sub: 'DM: owner facts. Any channel or group: public-level facts only, restricted tools', color: 'rose' })
d.node('frz', { x: 1026, y: 600, w: 200, h: 120, icon: 'lucide:power', title: 'Frozen or halted', sub: 'No model call, no reply', color: 'rose', accent: '#dc2626' })
d.node('air', { x: 1244, y: 600, w: 200, h: 150, icon: 'lucide:scan-eye', title: 'Airlock and budget', sub: 'Others\' contact details become placeholders; BudgetGuard reserves worst-case cost', color: 'rose' })
d.node('apr', { x: 1462, y: 600, w: 200, h: 150, icon: 'lucide:stamp', title: 'Approval card', sub: 'Side effects wait for the owner. Not allowed ones are refused', color: 'rose' })
d.node('mdl', { x: 1680, y: 600, w: 200, h: 120, icon: 'brand:claude', title: 'Model', sub: 'Text and a proposal', color: 'slate' })
d.edge('verify', 'drop', { from: 'b', to: 't', kind: 'deny' })
d.edge('resolve', 'str', { from: 'b', to: 't', kind: 'deny' })
d.edge('aud', 'dm', { from: 'b', to: 't' })
d.edge('safe', 'frz', { from: 'b', to: 't', kind: 'deny' })
d.edge('think', 'air', { from: 'b', to: 't' })
d.edge('air', 'mdl', { from: 'b', to: 'b', via: [[1344, 776], [1780, 776]] })
d.edge('pol', 'apr', { from: 'b', to: 't' })

d.note(60, 880, 560, ['Meera, a stranger, writes to the owner\'s Telegram bot. Step 1 passes (the bot token is genuine). Step 2 finds no linked person for her Telegram id, so she is a stranger. The receptionist answers from public facts only and records a message for the owner. No memory is read, no tool runs.'], { title: 'Meera messages the bot', color: 'rose' })
d.note(650, 880, 560, ['The owner types "give me a digest" in the DM: audience is the owner alone, private notes are visible and tools are all available. The same words in a shared Slack channel: the audience contains unknown people, so only public-level notes are visible, mail and calendar tools are refused and nothing is learned.'], { title: 'The owner asks for a digest', color: 'amber' })
d.note(1240, 880, 620, ['The model proposes gmail_send. Step 5 does not send: decide() records an Approval card sealed with a hash of the exact payload. The owner approves in the app, and only then does the executor run, if the draft is unchanged.'], { title: 'An email send', color: 'green' })
d.legend()
export default d
