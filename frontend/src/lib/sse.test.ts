import { describe, expect, it } from 'vitest'
import { readEvents } from '@/lib/api'

function body(...chunks: string[]): ReadableStream<Uint8Array<ArrayBuffer>> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(c) {
      for (const x of chunks) c.enqueue(enc.encode(x))
      c.close()
    },
  })
}
async function collect(...chunks: string[]) {
  const seen: [string, unknown][] = []
  await readEvents(body(...chunks), (e, d) => seen.push([e, d]))
  return seen
}

describe('readEvents (the chat stream parser)', () => {
  it('reads events split across chunks at any point', async () => {
    expect(await collect('event: token\ndata: {"t"', ':"hi"}\n\nevent: done\nda', 'ta: {"ok":true}\n\n')).toEqual([
      ['token', { t: 'hi' }],
      ['done', { ok: true }],
    ])
  })
  it('joins multi-line data', async () => expect(await collect('data: {"a":\ndata: 1}\n\n')).toEqual([['message', { a: 1 }]]))
  it('accepts CRLF line ends', async () => expect(await collect('event: x\r\ndata: {"n":1}\r\n\r\n')).toEqual([['x', { n: 1 }]]))
  it('ignores comments and events without data', async () => expect(await collect(': ping\n\nevent: x\n\n')).toEqual([]))
  it('drops an unfinished last event (the caller treats a missing done as a lost connection)', async () =>
    expect(await collect('event: token\ndata: {"t":1}')).toEqual([]))
  it('rejects on malformed JSON instead of swallowing it', async () => {
    await expect(collect('data: {oops\n\n')).rejects.toThrow()
  })
})
