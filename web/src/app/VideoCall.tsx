import {
  Captions,
  CaptionsOff,
  Check,
  Keyboard,
  LoaderCircle,
  Mic,
  MicOff,
  PhoneOff,
  RotateCcw,
  Send,
  ShieldCheck,
  User,
  Video,
  VideoOff,
  Volume2,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type JSX,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { Orb, type Mood } from './Orb'
import { createRecognizer, prepareVoices, recognitionLang, speak, speechSupport, stopSpeaking, unlockSpeech, type Recognizer } from './speech'
import { Button, Notice } from './ui'

// The video call introduction: the owner meets their assistant face to face.
// The assistant asks each question aloud, listens to the answer, and hands the
// transcript to the caller. The camera preview never leaves this screen: no
// frames are uploaded or recorded, and no audio is captured for display.

export type CallQuestion = { id: string; text: string }
export type CallTurnResult = { next: CallQuestion | null; reply?: string | null } // reply: a short acknowledgement said before the next question

export type VideoCallProps = {
  agentName: string
  firstQuestion: CallQuestion
  onAnswer: (questionId: string, transcript: string) => Promise<CallTurnResult>
  onFinish: () => void // called when there is no next question and the closing line has been spoken
  onCancel: () => void // user hangs up early
}

const SILENCE_MS = 2200
const CLOSING_LINE = "Thanks, that's really helpful. I've saved everything to my notebook."
const AVA_TILE_BG =
  'radial-gradient(60% 55% at 50% 48%, rgba(155,132,255,0.32) 0%, rgba(155,132,255,0) 70%), linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02))'
const YOU_TILE_BG = 'radial-gradient(70% 60% at 50% 40%, rgba(124,92,250,0.28) 0%, rgba(124,92,250,0) 70%), #1d1745'

type Phase = 'connecting' | 'speaking' | 'answering' | 'thinking' | 'failed' | 'closing' | 'ended'
type LineKind = 'question' | 'reply' | 'closing'
type Cam = 'starting' | 'on' | 'off' | 'denied' | 'unavailable'
type Voice = 'ok' | 'unsupported' | 'denied' | 'nomic' | 'broken'

// Mutable call state that async callbacks (speech, recognition, timers) read.
type Live = {
  alive: boolean
  flow: number // bumped whenever a new sequence starts, so stale awaits stop
  phase: Phase
  question: CallQuestion
  committed: string
  interim: string
  muted: boolean
  typing: boolean
  voice: Voice
  rec: Recognizer | null
  turn: number // identifies the current recognizer; stale events are ignored
  startedAt: number
  gotResult: boolean
  quickFails: number
  silenceTimer: number
  restartTimer: number
  cancelSpeech: (() => void) | null
  sent: string
}

const VOICE_NOTE: Record<Exclude<Voice, 'ok'>, string> = {
  unsupported: "Voice answers don't work in this browser, so type them here.",
  denied: 'Your microphone is blocked, so type your answer here. To talk instead, allow the microphone for this site, then choose Use my voice.',
  nomic: 'No microphone found, so type your answer here.',
  broken: 'Voice stopped working, so type your answer here.',
}

const CAM_NOTE: Record<Cam, string> = {
  starting: 'Starting your camera…',
  on: '',
  off: 'Camera off',
  denied: 'Camera blocked. Allow it for this site to show your face.',
  unavailable: 'No camera found',
}

export function VideoCall({ agentName, firstQuestion, onAnswer, onFinish, onCancel }: VideoCallProps): JSX.Element {
  const [support] = useState(speechSupport)
  const [phase, setPhaseView] = useState<Phase>('connecting')
  const [count, setCount] = useState(1)
  const [line, setLine] = useState<{ text: string; kind: LineKind } | null>(null)
  const [committed, setCommitted] = useState('')
  const [interim, setInterim] = useState('')
  const [draft, setDraft] = useState('')
  const [typing, setTypingView] = useState(!support.recognition)
  const [voice, setVoiceView] = useState<Voice>(support.recognition ? 'ok' : 'unsupported')
  const [muted, setMutedView] = useState(false)
  const [listening, setListening] = useState(false)
  const [captions, setCaptions] = useState(true)
  const [cam, setCam] = useState<Cam>(() => (cameraApi() ? 'starting' : 'unavailable'))
  const [error, setError] = useState<string | null>(null)
  const [voiceBlocked, setVoiceBlocked] = useState(false)
  const [confirmLeave, setConfirmLeave] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const big = useMedia('(min-width: 768px) and (min-height: 700px)')
  const wide = useMedia('(min-width: 768px)')
  const inputId = useId()

  const s = useRef<Live>({
    alive: false,
    flow: 0,
    phase: 'connecting',
    question: firstQuestion,
    committed: '',
    interim: '',
    muted: false,
    typing: !support.recognition,
    voice: support.recognition ? 'ok' : 'unsupported',
    rec: null,
    turn: 0,
    startedAt: 0,
    gotResult: false,
    quickFails: 0,
    silenceTimer: 0,
    restartTimer: 0,
    cancelSpeech: null,
    sent: '',
  })
  const cb = useRef({ onAnswer, onFinish, onCancel })
  useLayoutEffect(() => {
    cb.current = { onAnswer, onFinish, onCancel }
  })

  const rootRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const camRequest = useRef(0)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const stayRef = useRef<HTMLButtonElement>(null)
  const leaveRef = useRef<HTMLButtonElement>(null)

  // ---------- state helpers (mirror into the ref for async callbacks) ----------

  function setPhase(p: Phase) {
    s.current.phase = p
    setPhaseView(p)
  }
  function setTranscript(c: string, i: string) {
    s.current.committed = c
    s.current.interim = i
    setCommitted(c)
    setInterim(i)
  }
  function setTyping(on: boolean) {
    s.current.typing = on
    setTypingView(on)
  }
  function setVoice(v: Voice) {
    s.current.voice = v
    setVoiceView(v)
  }
  function setMuted(m: boolean) {
    s.current.muted = m
    setMutedView(m)
  }
  function current(my: number) {
    return s.current.alive && s.current.flow === my
  }
  function shouldListen() {
    const st = s.current
    return st.alive && st.phase === 'answering' && st.voice === 'ok' && !st.muted && !st.typing
  }

  // ---------- listening ----------

  function clearSilence() {
    const st = s.current
    if (st.silenceTimer) window.clearTimeout(st.silenceTimer)
    st.silenceTimer = 0
  }
  function clearRestart() {
    const st = s.current
    if (st.restartTimer) window.clearTimeout(st.restartTimer)
    st.restartTimer = 0
  }
  function scheduleStart(ms: number) {
    const st = s.current
    clearRestart()
    st.restartTimer = window.setTimeout(() => {
      st.restartTimer = 0
      startRecognizer()
    }, ms)
  }

  // The answer ends after SILENCE_MS with no new words, once something has been said.
  function armSilence() {
    const st = s.current
    clearSilence()
    if (st.muted || st.typing || !joinText(st.committed, st.interim)) return
    st.silenceTimer = window.setTimeout(() => {
      st.silenceTimer = 0
      if (st.phase === 'answering' && !st.typing && !st.muted) void submit(joinText(st.committed, st.interim))
    }, SILENCE_MS)
  }

  function startRecognizer() {
    const st = s.current
    if (st.rec || !shouldListen()) return
    const turn = ++st.turn
    const rec = createRecognizer({
      lang: recognitionLang(),
      onStart: () => {
        if (turn === st.turn) setListening(true)
      },
      onFinal: (text) => {
        if (turn !== st.turn || st.phase !== 'answering') return
        st.gotResult = true
        setTranscript(joinText(st.committed, text), st.interim)
        armSilence()
      },
      onInterim: (text) => {
        if (turn !== st.turn || st.phase !== 'answering') return
        if (text) st.gotResult = true
        setTranscript(st.committed, text)
        if (text) armSilence()
      },
      onError: (code) => {
        if (turn !== st.turn) return
        if (code === 'not-allowed') fallBack('denied')
        else if (code === 'audio-capture') fallBack('nomic')
        else if (code === 'service-not-allowed' || code === 'network' || code === 'language-not-supported') fallBack('broken')
        // 'no-speech' and 'aborted' are routine: onEnd restarts listening if needed.
      },
      onEnd: () => {
        if (turn !== st.turn) return
        st.rec = null
        setListening(false)
        if (!shouldListen()) return
        // Browsers end sessions after a while; keep listening, but give up if it keeps failing at once.
        const quick = !st.gotResult && performance.now() - st.startedAt < 1500
        st.quickFails = quick ? st.quickFails + 1 : 0
        if (st.quickFails >= 4) fallBack('broken')
        else scheduleStart(quick ? 700 : 150)
      },
    })
    if (!rec) {
      fallBack('unsupported')
      return
    }
    st.rec = rec
    st.startedAt = performance.now()
    st.gotResult = false
    if (!rec.start()) {
      rec.dispose()
      st.rec = null
      st.quickFails += 1
      if (st.quickFails >= 4) fallBack('broken')
      else scheduleStart(700)
    }
  }

  // Stop listening now and forget the recognizer. Words still in flight are kept.
  function stopListening() {
    const st = s.current
    clearSilence()
    clearRestart()
    st.turn++
    const rec = st.rec
    st.rec = null
    rec?.dispose()
    if (st.interim) setTranscript(joinText(st.committed, st.interim), '')
    setListening(false)
  }

  function fallBack(v: Exclude<Voice, 'ok'>) {
    stopListening()
    setVoice(v)
    const heard = s.current.committed
    setDraft((d) => (d.trim() ? d : heard))
    setTyping(true)
  }

  function listen() {
    setPhase('answering')
    s.current.quickFails = 0
    // A short beat so the tail of the agent's voice isn't picked up.
    if (shouldListen()) scheduleStart(250)
  }

  // ---------- speaking and the question flow ----------

  function say(text: string, kind: LineKind): Promise<void> {
    const st = s.current
    stopListening() // never listen while speaking
    st.cancelSpeech?.()
    setLine({ text, kind })
    setPhase(kind === 'closing' ? 'closing' : 'speaking')
    const began = performance.now()
    return new Promise((resolve) => {
      st.cancelSpeech = speak(text, {
        rate: 1,
        onEnd: ({ ok, error }) => {
          st.cancelSpeech = null
          if (error === 'not-allowed' || error === 'no-start') setVoiceBlocked(true)
          else if (ok) setVoiceBlocked(false)
          // No voice: leave the caption up long enough to read before moving on.
          const wait = ok ? 0 : Math.max(0, readingTime(text) - (performance.now() - began))
          window.setTimeout(resolve, wait)
        },
      })
    })
  }

  async function ask(q: CallQuestion, my: number) {
    s.current.question = q
    setTranscript('', '')
    // Keep the draft: submit() already cleared it, and anything here was typed while Ava was talking.
    await say(q.text, 'question')
    if (current(my)) listen()
  }

  async function submit(raw: string) {
    const st = s.current
    const text = raw.trim()
    if (!text || (st.phase !== 'answering' && st.phase !== 'failed')) return
    const my = ++st.flow
    stopListening()
    st.sent = text
    setError(null)
    setPhase('thinking')
    let result: CallTurnResult
    try {
      result = await cb.current.onAnswer(st.question.id, text)
    } catch (e) {
      if (!current(my)) return
      setError(e instanceof Error && e.message ? e.message : 'Something went wrong.')
      setPhase('failed')
      return
    }
    if (!current(my)) return
    setTranscript('', '')
    setDraft('')
    const reply = result.reply?.trim()
    const next = result.next
    if (reply) {
      await say(reply, 'reply')
      if (!current(my)) return
    }
    if (next) {
      setCount((c) => c + 1)
      await ask(next, my)
      return
    }
    await say(CLOSING_LINE, 'closing')
    if (!current(my)) return
    setPhase('ended')
    stopCamera()
    cb.current.onFinish()
  }

  async function repeat() {
    const st = s.current
    if (st.phase !== 'answering' && st.phase !== 'failed') return
    const my = ++st.flow
    setError(null)
    await say(st.question.text, 'question')
    if (current(my)) listen()
  }

  function retry() {
    void submit(s.current.sent)
  }

  function answerAgain() {
    s.current.flow++
    setError(null)
    setTranscript('', '')
    setDraft('')
    listen()
  }

  function openTyping() {
    stopListening()
    setDraft(s.current.committed)
    setTyping(true)
  }

  function switchToVoice() {
    if (!support.recognition) return
    const typed = draft.trim()
    if (typed) setTranscript(typed, '')
    setDraft('')
    s.current.quickFails = 0
    setMuted(false)
    setVoice('ok')
    setTyping(false)
    if (shouldListen()) startRecognizer()
  }

  function onMic() {
    const st = s.current
    if (st.voice !== 'ok' || st.typing) {
      switchToVoice()
      return
    }
    const m = !st.muted
    setMuted(m)
    if (m) {
      clearSilence()
      clearRestart()
      st.rec?.stop() // keep what was already said
    } else if (shouldListen()) startRecognizer()
  }

  function hearAgent() {
    unlockSpeech() // must run inside the click for Safari and iOS
    setVoiceBlocked(false)
    void repeat()
  }

  function teardown() {
    const st = s.current
    stopListening()
    st.cancelSpeech?.()
    st.cancelSpeech = null
    stopSpeaking()
  }

  function leave() {
    const st = s.current
    st.flow++
    st.alive = false
    teardown()
    stopCamera()
    setPhase('ended')
    cb.current.onCancel()
  }

  // ---------- camera (local preview only) ----------

  // Opens the camera (video only) and resolves to the new camera state, or null
  // if a newer request or stopCamera() superseded this one.
  async function openCamera(): Promise<Cam | null> {
    const id = ++camRequest.current
    if (!cameraApi()) return 'unavailable'
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      })
      if (id !== camRequest.current) {
        stream.getTracks().forEach((t) => t.stop())
        return null
      }
      streamRef.current = stream
      stream.getVideoTracks().forEach((t) => t.addEventListener('ended', () => onCameraLost(stream)))
      return 'on'
    } catch (e) {
      if (id !== camRequest.current) return null
      const name = e instanceof DOMException ? e.name : ''
      return name === 'NotAllowedError' || name === 'SecurityError' ? 'denied' : 'unavailable'
    }
  }

  function startCamera() {
    void openCamera().then((next) => {
      if (next) setCam(next)
    })
  }

  // The camera was unplugged or permission was revoked mid-call.
  function onCameraLost(stream: MediaStream) {
    if (streamRef.current !== stream) return
    streamRef.current = null
    setCam('off')
  }

  function stopCamera() {
    camRequest.current++
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }

  function toggleCamera() {
    if (cam === 'on') {
      stopCamera()
      setCam('off')
    } else if (cameraApi()) {
      setCam('starting')
      startCamera()
    }
  }

  // ---------- effects ----------

  // Start the call: warm up voices, give the camera a moment, then ask the first question.
  useEffect(() => {
    const st = s.current
    st.alive = true
    prepareVoices()
    if (!st.typing) rootRef.current?.focus() // otherwise the typing box takes focus
    const my = ++st.flow
    const timer = window.setTimeout(() => {
      if (current(my)) void ask(st.question, my)
    }, 900)
    return () => {
      window.clearTimeout(timer)
      st.alive = false
      st.flow++
      teardown()
    }
    // Runs once per mount; the handlers read live state from refs.
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    startCamera()
    return () => stopCamera()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    if (cam === 'on' && streamRef.current) {
      v.srcObject = streamRef.current
      v.play().catch(() => {})
    } else v.srcObject = null
  }, [cam])

  const ended = phase === 'ended'
  useEffect(() => {
    if (ended) return
    const t = window.setInterval(() => setSeconds((x) => x + 1), 1000)
    return () => window.clearInterval(t)
  }, [ended])

  useEffect(() => {
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [committed, interim])

  useEffect(() => {
    if (typing) inputRef.current?.focus()
  }, [typing])

  useEffect(() => {
    if (confirmLeave) stayRef.current?.focus()
  }, [confirmLeave])

  // ---------- view ----------

  const speakingNow = phase === 'speaking' || phase === 'closing'
  const canAnswer = phase === 'answering' || phase === 'failed'
  const voiceOn = voice === 'ok' && !typing
  const heard = joinText(committed, interim)
  const hearing = voiceOn && listening && !muted && interim.length > 0
  const micOff = !support.recognition || voice !== 'ok' || typing || muted
  const mood: Mood = phase === 'thinking' ? 'thinking' : phase === 'closing' || ended ? 'happy' : 'idle'
  const orbSize = big ? 220 : wide ? 170 : 136

  const status: Record<Phase, string> = {
    connecting: 'Joining…',
    speaking: 'Speaking',
    closing: 'Speaking',
    answering: voiceOn ? (muted ? 'Waiting · your mic is muted' : 'Listening') : 'Waiting for your answer',
    thinking: 'Thinking…',
    failed: 'Waiting',
    ended: 'Call ended',
  }
  const srStatus: Record<Phase, string> = {
    connecting: `Connecting to ${agentName}.`,
    speaking: `${agentName} is speaking.`,
    closing: `${agentName} is wrapping up.`,
    answering: voiceOn
      ? muted
        ? 'Your microphone is muted.'
        : `${agentName} is listening. Choose Done answering when you finish.`
      : 'Type your answer, then choose Send.',
    thinking: `${agentName} is thinking.`,
    failed: 'Your answer did not go through.',
    ended: 'The call has ended.',
  }
  const hint =
    phase === 'connecting' || phase === 'speaking'
      ? `Answer when ${agentName} finishes.`
      : phase === 'answering'
        ? muted
          ? 'Your mic is muted. Unmute to answer, or type instead.'
          : listening
            ? 'Listening… take your time.'
            : 'Turning on your mic…'
        : phase === 'closing' || ended
          ? 'All done.'
          : ''
  const micLabel = !support.recognition
    ? "Voice answers aren't available in this browser"
    : typing || voice !== 'ok'
      ? 'Answer with your voice'
      : muted
        ? 'Unmute microphone'
        : 'Mute microphone'

  function onDraftKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (canAnswer && draft.trim()) void submit(draft)
    }
  }

  // Keep Tab inside the call; it covers the whole page.
  function onRootKey(e: KeyboardEvent<HTMLDivElement>) {
    const root = rootRef.current
    if (e.key !== 'Tab' || !root) return
    const items = Array.from(root.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), input:not([disabled]), a[href]')).filter(
      (el) => el.offsetParent !== null,
    )
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === root)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }

  function onConfirmKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.stopPropagation()
      setConfirmLeave(false)
      leaveRef.current?.focus()
    }
  }

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={`Video call with ${agentName}`}
      onKeyDown={onRootKey}
      className="fixed inset-0 z-50 flex flex-col bg-[linear-gradient(180deg,#2a2160,#120e2a)] text-white outline-none"
    >
      <p className="sr-only" role="status">
        {srStatus[phase]}
      </p>

      <header className="mx-auto flex w-full max-w-6xl shrink-0 items-center justify-between gap-3 px-4 pt-4 sm:px-6">
        <div className="min-w-0">
          <p className="truncate font-medium text-white tight">Introduce yourself to {agentName}</p>
          <p className="text-xs text-white/55">{ended ? 'Call ended' : phase === 'connecting' ? 'Connecting…' : `Question ${count}`}</p>
        </div>
        <span aria-hidden className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/70 tabular-nums">
          {clock(seconds)}
        </span>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="relative mx-auto grid w-full max-w-6xl gap-4 p-4 sm:px-6 md:h-full md:grid-cols-2 md:grid-rows-[minmax(0,1fr)_auto] md:gap-5">
          {/* The agent */}
          <section
            aria-label={agentName}
            className="relative grid h-[40dvh] min-h-[220px] place-items-center overflow-hidden rounded-[28px] ring-1 ring-white/10 md:h-auto md:min-h-[220px]"
            style={{ background: AVA_TILE_BG }}
          >
            <div className="relative">
              <AnimatePresence>
                {speakingNow &&
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
              <Orb hue="indigo" size={orbSize} mood={mood} track label={`${agentName}: ${status[phase]}`} />
            </div>
            <span className="absolute top-3 left-3 inline-flex max-w-[calc(100%-8rem)] items-center gap-2 rounded-full bg-night-900/55 px-3 py-1.5 text-sm text-white backdrop-blur md:max-w-[calc(100%-1.5rem)]">
              <span className={`size-2 shrink-0 rounded-full ${speakingNow ? 'bg-violet-400' : phase === 'answering' && voiceOn && !muted ? 'bg-[#8fb0ff]' : 'bg-white/40'}`} aria-hidden />
              <span className="truncate">
                {agentName} <span className="text-white/60">· {status[phase]}</span>
              </span>
            </span>
            {voiceBlocked && canAnswer && (
              <button
                type="button"
                onClick={hearAgent}
                className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-2 rounded-full bg-white px-4 py-2 text-sm font-medium text-ink shadow-lg hover:bg-lilac-100 focus-visible:outline-white"
              >
                <Volume2 className="size-4 text-violet-600" aria-hidden /> Tap to hear {agentName}
              </button>
            )}
          </section>

          {/* You: a small picture-in-picture on phones, a full tile on wider screens */}
          <section
            aria-label="You"
            className={`overflow-hidden ring-1 transition-shadow max-md:absolute max-md:top-6 max-md:right-6 max-md:z-10 max-md:h-32 max-md:w-24 max-md:rounded-2xl max-md:shadow-[0_14px_30px_-10px_rgba(0,0,0,0.65)] md:relative md:min-h-[220px] md:rounded-[28px] ${
              hearing ? 'ring-2 ring-violet-400' : 'ring-white/15'
            }`}
            style={{ background: YOU_TILE_BG }}
          >
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              aria-label="Your camera preview, shown only on this screen"
              className={`absolute inset-0 size-full object-cover ${cam === 'on' ? '' : 'hidden'}`}
              style={{ transform: 'scaleX(-1)' }}
            />
            {cam !== 'on' && (
              <div className="absolute inset-0 grid place-items-center">
                <div className="flex flex-col items-center gap-3 px-3 text-center">
                  <span className={`grid place-items-center rounded-full bg-lilac-100 text-violet-700 ${wide ? 'size-24' : 'size-11'}`}>
                    {cam === 'starting' ? (
                      <LoaderCircle className={`${wide ? 'size-8' : 'size-5'} motion-safe:animate-spin`} aria-hidden />
                    ) : (
                      <User className={wide ? 'size-10' : 'size-5'} aria-hidden />
                    )}
                  </span>
                  <p className={wide ? 'max-w-[16rem] text-sm text-white/65' : 'sr-only'}>{CAM_NOTE[cam]}</p>
                </div>
              </div>
            )}
            <span className="absolute bottom-2 left-2 inline-flex items-center gap-1.5 rounded-full bg-night-900/60 px-2.5 py-1 text-xs text-white backdrop-blur md:bottom-3 md:left-3 md:text-sm">
              You
              {micOff && (
                <>
                  <MicOff className="size-3.5 text-[#ffb3c6]" aria-hidden />
                  <span className="sr-only">, microphone off</span>
                </>
              )}
            </span>
          </section>

          {/* What the agent is saying */}
          <div className="rounded-[22px] bg-white/5 px-5 py-4 ring-1 ring-white/10 md:min-h-[9rem]">
            <div className="mb-1.5 flex min-h-7 items-center justify-between gap-3">
              <p className="text-xs font-medium text-violet-400">
                {line && line.kind !== 'question' ? agentName : `Question ${count}`}
              </p>
              {canAnswer && (
                <button
                  type="button"
                  onClick={() => void repeat()}
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs text-white/65 hover:bg-white/10 hover:text-white focus-visible:outline-white"
                  aria-label="Ask the question again"
                >
                  <RotateCcw className="size-3.5" aria-hidden /> Repeat
                </button>
              )}
            </div>
            <div aria-live="polite" aria-atomic="true">
              {!captions ? (
                <p className="text-sm text-white/45">Captions are off.</p>
              ) : line ? (
                <motion.p
                  key={`${line.kind}:${line.text}`}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
                  className={`text-lg leading-snug transition-colors md:text-xl ${speakingNow || line.kind === 'question' ? 'text-white' : 'text-white/70'}`}
                >
                  {line.text}
                </motion.p>
              ) : (
                <p className="text-white/45">{agentName} is joining…</p>
              )}
            </div>
            {!support.synthesis && <p className="mt-2 text-xs text-white/50">{agentName}'s voice isn't available in this browser, so read along here.</p>}
          </div>

          {/* Your answer */}
          <div className="flex flex-col rounded-[22px] bg-white/5 p-4 ring-1 ring-white/10 md:min-h-[9rem]">
            {phase === 'failed' && error && (
              <div className="mb-3">
                <Notice tone="error">
                  <p>That answer didn't go through. {error}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button type="button" onClick={retry} className="rounded-full bg-danger px-3.5 py-1.5 text-xs font-medium text-white hover:bg-[#a83150]">
                      Try again
                    </button>
                    {!typing && (
                      <button type="button" onClick={answerAgain} className="rounded-full bg-white px-3.5 py-1.5 text-xs font-medium text-danger ring-1 ring-danger/30 hover:bg-[#fff5f7]">
                        Answer again
                      </button>
                    )}
                  </div>
                </Notice>
              </div>
            )}

            {!typing ? (
              <>
                <div className="mb-1 flex items-center gap-2 text-xs font-medium text-white/50">
                  You {listening && !muted && <ListeningBars active={hearing} />}
                </div>
                <div ref={transcriptRef} className="max-h-28 min-h-[2.75rem] overflow-y-auto text-[17px] leading-snug text-white">
                  {!captions ? (
                    <span className="text-sm text-white/45">Captions are off.</span>
                  ) : heard ? (
                    <>
                      {committed}
                      {committed && interim ? ' ' : ''}
                      <span className="text-white/55">{interim}</span>
                    </>
                  ) : (
                    <span className="text-white/45">{hint}</span>
                  )}
                </div>
                <div className="mt-auto flex flex-wrap items-center justify-between gap-3 pt-3">
                  <button
                    type="button"
                    onClick={openTyping}
                    disabled={ended || phase === 'thinking'}
                    className="inline-flex items-center gap-1.5 rounded-full px-1 py-1 text-sm text-white/65 underline-offset-4 hover:text-white hover:underline focus-visible:outline-white disabled:opacity-40"
                  >
                    <Keyboard className="size-4" aria-hidden /> Type instead
                  </button>
                  {phase !== 'failed' && (
                    <Button tone="violet" className="px-5 py-2.5 text-sm" disabled={!canAnswer || !heard} onClick={() => void submit(heard)}>
                      <span className="inline-flex items-center gap-2">
                        {phase === 'thinking' ? (
                          <>
                            <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden /> Sending…
                          </>
                        ) : (
                          <>
                            <Check className="size-4" aria-hidden /> Done answering
                          </>
                        )}
                      </span>
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <>
                {voice !== 'ok' && <p className="mb-2 text-sm text-white/70">{VOICE_NOTE[voice]}</p>}
                <label htmlFor={inputId} className="mb-1.5 block text-xs font-medium text-white/50">
                  Type your answer
                </label>
                <div className="flex items-end gap-2 rounded-[20px] bg-white p-1.5 pl-4 focus-within:ring-2 focus-within:ring-violet-400/60">
                  <textarea
                    id={inputId}
                    ref={inputRef}
                    rows={2}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onDraftKey}
                    placeholder="Type your answer"
                    disabled={phase === 'thinking' || ended}
                    className="max-h-36 min-h-[2.75rem] flex-1 resize-none bg-transparent py-2 text-ink outline-none placeholder:text-muted/70 disabled:opacity-60"
                  />
                  <motion.button
                    type="button"
                    whileTap={{ scale: 0.94 }}
                    onClick={() => void submit(draft)}
                    disabled={!canAnswer || !draft.trim()}
                    className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-gradient-to-br from-violet-500 to-violet-600 px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:from-lilac-200 disabled:to-lilac-200 disabled:text-violet-700/60"
                  >
                    {phase === 'thinking' ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden /> : <Send className="size-4" aria-hidden />}
                    Send
                  </motion.button>
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-white/50">
                  <span>
                    {phase === 'connecting' || phase === 'speaking'
                      ? `You can send once ${agentName} finishes.`
                      : phase === 'thinking'
                        ? 'Sending…'
                        : 'Enter to send, Shift + Enter for a new line'}
                  </span>
                  {support.recognition && (
                    <button
                      type="button"
                      onClick={switchToVoice}
                      disabled={ended || phase === 'thinking'}
                      className="inline-flex items-center gap-1.5 rounded-full px-1 py-1 text-sm text-white/65 underline-offset-4 hover:text-white hover:underline focus-visible:outline-white disabled:opacity-40"
                    >
                      <Mic className="size-4" aria-hidden /> Use my voice
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </main>

      <footer className="shrink-0 px-4 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto flex max-w-md items-center justify-center gap-2.5 sm:gap-3">
          <RoundButton label={micLabel} off={micOff} disabled={!support.recognition || ended} onClick={onMic}>
            {micOff ? <MicOff className="size-5" aria-hidden /> : <Mic className="size-5" aria-hidden />}
          </RoundButton>
          <RoundButton label={cam === 'on' ? 'Turn camera off' : 'Turn camera on'} off={cam !== 'on'} disabled={ended || cam === 'starting' || !cameraApi()} onClick={toggleCamera}>
            {cam === 'on' ? <Video className="size-5" aria-hidden /> : <VideoOff className="size-5" aria-hidden />}
          </RoundButton>
          <RoundButton label={captions ? 'Turn captions off' : 'Turn captions on'} off={!captions} onClick={() => setCaptions((c) => !c)}>
            {captions ? <Captions className="size-5" aria-hidden /> : <CaptionsOff className="size-5" aria-hidden />}
          </RoundButton>
          <div className="relative">
            <motion.button
              ref={leaveRef}
              type="button"
              whileTap={{ scale: 0.94 }}
              onClick={() => setConfirmLeave(true)}
              aria-label="Leave the call"
              aria-expanded={confirmLeave}
              className="inline-flex h-12 items-center gap-2 rounded-full bg-danger px-5 font-medium text-white transition-colors hover:bg-[#a83150] focus-visible:outline-white sm:h-14 sm:px-6"
            >
              <PhoneOff className="size-5" aria-hidden /> Leave
            </motion.button>
            <AnimatePresence>
              {confirmLeave && (
                <motion.div
                  role="group"
                  aria-label="Confirm leaving the call"
                  onKeyDown={onConfirmKey}
                  initial={{ opacity: 0, y: 8, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 6, scale: 0.98 }}
                  transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
                  className="absolute right-0 bottom-full z-20 mb-3 w-64 origin-bottom-right rounded-2xl bg-white p-4 text-ink shadow-[0_20px_50px_-15px_rgba(0,0,0,0.6)]"
                >
                  <p className="font-medium">Leave the call?</p>
                  <p className="mt-1 text-sm text-muted">{agentName} will stop asking questions.</p>
                  <div className="mt-3 flex gap-2">
                    <button
                      ref={stayRef}
                      type="button"
                      onClick={() => {
                        setConfirmLeave(false)
                        leaveRef.current?.focus()
                      }}
                      className="flex-1 rounded-full bg-haze-100 px-4 py-2 text-sm font-medium text-ink hover:bg-[#e9e6f5]"
                    >
                      Stay
                    </button>
                    <button type="button" onClick={leave} className="flex-1 rounded-full bg-danger px-4 py-2 text-sm font-medium text-white hover:bg-[#a83150]">
                      Leave
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
        <div className="mx-auto mt-3 flex max-w-5xl flex-col items-center gap-1 text-center text-xs text-white/50 sm:flex-row sm:flex-wrap sm:justify-center sm:gap-x-5">
          <p className="inline-flex items-center gap-1.5 sm:shrink-0">
            <ShieldCheck className="size-3.5 shrink-0" aria-hidden /> Your camera stays on this screen. Nothing is recorded.
          </p>
          {support.recognition && (
            <p className="inline-flex items-center gap-1.5 sm:shrink-0">
              <Mic className="size-3.5 shrink-0" aria-hidden /> Speech is turned into text by your browser. In Chrome this uses Google's speech service.
            </p>
          )}
        </div>
      </footer>
    </div>
  )
}

function RoundButton({
  label,
  off = false,
  disabled = false,
  onClick,
  children,
}: {
  label: string
  off?: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <motion.button
      type="button"
      whileTap={disabled ? undefined : { scale: 0.92 }}
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`grid size-12 shrink-0 place-items-center rounded-full transition-colors focus-visible:outline-white disabled:cursor-not-allowed disabled:opacity-40 sm:size-14 ${
        off ? 'bg-white text-night-800 hover:bg-lilac-100' : 'bg-white/10 text-white ring-1 ring-white/15 hover:bg-white/20'
      }`}
    >
      {children}
    </motion.button>
  )
}

function ListeningBars({ active }: { active: boolean }) {
  return (
    <span className="inline-flex h-3 items-end gap-0.5" aria-hidden>
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-full w-0.5 rounded-full bg-violet-400"
          style={{ originY: 1 }}
          animate={active ? { scaleY: [0.35, 1, 0.35] } : { scaleY: 0.35 }}
          transition={active ? { duration: 0.8, repeat: Infinity, delay: i * 0.15 } : { duration: 0.2 }}
        />
      ))}
    </span>
  )
}

function useMedia(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      const m = window.matchMedia(query)
      m.addEventListener('change', notify)
      return () => m.removeEventListener('change', notify)
    },
    [query],
  )
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  )
}

// getUserMedia exists only in secure contexts (https or localhost).
function cameraApi(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.mediaDevices?.getUserMedia === 'function'
}

function joinText(...parts: string[]): string {
  return parts
    .map((p) => p.trim())
    .filter(Boolean)
    .join(' ')
}

// Roughly how long it takes to read a line, for when the voice can't play.
function readingTime(text: string): number {
  return Math.min(9000, Math.max(2200, 1000 + text.length * 55))
}

function clock(total: number): string {
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}
