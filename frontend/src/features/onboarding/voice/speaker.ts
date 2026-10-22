// Ava's voice on the call: plays each sentence in order as it arrives, either audio the server
// made (a natural voice) or the browser's own speech when there is no server voice. Can be
// stopped at once when the owner talks over her, and says what they heard up to that point.

import { speak, type VoiceStyle } from '@/features/onboarding/speech'

type Item = { text: string; audio?: AudioBuffer | 'browser' | 'pending' }

export type SpeakerOptions = {
  /** 'server': wait for each sentence's audio; 'browser': speak the text with speechSynthesis. */
  mode: 'server' | 'browser'
  style: VoiceStyle
  lang: string
  volume?: number
  onSentence: (i: number, text: string) => void
  /** The first sound of this reply has started playing. */
  onFirstSound?: () => void
  /** Everything queued has been said and the reply is complete. */
  onIdle: () => void
}

export class Speaker {
  private ctx: AudioContext | null = null
  private gain: GainNode | null = null
  private items: Item[] = []
  private cur = 0
  private playing = false
  private complete = false
  private stopped = false
  private source: AudioBufferSourceNode | null = null
  private cancelSpeech: (() => void) | null = null
  private sounded = false

  constructor(private readonly opts: SpeakerOptions) {
    if (opts.mode === 'server' && typeof AudioContext !== 'undefined') {
      this.ctx = new AudioContext()
      this.gain = this.ctx.createGain()
      this.gain.gain.value = opts.volume ?? 1
      this.gain.connect(this.ctx.destination)
    }
  }

  /** Start a new reply (forgets the previous one). */
  reset(): void {
    this.halt()
    this.items = []
    this.cur = 0
    this.complete = false
    this.stopped = false
    this.sounded = false
    void this.ctx?.resume()
  }

  add(i: number, text: string): void {
    this.items[i] = { ...this.items[i], text }
    this.pump()
  }

  async audio(i: number, b64: string): Promise<void> {
    const item = (this.items[i] ??= { text: '' })
    if (!b64 || !this.ctx) {
      item.audio = 'browser' // the server's voice failed or is off: say it with the browser's
      this.pump()
      return
    }
    item.audio = 'pending'
    try {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
      item.audio = await this.ctx.decodeAudioData(bytes.buffer)
    } catch {
      item.audio = 'browser'
    }
    this.pump()
  }

  /** No more sentences will come for this reply. */
  finish(): void {
    this.complete = true
    this.pump()
  }

  /** Stop at once (the owner is talking). Returns the part of the reply they heard. */
  stop(): string {
    const heard = this.items
      .slice(0, this.playing ? this.cur + 1 : this.cur)
      .map((x) => x?.text ?? '')
      .join(' ')
      .trim()
    this.stopped = true
    this.halt()
    return heard
  }

  get busy(): boolean {
    return !this.stopped && (this.playing || this.cur < this.items.length || !this.complete)
  }

  close(): void {
    this.halt()
    void this.ctx?.close()
    this.ctx = null
  }

  private halt(): void {
    this.playing = false
    if (this.source) {
      this.source.onended = null
      try {
        this.source.stop()
      } catch {
        // already stopped
      }
      this.source = null
    }
    this.cancelSpeech?.()
    this.cancelSpeech = null
  }

  private pump(): void {
    if (this.playing || this.stopped) return
    const item = this.items[this.cur]
    if (!item || !item.text) {
      if (this.complete && this.cur >= this.items.length) this.opts.onIdle()
      return
    }
    const useBrowser = this.opts.mode === 'browser' || item.audio === 'browser'
    if (!useBrowser && (item.audio === undefined || item.audio === 'pending')) return // its audio isn't here yet
    const i = this.cur
    this.playing = true
    this.opts.onSentence(i, item.text)
    const next = () => {
      if (this.stopped || i !== this.cur) return
      this.playing = false
      this.source = null
      this.cancelSpeech = null
      this.cur += 1
      this.pump()
    }
    if (!this.sounded) {
      this.sounded = true
      this.opts.onFirstSound?.()
    }
    if (useBrowser) {
      this.cancelSpeech = speak(item.text, {
        lang: this.opts.lang,
        style: this.opts.style,
        rate: 0.98,
        volume: this.opts.volume ?? 1,
        onEnd: () => next(),
      })
      return
    }
    const ctx = this.ctx!
    const src = ctx.createBufferSource()
    src.buffer = item.audio as AudioBuffer
    src.connect(this.gain!)
    src.onended = next
    this.source = src
    src.start()
  }
}
