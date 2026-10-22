// Voice activity detection on this device (Silero VAD v5 via @ricky0123/vad-web, ADR 0012).
// It decides when the owner starts and stops talking, so the call takes turns by itself. The
// model and its runtime are served from this site (/vad/, see scripts/copy-vad-assets.mjs).
// Audio stays in the browser until a finished utterance is posted for that one turn.

export type Vad = {
  pause: () => void
  resume: () => void
  destroy: () => void
  /** While Ava speaks, ask for clearer, longer speech before counting it as talking over her. */
  setGuarded: (guarded: boolean) => void
  stream: MediaStream | null
}

export type VadOptions = {
  /** Speech long enough to be real (not a cough or a click): the owner has started talking. */
  onSpeechStart: () => void
  /** The owner stopped talking: the utterance as a 16 kHz WAV, and how long it was. */
  onSpeechEnd: (wav: Blob, ms: number) => void
  /** Speech started but was too short to count. */
  onMisfire?: () => void
}

// Normal turn-taking: end a turn after about 0.9 s of silence, ignore anything under 250 ms.
const NORMAL = { positiveSpeechThreshold: 0.5, negativeSpeechThreshold: 0.35, minSpeechMs: 250, redemptionMs: 900 }
// While Ava is speaking: echo cancellation removes most of her voice, but be stricter anyway.
const GUARDED = { positiveSpeechThreshold: 0.8, negativeSpeechThreshold: 0.6, minSpeechMs: 450, redemptionMs: 900 }

export async function startVad(options: VadOptions): Promise<Vad> {
  const { MicVAD, utils } = await import('@ricky0123/vad-web')
  let stream: MediaStream | null = null
  const vad = await MicVAD.new({
    model: 'v5',
    baseAssetPath: '/vad/',
    onnxWASMBasePath: '/vad/',
    ...NORMAL,
    preSpeechPadMs: 300,
    getStream: async () => {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      return stream
    },
    onSpeechRealStart: () => options.onSpeechStart(),
    onSpeechEnd: (audio: Float32Array) => {
      const wav = new Blob([utils.encodeWAV(audio)], { type: 'audio/wav' })
      options.onSpeechEnd(wav, Math.round((audio.length / 16000) * 1000))
    },
    onVADMisfire: () => options.onMisfire?.(),
  })
  await vad.start()
  return {
    pause: () => void vad.pause(),
    resume: () => void vad.start(),
    destroy: () => void vad.destroy(),
    setGuarded: (guarded) => vad.setOptions(guarded ? GUARDED : NORMAL),
    get stream() {
      return stream
    },
  }
}
