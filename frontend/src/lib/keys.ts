import type { KeyboardEvent } from 'react'

// Enter sends, Shift+Enter is a new line, and Enter that confirms an IME candidate (Japanese,
// Chinese, Korean...) is neither: it must reach the input method untouched. Browsers flag that
// with isComposing, and Safari fires keyCode 229 after compositionend.
export function isSendKey(e: Pick<KeyboardEvent, 'key' | 'shiftKey'> & { nativeEvent: { isComposing?: boolean; keyCode?: number } }): boolean {
  if (e.key !== 'Enter' || e.shiftKey) return false
  return !(e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229)
}
