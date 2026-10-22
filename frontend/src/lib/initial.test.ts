import { describe, expect, it } from 'vitest'
import { initialOf } from '@/lib/initial'

describe('initialOf', () => {
  it('takes the first whole character, upper-cased', () => {
    expect(initialOf('  priya')).toBe('P')
    expect(initialOf('🙂 Priya')).toBe('🙂')
    expect(initialOf('émile')).toBe('É')
  })
  it('has a placeholder for nothing', () => expect(initialOf('   ')).toBe('?'))
})
