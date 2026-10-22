'use client'

import { Check, Keyboard, LoaderCircle, Mic, MicOff, PhoneOff, Plus, Send, ShieldCheck, Trash2, Video, VideoOff, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useId, useRef, useState, type FormEvent, type JSX, type ReactNode } from 'react'
import { Orb, type Mood } from '@/components/Orb'
import { AppError, call, streamApp } from '@/lib/api'
import { createRecognizer, recognitionLang, speechSupport, stopSpeaking, type Recognizer, type VoiceStyle } from '@/features/onboarding/speech'
import { Speaker } from '@/features/onboarding/voice/speaker'
import { startVad, type Vad } from '@/features/onboarding/voice/vad'

// The introduction's voice call, v2 (ADR 0012). Ava speaks first; the owner just talks. Voice
// activity detection on this device notices when they start and stop, so there is nothing to press;
// talking over Ava stops her. Each finished utterance goes to the server, which hears it, lets Ava
// decide what to ask next, and streams her reply back sentence by sentence. At the end the owner
// checks what she learned before anything is saved.

export type VoiceConfig = {
  enabled: boolean
  asr: 'deepgram' | 'browser' | 'fake'
  tts: 'elevenlabs' | 'deepgram' | 'browser' | 'fake'
  brain: 'claude' | 'scripted'
  voice: VoiceStyle
  voices: { value: VoiceStyle; label: string }[]
  topics: number
  max_turn_seconds: number
  speech_usd_per_minute: number
  in_progress: boolean
}

type Fact = { id?: string; content: string; category: string }
type Phase = 'connecting' | 'speaking' | 'listening' | 'hearing' | 'thinking' | 'ending' | 'summary' | 'saving'
type Ear = 'server' | 'browser' | 'none'

declare global {
  interface Window {
    /** Speech end to Ava's first sound, per turn (ms). Read by the end-to-end tests. */
    __voiceLatency?: number[]
  }
}

const STATUS: Record<Phase, string> = {
  connecting: 'Connecting…',
  speaking: 'Speaking',
  listening: 'Listening',
  hearing: 'Hearing you',
  thinking: 'Thinking…',
  ending: 'Wrapping up…',
  summary: 'Call ended',
  saving: 'Saving…',
}

function earFor(cfg: VoiceConfig): Ear {
  if (cfg.asr === 'deepgram' || cfg.asr === 'fake') return 'server'
  return speechSupport().recognition ? 'browser' : 'none'
}

function privacyLine(cfg: VoiceConfig, ear: Ear): string {
  if (ear === 'server') return `Your speech is turned into text by ${cfg.asr === 'deepgram' ? 'Deepgram' : 'the server'}; no audio is kept.`
  if (ear === 'browser') return "Your speech is turned into text by your browser (in Chrome, Google's speech service)."
  return 'Type your answers; this browser cannot turn speech into text.'
}

export function VoiceIntro({
  agentName,
  config,
  onDone,
  onCancel,
}: {
  agentName: string
  config: VoiceConfig
  onDone: (saved: boolean) => void
  onCancel: () => void
}): JSX.Element {
  const lang = recognitionLang()
  const [ear, setEarView] = useState<Ear>(() => earFor(config))
  const [phase, setPhaseView] = useState<Phase>('connecting')
  const [avaLine, setAvaLine] = useState('')
  const [youLine, setYouLine] = useState('')
  const [muted, setMuted] = useState(false)
  const [typing, setTyping] = useState(() => earFor(config) === 'none')
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [style, setStyle] = useState<VoiceStyle>(config.voice)
  const [facts, setFacts] = useState<Fact[]>([])
  const [summary, setSummary] = useState('')
  const [camOn, setCamOn] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [lastLatency, setLastLatency] = useState<number | null>(null)
  const draftId = useId()

  const r = useRef({
    phase: 'connecting' as Phase,
    ear: earFor(config),
    alive: true,
    muted: false,
    typing: earFor(config) === 'none',
    ended: false,
    vad: null as Vad | null,
    speaker: null as Speaker | null,
    abort: null as AbortController | null,
    rec: null as Recognizer | null,
    heardBuf: '',
    interim: '',
    pendingHeard: null as string | null,
    speechEndAt: 0,
  })
  const videoRef = useRef<HTMLVideoElement>(null)
  const camRef = useRef<MediaStream | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  function setEar(e: Ear) {
    r.current.ear = e
    setEarView(e)
  }
  function setPhase(p: Phase) {
    r.current.phase = p
    setPhaseView(p)
  }

  // ---------- speaking ----------

  function makeSpeaker(s: VoiceStyle): Speaker {
    return new Speaker({
      mode: config.tts === 'browser' ? 'browser' : 'server',
      style: s,
      lang,
      onSentence: (i, text) => {
        if (!r.current.alive) return
        setPhase('speaking')
        setAvaLine((line) => (i === 0 ? text : `${line} ${text}`)) // the whole reply so far, as it's said
        r.current.vad?.setGuarded(true)
      },
      onFirstSound: () => {
        const at = r.current.speechEndAt
        if (!at) return
        const ms = Math.round(performance.now() - at)
        r.current.speechEndAt = 0
        window.__voiceLatency = [...(window.__voiceLatency ?? []), ms]
        setLastLatency(ms)
      },
      onIdle: () => {
        const st = r.current
        if (!st.alive) return
        st.vad?.setGuarded(false)
        if (st.ended) void endCall()
        else if (st.phase === 'speaking' || st.phase === 'connecting') setPhase('listening')
      },
    })
  }

  // ---------- one exchange with the server ----------

  async function exchange(path: string, body: FormData | Record<string, unknown>) {
    const st = r.current
    st.abort?.abort()
    const controller = new AbortController()
    st.abort = controller
    st.speaker?.reset()
    setNotice(null)
    try {
      await streamApp(
        path,
        body,
        (event, data) => {
          if (controller.signal.aborted || !st.alive) return
          if (event === 'transcript') setYouLine(String(data.text ?? ''))
          else if (event === 'sentence') st.speaker?.add(Number(data.i), String(data.text ?? ''))
          else if (event === 'audio') void st.speaker?.audio(Number(data.i), String(data.b64 ?? ''))
          else if (event === 'done') {
            if (data.empty) {
              setPhase('listening')
              return
            }
            st.ended = Boolean(data.ended)
            st.speaker?.finish()
          } else if (event === 'error') {
            setNotice(String(data.message ?? 'Something went wrong.'))
            st.speaker?.finish()
            setPhase('listening')
          }
        },
        controller.signal,
      )
    } catch (e) {
      if (controller.signal.aborted) return
      setNotice(e instanceof AppError ? e.message : 'Something went wrong. Try again.')
      setPhase('listening')
    }
  }

  function sendTurn(fields: { audio?: Blob; text?: string }) {
    const st = r.current
    const form = new FormData()
    if (fields.audio) form.append('audio', fields.audio, 'answer.wav')
    if (fields.text) form.append('text', fields.text)
    form.append('lang', lang)
    if (st.pendingHeard !== null) {
      form.append('interrupted', 'true')
      form.append('heard', st.pendingHeard)
    }
    st.pendingHeard = null
    setPhase('thinking')
    void exchange('/intro/voice/turn', form)
  }

  // ---------- listening ----------

  function onSpeechStart() {
    const st = r.current
    if (!st.alive || st.muted || st.typing || st.ended) return
    if (st.phase === 'speaking' || st.speaker?.busy) {
      st.pendingHeard = st.speaker?.stop() ?? '' // barge-in: Ava stops at once
      st.abort?.abort()
      st.vad?.setGuarded(false)
    } else if (st.phase === 'thinking') {
      st.pendingHeard = '' // they spoke again before Ava answered: she said nothing yet
      st.abort?.abort()
    }
    st.heardBuf = ''
    st.interim = ''
    setYouLine('')
    setPhase('hearing')
  }

  async function onSpeechEnd(wav: Blob) {
    const st = r.current
    if (!st.alive || st.muted || st.typing || st.ended) return
    st.speechEndAt = performance.now()
    if (st.ear === 'server') {
      sendTurn({ audio: wav })
      return
    }
    setPhase('thinking')
    // The browser's recognizer finishes a moment after the voice stops.
    for (let waited = 0; waited < 900 && !st.heardBuf; waited += 100) await new Promise((ok) => setTimeout(ok, 100))
    const text = `${st.heardBuf} ${st.heardBuf ? '' : st.interim}`.trim()
    st.heardBuf = ''
    st.interim = ''
    if (!text) {
      setPhase('listening')
      return
    }
    setYouLine(text)
    sendTurn({ text })
  }

  function startRecognizer() {
    const st = r.current
    if (st.rec || !st.alive) return
    const rec = createRecognizer({
      lang,
      onFinal: (text) => {
        st.heardBuf = `${st.heardBuf} ${text}`.trim()
        if (st.phase === 'hearing') setYouLine(st.heardBuf)
      },
      onInterim: (text) => {
        st.interim = text
        if (st.phase === 'hearing') setYouLine(`${st.heardBuf} ${text}`.trim())
      },
      onError: (code) => {
        if (code === 'not-allowed' || code === 'network' || code === 'service-not-allowed') {
          setEar('none')
          setTypingMode(true)
          setNotice("Your browser can't turn speech into text here, so type your answers.")
        }
      },
      onEnd: () => {
        st.rec = null
        if (st.alive && !st.ended && !st.typing) window.setTimeout(startRecognizer, 250) // keep listening
      },
    })
    if (rec && rec.start()) st.rec = rec
  }

  function setTypingMode(on: boolean) {
    r.current.typing = on
    setTyping(on)
    if (on) r.current.vad?.pause()
    else if (!r.current.muted) r.current.vad?.resume()
  }

  function toggleMute() {
    const st = r.current
    const m = !st.muted
    st.muted = m
    setMuted(m)
    if (m) {
      st.vad?.pause()
      st.rec?.abort()
    } else {
      st.vad?.resume()
      if (st.ear === 'browser') startRecognizer()
    }
  }

  function sendTyped(e?: FormEvent) {
    e?.preventDefault()
    const text = draft.trim()
    const st = r.current
    if (!text || st.ended || st.phase === 'thinking') return
    if (st.speaker?.busy) st.pendingHeard = st.speaker.stop()
    setDraft('')
    setYouLine(text)
    st.speechEndAt = performance.now()
    sendTurn({ text })
  }

  async function chooseVoice(s: VoiceStyle) {
    setStyle(s)
    r.current.speaker?.close()
    r.current.speaker = makeSpeaker(s)
    try {
      await call('/intro/voice/settings', { method: 'POST', json: { voice: s } })
    } catch {
      // the next call keeps the old choice; this one already uses the new voice
    }
  }

  // ---------- ending ----------

  function stopEverything() {
    const st = r.current
    st.abort?.abort()
    st.speaker?.stop()
    st.vad?.destroy()
    st.vad = null
    st.rec?.dispose()
    st.rec = null
    stopSpeaking()
    camRef.current?.getTracks().forEach((t) => t.stop())
    camRef.current = null
  }

  async function endCall() {
    const st = r.current
    if (st.phase === 'ending' || st.phase === 'summary' || st.phase === 'saving') return
    st.ended = true
    stopEverything()
    setCamOn(false)
    setPhase('ending')
    try {
      const res = await call<{ facts: Fact[]; summary: string }>('/intro/voice/finish', { method: 'POST' })
      setFacts(res.facts)
      setSummary(res.summary)
      setPhase('summary')
    } catch (e) {
      setNotice(e instanceof AppError ? e.message : 'Something went wrong.')
      setPhase('summary')
    }
  }

  async function save(keep: boolean) {
    setPhase('saving')
    const chosen = keep ? facts.map((f) => ({ content: f.content.trim(), category: f.category })).filter((f) => f.content) : []
    try {
      await call('/intro/voice/confirm', { method: 'POST', json: { facts: chosen } })
      onDone(keep && chosen.length > 0)
    } catch (e) {
      setNotice(e instanceof AppError ? e.message : 'Saving failed. Try again.')
      setPhase('summary')
    }
  }

  function leave() {
    r.current.alive = false
    stopEverything()
    r.current.speaker?.close()
    onCancel()
  }

  // ---------- start ----------

  useEffect(() => {
    const st = r.current
    st.alive = true
    st.speaker = makeSpeaker(config.voice)
    window.__voiceLatency = []
    rootRef.current?.focus()
    void (async () => {
      if (earFor(config) !== 'none') {
        try {
          st.vad = await startVad({ onSpeechStart, onSpeechEnd: (wav) => void onSpeechEnd(wav) })
          if (!st.alive) st.vad.destroy()
          if (earFor(config) === 'browser') startRecognizer()
        } catch (e) {
          const denied = e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'SecurityError')
          setEar('none')
          setTypingMode(true)
          setNotice(denied ? 'Your microphone is blocked, so type your answers. Allow it for this site to talk instead.' : "Voice detection couldn't start here, so type your answers.")
        }
      }
      if (st.alive) await exchange('/intro/voice/start', { lang })
    })()
    return () => {
      st.alive = false
      stopEverything()
      st.speaker?.close()
    }
    // Runs once per call; handlers read live state from the ref.
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (phase === 'summary' || phase === 'saving') return
    const t = window.setInterval(() => setSeconds((x) => x + 1), 1000)
    return () => window.clearInterval(t)
  }, [phase])

  useEffect(() => {
    if (!camOn) {
      camRef.current?.getTracks().forEach((t) => t.stop())
      camRef.current = null
      return
    }
    let cancelled = false
    navigator.mediaDevices
      ?.getUserMedia({ video: { facingMode: 'user' }, audio: false })
      .then((s) => {
        if (cancelled) return s.getTracks().forEach((t) => t.stop())
        camRef.current = s
        if (videoRef.current) videoRef.current.srcObject = s
      })
      .catch(() => setCamOn(false))
    return () => {
      cancelled = true
    }
  }, [camOn])

  // ---------- view ----------

  const inCall = phase !== 'summary' && phase !== 'saving'
  const mood: Mood = phase === 'thinking' || phase === 'ending' ? 'thinking' : phase === 'summary' ? 'happy' : 'idle'
  const listeningNow = (phase === 'listening' || phase === 'hearing') && !muted && !typing

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={`Voice call with ${agentName}`}
      className="fixed inset-0 z-50 flex flex-col bg-[linear-gradient(180deg,#2a2160,#120e2a)] text-white outline-none"
    >
      <header className="mx-auto flex w-full max-w-4xl shrink-0 items-center justify-between gap-3 px-4 pt-4 sm:px-6">
        <div className="min-w-0">
          <p className="truncate font-medium tight">Get to know {agentName}</p>
          <p className="text-xs text-white/55">{inCall ? 'Just talk. You can skip any question.' : 'What I learned'}</p>
        </div>
        <div className="flex items-center gap-2">
          {inCall && (
            <label className="text-xs text-white/70">
              <span className="sr-only">{agentName}'s voice</span>
              <select
                value={style}
                onChange={(e) => void chooseVoice(e.target.value as VoiceStyle)}
                className="rounded-full bg-white/10 px-3 py-1.5 text-xs text-white ring-1 ring-white/15 focus-visible:outline-white"
              >
                {config.voices.map((v) => (
                  <option key={v.value} value={v.value} className="text-ink">
                    {v.label} voice
                  </option>
                ))}
              </select>
            </label>
          )}
          <span aria-hidden className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/70 tabular-nums">
            {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}
          </span>
          <button type="button" onClick={leave} aria-label="Close without saving" className="grid size-9 place-items-center rounded-full text-white/70 hover:bg-white/10 hover:text-white focus-visible:outline-white">
            <X className="size-4" aria-hidden />
          </button>
        </div>
      </header>

      {inCall ? (
        <main className="mx-auto flex w-full max-w-4xl min-h-0 flex-1 flex-col items-center justify-center gap-6 overflow-y-auto px-4 py-6 sm:px-6">
          <div className="relative">
            <AnimatePresence>
              {phase === 'speaking' &&
                [0, 1].map((i) => (
                  <motion.span
                    key={`ring-${i}`}
                    aria-hidden
                    className="pointer-events-none absolute inset-0 rounded-full border-2 border-violet-400/70"
                    initial={{ opacity: 0, scale: 1 }}
                    animate={{ opacity: [0.75, 0], scale: [1, 1.5] }}
                    exit={{ opacity: 0, transition: { duration: 0.3 } }}
                    transition={{ duration: 1.9, repeat: Infinity, delay: i * 0.95, ease: 'easeOut' }}
                  />
                ))}
            </AnimatePresence>
            <Orb hue="indigo" size={170} mood={mood} track label={`${agentName}: ${STATUS[phase]}`} />
            {camOn && (
              <video ref={videoRef} autoPlay playsInline muted aria-label="Your camera, shown only on this screen" className="absolute -right-24 bottom-0 h-28 w-20 rounded-2xl object-cover ring-1 ring-white/20" style={{ transform: 'scaleX(-1)' }} />
            )}
          </div>

          <p data-testid="voice-status" role="status" aria-live="polite" className="inline-flex items-center gap-2 rounded-full bg-night-900/55 px-3 py-1.5 text-sm">
            <span className={`size-2 rounded-full ${phase === 'speaking' ? 'bg-violet-400' : phase === 'hearing' ? 'bg-emerald-400' : listeningNow ? 'bg-[#8fb0ff]' : 'bg-white/40'}`} aria-hidden />
            {muted && inCall && phase !== 'speaking' ? 'Muted' : typing && (phase === 'listening' || phase === 'hearing') ? 'Waiting for your answer' : STATUS[phase]}
          </p>

          <section aria-label="Captions" className="w-full max-w-2xl space-y-3">
            <div className="rounded-[22px] bg-white/5 px-5 py-4 ring-1 ring-white/10">
              <p className="mb-1 text-xs font-medium text-violet-400">{agentName}</p>
              <p data-testid="ava-caption" aria-live="polite" className="min-h-[1.75rem] text-lg leading-snug md:text-xl">
                {avaLine || <span className="text-white/45">{agentName} is joining…</span>}
              </p>
            </div>
            <div className="rounded-[22px] bg-white/5 px-5 py-4 ring-1 ring-white/10">
              <p className="mb-1 text-xs font-medium text-white/50">You</p>
              <p data-testid="you-caption" className="min-h-[1.5rem] text-[17px] leading-snug text-white/90">
                {youLine || <span className="text-white/45">{typing ? 'Type your answer below.' : listeningNow ? 'Go ahead, I’m listening.' : ' '}</span>}
              </p>
            </div>
            {notice && (
              <p role="alert" className="rounded-2xl bg-[#ffb3c6]/15 px-4 py-2 text-sm text-[#ffd6e0]">
                {notice}
              </p>
            )}
            {typing && (
              <form onSubmit={sendTyped} className="flex items-end gap-2 rounded-[20px] bg-white p-1.5 pl-4 focus-within:ring-2 focus-within:ring-violet-400/60">
                <label htmlFor={draftId} className="sr-only">
                  Type your answer
                </label>
                <input
                  id={draftId}
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Type your answer"
                  className="h-10 flex-1 bg-transparent text-ink outline-none placeholder:text-muted/70"
                />
                <button type="submit" disabled={!draft.trim() || phase === 'thinking'} className="inline-flex h-10 items-center gap-1.5 rounded-full bg-violet-600 px-4 text-sm font-medium text-white disabled:opacity-50">
                  <Send className="size-4" aria-hidden /> Send
                </button>
              </form>
            )}
          </section>
        </main>
      ) : (
        <Summary agentName={agentName} summary={summary} facts={facts} setFacts={setFacts} saving={phase === 'saving'} notice={notice} onSave={() => void save(true)} onDiscard={() => void save(false)} />
      )}

      {inCall && (
        <footer className="shrink-0 px-4 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <div className="mx-auto flex max-w-md items-center justify-center gap-3">
            <Round label={muted ? 'Unmute microphone' : 'Mute microphone'} off={muted} disabled={ear === 'none' || typing} onClick={toggleMute}>
              {muted ? <MicOff className="size-5" aria-hidden /> : <Mic className="size-5" aria-hidden />}
            </Round>
            <Round label={typing ? 'Talk instead' : 'Type instead'} off={typing} disabled={ear === 'none'} onClick={() => setTypingMode(!typing)}>
              <Keyboard className="size-5" aria-hidden />
            </Round>
            <Round label={camOn ? 'Turn camera off' : 'Turn camera on'} off={!camOn} onClick={() => setCamOn((c) => !c)}>
              {camOn ? <Video className="size-5" aria-hidden /> : <VideoOff className="size-5" aria-hidden />}
            </Round>
            <button type="button" onClick={() => void endCall()} className="inline-flex h-12 items-center gap-2 rounded-full bg-danger px-5 font-medium text-white hover:bg-[#a83150] focus-visible:outline-white sm:h-14 sm:px-6">
              <PhoneOff className="size-5" aria-hidden /> End call
            </button>
          </div>
          <p className="mx-auto mt-3 flex max-w-2xl items-center justify-center gap-1.5 text-center text-xs text-white/50">
            <ShieldCheck className="size-3.5 shrink-0" aria-hidden /> {privacyLine(config, ear)} Nothing is recorded.
            {lastLatency !== null && <span className="sr-only" data-testid="voice-latency">{lastLatency}</span>}
          </p>
        </footer>
      )}
    </div>
  )
}

function Summary({
  agentName,
  summary,
  facts,
  setFacts,
  saving,
  notice,
  onSave,
  onDiscard,
}: {
  agentName: string
  summary: string
  facts: Fact[]
  setFacts: (f: Fact[]) => void
  saving: boolean
  notice: string | null
  onSave: () => void
  onDiscard: () => void
}) {
  const edit = (i: number, content: string) => setFacts(facts.map((f, j) => (j === i ? { ...f, content } : f)))
  return (
    <main data-testid="voice-summary" className="mx-auto w-full max-w-2xl min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
      <h2 className="text-xl font-medium">What {agentName} learned</h2>
      <p className="mt-1 text-sm text-white/65">{summary || 'Check each line, change or remove anything, then save.'} Only you can see these.</p>
      {notice && (
        <p role="alert" className="mt-3 rounded-2xl bg-[#ffb3c6]/15 px-4 py-2 text-sm text-[#ffd6e0]">
          {notice}
        </p>
      )}
      <ul className="mt-4 space-y-2">
        {facts.map((f, i) => (
          <li key={f.id ?? i} className="flex items-start gap-2">
            <label className="sr-only" htmlFor={`fact-${i}`}>
              Fact {i + 1}
            </label>
            <textarea
              id={`fact-${i}`}
              value={f.content}
              rows={2}
              maxLength={500}
              onChange={(e) => edit(i, e.target.value)}
              className="min-h-[2.75rem] flex-1 resize-none rounded-2xl bg-white/10 px-4 py-2.5 text-base md:text-sm text-white ring-1 ring-white/15 outline-none focus:ring-violet-400"
            />
            <button type="button" onClick={() => setFacts(facts.filter((_, j) => j !== i))} aria-label={`Remove fact ${i + 1}`} className="mt-1.5 grid size-9 place-items-center rounded-full text-white/60 hover:bg-white/10 hover:text-white">
              <Trash2 className="size-4" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => setFacts([...facts, { content: '', category: 'about' }])} className="mt-3 inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-sm text-white/70 hover:text-white">
        <Plus className="size-4" aria-hidden /> Add something
      </button>
      <div className="mt-6 flex flex-wrap gap-3">
        <button type="button" onClick={onSave} disabled={saving} className="inline-flex items-center gap-2 rounded-full bg-white px-5 py-2.5 text-sm font-medium text-ink hover:bg-lilac-100 disabled:opacity-60">
          {saving ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden /> : <Check className="size-4 text-violet-600" aria-hidden />} Save to my notebook
        </button>
        <button type="button" onClick={onDiscard} disabled={saving} className="rounded-full px-5 py-2.5 text-sm text-white/75 ring-1 ring-white/20 hover:bg-white/10">
          Don't save anything
        </button>
      </div>
    </main>
  )
}

function Round({ label, off, disabled = false, onClick, children }: { label: string; off: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`grid size-12 shrink-0 place-items-center rounded-full transition-colors focus-visible:outline-white disabled:cursor-not-allowed disabled:opacity-40 sm:size-14 ${
        off ? 'bg-white text-night-800 hover:bg-lilac-100' : 'bg-white/10 text-white ring-1 ring-white/15 hover:bg-white/20'
      }`}
    >
      {children}
    </button>
  )
}
