'use client'

import { useState } from 'react'
import { SPEECH_LANGUAGES, browserLang, recognitionLang, setRecognitionLang } from '@/features/onboarding/speech'

// "Listening in: English (US)". Shows which language the microphone is set to and lets the person change it; the choice is kept
// on this browser. The next answer is heard in the new language.
export function SpeechLanguage({ className = '', dark = false }: { className?: string; dark?: boolean }) {
  const [lang, setLang] = useState(recognitionLang)
  const options = SPEECH_LANGUAGES.some((l) => l.code === lang) ? SPEECH_LANGUAGES : [{ code: lang, label: lang }, ...SPEECH_LANGUAGES]
  return (
    <label className={`flex items-center gap-2 text-xs ${dark ? 'text-white/70' : 'text-muted'} ${className}`}>
      <span>Listening in</span>
      <select
        value={lang}
        onChange={(e) => {
          setRecognitionLang(e.target.value)
          setLang(e.target.value)
        }}
        aria-label="Language the microphone listens in"
        title={lang === browserLang() ? 'Your browser\'s language' : undefined}
        className={`rounded-full px-3 py-1.5 text-base md:text-xs ${dark ? 'bg-white/10 text-white ring-1 ring-white/15 focus-visible:outline-white' : 'bg-white text-ink'}`}
      >
        {options.map((l) => (
          <option key={l.code} value={l.code} className="text-ink">
            {l.label}
          </option>
        ))}
      </select>
    </label>
  )
}
