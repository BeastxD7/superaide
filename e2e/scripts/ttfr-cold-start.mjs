// #411, the self-hosted half: how long from a fresh checkout to an app a person can open, step by step.
//   node scripts/ttfr-cold-start.mjs [--skip-install]     writes test-results/ttfr/cold-start.json
// Times: install_backend (uv sync), install_frontend (npm ci), build_frontend, database_ready (create + migrate a fresh
// database), api_up (until /health answers), web_up (until the site answers). Run it on a clean checkout with no caches
// for the honest number (the nightly workflow does); on a used machine it is a lower bound.
// Needs Postgres at E2E_PG_ADMIN_URL (default localhost:5432). It only creates and drops `superaide_ttfr_cold`.
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import pg from 'pg'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..', '..')
const { apiEnv, PG_ADMIN_URL } = await import('../src/env.ts')
const skipInstall = process.argv.includes('--skip-install')
const API_PORT = 8190
const WEB_PORT = 3190
const DB = 'superaide_ttfr_cold'
const dbUrl = (() => {
  const u = new URL(PG_ADMIN_URL)
  u.pathname = `/${DB}`
  return u.toString().replace(/^postgres(ql)?:\/\//, 'postgresql+psycopg://')
})()

const stages = []
const t0 = performance.now()
let last = 0
const mark = (stage) => {
  const at = Math.round(performance.now() - t0)
  stages.push({ stage, at_ms: at, step_ms: at - last })
  last = at
  console.log(`ttfr-cold: ${stage} at ${(at / 1000).toFixed(1)}s`)
}

function run(cmd, args, cwd, env = {}) {
  const r = spawnSync(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: ['ignore', 'ignore', 'inherit'] })
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(' ')} failed (${r.status})`)
}

async function until(url, what, ms = 180_000) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    try {
      if ((await fetch(url)).ok) return
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`${what} did not answer at ${url} within ${ms / 1000}s`)
}

const children = []
const stop = () => children.forEach((c) => c.kill('SIGTERM'))
process.on('exit', stop)

try {
  mark('start')
  if (!skipInstall) {
    run('uv', ['sync', '--frozen'], resolve(root, 'backend'))
    mark('install_backend')
    run('npm', ['ci', '--no-audit', '--no-fund'], resolve(root, 'frontend'))
    mark('install_frontend')
  }
  run('npm', ['run', 'build'], resolve(root, 'frontend'), { NEXT_TELEMETRY_DISABLED: '1' })
  mark('build_frontend')

  const admin = new pg.Client({ connectionString: PG_ADMIN_URL })
  await admin.connect()
  await admin.query(`DROP DATABASE IF EXISTS ${DB}`)
  await admin.query(`CREATE DATABASE ${DB}`)
  await admin.end()
  const env = { ...apiEnv(), DATABASE_URL: dbUrl }
  run('uv', ['run', '--quiet', 'alembic', 'upgrade', 'head'], resolve(root, 'backend'), env)
  mark('database_ready')

  children.push(spawn('uv', ['run', '--quiet', 'superaide', 'serve', '--port', String(API_PORT)], { cwd: resolve(root, 'backend'), env: { ...process.env, ...env }, stdio: 'ignore' }))
  await until(`http://127.0.0.1:${API_PORT}/health`, 'the API')
  mark('api_up')

  children.push(spawn('npx', ['next', 'start', '--port', String(WEB_PORT)], { cwd: resolve(root, 'frontend'), env: { ...process.env, API_INTERNAL_URL: `http://127.0.0.1:${API_PORT}`, NEXT_TELEMETRY_DISABLED: '1' }, stdio: 'ignore' }))
  await until(`http://127.0.0.1:${WEB_PORT}/api/health`, 'the site')
  mark('web_up')
} finally {
  stop()
  const admin = new pg.Client({ connectionString: PG_ADMIN_URL })
  await admin.connect().then(() => admin.query(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`)).catch(() => undefined)
  await admin.end().catch(() => undefined)
}

const out = resolve(here, '..', 'test-results', 'ttfr', 'cold-start.json')
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, JSON.stringify({ measured_at: new Date().toISOString(), skipped_install: skipInstall, total_seconds: Math.round(stages.at(-1).at_ms / 100) / 10, stages }, null, 2) + '\n')
console.log(`ttfr-cold: wrote ${out}`)
