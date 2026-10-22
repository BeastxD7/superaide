// A stand-in for Google's sign-in, for the E2E "Continue with Google" tests only.
// The API reaches it through GOOGLE_FAKE_BASE_URL, which it honours only while ENABLE_SIMULATOR is on.
//
//   GET  /o/oauth2/v2/auth   the account chooser: "Continue as …" or "Cancel"
//   POST /token              swaps the code for an ID token signed with this server's own key
//   GET  /oauth2/v3/certs    that key, as a JWKS
//
// The code carries the nonce and the chosen person, so the ID token matches the request exactly.
import { createServer } from 'node:http'
import { createSign, generateKeyPairSync } from 'node:crypto'

const port = Number(process.env.FAKE_GOOGLE_PORT ?? 8300)
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const KID = 'e2e-fake-google'
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: KID, use: 'sig', alg: 'RS256' }

const b64 = (data) => Buffer.from(data).toString('base64url')
const escape = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)

function idToken(claims) {
  const head = b64(JSON.stringify({ alg: 'RS256', kid: KID, typ: 'JWT' }))
  const body = b64(JSON.stringify(claims))
  const sign = createSign('RSA-SHA256')
  sign.update(`${head}.${body}`)
  return `${head}.${body}.${sign.sign(privateKey).toString('base64url')}`
}

function chooser(q) {
  const back = (params) => `${q.get('redirect_uri')}?${new URLSearchParams({ state: q.get('state') ?? '', ...params })}`
  const person = (email, name) =>
    back({ code: b64(JSON.stringify({ nonce: q.get('nonce'), email, name, sub: `fake-${email}`, aud: q.get('client_id') })) })
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sign in – Google Accounts (fake)</title></head>
<body style="font-family:sans-serif;max-width:28rem;margin:4rem auto">
<h1>Choose an account</h1>
<p>to continue to Superaide. Scopes: <code id="scope">${escape(q.get('scope'))}</code></p>
<p><a href="${escape(person('e2e-google@example.com', 'Gita Google'))}">Continue as Gita Google</a></p>
<p><a href="${escape(back({ error: 'access_denied' }))}">Cancel</a></p>
</body></html>`
}

createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`)
  if (req.method === 'GET' && url.pathname === '/o/oauth2/v2/auth') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }).end(chooser(url.searchParams))
    return
  }
  if (req.method === 'GET' && url.pathname === '/oauth2/v3/certs') {
    res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ keys: [jwk] }))
    return
  }
  if (req.method === 'POST' && url.pathname === '/token') {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      const form = new URLSearchParams(raw)
      let who
      try {
        who = JSON.parse(Buffer.from(form.get('code') ?? '', 'base64url').toString())
      } catch {
        res.writeHead(400, { 'content-type': 'application/json' }).end('{"error":"invalid_grant"}')
        return
      }
      const now = Math.floor(Date.now() / 1000)
      const token = idToken({
        iss: 'https://accounts.google.com',
        aud: form.get('client_id'),
        azp: form.get('client_id'),
        sub: who.sub,
        email: who.email,
        email_verified: true,
        name: who.name,
        nonce: who.nonce,
        iat: now,
        exp: now + 3600,
      })
      res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ id_token: token, access_token: 'fake' }))
    })
    return
  }
  if (url.pathname === '/health') {
    res.writeHead(200).end('ok')
    return
  }
  res.writeHead(404).end()
}).listen(port, '127.0.0.1')
