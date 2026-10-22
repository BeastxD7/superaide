'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ArrowUpRight, LogOut } from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useId, useRef, useState, type ComponentProps, type ReactNode } from 'react'
import type { Session } from '@/lib/session-context'
import { homeFor } from '@/lib/session'

export const PRODUCT = 'Superaide'

// The product's mark: an agent's face, the same one as the browser tab icon (src/app/icon.svg).
export function BrandMark({ className = 'size-6' }: { className?: string }) {
  const id = useId()
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden>
      <defs>
        <linearGradient id={`${id}b`} x1="0.1" y1="0" x2="0.9" y2="1">
          <stop offset="0" stopColor="#d9c9ff" />
          <stop offset="0.38" stopColor="#b9a6ff" />
          <stop offset="0.68" stopColor="#f3b6f5" />
          <stop offset="1" stopColor="#9cc7ff" />
        </linearGradient>
        <radialGradient id={`${id}s`} cx="0.32" cy="0.26" r="0.48">
          <stop offset="0" stopColor="#fff" stopOpacity="0.95" />
          <stop offset="0.45" stopColor="#fff" stopOpacity="0.32" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="30" fill={`url(#${id}b)`} />
      <circle cx="32" cy="32" r="30" fill={`url(#${id}s)`} />
      <rect x="21.5" y="25.5" width="7" height="13" rx="3.5" fill="#1d1640" />
      <rect x="35.5" y="25.5" width="7" height="13" rx="3.5" fill="#1d1640" />
    </svg>
  )
}

export function Sparkle({ className = 'size-5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden>
      <path d="M12 1.5c.6 5.6 4.9 9.9 10.5 10.5-5.6.6-9.9 4.9-10.5 10.5C11.4 16.9 7.1 12.6 1.5 12 7.1 11.4 11.4 7.1 12 1.5z" fill="currentColor" />
    </svg>
  )
}

type ButtonProps = {
  tone?: 'dark' | 'light' | 'violet' | 'ghost'
  icon?: boolean
  children: ReactNode
  className?: string
} & Omit<ComponentProps<typeof motion.button>, 'children'>

const TONES = {
  dark: 'bg-ink text-white hover:bg-night-800',
  light: 'bg-haze-100 text-ink hover:bg-[#e9e6f5]',
  violet:
    'bg-gradient-to-r from-violet-500 to-violet-600 text-white shadow-[0_10px_30px_-10px_rgba(103,71,238,0.7)] hover:from-violet-600 hover:to-violet-700',
  ghost: 'bg-transparent text-ink hover:bg-mist',
}

export function Button({ tone = 'dark', icon = false, children, className = '', ...rest }: ButtonProps) {
  return (
    <motion.button
      whileTap={{ scale: 0.97 }}
      className={`inline-flex items-center ${className.includes('justify-') ? '' : 'justify-center'} gap-3 rounded-full font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
        icon ? 'py-1.5 pr-1.5 pl-5' : 'px-5 py-3'
      } ${TONES[tone]} ${className}`}
      {...rest}
    >
      <span className={icon ? 'py-1.5' : ''}>{children}</span>
      {icon && (
        <span
          className={`grid size-9 place-items-center rounded-full ${tone === 'dark' || tone === 'violet' ? 'bg-white text-ink' : 'bg-ink text-white'}`}
        >
          <ArrowUpRight className="size-4" strokeWidth={2.2} />
        </span>
      )}
    </motion.button>
  )
}

export function NavBar({ session, links = true, cta = true }: { session: Session; links?: boolean; cta?: boolean }) {
  const router = useRouter()
  const account = session.account
  const hire = account?.hire
  return (
    <header className="sticky top-0 z-30 px-4 pt-4 sm:px-6">
      <nav className="glass mx-auto flex max-w-6xl items-center justify-between gap-4 rounded-full border border-white/60 py-2 pr-2 pl-5 shadow-[0_8px_30px_-18px_rgba(27,22,56,0.45)]">
        <Link href="/" className="flex items-center gap-2 font-semibold text-ink tight">
          <BrandMark className="size-7" />
          <span className="text-lg">{PRODUCT}</span>
        </Link>
        {links && (
          <div className="hidden items-center gap-1 rounded-full bg-mist p-1 text-sm md:flex">
            <Link className="rounded-full px-4 py-1.5 text-body hover:bg-white" href="/">
              Agents
            </Link>
            <a className="rounded-full px-4 py-1.5 text-body hover:bg-white" href="/#how">
              How hiring works
            </a>
            <a className="rounded-full px-4 py-1.5 text-body hover:bg-white" href="/#safety">
              Safety
            </a>
          </div>
        )}
        <div className="flex items-center gap-2">
          {account ? (
            <>
              {hire ? (
                <Button tone="dark" icon onClick={() => router.push(homeFor(account))}>
                  {hire.onboarding_done ? 'Open dashboard' : 'Continue with Ava'}
                </Button>
              ) : cta ? (
                <Button tone="dark" icon onClick={() => router.push('/agents/ava')}>
                  Meet Ava
                </Button>
              ) : null}
              <AccountMenu session={session} />
            </>
          ) : (
            <>
              <Link href="/login" className="rounded-full px-4 py-2.5 text-sm font-medium text-ink hover:bg-mist">
                Log in
              </Link>
              {cta && (
                <Button tone="dark" icon onClick={() => router.push('/agents/ava')}>
                  Meet Ava
                </Button>
              )}
            </>
          )}
        </div>
      </nav>
    </header>
  )
}

// Who is signed in, on every signed-in screen: the account's initial opens a
// small menu with the name, email and Log out.
export function AccountMenu({ session }: { session: Session }) {
  const account = session.account
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!account) return null
  const initial = account.user.name.trim().charAt(0).toUpperCase() || '?'
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`Account: signed in as ${account.user.name}`}
        title={`${account.user.name} (${account.user.email})`}
        className="grid size-10 place-items-center rounded-full bg-lilac-100 text-sm font-semibold text-violet-700 hover:bg-lilac-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500"
      >
        {initial}
      </button>
      {open && (
        <div
          id={menuId}
          className="absolute right-0 z-40 mt-2 w-64 rounded-2xl border border-line bg-white p-2 text-left shadow-[0_18px_50px_-20px_rgba(27,22,56,0.45)]"
        >
          <div className="px-3 py-2">
            <p className="text-xs text-muted">Signed in as</p>
            <p className="truncate font-medium text-ink">{account.user.name}</p>
            <p className="truncate text-sm text-muted">{account.user.email}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              void session.logOut()
            }}
            className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-ink hover:bg-mist"
          >
            <LogOut className="size-4" />
            Log out
          </button>
        </div>
      )}
    </div>
  )
}

export function Footer() {
  return (
    <footer className="px-4 pb-6 sm:px-6">
      <div className="mx-auto max-w-6xl overflow-hidden rounded-[28px] bg-[linear-gradient(180deg,#2a2160,#120e2a)] text-white">
        <div className="flex flex-col gap-6 px-8 pt-10 sm:flex-row sm:items-start sm:justify-between">
          <p className="max-w-sm text-lg text-white/80">AI teammates that start as beginners and grow up by working with you.</p>
          <div className="flex gap-8 text-sm text-white/60">
            <Link href="/agents/ava" className="hover:text-white">
              Meet Ava
            </Link>
            <a href="/#safety" className="hover:text-white">
              Safety
            </a>
            <a href="/console" className="hover:text-white">
              Operator console
            </a>
          </div>
        </div>
        <p
          aria-hidden
          className="-mb-[0.18em] mt-6 select-none bg-gradient-to-b from-white/90 to-white/5 bg-clip-text px-4 text-center text-[16vw] leading-none font-semibold text-transparent tight lg:text-[184px]"
        >
          {PRODUCT}
        </p>
      </div>
    </footer>
  )
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'error'; children: ReactNode }) {
  const styles = {
    info: 'bg-sky-100 text-ink',
    warn: 'bg-rose-100 text-[#6b3d06]',
    error: 'bg-[#fbe8ea] text-danger',
  }[tone]
  return <div className={`rounded-2xl px-4 py-3 text-sm ${styles}`}>{children}</div>
}
