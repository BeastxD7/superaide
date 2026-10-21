// Thin, typed wrappers around the browser's Web Speech API.
//
// - speak(): the agent's voice, via speechSynthesis.
// - createRecognizer(): the owner's answers, via SpeechRecognition. In Chrome the
//   browser sends the audio to Google's speech service to turn it into text. This
//   module never sends, stores or records audio itself.

// ---------- Recognition types ----------
// TypeScript's DOM lib leaves out the SpeechRecognition interface itself (Chrome
// and Safari still ship it prefixed), so declare the small part used here. The
// names are local to this module so they can't clash with the DOM lib.

export type RecognitionErrorCode =
  | 'aborted'
  | 'audio-capture'
  | 'bad-grammar'
  | 'language-not-supported'
  | 'network'
  | 'no-speech'
  | 'not-allowed'
  | 'service-not-allowed'
  | 'unknown'

const KNOWN_ERRORS: ReadonlySet<string> = new Set([
  'aborted',
  'audio-capture',
  'bad-grammar',
  'language-not-supported',
  'network',
  'no-speech',
  'not-allowed',
  'service-not-allowed',
])

interface RecognitionAlternative {
  readonly transcript: string
  readonly confidence: number
}

interface RecognitionResult {
  readonly isFinal: boolean
  readonly length: number
  readonly [index: number]: RecognitionAlternative | undefined
}

interface RecognitionResultList {
  readonly length: number
  readonly [index: number]: RecognitionResult | undefined
}

interface RecognitionResultEvent extends Event {
  readonly resultIndex: number
  readonly results: RecognitionResultList
}

interface RecognitionErrorEvent extends Event {
  readonly error: string
  readonly message?: string
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  onstart: ((event: Event) => void) | null
  onresult: ((event: RecognitionResultEvent) => void) | null
  onerror: ((event: RecognitionErrorEvent) => void) | null
  onend: ((event: Event) => void) | null
  start(): void
  stop(): void
  abort(): void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

type SpeechWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
}

function recognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const w = window as SpeechWindow
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

// ---------- Support ----------

export type SpeechSupport = {
  recognition: boolean
  synthesis: boolean
  /** The browser has the speech-to-text API but no service behind it (Brave), so it always fails. */
  noService: boolean
}

export function speechSupport(): SpeechSupport {
  if (typeof window === 'undefined') return { recognition: false, synthesis: false, noService: false }
  // Brave ships the API without Google's speech service: every attempt ends in a 'network' error.
  const noService = 'brave' in navigator
  return {
    recognition: recognitionConstructor() !== null && !noService,
    synthesis: 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function',
    noService,
  }
}

/** The language to listen in: the browser's own, or US English. */
export function recognitionLang(): string {
  const lang = typeof navigator !== 'undefined' ? navigator.language.split('@')[0] : ''
  return lang || 'en-US'
}

// ---------- Synthesis ----------

const PREFERRED_VOICES = ['Samantha', 'Ava', 'Google UK English Female', 'Google US English']

/** A natural-sounding English voice if the device has one, else null (browser default). */
export function pickVoice(voices: readonly SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter((v) => v.lang.toLowerCase().replace('_', '-').startsWith('en'))
  for (const name of PREFERRED_VOICES) {
    const match = english.find((v) => v.name.includes(name))
    if (match) return match
  }
  return (
    english.find((v) => /natural/i.test(v.name)) ??
    english.find((v) => v.default) ??
    english.find((v) => v.lang === 'en-US') ??
    english[0] ??
    null
  )
}

// Chrome loads its voice list lazily and announces it with `voiceschanged`.
function loadVoices(timeoutMs = 1500): Promise<SpeechSynthesisVoice[]> {
  const synth = window.speechSynthesis
  const now = synth.getVoices()
  if (now.length > 0) return Promise.resolve(now)
  return new Promise((resolve) => {
    let timer = 0
    const done = () => {
      window.clearTimeout(timer)
      synth.removeEventListener('voiceschanged', done)
      resolve(synth.getVoices())
    }
    timer = window.setTimeout(done, timeoutMs)
    synth.addEventListener('voiceschanged', done)
  })
}

/** Start loading voices early so the first line doesn't wait for them. */
export function prepareVoices(): void {
  if (speechSupport().synthesis) void loadVoices()
}

/**
 * Safari and iOS only allow speech that starts from a tap or click. Call this
 * synchronously inside the click handler that opens the call.
 */
export function unlockSpeech(): void {
  if (!speechSupport().synthesis) return
  try {
    const u = new SpeechSynthesisUtterance(' ')
    u.volume = 0
    window.speechSynthesis.speak(u)
  } catch {
    // Nothing to unlock.
  }
}

/** Stop any speech in progress, from anywhere in the app. */
export function stopSpeaking(): void {
  if (speechSupport().synthesis) window.speechSynthesis.cancel()
}

export type SpeakResult = {
  ok: boolean
  /** 'unsupported', 'no-start' (the browser silently dropped it), or a SpeechSynthesisErrorEvent code such as 'not-allowed'. */
  error?: string
}

export type SpeakOptions = {
  rate?: number
  pitch?: number
  /** Used only when no English voice is found. */
  lang?: string
  onStart?: () => void
  /** Called exactly once when the line has finished or failed, never after cancel. */
  onEnd?: (result: SpeakResult) => void
}

// Chrome can garbage-collect an utterance mid-sentence and then never fire
// `end`, so hold a reference until it's done.
const inFlight = new Set<SpeechSynthesisUtterance>()

/** Say `text` aloud. Returns a function that stops it (and suppresses onEnd). */
export function speak(text: string, options: SpeakOptions = {}): () => void {
  const { rate = 1, pitch = 1, lang = 'en-US', onStart, onEnd } = options
  let done = false
  let utterance: SpeechSynthesisUtterance | null = null
  let poll = 0
  let kick = 0

  const stopTimers = () => {
    window.clearInterval(poll)
    window.clearTimeout(kick)
  }
  const detach = (u: SpeechSynthesisUtterance) => {
    u.onstart = null
    u.onend = null
    u.onerror = null
    inFlight.delete(u)
  }
  const finish = (result: SpeakResult) => {
    if (done) return
    done = true
    stopTimers()
    if (utterance) detach(utterance)
    onEnd?.(result)
  }
  const cancel = () => {
    if (done) return
    done = true
    stopTimers()
    if (utterance) {
      detach(utterance)
      const synth = window.speechSynthesis
      if (synth.speaking || synth.pending) synth.cancel()
    }
  }

  if (!speechSupport().synthesis || !text.trim()) {
    kick = window.setTimeout(() => finish({ ok: !text.trim(), error: text.trim() ? 'unsupported' : undefined }), 0)
    return cancel
  }

  const synth = window.speechSynthesis
  void loadVoices().then((voices) => {
    if (done) return
    const u = new SpeechSynthesisUtterance(text)
    const voice = pickVoice(voices)
    if (voice) u.voice = voice
    u.lang = voice?.lang ?? lang
    u.rate = rate
    u.pitch = pitch
    u.volume = 1

    let started = false
    let quietTicks = 0
    let began = Date.now()
    let lastNudge = Date.now()
    const limit = 8000 + text.length * 90
    // Chrome's online voices stop after ~15 seconds unless nudged; Android treats pause as stop.
    const nudge = voice !== null && !voice.localService && !/android/i.test(navigator.userAgent)

    u.onstart = () => {
      started = true
      onStart?.()
    }
    u.onend = () => finish({ ok: true })
    u.onerror = (e) => finish({ ok: e.error === 'interrupted' || e.error === 'canceled', error: e.error })
    utterance = u
    inFlight.add(u)

    const go = () => {
      if (done) return
      began = Date.now()
      synth.speak(u)
      if (synth.paused) synth.resume()
      // Some browsers never fire `end` (or `start`); watch the queue as a backstop.
      poll = window.setInterval(() => {
        if (done) return
        const busy = synth.speaking || synth.pending
        if (synth.speaking) started = true
        const age = Date.now() - began
        if (started && !busy) {
          quietTicks += 1
          if (quietTicks >= 3) finish({ ok: true })
          return
        }
        quietTicks = 0
        if (!started && !busy && age > 5000) {
          finish({ ok: false, error: 'no-start' })
          return
        }
        if (age > limit) {
          detach(u)
          synth.cancel()
          finish({ ok: true })
          return
        }
        if (nudge && started && Date.now() - lastNudge > 10000) {
          lastNudge = Date.now()
          synth.pause()
          synth.resume()
        }
      }, 400)
    }

    // Clear anything stuck in the queue first; Chrome can ignore a speak() made in the same tick as cancel().
    if (synth.speaking || synth.pending) {
      synth.cancel()
      kick = window.setTimeout(go, 80)
    } else go()
  })

  return cancel
}

// ---------- Recognition ----------

export type Recognizer = {
  /** Start listening. Returns false if the browser refused to start. */
  start: () => boolean
  /** Stop listening, but still deliver what was already heard (as onFinal). */
  stop: () => void
  /** Stop listening and drop anything not yet final. onEnd still fires. */
  abort: () => void
  /** Abort and detach every handler, so nothing fires afterwards. */
  dispose: () => void
}

export type RecognizerOptions = {
  lang?: string
  onStart?: () => void
  /** The words heard so far that may still change ('' when there are none). */
  onInterim?: (text: string) => void
  /** A newly finished stretch of speech. Called once per stretch. */
  onFinal?: (text: string) => void
  onError?: (error: RecognitionErrorCode, message: string) => void
  /** Listening stopped: after stop/abort, an error, or the browser ending the session. */
  onEnd?: () => void
}

/** A continuous recognizer with interim results, or null when the browser has none. */
export function createRecognizer(options: RecognizerOptions): Recognizer | null {
  const Ctor = recognitionConstructor()
  if (!Ctor) return null
  let rec: SpeechRecognitionLike
  try {
    rec = new Ctor()
  } catch {
    return null
  }
  rec.lang = options.lang ?? recognitionLang()
  rec.continuous = true
  rec.interimResults = true
  rec.maxAlternatives = 1

  // Results accumulate over a session; remember which final ones were passed on.
  let reported = new Set<number>()

  rec.onstart = () => {
    reported = new Set()
    options.onStart?.()
  }
  rec.onresult = (event) => {
    let interim = ''
    for (let i = 0; i < event.results.length; i++) {
      const result = event.results[i]
      if (!result) continue
      const text = result[0]?.transcript ?? ''
      if (result.isFinal) {
        if (reported.has(i)) continue
        reported.add(i)
        const clean = text.replace(/\s+/g, ' ').trim()
        if (clean) options.onFinal?.(clean)
      } else {
        interim += ` ${text}`
      }
    }
    options.onInterim?.(interim.replace(/\s+/g, ' ').trim())
  }
  rec.onerror = (event) => {
    const code: RecognitionErrorCode = KNOWN_ERRORS.has(event.error) ? (event.error as RecognitionErrorCode) : 'unknown'
    options.onError?.(code, event.message ?? '')
  }
  rec.onend = () => options.onEnd?.()

  const safely = (fn: () => void) => {
    try {
      fn()
    } catch {
      // Already stopped.
    }
  }

  return {
    start: () => {
      try {
        rec.start()
        return true
      } catch {
        return false
      }
    },
    stop: () => safely(() => rec.stop()),
    abort: () => safely(() => rec.abort()),
    dispose: () => {
      rec.onstart = null
      rec.onresult = null
      rec.onerror = null
      rec.onend = null
      safely(() => rec.abort())
    },
  }
}
