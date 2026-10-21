import type { ReactNode } from 'react'
import { siAnthropic, siClaude, siDiscord, siGithub, siInstagram, siTelegram, siWhatsapp } from 'simple-icons'

// Every brand mark the app shows, in one place. Two sources, both CC0-1.0:
//
// - simple-icons 16.34.0 (npm dependency): the official single-colour marks, drawn in
//   the brand's own hex (or currentColor when `mono`).
// - SVG Logos (gilbarbara/logos, via @iconify-json/logos 1.2.17), hand-inlined below for
//   the official full-colour marks simple-icons doesn't carry or only has in one colour:
//   Telegram, Slack, Google "G", Gmail and Google Calendar (2020 product icons).
//
// The trademarks belong to their owners; we show them only to name the service. Per the
// brand guidelines, full-colour marks are never recoloured: Google marks have no mono form
// here and always draw in colour. Slack's one-colour form is official, so `mono` fills its
// paths with currentColor.

export type Brand =
  | 'telegram'
  | 'slack'
  | 'google'
  | 'gmail'
  | 'googlecalendar'
  | 'claude'
  | 'anthropic'
  | 'instagram'
  | 'whatsapp'
  | 'discord'
  | 'github'

type Drawing = { viewBox: string; draw: (uid: string) => ReactNode }
export type Mark = { title: string; colour: Drawing; mono: Drawing | null }

const simple = (icon: { title: string; hex: string; path: string }, mono = true): Mark => ({
  title: icon.title,
  colour: { viewBox: '0 0 24 24', draw: () => <path fill={`#${icon.hex}`} d={icon.path} /> },
  mono: mono ? { viewBox: '0 0 24 24', draw: () => <path fill="currentColor" d={icon.path} /> } : null,
})

const SLACK_PATHS = [
  ['#e01e5a', 'M53.841 161.32c0 14.832-11.987 26.82-26.819 26.82S.203 176.152.203 161.32c0-14.831 11.987-26.818 26.82-26.818H53.84zm13.41 0c0-14.831 11.987-26.818 26.819-26.818s26.819 11.987 26.819 26.819v67.047c0 14.832-11.987 26.82-26.82 26.82c-14.83 0-26.818-11.988-26.818-26.82z'],
  ['#36c5f0', 'M94.07 53.638c-14.832 0-26.82-11.987-26.82-26.819S79.239 0 94.07 0s26.819 11.987 26.819 26.819v26.82zm0 13.613c14.832 0 26.819 11.987 26.819 26.819s-11.987 26.819-26.82 26.819H26.82C11.987 120.889 0 108.902 0 94.069c0-14.83 11.987-26.818 26.819-26.818z'],
  ['#2eb67d', 'M201.55 94.07c0-14.832 11.987-26.82 26.818-26.82s26.82 11.988 26.82 26.82s-11.988 26.819-26.82 26.819H201.55zm-13.41 0c0 14.832-11.988 26.819-26.82 26.819c-14.831 0-26.818-11.987-26.818-26.82V26.82C134.502 11.987 146.489 0 161.32 0s26.819 11.987 26.819 26.819z'],
  ['#ecb22e', 'M161.32 201.55c14.832 0 26.82 11.987 26.82 26.818s-11.988 26.82-26.82 26.82c-14.831 0-26.818-11.988-26.818-26.82V201.55zm0-13.41c-14.831 0-26.818-11.988-26.818-26.82c0-14.831 11.987-26.818 26.819-26.818h67.25c14.832 0 26.82 11.987 26.82 26.819s-11.988 26.819-26.82 26.819z'],
] as const

export const MARKS: Record<Brand, Mark> = {
  telegram: {
    title: 'Telegram',
    colour: {
      viewBox: '0 0 256 256',
      draw: (uid) => (
        <>
          <defs>
            <linearGradient id={`${uid}-tg`} x1="50%" x2="50%" y1="0%" y2="100%">
              <stop offset="0%" stopColor="#2aabee" />
              <stop offset="100%" stopColor="#229ed9" />
            </linearGradient>
          </defs>
          <circle cx="128" cy="128" r="128" fill={`url(#${uid}-tg)`} />
          <path
            fill="#fff"
            d="M57.94 126.648q55.98-24.384 74.64-32.152c35.56-14.786 42.94-17.354 47.76-17.441c1.06-.017 3.42.245 4.96 1.49c1.28 1.05 1.64 2.47 1.82 3.467c.16.996.38 3.266.2 5.038c-1.92 20.24-10.26 69.356-14.5 92.026c-1.78 9.592-5.32 12.808-8.74 13.122c-7.44.684-13.08-4.912-20.28-9.63c-11.26-7.386-17.62-11.982-28.56-19.188c-12.64-8.328-4.44-12.906 2.76-20.386c1.88-1.958 34.64-31.748 35.26-34.45c.08-.338.16-1.598-.6-2.262c-.74-.666-1.84-.438-2.64-.258c-1.14.256-19.12 12.152-54 35.686c-5.1 3.508-9.72 5.218-13.88 5.128c-4.56-.098-13.36-2.584-19.9-4.708c-8-2.606-14.38-3.984-13.82-8.41c.28-2.304 3.46-4.662 9.52-7.072"
          />
        </>
      ),
    },
    mono: { viewBox: '0 0 24 24', draw: () => <path fill="currentColor" d={siTelegram.path} /> },
  },
  slack: {
    title: 'Slack',
    colour: { viewBox: '0 0 256 256', draw: () => SLACK_PATHS.map(([fill, d]) => <path key={fill} fill={fill} d={d} />) },
    mono: { viewBox: '0 0 256 256', draw: () => SLACK_PATHS.map(([fill, d]) => <path key={fill} fill="currentColor" d={d} />) },
  },
  google: {
    title: 'Google',
    colour: {
      viewBox: '0 0 256 262',
      draw: () => (
        <>
          <path fill="#4285f4" d="M255.878 133.451c0-10.734-.871-18.567-2.756-26.69H130.55v48.448h71.947c-1.45 12.04-9.283 30.172-26.69 42.356l-.244 1.622l38.755 30.023l2.685.268c24.659-22.774 38.875-56.282 38.875-96.027" />
          <path fill="#34a853" d="M130.55 261.1c35.248 0 64.839-11.605 86.453-31.622l-41.196-31.913c-11.024 7.688-25.82 13.055-45.257 13.055c-34.523 0-63.824-22.773-74.269-54.25l-1.531.13l-40.298 31.187l-.527 1.465C35.393 231.798 79.49 261.1 130.55 261.1" />
          <path fill="#fbbc05" d="M56.281 156.37c-2.756-8.123-4.351-16.827-4.351-25.82c0-8.994 1.595-17.697 4.206-25.82l-.073-1.73L15.26 71.312l-1.335.635C5.077 89.644 0 109.517 0 130.55s5.077 40.905 13.925 58.602z" />
          <path fill="#eb4335" d="M130.55 50.479c24.514 0 41.05 10.589 50.479 19.438l36.844-35.974C195.245 12.91 165.798 0 130.55 0C79.49 0 35.393 29.301 13.925 71.947l42.211 32.783c10.59-31.477 39.891-54.251 74.414-54.251" />
        </>
      ),
    },
    mono: null,
  },
  gmail: {
    title: 'Gmail',
    colour: {
      viewBox: '0 0 256 193',
      draw: () => (
        <>
          <path fill="#4285f4" d="M58.182 192.05V93.14L27.507 65.077L0 49.504v125.091c0 9.658 7.825 17.455 17.455 17.455z" />
          <path fill="#34a853" d="M197.818 192.05h40.727c9.659 0 17.455-7.826 17.455-17.455V49.505l-31.156 17.837l-27.026 25.798z" />
          <path fill="#ea4335" d="m58.182 93.14l-4.174-38.647l4.174-36.989L128 69.868l69.818-52.364l4.669 34.992l-4.669 40.644L128 145.504z" />
          <path fill="#fbbc04" d="M197.818 17.504V93.14L256 49.504V26.231c0-21.585-24.64-33.89-41.89-20.945z" />
          <path fill="#c5221f" d="m0 49.504l26.759 20.07L58.182 93.14V17.504L41.89 5.286C24.61-7.66 0 4.646 0 26.23z" />
        </>
      ),
    },
    mono: null,
  },
  googlecalendar: {
    title: 'Google Calendar',
    colour: {
      viewBox: '0 0 256 256',
      draw: () => (
        <>
          <path fill="#fff" d="M195.368 60.632H60.632v134.736h134.736z" />
          <path fill="#ea4335" d="M195.368 256L256 195.368l-30.316-5.172l-30.316 5.172l-5.533 27.73z" />
          <path fill="#188038" d="M0 195.368v40.421C0 246.956 9.044 256 20.21 256h40.422l6.225-30.316l-6.225-30.316l-33.033-5.172z" />
          <path fill="#1967d2" d="M256 60.632V20.21C256 9.044 246.956 0 235.79 0h-40.422q-5.532 22.554-5.533 33.196q0 10.641 5.533 27.436q20.115 5.76 30.316 5.76T256 60.631" />
          <path fill="#fbbc04" d="M256 60.632h-60.632v134.736H256z" />
          <path fill="#34a853" d="M195.368 195.368H60.632V256h134.736z" />
          <path fill="#4285f4" d="M195.368 0H20.211C9.044 0 0 9.044 0 20.21v175.158h60.632V60.632h134.736z" />
          <path fill="#4285f4" d="M88.27 165.154c-5.036-3.402-8.523-8.37-10.426-14.94l11.689-4.816q1.59 6.063 5.558 9.398c2.627 2.223 5.827 3.318 9.566 3.318q5.734 0 9.852-3.487c2.746-2.324 4.127-5.288 4.127-8.875q0-5.508-4.345-8.994c-2.897-2.324-6.535-3.486-10.88-3.486h-6.754v-11.57h6.063q5.608 0 9.448-3.033c2.56-2.02 3.84-4.783 3.84-8.303c0-3.132-1.145-5.625-3.435-7.494c-2.29-1.87-5.188-2.813-8.708-2.813c-3.436 0-6.164.91-8.185 2.745a16.1 16.1 0 0 0-4.413 6.754l-11.57-4.817c1.532-4.345 4.345-8.185 8.471-11.503s9.398-4.985 15.798-4.985c4.733 0 8.994.91 12.767 2.745c3.772 1.836 6.736 4.379 8.875 7.613c2.14 3.25 3.2 6.888 3.2 10.93c0 4.126-.993 7.613-2.98 10.476s-4.43 5.052-7.327 6.585v.69a22.25 22.25 0 0 1 9.398 7.327c2.442 3.284 3.672 7.208 3.672 11.79c0 4.58-1.163 8.673-3.487 12.26c-2.324 3.588-5.54 6.417-9.617 8.472c-4.092 2.055-8.69 3.1-13.793 3.1c-5.912.016-11.369-1.685-16.405-5.087m71.797-58.005l-12.833 9.28l-6.417-9.734l23.023-16.607h8.825v78.333h-12.598z" />
        </>
      ),
    },
    mono: null,
  },
  claude: simple(siClaude),
  // Anthropic's and Instagram's glyphs are one-colour marks shown in ink (black/white per
  // their guidelines), so they follow the surrounding text colour and survive dark mode.
  anthropic: { ...simple(siAnthropic), colour: simple(siAnthropic).mono! },
  instagram: { ...simple(siInstagram), colour: simple(siInstagram).mono! },
  whatsapp: simple(siWhatsapp),
  discord: simple(siDiscord),
  github: { ...simple(siGithub), colour: simple(siGithub).mono! },
}

export function isBrand(value: string | null | undefined): value is Brand {
  return !!value && Object.hasOwn(MARKS, value)
}
