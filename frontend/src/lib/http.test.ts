import { describe, expect, it } from 'vitest'
import { errorFromBody, errorFromDetail } from '@/lib/http'

describe('errorFromDetail', () => {
  it('reads a string detail', () => expect(errorFromDetail(400, 'Nope', 'fb').message).toBe('Nope'))
  it('reads an object with a message and a field', () => {
    const e = errorFromDetail(422, { message: 'Too short', field: 'name' }, 'fb')
    expect([e.status, e.message, e.field]).toEqual([422, 'Too short', 'name'])
  })
  it('reads the first FastAPI validation error', () => {
    const e = errorFromDetail(422, [{ loc: ['body', 'email'], msg: 'bad email' }], 'fb')
    expect([e.message, e.field]).toEqual(['bad email', 'email'])
  })
  it('falls back for an empty array, a missing detail or an odd one', () => {
    expect(errorFromDetail(422, [], 'fb').message).toBe('Check the form and try again.')
    expect(errorFromDetail(500, undefined, 'Request failed').message).toBe('Request failed')
    expect(errorFromDetail(500, 42, 'Request failed').message).toBe('Request failed')
  })
})

describe('errorFromBody (the standard error envelope)', () => {
  const env = (message: string, detail: unknown = null, old: unknown = message) => ({
    message,
    detail: old,
    error: { code: 'x', detail, request_id: 'r1' },
  })
  it('reads error.message', () => expect(errorFromBody(409, env('Taken.'), 'fb').message).toBe('Taken.'))
  it('keeps the field of a structured detail', () => {
    const e = errorFromBody(422, env('Too short', { field: 'name' }, { message: 'Too short', field: 'name' }), 'fb')
    expect([e.message, e.field]).toEqual(['Too short', 'name'])
  })
  it('keeps the per-field validation list', () => {
    const list = [{ loc: ['body', 'email'], msg: 'bad email' }]
    const e = errorFromBody(422, env('bad email', list, list), 'fb')
    expect([e.message, e.field]).toEqual(['bad email', 'email'])
  })
  it('falls back to the old detail when there is no message', () => {
    expect(errorFromBody(400, { detail: 'Old shape' }, 'fb').message).toBe('Old shape')
    expect(errorFromBody(500, null, 'Request failed').message).toBe('Request failed')
  })
})
