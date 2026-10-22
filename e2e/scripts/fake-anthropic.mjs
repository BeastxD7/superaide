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
    let parsed = {}
    try {
      parsed = JSON.parse(body)
      wantsJson = Boolean(parsed?.output_config?.format)
    } catch {
      // not JSON: treat as plain
    }
    const system = JSON.stringify(parsed?.system ?? '')
    let text = wantsJson ? JSON.stringify(FACTS) : 'Hello from the fake Claude.'
    if (system.includes('This is a rehearsal')) {
      // The rehearsal room (#58): the other side raises an objection.
      text = 'That is a big ask. What has changed since your last review?'
    } else if (system.includes('three-point debrief')) {
      // Three points, each quoting the first words the person said (taken from the transcript).
      const said = /PERSON: ([^\\]+)/.exec(JSON.stringify(parsed?.messages ?? ''))?.[1] ?? ''
      const quote = said.split(' ').slice(0, 6).join(' ')
      const heads = ['What worked', 'What to tighten', 'What to prepare for']
      text = JSON.stringify({
        points: heads.map((heading) => ({ heading, advice: 'Be specific about what you want and why.', quote })),
      })
    }
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
