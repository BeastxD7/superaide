// A tiny diagram library: zones, service boxes with icons, numbered orthogonal arrows, matrices and a legend.
// Each diagram file in this folder describes one picture with it and writes an SVG; render.mjs turns SVGs into PNGs.
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const ICONS = JSON.parse(readFileSync(join(here, 'icons.json'), 'utf8'))
const FONT = "-apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif"
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export const COLORS = {
  blue: { line: '#4f8ef7', fill: '#f2f7ff', ink: '#1d4ed8' },
  green: { line: '#34a853', fill: '#f1faf3', ink: '#15803d' },
  amber: { line: '#f2a900', fill: '#fff9ec', ink: '#a16207' },
  violet: { line: '#8b5cf6', fill: '#f7f3ff', ink: '#6d28d9' },
  rose: { line: '#ef5b7b', fill: '#fff4f6', ink: '#be123c' },
  slate: { line: '#94a3b8', fill: '#f8fafc', ink: '#475569' },
  teal: { line: '#14b8a6', fill: '#f0fdfa', ink: '#0f766e' },
}
const BRAND_HEX = {
  telegram: '#26A5E4', gmail: '#EA4335', googlecalendar: '#4285F4', postgresql: '#336791', nextdotjs: '#111111',
  react: '#149ECA', python: '#3776AB', fastapi: '#009688', docker: '#2496ED', cloudflare: '#F38020', github: '#181717',
  githubactions: '#2088FF', claude: '#D97757', anthropic: '#191919', google: '#4285F4', caddy: '#1F88C0',
  tailwindcss: '#06B6D4', deepgram: '#13EF93', elevenlabs: '#111111', ollama: '#111111', sqlalchemy: '#D71F00',
  apple: '#111111', brave: '#FB542B', googlegemini: '#8E75B2', mistralai: '#FA520F', uv: '#DE5FE9', ruff: '#D7FF64',
  pytest: '#0A9EDC', typescript: '#3178C6',
}

function iconSvg(name, x, y, size, color) {
  if (!name) return ''
  const [kind, id] = name.split(':')
  if (kind === 'slack') {
    const s = size / 256
    return `<g transform="translate(${x} ${y}) scale(${s})">${ICONS.slack.map(([c, d]) => `<path d="${d}" fill="${c}"/>`).join('')}</g>`
  }
  if (kind === 'brand') {
    const d = ICONS.brand[id]
    if (!d) throw new Error(`no brand icon ${id}`)
    let fill = BRAND_HEX[id] ?? '#334155'
    if (id === 'ruff') fill = '#6b7a00'
    return `<g transform="translate(${x} ${y}) scale(${size / 24})"><path d="${d}" fill="${fill}"/></g>`
  }
  const nodes = ICONS.lucide[id]
  if (!nodes) throw new Error(`no lucide icon ${id}`)
  const inner = nodes.map(([tag, a]) => `<${tag} ${Object.entries(a).map(([k, v]) => `${k}="${v}"`).join(' ')}/>`).join('')
  return `<g transform="translate(${x} ${y}) scale(${size / 24})" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</g>`
}

// Rough text measurement (no fonts at build time): average glyph widths for the system sans-serif.
const tw = (s, fs, bold = false) => s.length * fs * (bold ? 0.58 : 0.53)
export function wrap(text, maxPx, fs, bold = false) {
  const words = String(text).split(/\s+/)
  const lines = []
  let cur = ''
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w
    if (tw(next, fs, bold) > maxPx && cur) { lines.push(cur); cur = w } else cur = next
  }
  if (cur) lines.push(cur)
  return lines
}

function roundedPath(pts, r = 10) {
  let d = `M${pts[0][0]} ${pts[0][1]}`
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1], [cx, cy] = pts[i], [nx, ny] = pts[i + 1]
    const l1 = Math.hypot(cx - px, cy - py), l2 = Math.hypot(nx - cx, ny - cy)
    const rr = Math.min(r, l1 / 2, l2 / 2)
    const ax = cx - ((cx - px) / l1) * rr, ay = cy - ((cy - py) / l1) * rr
    const bx = cx + ((nx - cx) / l2) * rr, by = cy + ((ny - cy) / l2) * rr
    d += ` L${ax} ${ay} Q${cx} ${cy} ${bx} ${by}`
  }
  const last = pts[pts.length - 1]
  return d + ` L${last[0]} ${last[1]}`
}

export class Diagram {
  constructor({ name, title, subtitle, width = 1920, height = 1080 }) {
    Object.assign(this, { name, title, subtitle, width, height })
    this.nodes = {}
    this.out = []
    this.top = [] // edges, badges and labels are drawn above zones and boxes
  }

  zone(id, { x, y, w, h, label, color = 'blue', icon, right = false }) {
    const c = COLORS[color]
    this.out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="18" fill="${c.fill}" stroke="${c.line}" stroke-width="1.6" stroke-dasharray="${color === 'slate' ? '6 5' : '0'}"/>`)
    const ix = icon ? 30 : 0
    if (right) { this.out.push(`<text x="${x + w - 18}" y="${y + 28}" font-size="15" font-weight="700" letter-spacing="0.6" fill="${c.ink}" text-anchor="end">${esc(label.toUpperCase())}</text>`); return }
    this.out.push(iconSvg(icon, x + 16, y + 11, 22, c.ink))
    this.out.push(`<text x="${x + 18 + ix}" y="${y + 28}" font-size="15" font-weight="700" letter-spacing="0.6" fill="${c.ink}">${esc(label.toUpperCase())}</text>`)
  }

  node(id, { x, y, w = 230, h, icon, title, sub, color = 'slate', accent }) {
    const fs = 14.5, sfs = 12
    const textW = w - (icon ? 62 : 24)
    const tl = wrap(title, textW, fs, true)
    const sl = sub ? wrap(sub, textW, sfs) : []
    const need = 22 + tl.length * 18 + sl.length * 15
    h = h ?? Math.max(66, need)
    this.nodes[id] = { x, y, w, h }
    const c = COLORS[color]
    const stroke = accent ?? '#cbd5e1'
    const out = []
    out.push(`<rect x="${x + 1}" y="${y + 3}" width="${w}" height="${h}" rx="12" fill="#0f172a" opacity="0.07"/>`)
    out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="#fff" stroke="${stroke}" stroke-width="${accent ? 2 : 1.3}"/>`)
    if (icon) {
      out.push(`<rect x="${x + 10}" y="${y + h / 2 - 20}" width="40" height="40" rx="10" fill="${c.fill}" stroke="${c.line}" stroke-opacity="0.45"/>`)
      out.push(iconSvg(icon, x + 18, y + h / 2 - 12, 24, c.ink))
    }
    const tx = x + (icon ? 58 : 12)
    const total = tl.length * 18 + sl.length * 15
    let ty = y + h / 2 - total / 2 + 14
    for (const l of tl) { out.push(`<text x="${tx}" y="${ty}" font-size="${fs}" font-weight="700" fill="#0f172a">${esc(l)}</text>`); ty += 18 }
    ty -= 2
    for (const l of sl) { out.push(`<text x="${tx}" y="${ty + 2}" font-size="${sfs}" fill="#64748b">${esc(l)}</text>`); ty += 15 }
    this.out.push(out.join(''))
    return this.nodes[id]
  }

  // A free label (no box).
  text(x, y, s, { size = 13, bold = false, color = '#475569', anchor = 'start' } = {}) {
    this.out.push(`<text x="${x}" y="${y}" font-size="${size}" font-weight="${bold ? 700 : 400}" fill="${color}" text-anchor="${anchor}">${esc(s)}</text>`)
  }

  // A small note card (wrapped text) with a coloured left bar.
  note(x, y, w, lines, { color = 'amber', title } = {}) {
    const c = COLORS[color]
    const wrapped = lines.flatMap((l) => wrap(l, w - 28, 12.5))
    const h = 16 + (title ? 20 : 0) + wrapped.length * 17
    this.out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${c.fill}" stroke="${c.line}" stroke-opacity="0.7"/>`)
    this.out.push(`<rect x="${x}" y="${y + 8}" width="4" height="${h - 16}" rx="2" fill="${c.line}"/>`)
    let ty = y + 22
    if (title) { this.out.push(`<text x="${x + 16}" y="${ty}" font-size="13" font-weight="700" fill="${c.ink}">${esc(title)}</text>`); ty += 20 }
    for (const l of wrapped) { this.out.push(`<text x="${x + 16}" y="${ty}" font-size="12.5" fill="#334155">${esc(l)}</text>`); ty += 17 }
    return h
  }

  anchor(id, side, f = 0.5) {
    const n = this.nodes[id]
    if (!n) throw new Error(`unknown node ${id}`)
    return {
      r: [n.x + n.w, n.y + n.h * f], l: [n.x, n.y + n.h * f], t: [n.x + n.w * f, n.y], b: [n.x + n.w * f, n.y + n.h],
    }[side]
  }

  // Arrow from node a to node b. Sides: l r t b. `via` = extra waypoints. `n` = step number. `label` = text on the line.
  edge(a, b, { from = 'r', to = 'l', fa = 0.5, fb = 0.5, via, n, label, kind = 'solid', both = false, at = 0.5, lw } = {}) {
    const s = this.anchor(a, from, fa), e = this.anchor(b, to, fb)
    let pts = [s]
    if (via) pts.push(...via)
    else if (from === 'r' && to === 'l' && s[1] !== e[1]) { const mx = (s[0] + e[0]) / 2; pts.push([mx, s[1]], [mx, e[1]]) }
    else if (from === 'l' && to === 'r' && s[1] !== e[1]) { const mx = (s[0] + e[0]) / 2; pts.push([mx, s[1]], [mx, e[1]]) }
    else if (from === 'b' && to === 't' && s[0] !== e[0]) { const my = (s[1] + e[1]) / 2; pts.push([s[0], my], [e[0], my]) }
    else if (from === 't' && to === 'b' && s[0] !== e[0]) { const my = (s[1] + e[1]) / 2; pts.push([s[0], my], [e[0], my]) }
    pts.push(e)
    const color = { solid: '#334155', dashed: '#64748b', deny: '#dc2626', ok: '#16a34a', data: '#7c3aed' }[kind] ?? '#334155'
    const dash = kind === 'dashed' ? '7 6' : '0'
    const marker = `url(#arr-${kind === 'solid' || kind === 'dashed' ? kind : kind})`
    this.top.push(`<path d="${roundedPath(pts)}" fill="none" stroke="${color}" stroke-width="${lw ?? 2}" stroke-dasharray="${dash}" marker-end="${marker}"${both ? ` marker-start="url(#arrs-${kind})"` : ''}/>`)
    // place badge/label on the longest segment
    let best = 0, bi = 0
    for (let i = 0; i < pts.length - 1; i++) { const l = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]); if (l > best) { best = l; bi = i } }
    const [p, q] = [pts[bi], pts[bi + 1]]
    const mx = p[0] + (q[0] - p[0]) * at, my = p[1] + (q[1] - p[1]) * at
    if (label) {
      const lines = String(label).split('\n')
      const w = Math.max(...lines.map((l) => tw(l, 11.5))) + 14 + (n ? 22 : 0)
      const h = lines.length * 14 + 8
      this.top.push(`<rect x="${mx - w / 2}" y="${my - h / 2}" width="${w}" height="${h}" rx="${h / 2 > 11 ? 8 : h / 2}" fill="#fff" stroke="${color}" stroke-opacity="0.35"/>`)
      lines.forEach((l, i) => this.top.push(`<text x="${mx + (n ? 11 : 0)}" y="${my - h / 2 + 14 + i * 14}" font-size="11.5" fill="#1e293b" text-anchor="middle">${esc(l)}</text>`))
      if (n) this.badge(mx - w / 2 + 13, my, n, color)
    } else if (n) this.badge(mx, my, n, color)
  }

  badge(x, y, n, color = '#334155') {
    this.top.push(`<circle cx="${x}" cy="${y}" r="10.5" fill="${color}"/><text x="${x}" y="${y + 4.2}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">${esc(n)}</text>`)
  }

  // A table drawn with rects: cols = [{w,label}], rows = [[cell,...]]; cell = string or {t, bg, fg}.
  table(x, y, cols, rows, { rowH = 34, headH = 38, head = '#0f172a' } = {}) {
    let cx = x
    const W = cols.reduce((s, c) => s + c.w, 0)
    this.out.push(`<rect x="${x}" y="${y}" width="${W}" height="${headH + rows.length * rowH}" rx="10" fill="#fff" stroke="#cbd5e1"/>`)
    this.out.push(`<path d="M${x} ${y + headH} L${x} ${y + 10} Q${x} ${y} ${x + 10} ${y} L${x + W - 10} ${y} Q${x + W} ${y} ${x + W} ${y + 10} L${x + W} ${y + headH} Z" fill="${head}"/>`)
    cols.forEach((c) => {
      this.out.push(`<text x="${cx + c.w / 2}" y="${y + headH / 2 + 5}" font-size="13" font-weight="700" fill="#fff" text-anchor="middle">${esc(c.label)}</text>`)
      cx += c.w
    })
    rows.forEach((r, ri) => {
      let rx = x
      const ry = y + headH + ri * rowH
      if (ri % 2) this.out.push(`<rect x="${x}" y="${ry}" width="${W}" height="${rowH}" fill="#f8fafc"/>`)
      r.forEach((cell, ci) => {
        const c = typeof cell === 'string' ? { t: cell } : cell
        if (c.bg) this.out.push(`<rect x="${rx + 4}" y="${ry + 4}" width="${cols[ci].w - 8}" height="${rowH - 8}" rx="7" fill="${c.bg}"/>`)
        const left = ci === 0
        this.out.push(`<text x="${left ? rx + 12 : rx + cols[ci].w / 2}" y="${ry + rowH / 2 + 4.5}" font-size="${c.size ?? 12.5}" font-weight="${left || c.bold ? 700 : 400}" fill="${c.fg ?? '#1e293b'}" text-anchor="${left ? 'start' : 'middle'}">${esc(c.t)}</text>`)
        rx += cols[ci].w
      })
    })
    this.out.push(`<rect x="${x}" y="${y}" width="${W}" height="${headH + rows.length * rowH}" rx="10" fill="none" stroke="#cbd5e1"/>`)
    return headH + rows.length * rowH
  }

  legend(extra = []) {
    const y = this.height - 62
    const items = [
      { t: 'Numbered step: follow 1, 2, 3', draw: (x) => `<circle cx="${x + 10}" cy="${y + 24}" r="10.5" fill="#334155"/><text x="${x + 10}" y="${y + 28.2}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">1</text>`, w: 22 },
      { t: 'Request or data flow', draw: (x) => `<path d="M${x} ${y + 24} H${x + 34}" stroke="#334155" stroke-width="2" marker-end="url(#arr-solid)"/>`, w: 40 },
      { t: 'Background or asynchronous', draw: (x) => `<path d="M${x} ${y + 24} H${x + 34}" stroke="#64748b" stroke-width="2" stroke-dasharray="7 6" marker-end="url(#arr-dashed)"/>`, w: 40 },
      { t: 'Refused or blocked', draw: (x) => `<path d="M${x} ${y + 24} H${x + 34}" stroke="#dc2626" stroke-width="2" marker-end="url(#arr-deny)"/>`, w: 40 },
      ...extra,
    ]
    let x = 60
    this.out.push(`<rect x="40" y="${y}" width="${this.width - 80}" height="48" rx="12" fill="#fff" stroke="#e2e8f0"/>`)
    this.out.push(`<text x="58" y="${y - 8}" font-size="11" font-weight="700" letter-spacing="1" fill="#94a3b8">LEGEND</text>`)
    for (const it of items) {
      this.out.push(it.draw(x))
      this.out.push(`<text x="${x + it.w + 8}" y="${y + 29}" font-size="12.5" fill="#334155">${esc(it.t)}</text>`)
      x += it.w + 8 + tw(it.t, 12.5) + 34
    }
    this.out.push(`<text x="${this.width - 58}" y="${y + 29}" font-size="12" fill="#94a3b8" text-anchor="end">Superaide  |  docs/architecture/src/${esc(this.name)}.mjs</text>`)
  }

  svg() {
    const { width: W, height: H } = this
    const mk = (id, color, rev = false) => `<marker id="${id}" viewBox="0 0 10 10" refX="${rev ? 1 : 9}" refY="5" markerWidth="8" markerHeight="8" orient="${rev ? 'auto-start-reverse' : 'auto'}"><path d="M0 1 L10 5 L0 9 z" fill="${color}"/></marker>`
    const colors = { solid: '#334155', dashed: '#64748b', deny: '#dc2626', ok: '#16a34a', data: '#7c3aed' }
    const defs = Object.entries(colors).map(([k, c]) => mk(`arr-${k}`, c) + mk(`arrs-${k}`, c, true)).join('')
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="${FONT}">
<defs>${defs}</defs>
<rect width="${W}" height="${H}" fill="#ffffff"/>
<text x="40" y="52" font-size="30" font-weight="800" fill="#0f172a">${esc(this.title)}</text>
<text x="40" y="78" font-size="15" fill="#64748b">${esc(this.subtitle)}</text>
<rect x="0" y="0" width="8" height="${H}" fill="#6d5dfc"/>
${this.out.join('\n')}
${this.top.join('\n')}
</svg>
`
  }
}
