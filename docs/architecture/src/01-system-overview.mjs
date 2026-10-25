import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '01-system-overview',
  title: 'Superaide: system overview',
  subtitle: 'One assistant (Ava) that acts for one owner across web, Telegram and Slack. Identity, memory access and actions are decided in code; the model only writes text.',
  height: 1080,
})
d.zone('users', { x: 40, y: 120, w: 300, h: 840, label: 'Users & channels', color: 'blue', icon: 'lucide:users' })
d.zone('edge', { x: 380, y: 120, w: 300, h: 600, label: 'Edge', color: 'violet', icon: 'lucide:globe' })
d.zone('app', { x: 720, y: 120, w: 600, h: 600, label: 'Application', color: 'green', icon: 'lucide:server' })
d.zone('ext', { x: 1360, y: 120, w: 520, h: 600, label: 'External services', color: 'slate', icon: 'lucide:zap' })
d.zone('data', { x: 380, y: 750, w: 1500, h: 210, label: 'Data', color: 'amber', icon: 'lucide:database' })

d.node('owner', { x: 62, y: 170, w: 256, icon: 'lucide:user-check', title: 'The owner', sub: 'Talks to Ava, approves actions, sets the rules', color: 'blue' })
d.node('web', { x: 62, y: 290, w: 256, icon: 'lucide:monitor-smartphone', title: 'Web app and dashboard', sub: 'Browser or installed PWA, in-app chat', color: 'blue' })
d.node('tg', { x: 62, y: 410, w: 256, icon: 'brand:telegram', title: 'Telegram', sub: 'Owner DM, colleagues, strangers like Meera', color: 'blue' })
d.node('sl', { x: 62, y: 530, w: 256, icon: 'slack:', title: 'Slack', sub: 'DMs and shared channels', color: 'blue' })
d.node('strangers', { x: 62, y: 650, w: 256, icon: 'lucide:ghost', title: 'Strangers and colleagues', sub: 'Unknown senders get the receptionist, never memory', color: 'blue' })
d.node('console', { x: 62, y: 780, w: 256, icon: 'lucide:terminal', title: 'Operator console', sub: '/console with the admin token: people, spend, safety log', color: 'blue' })

d.node('tunnel', { x: 402, y: 190, w: 256, h: 84, icon: 'brand:cloudflare', title: 'Tunnel or HTTPS', sub: 'Cloudflare quick tunnel (share.sh) or Caddy on a VPS', color: 'violet' })
d.node('next', { x: 402, y: 340, w: 256, h: 120, icon: 'brand:nextdotjs', title: 'Next.js 16 server', sub: 'Pages, plus a same-origin proxy: /api/* goes to FastAPI, so the session cookie stays first-party', color: 'violet' })

d.node('api', { x: 742, y: 190, w: 255, h: 84, icon: 'brand:fastapi', title: 'FastAPI backend', sub: 'JSON only: accounts, chat, approvals, operator API', color: 'green' })
d.node('runner', { x: 1042, y: 190, w: 255, h: 84, icon: 'lucide:radio', title: 'Channel runners', sub: 'Telegram polling, Slack Socket Mode', color: 'green' })
d.node('pipe', { x: 742, y: 320, w: 555, h: 84, icon: 'lucide:workflow', title: 'Message pipeline', sub: 'verify, identity, audience gate, airlock, think, policy, reply', color: 'green' })
d.node('mem', { x: 742, y: 450, w: 255, h: 84, icon: 'lucide:brain', title: 'Memory with audiences', sub: 'The audience filter runs inside the SQL query', color: 'green' })
d.node('policy', { x: 1042, y: 450, w: 255, h: 84, icon: 'lucide:gavel', title: 'Policy and approvals', sub: 'Side effects need the owner; spend cap; kill switch', color: 'green' })
d.node('jobs', { x: 742, y: 590, w: 255, h: 84, icon: 'lucide:timer', title: 'Jobs worker', sub: 'Reminders, digests, mail triage, sleep job', color: 'green' })
d.node('cap', { x: 1042, y: 590, w: 255, h: 84, icon: 'lucide:layers', title: 'Capabilities', sub: 'Gmail, Calendar, web research, voice', color: 'green' })

d.node('tgapi', { x: 1382, y: 190, w: 225, h: 84, icon: 'brand:telegram', title: 'Telegram Bot API', sub: 'Long polling', color: 'slate' })
d.node('slapi', { x: 1640, y: 190, w: 225, h: 84, icon: 'slack:', title: 'Slack API', sub: 'Socket Mode, no public URL', color: 'slate' })
d.node('llm', { x: 1382, y: 320, w: 225, h: 84, icon: 'brand:claude', title: 'Model providers', sub: 'Claude by default; others by preset', color: 'slate' })
d.node('dg', { x: 1640, y: 320, w: 225, h: 84, icon: 'brand:deepgram', title: 'Deepgram', sub: 'Live voice, only if a key is set', color: 'slate' })
d.node('goog', { x: 1382, y: 590, w: 225, h: 84, icon: 'brand:google', title: 'Google APIs', sub: 'Sign-in, Gmail, Calendar', color: 'slate' })

d.node('bak', { x: 402, y: 800, w: 300, h: 84, icon: 'lucide:archive', title: 'Backups', sub: 'Scripted database dumps (deploy/)', color: 'amber' })
d.node('pg', { x: 742, y: 800, w: 400, h: 100, icon: 'brand:postgresql', title: 'Postgres 17 + pgvector', sub: 'The only store. tenant_id and agent_id on every row, Row-Level Security', color: 'amber' })
d.node('vault', { x: 1180, y: 800, w: 340, h: 84, icon: 'lucide:key-round', title: 'Credentials vault', sub: 'AES-256-GCM ciphertext in Postgres', color: 'amber' })
d.node('key', { x: 1560, y: 800, w: 300, h: 84, icon: 'brand:apple', title: 'Master key', sub: 'macOS Keychain, key file or env', color: 'amber' })

d.edge('web', 'tunnel', { from: 'r', to: 'l', n: 1, label: 'HTTPS', via: [[360, 332], [360, 232]] })
d.edge('tunnel', 'next', { from: 'b', to: 't', n: 2 })
d.edge('next', 'api', { from: 'r', to: 'l', n: 3, label: '/api/*', via: [[700, 400], [700, 232]] })
d.edge('api', 'pipe', { from: 'b', to: 't', n: 4 })
d.edge('runner', 'pipe', { from: 'b', to: 't' })
d.edge('pipe', 'mem', { from: 'b', to: 't', fa: 0.23, n: 5 })
d.edge('pipe', 'policy', { from: 'b', to: 't', fa: 0.77 })
d.edge('mem', 'jobs', { from: 'b', to: 't', kind: 'dashed' })
d.edge('policy', 'cap', { from: 'b', to: 't', n: 7, label: 'approved' })
d.edge('pipe', 'llm', { from: 'r', to: 'l', n: 6, label: 'capped call' })
d.edge('runner', 'tgapi', { from: 'r', to: 'l', kind: 'dashed', label: 'polls' })
d.edge('runner', 'slapi', { from: 't', to: 't', kind: 'dashed', via: [[1170, 166], [1752, 166]] })
d.edge('cap', 'goog', { from: 'r', to: 'l', kind: 'dashed' })
d.edge('cap', 'dg', { from: 'r', to: 'b', kind: 'dashed', via: [[1330, 632], [1330, 440], [1752, 440]], fa: 0.2 })
d.edge('jobs', 'pg', { from: 'b', to: 't', fb: 0.3, n: 8, kind: 'dashed' })
d.edge('cap', 'vault', { from: 'b', to: 't', fa: 0.3, fb: 0.2, via: [[1119, 700], [1119, 770], [1248, 770]], kind: 'data' })
d.edge('vault', 'key', { from: 'r', to: 'l', kind: 'data' })
d.edge('pg', 'bak', { from: 'l', to: 'r', kind: 'dashed' })

d.legend()
export default d
