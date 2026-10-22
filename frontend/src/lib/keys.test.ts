import { describe, expect, it } from 'vitest'
import { isSendKey } from '@/lib/keys'

const press = (key: string, shiftKey = false, native: { isComposing?: boolean; keyCode?: number } = {}) => ({ key, shiftKey, nativeEvent: native })

describe('isSendKey', () => {
  it('sends on a plain Enter', () => expect(isSendKey(press('Enter'))).toBe(true))
  it('does not send on Shift+Enter or other keys', () => {
    expect(isSendKey(press('Enter', true))).toBe(false)
    expect(isSendKey(press('a'))).toBe(false)
  })
  it('does not send when Enter confirms an IME candidate', () => {
    expect(isSendKey(press('Enter', false, { isComposing: true }))).toBe(false)
    expect(isSendKey(press('Enter', false, { keyCode: 229 }))).toBe(false)
  })
})
