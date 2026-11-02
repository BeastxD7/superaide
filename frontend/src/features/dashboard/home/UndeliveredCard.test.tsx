import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UndeliveredCard, type Undelivered } from './UndeliveredCard'

const hook = vi.hoisted(() => ({ data: null as unknown }))
vi.mock('@/features/dashboard/data', () => ({ useAppData: () => ({ data: hook.data, error: null, reload: vi.fn() }) }))

const item = (over: Partial<Undelivered>): Undelivered => ({
  id: 'a', channel: 'telegram', channel_name: 'Telegram', text: 'Meera needs you within 5 minutes', state: 'queued',
  attempts: 1, next_attempt_at: null, reason: 'Telegram could not be reached.', at: '2026-11-02T10:00:00Z', ...over,
})

const html = () => renderToStaticMarkup(<UndeliveredCard name="Ava" />)

describe('UndeliveredCard', () => {
  beforeEach(() => { hook.data = null })

  it('shows nothing when everything arrived', () => {
    hook.data = { count: 0, items: [] }
    expect(html()).toBe('')
  })

  it('says a waiting push is being retried', () => {
    hook.data = { count: 1, items: [item({})] }
    const out = html()
    expect(out).toContain('1 notification did not reach you')
    expect(out).toContain('Retrying on Telegram')
  })

  it('says plainly that a given-up push was not delivered', () => {
    hook.data = { count: 2, items: [item({ state: 'failed', attempts: 4 }), item({ id: 'b' })] }
    const out = html()
    expect(out).toContain('2 notifications did not reach you')
    expect(out).toContain('Not delivered on Telegram')
    expect(out).toContain('4 tries')
  })
})
