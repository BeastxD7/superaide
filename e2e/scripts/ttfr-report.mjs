// #411: combine the two measurements, check them against the targets, keep a trend.
//   node scripts/ttfr-report.mjs [history.json]        reads test-results/ttfr/{journey,cold-start}.json
// Hosted = the browser journey alone (nothing to install). Self-hosted = cold start + the same journey.
// Writes test-results/ttfr/ttfr.json and a markdown table (to $GITHUB_STEP_SUMMARY when set); appends one row to the
// history file (last 90 runs). Exits 1 when a target is missed. Numbers only: no names, emails or tokens.
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const TARGETS = { hosted: 180, self_hosted: 600 } // seconds (docs/onboarding-time.md)
const here = dirname(fileURLToPath(import.meta.url))
const dir = resolve(here, '..', 'test-results', 'ttfr')
const read = (name) => (existsSync(resolve(dir, name)) ? JSON.parse(readFileSync(resolve(dir, name), 'utf8')) : null)

export function summarize(journey, cold, now = new Date()) {
  const hosted = journey ? journey.total_seconds : null
  const selfHosted = journey && cold ? Math.round((journey.total_seconds + cold.total_seconds) * 10) / 10 : null
  return {
    measured_at: now.toISOString(),
    commit: journey?.commit ?? process.env.GITHUB_SHA ?? null,
    targets_seconds: TARGETS,
    hosted_seconds: hosted,
    self_hosted_seconds: selfHosted,
    hosted_ok: hosted !== null && hosted <= TARGETS.hosted,
    self_hosted_ok: selfHosted !== null && selfHosted <= TARGETS.self_hosted,
    journey_stages: journey?.stages ?? [],
    cold_start_stages: cold?.stages ?? [],
  }
}

export function trendTable(rows) {
  const lines = ['| Date | Commit | Hosted (s) | Self-hosted (s) |', '| --- | --- | ---: | ---: |']
  for (const r of rows.slice(-14).reverse())
    lines.push(`| ${r.measured_at.slice(0, 10)} | ${(r.commit ?? '').slice(0, 7)} | ${r.hosted_seconds ?? 'n/a'} | ${r.self_hosted_seconds ?? 'n/a'} |`)
  return lines.join('\n')
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const journey = read('journey.json')
  const cold = read('cold-start.json')
  if (!journey) {
    console.error('ttfr-report: no journey.json: the browser journey did not finish')
    process.exit(1)
  }
  const result = summarize(journey, cold)
  mkdirSync(dir, { recursive: true })
  writeFileSync(resolve(dir, 'ttfr.json'), JSON.stringify(result, null, 2) + '\n')

  const historyPath = process.argv[2]
  let history = []
  if (historyPath && existsSync(historyPath)) history = JSON.parse(readFileSync(historyPath, 'utf8'))
  history.push({ measured_at: result.measured_at, commit: result.commit, hosted_seconds: result.hosted_seconds, self_hosted_seconds: result.self_hosted_seconds })
  history = history.slice(-90)
  if (historyPath) writeFileSync(historyPath, JSON.stringify(history, null, 2) + '\n')

  const stageRows = (stages) => stages.filter((s) => s.stage !== 'start').map((s) => `| ${s.stage} | ${(s.step_ms / 1000).toFixed(1)} |`).join('\n')
  const md = [
    '## Time to first useful reply',
    '',
    `Hosted: **${result.hosted_seconds}s** (target ${TARGETS.hosted}s) ${result.hosted_ok ? 'OK' : 'MISSED'}`,
    `Self-hosted: **${result.self_hosted_seconds ?? 'n/a'}s** (target ${TARGETS.self_hosted}s) ${result.self_hosted_ok ? 'OK' : 'MISSED'}`,
    '',
    '| Journey stage | Seconds since the one before |',
    '| --- | ---: |',
    stageRows(result.journey_stages),
    '',
    '| Cold-start stage | Seconds since the one before |',
    '| --- | ---: |',
    stageRows(result.cold_start_stages),
    '',
    '### Trend (last 14 runs)',
    '',
    trendTable(history),
    '',
  ].join('\n')
  console.log(md)
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, md + '\n')
  if (!result.hosted_ok || (cold && !result.self_hosted_ok)) process.exit(1)
}
