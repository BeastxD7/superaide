// A stand-in for Anthropic's Messages API, for the E2E "brain first" tests only (E2E_BRAIN=1).
// The API reaches it through ANTHROPIC_BASE_URL. Every request gets one answer: when it asks for
// structured output (reading a shared document) the answer is one fact about the owner, otherwise a
// short sentence. No streaming. Requests are counted at GET /count so a test can see the model was used.
import { createServer } from 'node:http'

const port = Number(process.env.FAKE_ANTHROPIC_PORT ?? 8400)
let calls = 0

const FACTS = {
  facts: [
    { content: 'The owner leads applied AI at Acme in Singapore.', category: 'work', confidence: 0.9, same_as: null },
  ],
}

createServer((req, res) => {
  if (req.url === '/health') return void res.writeHead(200).end('ok')
  if (req.url === '/count') return void res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ calls }))
  if (req.method !== 'POST' || !req.url?.startsWith('/v1/messages')) return void res.writeHead(404).end()
  let body = ''
  req.on('data', (c) => (body += c))
  req.on('end', () => {
    calls += 1
    let wantsJson = false
    try {
      wantsJson = Boolean(JSON.parse(body)?.output_config?.format)
    } catch {
      // not JSON: treat as plain
    }
    const text = wantsJson ? JSON.stringify(FACTS) : 'Hello from the fake Claude.'
    res.writeHead(200, { 'content-type': 'application/json', 'request-id': `req_fake_${calls}` })
    res.end(
      JSON.stringify({
        id: `msg_fake_${calls}`,
        type: 'message',
        role: 'assistant',
        model: 'claude-haiku-5-5',
        content: [{ type: 'text', text }],
        stop_reason: 'end_turn',
        stop_sequence: null,
        usage: { input_tokens: 120, output_tokens: 40 },
      }),
    )
  })
}).listen(port, '127.0.0.1', () => console.log(`fake anthropic on :${port}`))
