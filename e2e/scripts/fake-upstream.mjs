// A stand-in for the backend API, for the proxy contract tests only (E2E_PROXY=1).
// The Next.js /api proxy is pointed at this instead of FastAPI, so the tests can see exactly what the
// proxy sent upstream and how it treats what comes back.
//
//   /health            200
//   /api/echo          200 JSON of the request the proxy made (method, url, headers, body)
//   /api/cookies       200 with two Set-Cookie headers
//   /api/status/204    204        /api/status/304   304
//   /api/gzip          200 gzip-compressed JSON with Content-Encoding: gzip
//   /api/redirect      302 to /api/echo
//   /api/sse           an event, a 1.5 s pause, a second event (to see the stream is not buffered)
//   anything else      404 {detail: 'from upstream'}: proof a request got past the proxy
//   /api/upload        reads the whole body and answers its size and content type
import { createServer } from 'node:http'
import { gzipSync } from 'node:zlib'

const port = Number(process.env.FAKE_UPSTREAM_PORT ?? 8400)

createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://x')
  const chunks = []
  if (url.pathname !== '/api/upload') for await (const c of req) chunks.push(c)
  const json = (status, body, headers = {}) => {
    res.writeHead(status, { 'content-type': 'application/json', ...headers })
    res.end(JSON.stringify(body))
  }
  switch (url.pathname) {
    case '/health':
      return json(200, { status: 'ok' })
    case '/api/echo':
      return json(200, { method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString('utf8') })
    case '/api/cookies':
      res.setHeader('set-cookie', ['a=1; Path=/; HttpOnly', 'b=2; Path=/; SameSite=Lax'])
      return json(200, { ok: true })
    case '/api/status/204':
      res.writeHead(204)
      return res.end()
    case '/api/status/304':
      res.writeHead(304, { etag: '"x"' })
      return res.end()
    case '/api/gzip': {
      const body = gzipSync(JSON.stringify({ hello: 'world' }))
      res.writeHead(200, { 'content-type': 'application/json', 'content-encoding': 'gzip', 'content-length': body.length })
      return res.end(body)
    }
    case '/api/redirect':
      res.writeHead(302, { location: '/api/echo' })
      return res.end()
    case '/api/sse': {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
      res.write('event: first\ndata: {}\n\n')
      setTimeout(() => {
        res.write('event: second\ndata: {}\n\n')
        res.end()
      }, 1500)
      return
    }
    case '/api/upload': {
      let size = 0
      for await (const c of req) size += c.length
      return json(200, { size, contentType: req.headers['content-type'] ?? '' })
    }
    default:
      return json(404, { detail: 'from upstream', path: url.pathname })
  }
}).listen(port, '127.0.0.1', () => console.log(`fake upstream on ${port}`))
