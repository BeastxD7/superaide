#!/usr/bin/env node
// Balanced CI shards for Playwright, by measured time instead of test count.
//
//   node scripts/shard.mjs <shard> <of>          print the spec files of shard N (1-based), one per line
//   node scripts/shard.mjs --update <report.json>  rewrite shard-timings.json from a Playwright JSON report
//
// E2E_ONLY (space or newline separated spec paths relative to e2e/, e.g. "tests/hire-path.spec.ts") limits the run
// to those specs: scripts/select_tests.py sets it when a pull request only needs some of them. The selected specs
// are still balanced over the shards, and a shard that gets none prints nothing (the workflow then skips it).
//
// Playwright's own --shard splits by test count; a few slow specs made one shard twice as long as
// the others. Here every spec file under tests/ (minus the opt-in proxy and dev projects) is placed,
// slowest first, on the shard with the least time so far. A file missing from shard-timings.json gets
// the average weight, so a new spec still runs on exactly one shard (nothing can fall out).
import { readFileSync, readdirSync, writeFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const timingsPath = join(root, 'shard-timings.json')

function specFiles(dir = join(root, 'tests')) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...specFiles(p))
    else if (/\.spec\.ts$/.test(name) && !/\.(proxy|dev)\.spec\.ts$/.test(name)) out.push(relative(root, p))
  }
  return out.sort()
}

function walk(suite, acc) {
  for (const s of suite.suites ?? []) walk(s, acc)
  for (const spec of suite.specs ?? []) {
    for (const t of spec.tests ?? []) for (const r of t.results ?? []) acc[spec.file] = (acc[spec.file] ?? 0) + r.duration / 1000
  }
}

const [a, b] = process.argv.slice(2)
if (a === '--update') {
  const report = JSON.parse(readFileSync(b, 'utf8'))
  const acc = {}
  for (const s of report.suites) walk(s, acc)
  const rounded = Object.fromEntries(Object.entries(acc).sort().map(([k, v]) => [k.startsWith('tests/') ? k : `tests/${k}`, Math.round(v)]))
  writeFileSync(timingsPath, JSON.stringify(rounded, null, 2) + '\n')
  console.error(`wrote ${Object.keys(rounded).length} files, ${Object.values(rounded).reduce((x, y) => x + y, 0)} s total`)
} else {
  const shard = Number(a)
  const of = Number(b)
  if (!Number.isInteger(shard) || !Number.isInteger(of) || shard < 1 || shard > of) {
    console.error('usage: shard.mjs <shard 1..of> <of> | --update <report.json>')
    process.exit(2)
  }
  const timings = JSON.parse(readFileSync(timingsPath, 'utf8'))
  const only = process.env.E2E_ONLY?.split(/\s+/).filter(Boolean)
  const files = only?.length ? specFiles().filter((f) => only.includes(f)) : specFiles()
  const known = files.filter((f) => f in timings)
  const avg = known.length ? known.reduce((s, f) => s + timings[f], 0) / known.length : 10
  const weight = (f) => timings[f] ?? avg
  const bins = Array.from({ length: of }, () => ({ t: 0, files: [] }))
  for (const f of [...files].sort((x, y) => weight(y) - weight(x) || x.localeCompare(y))) {
    const bin = bins.reduce((m, c) => (c.t < m.t ? c : m))
    bin.files.push(f)
    bin.t += weight(f)
  }
  console.error(bins.map((x, i) => `shard ${i + 1}: ${x.files.length} files, ~${Math.round(x.t)} s`).join('\n'))
  for (const f of bins[shard - 1].files.sort()) console.log(f)
}
