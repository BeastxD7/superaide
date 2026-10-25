// Renders docs/architecture/src/*.svg to docs/architecture/*.png with Playwright (Chromium, 2x scale, no network).
//   node docs/architecture/render.mjs            build every diagram from its src/*.mjs, then render
//   node docs/architecture/render.mjs 03 05      only the diagrams whose file name starts with these prefixes
// Playwright comes from e2e/node_modules (run `npm ci` in e2e/ first).
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, 'src')
const require = createRequire(join(here, '../../e2e/package.json'))
const { chromium } = require('playwright')

const only = process.argv.slice(2)
const files = readdirSync(src).filter((f) => /^\d\d-.*\.mjs$/.test(f) && (!only.length || only.some((p) => f.startsWith(p))))
const browser = await chromium.launch()
for (const f of files) {
  const base = f.replace(/\.mjs$/, '')
  const diagram = (await import(pathToFileURL(join(src, f)).href)).default
  const svg = diagram.svg()
  writeFileSync(join(src, `${base}.svg`), svg)
  const page = await browser.newPage({ viewport: { width: diagram.width, height: diagram.height }, deviceScaleFactor: 2 })
  await page.setContent(`<!doctype html><html><body style="margin:0;background:#fff">${svg}</body></html>`)
  await page.screenshot({ path: join(here, `${base}.png`), clip: { x: 0, y: 0, width: diagram.width, height: diagram.height } })
  await page.close()
  console.log('rendered', base)
}
await browser.close()
