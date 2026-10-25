// Dev-time helper: copies the icons the diagrams use out of node_modules into src/icons.json, so rendering needs
// no network and no node_modules. Run from the repo root: node docs/architecture/build-icons.mjs [path/to/frontend/node_modules]
// simple-icons (CC0) gives brand marks, lucide-react (ISC) gives generic glyphs, Slack's mark comes from
// frontend/src/components/brands/marks.tsx.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const nm = process.argv[2] ?? join(here, '../../frontend/node_modules')
const brands = ['telegram', 'gmail', 'googlecalendar', 'postgresql', 'nextdotjs', 'react', 'python', 'fastapi', 'docker',
  'cloudflare', 'github', 'githubactions', 'claude', 'anthropic', 'google', 'caddy', 'tailwindcss', 'deepgram',
  'elevenlabs', 'ollama', 'sqlalchemy', 'apple', 'brave', 'googlegemini', 'mistralai', 'uv', 'ruff', 'pytest', 'typescript']
const lucide = ['brain', 'database', 'mail', 'calendar', 'key-round', 'lock', 'user', 'users', 'bell', 'clock', 'globe',
  'mic', 'server', 'cloud', 'check', 'file-text', 'message-square', 'filter', 'search', 'workflow', 'hard-drive', 'timer',
  'eye', 'bot', 'settings', 'layout-dashboard', 'terminal', 'flask-conical', 'rocket', 'power', 'zap', 'network', 'archive',
  'refresh-cw', 'fingerprint-pattern', 'hand', 'gavel', 'scale', 'ban', 'receipt', 'coins', 'siren', 'id-card', 'layers',
  'link', 'webhook', 'smartphone', 'monitor', 'play', 'package', 'shield-check', 'shield', 'user-check', 'user-round',
  'moon', 'pen-line', 'send', 'badge-check', 'list-checks', 'circle-dollar-sign', 'route', 'inbox', 'git-branch',
  'git-merge', 'upload', 'download', 'trash-2', 'sparkles', 'scan-eye', 'phone', 'headset', 'footprints', 'stamp',
  'file-signature', 'toggle-right', 'monitor-smartphone', 'folder-lock', 'history', 'rotate-ccw', 'radio', 'cpu',
  'activity', 'book-open', 'notebook-pen', 'tags', 'timer-reset', 'octagon-x', 'building-2', 'laptop',
  'repeat', 'circle-user', 'door-open', 'ghost', 'quote', 'type', 'binary', 'text-search', 'vector-square', 'share-2']

const out = { brand: {}, lucide: {} }
for (const b of brands) {
  const svg = readFileSync(join(nm, 'simple-icons/icons', `${b}.svg`), 'utf8')
  out.brand[b] = svg.match(/<path d="([^"]+)"/)[1]
}
for (const n of lucide) {
  let src
  try { src = readFileSync(join(nm, 'lucide-react/dist/esm/icons', `${n}.mjs`), 'utf8') } catch { console.warn('missing lucide icon', n); continue }
  const alias = src.match(/from '\.\/([a-z0-9-]+)\.mjs'/)
  if (alias && !src.includes('node: [')) src = readFileSync(join(nm, 'lucide-react/dist/esm/icons', `${alias[1]}.mjs`), 'utf8')
  const start = src.indexOf('node: [') + 6
  const one = src.slice(start, src.indexOf('\n', start)).trimEnd()
  const end = one.endsWith(']]') ? start + one.length : src.indexOf('\n  ]', start) + 4
  if (start < 6 || end < 0) { console.warn('cannot parse lucide icon', n); continue }
  const node = eval("(" + src.slice(start, end) + ")")
  out.lucide[n] = node.map(([tag, attrs]) => {
    const { key, ...rest } = attrs
    return [tag, rest]
  })
}
// Slack: four-colour mark, viewBox 0 0 256 256
const marks = readFileSync(join(here, '../../frontend/src/components/brands/marks.tsx'), 'utf8')
const block = marks.match(/const SLACK_PATHS = \[([\s\S]*?)\] as const/)[1]
out.slack = [...block.matchAll(/\['(#[0-9a-f]+)', '([^']+)'\]/g)].map((m) => [m[1], m[2]])
writeFileSync(join(here, 'src/icons.json'), JSON.stringify(out))
console.log('icons:', Object.keys(out.brand).length, 'brand,', Object.keys(out.lucide).length, 'lucide, slack', out.slack.length)
