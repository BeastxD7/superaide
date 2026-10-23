import { afterEach, describe, expect, it, vi } from 'vitest'
import { pickVoice, recognitionLang, scoreVoice, setRecognitionLang } from '@/features/onboarding/speech'

const voice = (name: string, lang = 'en-US', extra: Partial<SpeechSynthesisVoice> = {}) =>
  ({ name, lang, localService: false, default: false, voiceURI: name, ...extra }) as SpeechSynthesisVoice

function storage(initial: Record<string, string> = {}, blocked = false) {
  const data = { ...initial }
  return {
    getItem: (k: string) => {
      if (blocked) throw new Error('blocked')
      return data[k] ?? null
    },
    setItem: (k: string, v: string) => {
      if (blocked) throw new Error('blocked')
      data[k] = v
    },
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('recognitionLang', () => {
  it('uses what was picked, if it looks like a language tag', () => {
    vi.stubGlobal('navigator', { language: 'en-GB' })
    vi.stubGlobal('localStorage', storage({ 'superaide-speech-lang': 'hi-IN' }))
    expect(recognitionLang()).toBe('hi-IN')
  })
  it('ignores a stored value that is not a language tag', () => {
    vi.stubGlobal('navigator', { language: 'fr-FR' })
    vi.stubGlobal('localStorage', storage({ 'superaide-speech-lang': '<script>' }))
    expect(recognitionLang()).toBe('fr-FR')
  })
  it('falls back to the browser language, then US English, and survives blocked storage', () => {
    vi.stubGlobal('navigator', { language: 'de-DE' })
    vi.stubGlobal('localStorage', storage({}, true))
    expect(recognitionLang()).toBe('de-DE')
    expect(() => setRecognitionLang('de-DE')).not.toThrow()
    vi.stubGlobal('navigator', { language: '' })
    expect(recognitionLang()).toBe('en-US')
  })
})

describe('pickVoice', () => {
  it('prefers a natural formal voice in the right language', () => {
    const voices = [voice('Albert'), voice('Fred'), voice('Microsoft Sonia Online (Natural)', 'en-GB'), voice('Samantha')]
    expect(pickVoice(voices, 'formal_female', 'en-GB')?.name).toBe('Microsoft Sonia Online (Natural)')
  })
  it('never picks a novelty voice when anything else fits', () => {
    expect(pickVoice([voice('Bubbles'), voice('Daniel')], 'formal_male')?.name).toBe('Daniel')
  })
  it('falls back to English when there is no voice for their language, and to null when there are none', () => {
    expect(pickVoice([voice('Samantha')], 'formal_female', 'ja-JP')?.name).toBe('Samantha')
    expect(pickVoice([], 'formal_female', 'en-US')).toBeNull()
  })
  it('scores a wrong-language voice below any right-language one', () => {
    expect(scoreVoice(voice('Kyoko', 'ja-JP'), 'formal_female', 'en-US')).toBeLessThan(0)
    expect(scoreVoice(voice('Samantha'), 'formal_female', 'en-US')).toBeGreaterThan(0)
  })
})
