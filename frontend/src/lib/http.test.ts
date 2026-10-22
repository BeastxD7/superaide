import { describe, expect, it } from 'vitest'
import { errorFromDetail } from '@/lib/http'

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
