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
    // A chat turn asks for the reply schema (brain/turn.py TurnOutput): answer it, so the owner's first message gets a reply.
    const schemaProps = parsed?.output_config?.format?.schema?.properties ?? {}
    if (wantsJson && 'reply' in schemaProps) {
      text = JSON.stringify({
        reply: 'Hello from the fake Claude.',
        requested_action: { kind: 'none', summary: '' },
        prediction: { expectation: 'The owner reads the reply.', confidence: 0.5 },
      })
    }
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
    if (parsed?.stream === true) {
      // The chat streams its answer (server-sent events); one text block in two pieces is enough for the SDK.
      const usage = { input_tokens: 120, output_tokens: 40 }
      const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify({ type: event, ...data })}\n\n`)
      res.writeHead(200, { 'content-type': 'text/event-stream', 'request-id': `req_fake_${calls}` })
      send('message_start', {
        message: { id: `msg_fake_${calls}`, type: 'message', role: 'assistant', model: 'claude-haiku-5-5', content: [], stop_reason: null, stop_sequence: null, usage },
      })
      send('content_block_start', { index: 0, content_block: { type: 'text', text: '' } })
      const half = Math.ceil(text.length / 2)
      for (const piece of [text.slice(0, half), text.slice(half)])
        send('content_block_delta', { index: 0, delta: { type: 'text_delta', text: piece } })
      send('content_block_stop', { index: 0 })
      send('message_delta', { delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 40 } })
      send('message_stop', {})
      return void res.end()
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
