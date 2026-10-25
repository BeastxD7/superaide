import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '02-runtime-topology',
  title: 'Runtime and deployment topology',
  subtitle: 'The same code runs two ways: on a Mac with start.command or share.sh, or on a VPS from docker-compose.prod.yml behind Caddy.',
  height: 1130,
})
d.zone('users', { x: 40, y: 120, w: 250, h: 880, label: 'Users', color: 'blue', icon: 'lucide:users' })
d.zone('edge', { x: 330, y: 120, w: 270, h: 880, label: 'Edge', color: 'violet', icon: 'lucide:globe' })
d.zone('mac', { x: 640, y: 120, w: 900, h: 430, label: 'Mac: start.command or share.sh', color: 'green', icon: 'brand:apple' })
d.zone('vps', { x: 640, y: 580, w: 900, h: 420, label: 'VPS: docker-compose.prod.yml', color: 'teal', icon: 'brand:docker' })
d.zone('ext', { x: 1580, y: 120, w: 300, h: 880, label: 'External services', color: 'slate', icon: 'lucide:zap' })

d.node('browser', { x: 62, y: 178, w: 206, h: 84, icon: 'lucide:monitor', title: 'Browser or PWA', sub: 'App, dashboard, /console', color: 'blue' })
d.node('tgc', { x: 62, y: 300, w: 206, h: 84, icon: 'brand:telegram', title: 'Telegram client', sub: 'Talks to the bot', color: 'blue' })
d.node('slc', { x: 62, y: 430, w: 206, h: 84, icon: 'slack:', title: 'Slack client', sub: 'Talks to the app', color: 'blue' })

d.node('tunnel', { x: 352, y: 170, w: 226, h: 100, icon: 'brand:cloudflare', title: 'Cloudflare tunnel', sub: 'Quick tunnel prints a public https address (new each run unless a hostname is set)', color: 'violet' })
d.node('caddy', { x: 352, y: 650, w: 226, h: 120, icon: 'brand:caddy', title: 'Caddy :443', sub: 'Let\'s Encrypt HTTPS, HSTS, rate limits, 12 MB body cap. The only published service', color: 'violet' })

d.node('next', { x: 662, y: 170, w: 250, h: 100, icon: 'brand:nextdotjs', title: 'Next.js :3000', sub: 'Pages and the /api/* proxy; sets the CSP nonce and redirects signed-out visitors', color: 'green' })
d.node('api', { x: 952, y: 170, w: 270, h: 100, icon: 'brand:fastapi', title: 'FastAPI :8000', sub: 'One process: API, jobs and channel runners. share.sh uses port 8010', color: 'green' })
d.node('pgm', { x: 662, y: 320, w: 250, h: 84, icon: 'brand:postgresql', title: 'Postgres 17 + pgvector', sub: 'Docker, or embedded in .local-db/', color: 'amber' })
d.node('kc', { x: 952, y: 320, w: 270, h: 84, icon: 'lucide:key-round', title: 'Master key', sub: 'macOS Keychain via keyring', color: 'amber' })
d.node('upd', { x: 662, y: 440, w: 560, h: 84, icon: 'lucide:refresh-cw', title: 'share.sh auto-update (every 5 minutes)', sub: 'fast-forward main, back up the database to ~/superaide-backups, uv sync, alembic upgrade, rebuild Next.js', color: 'green' })

d.node('web', { x: 662, y: 650, w: 250, h: 100, icon: 'brand:nextdotjs', title: 'web (Next.js)', sub: 'Forwards /api/* to the api service on the internal network', color: 'teal' })
d.node('vapi', { x: 952, y: 650, w: 270, h: 100, icon: 'brand:fastapi', title: 'api (uvicorn, 1 worker)', sub: 'Hosted mode, docs and simulator off, secure cookies. Runs as role superaide_app', color: 'teal' })
d.node('mig', { x: 1262, y: 650, w: 250, h: 100, icon: 'brand:sqlalchemy', title: 'migrate (one shot)', sub: 'alembic upgrade head, then superaide db-app-role', color: 'teal' })
d.node('wrk', { x: 662, y: 810, w: 250, h: 100, icon: 'lucide:timer', title: 'worker', sub: 'superaide worker: jobs, Telegram polling and Slack Socket Mode', color: 'teal' })
d.node('vdb', { x: 952, y: 810, w: 270, h: 100, icon: 'brand:postgresql', title: 'db: Postgres 17', sub: 'Internal network only, no route out', color: 'amber' })
d.node('bak', { x: 1262, y: 810, w: 250, h: 100, icon: 'lucide:archive', title: 'backup.sh (cron 03:15)', sub: 'pg_dump, 14 days kept. The master key is NOT in the dump: keep .env elsewhere', color: 'amber' })

d.node('llm', { x: 1602, y: 178, w: 256, h: 84, icon: 'brand:claude', title: 'Model providers', sub: 'Claude or a connected provider', color: 'slate' })
d.node('goog', { x: 1602, y: 290, w: 256, h: 84, icon: 'brand:google', title: 'Google APIs', sub: 'Sign-in, Gmail, Calendar', color: 'slate' })
d.node('dg', { x: 1602, y: 410, w: 256, h: 84, icon: 'brand:deepgram', title: 'Deepgram', sub: 'Live voice, optional', color: 'slate' })
d.node('tga', { x: 1602, y: 650, w: 256, h: 84, icon: 'brand:telegram', title: 'Telegram Bot API', sub: 'Long polling out of the worker', color: 'slate' })
d.node('sla', { x: 1602, y: 780, w: 256, h: 84, icon: 'slack:', title: 'Slack API', sub: 'Socket Mode out of the worker', color: 'slate' })

d.edge('browser', 'tunnel', { n: 1 })
d.edge('tunnel', 'next', { n: 2 })
d.edge('next', 'api', { n: 3 })
d.edge('api', 'pgm', { from: 'b', to: 't', fa: 0.1, via: [[979, 295], [787, 295]], kind: 'data' })
d.edge('api', 'kc', { from: 'b', to: 't', fa: 0.7, fb: 0.7, kind: 'data' })
d.edge('api', 'llm', { from: 'r', to: 'l', label: 'capped calls' })
d.edge('api', 'goog', { from: 'r', to: 'l', kind: 'dashed', via: [[1290, 220], [1290, 332]] })
d.edge('api', 'dg', { from: 'r', to: 'l', kind: 'dashed', via: [[1290, 220], [1290, 452]] })
d.edge('browser', 'caddy', { n: 4, via: [[310, 212], [310, 710]] })
d.edge('caddy', 'web', { n: 5 })
d.edge('web', 'vapi', { n: 6 })
d.edge('vapi', 'vdb', { from: 'b', to: 't', kind: 'data' })
d.edge('wrk', 'vdb', { from: 'r', to: 'l', kind: 'data' })
d.edge('vdb', 'bak', { from: 'r', to: 'l', kind: 'dashed' })
d.edge('mig', 'vdb', { from: 'b', to: 'r', kind: 'dashed', via: [[1387, 790], [1240, 790], [1240, 860]] })
d.edge('wrk', 'tga', { from: 'b', to: 'l', kind: 'dashed', via: [[787, 940], [1565, 940], [1565, 692]] })
d.edge('wrk', 'sla', { from: 'b', to: 'l', kind: 'dashed', via: [[820, 940], [1580, 940], [1580, 822]] })
d.note(40 + 0, 760, 250, ['Webhooks (/webhooks/*) for Telegram and Calendar push also arrive through the edge when PUBLIC_BASE_URL is https. Polling and Socket Mode need no inbound port.'], { title: 'No inbound port needed', color: 'violet' })
d.legend()
export default d
