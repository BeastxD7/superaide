import { ArrowUpRight, LogOut } from 'lucide-react'
import { motion } from 'motion/react'
import type { ComponentProps, ReactNode } from 'react'
import type { Session } from './AppRoot'
import { homeFor } from './session'

export const PRODUCT = 'Understudy'

export const STAGES = [
  { name: 'Infant', does: 'Watches, asks questions and drafts. Everything waits for your yes.' },
  { name: 'Child', does: 'Suggests ideas and drafts on its own. Still waits for your yes.' },
  { name: 'Adolescent', does: 'Publishes inside themes you have approved, with time for you to stop it.' },
  { name: 'Adult', does: 'Works on its own, within the limits you set.' },
]

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
  const account = session.account
  const hire = account?.hire
  return (
    <header className="sticky top-0 z-30 px-4 pt-4 sm:px-6">
      <nav className="glass mx-auto flex max-w-6xl items-center justify-between gap-4 rounded-full border border-white/60 py-2 pr-2 pl-5 shadow-[0_8px_30px_-18px_rgba(27,22,56,0.45)]">
        <a href="#/" className="flex items-center gap-2 font-semibold text-ink tight">
          <Sparkle className="size-5 text-violet-600" />
          <span className="text-lg">{PRODUCT}</span>
        </a>
        {links && (
          <div className="hidden items-center gap-1 rounded-full bg-mist p-1 text-sm md:flex">
            <a className="rounded-full px-4 py-1.5 text-body hover:bg-white" href="#/">
              Agents
            </a>
            <a className="rounded-full px-4 py-1.5 text-body hover:bg-white" href="#/?s=how">
              How hiring works
            </a>
            <a className="rounded-full px-4 py-1.5 text-body hover:bg-white" href="#/?s=safety">
              Safety
            </a>
          </div>
        )}
        <div className="flex items-center gap-2">
          {account ? (
            <>
              {hire ? (
                <Button tone="dark" icon onClick={() => (window.location.hash = homeFor(account))}>
                  {hire.onboarding_done ? 'Open Ava' : 'Continue with Ava'}
                </Button>
              ) : cta ? (
                <Button tone="dark" icon onClick={() => (window.location.hash = '/agents/ava')}>
                  Meet Ava
                </Button>
              ) : null}
              <span
                className="hidden size-10 place-items-center rounded-full bg-lilac-100 text-sm font-semibold text-violet-700 sm:grid"
                title={`${account.user.name} (${account.user.email})`}
                aria-label={`Signed in as ${account.user.name}`}
              >
                {account.user.name.charAt(0).toUpperCase()}
              </span>
              <button
                onClick={() => void session.logOut()}
                className="grid size-10 place-items-center rounded-full text-muted hover:bg-mist hover:text-ink"
                aria-label="Log out"
                title="Log out"
              >
                <LogOut className="size-4" />
              </button>
            </>
          ) : (
            <>
              <a href="#/login" className="rounded-full px-4 py-2.5 text-sm font-medium text-ink hover:bg-mist">
                Log in
              </a>
              {cta && (
                <Button tone="dark" icon onClick={() => (window.location.hash = '/agents/ava')}>
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

export function Footer() {
  return (
    <footer className="px-4 pb-6 sm:px-6">
      <div className="mx-auto max-w-6xl overflow-hidden rounded-[28px] bg-[linear-gradient(180deg,#2a2160,#120e2a)] text-white">
        <div className="flex flex-col gap-6 px-8 pt-10 sm:flex-row sm:items-start sm:justify-between">
          <p className="max-w-sm text-lg text-white/80">AI teammates that start as beginners and grow up by working with you.</p>
          <div className="flex gap-8 text-sm text-white/60">
            <a href="#/agents/ava" className="hover:text-white">
              Meet Ava
            </a>
            <a href="#/?s=safety" className="hover:text-white">
              Safety
            </a>
            <a href="#/console" className="hover:text-white">
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
