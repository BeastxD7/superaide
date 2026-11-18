// Measures what the Next.js /api proxy adds in front of the API (#419, ADR 0005 addendum).
//
// It runs the same requests straight at a no-op upstream (scripts/fake-upstream.mjs) and through the built Next
// server in front of it, so the difference is the proxy hop alone: no FastAPI time, no database. Run it after
// `npm run build` in frontend/:
//
//   node scripts/proxy-bench.mjs [requests=2000] [concurrency=1]
//
// Prints p50/p95/p99 per request kind (direct, proxied, added), the arrival of a two-event stream, and the Next
// server's resident memory before and after the load. Local loopback numbers: they show the hop's own cost, not
// network time on a hosted setup (the browser to server leg is the same with or without the proxy).
import { execFileSync, spawn } from 'node:child_process'
import { Agent, request } from 'node:http'
import { fileURLToPath } from 'node:url'

const N = Number(process.argv[2] ?? 2000)
const C = Number(process.argv[3] ?? 1)
const UP = 8490
const WEB = 3490
const agent = new Agent({ keepAlive: true, maxSockets: 64 })
const frontend = fileURLToPath(new URL('../../frontend', import.meta.url))
const here = fileURLToPath(new URL('.', import.meta.url))

const children = []
function start(cmd, args, opts) {
  const p = spawn(cmd, args, { stdio: 'ignore', ...opts })
  children.push(p)
  return p
}
async function waitFor(port, path) {
  for (let i = 0; i < 150; i++) {
    try {
      await once(port, 'GET', path)
      return
    } catch {
      await new Promise((r) => setTimeout(r, 200))
    }
  }
  throw new Error(`port ${port} did not come up`)
}
function once(port, method, path, body) {
  return new Promise((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port, method, path, agent, headers: body ? { 'content-type': 'application/json', 'content-length': body.length } : {} }, (res) => {
      res.resume()
      res.on('end', () => resolve(res.statusCode))
    })
    req.on('error', reject)
    req.end(body)
  })
}
async function timed(port, method, path, body) {
  const t = process.hrtime.bigint()
  const status = await once(port, method, path, body)
  if (status !== 200) throw new Error(`${path} answered ${status}`)
  return Number(process.hrtime.bigint() - t) / 1e6
}
async function run(port, method, path, body) {
  for (let i = 0; i < 100; i++) await timed(port, method, path, body) // warm up
  const out = []
  let left = N
  await Promise.all(
    Array.from({ length: C }, async () => {
      while (left-- > 0) out.push(await timed(port, method, path, body))
    }),
  )
  return out.sort((a, b) => a - b)
}
const q = (xs, p) => xs[Math.min(xs.length - 1, Math.floor((p / 100) * xs.length))]
const rssMb = (pid) => Number(execFileSync('ps', ['-o', 'rss=', '-p', String(pid)]).toString().trim()) / 1024
// `next start` runs the server in a child of the npm/npx wrapper: take the biggest process on the port.
const serverPid = () => Number(execFileSync('lsof', ['-ti', `tcp:${WEB}`, '-sTCP:LISTEN']).toString().trim().split('\n')[0])

function sse(port, path) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now()
    const arrivals = []
    const req = request({ host: '127.0.0.1', port, path, agent: false }, (res) => {
      res.on('data', (d) => arrivals.push({ at: Date.now() - t0, events: String(d).split('\n\n').filter(Boolean).length }))
      res.on('end', () => resolve(arrivals))
    })
    req.on('error', reject)
    req.end()
  })
}

try {
  start('node', [`${here}fake-upstream.mjs`], { env: { ...process.env, FAKE_UPSTREAM_PORT: String(UP) } })
  start('npx', ['next', 'start', '--port', String(WEB)], {
    cwd: frontend,
    env: { ...process.env, API_INTERNAL_URL: `http://127.0.0.1:${UP}`, NEXT_TELEMETRY_DISABLED: '1' },
  })
  await waitFor(UP, '/health')
  await waitFor(WEB, '/icon.svg')
  const pid = serverPid()
  const before = rssMb(pid)
  const small = Buffer.from(JSON.stringify({ text: 'x'.repeat(200) }))
  const big = Buffer.from(JSON.stringify({ text: 'x'.repeat(20000) }))
  const kinds = [
    ['GET small JSON', 'GET', '/health', '/api/health'],
    ['GET echo (headers back)', 'GET', '/api/echo', '/api/api/echo'],
    ['POST 200 B', 'POST', '/api/echo', '/api/api/echo', small],
    ['POST 20 KB', 'POST', '/api/echo', '/api/api/echo', big],
  ]
  console.log(`requests per kind: ${N}, concurrency: ${C}, node ${process.version}`)
  console.log('kind | direct p50/p95/p99 ms | proxied p50/p95/p99 ms | added p50/p95 ms')
  for (const [name, method, direct, proxied, body] of kinds) {
    const d = await run(UP, method, direct, body)
    const p = await run(WEB, method, proxied, body)
    const f = (xs) => [50, 95, 99].map((x) => q(xs, x).toFixed(2)).join('/')
    console.log(`${name} | ${f(d)} | ${f(p)} | ${(q(p, 50) - q(d, 50)).toFixed(2)}/${(q(p, 95) - q(d, 95)).toFixed(2)}`)
  }
  const direct = await sse(UP, '/api/sse')
  const via = await sse(WEB, '/api/api/sse')
  console.log(`stream direct  (ms, events per chunk): ${JSON.stringify(direct)}`)
  console.log(`stream proxied (ms, events per chunk): ${JSON.stringify(via)}`)
  console.log(`proxy server memory (RSS): before ${before.toFixed(0)} MB, after ${rssMb(pid).toFixed(0)} MB`)
} finally {
  for (const c of children) c.kill()
  try {
    execFileSync('sh', ['-c', `lsof -ti tcp:${WEB} | xargs kill 2>/dev/null; true`])
  } catch {}
  process.exit(0)
}
