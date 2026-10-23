'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ArrowUpRight, LogOut, Menu } from 'lucide-react'
import { motion } from 'motion/react'
import { useState, type ComponentProps, type ReactNode } from 'react'
import { BrandMark } from '@/components/BrandMark'
import type { Session } from '@/lib/session-context'
import { homeFor } from '@/lib/session'
import { initialOf } from '@/lib/initial'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'

export const PRODUCT = 'Superaide'

export { BrandMark }

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
  // 14px text and a 44px tap target on phones, 16px from sm up. Labels never wrap. With `icon`, the arrow
  // disc shows from sm up only: on a phone the button is a compact text pill.
  return (
    <motion.button
      whileTap={{ scale: 0.97 }}
      className={`inline-flex min-h-11 items-center ${className.includes('justify-') ? '' : 'justify-center'} gap-3 rounded-full text-[length:var(--text-control)] font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-45 sm:text-base ${
        icon ? 'px-4 py-2 sm:py-1.5 sm:pr-1.5 sm:pl-5' : 'px-5 py-3'
      } ${TONES[tone]} ${className}`}
      {...rest}
    >
      <span className={icon ? 'sm:py-1.5' : ''}>{children}</span>
      {icon && (
        <span
          aria-hidden
          className={`hidden size-9 place-items-center rounded-full sm:grid ${tone === 'dark' || tone === 'violet' ? 'bg-white text-ink' : 'bg-ink text-white'}`}
        >
          <ArrowUpRight className="size-4" strokeWidth={2.2} />
        </span>
      )}
    </motion.button>
  )
}

const NAV_LINKS = [
  { href: '/', label: 'Agents' },
  { href: '/#how', label: 'How hiring works' },
  { href: '/#safety', label: 'Safety' },
]

export function NavBar({ session, links = true, cta = true }: { session: Session; links?: boolean; cta?: boolean }) {
  const router = useRouter()
  const [menuOpen, setMenuOpen] = useState(false)
  const account = session.account
  const hire = account?.hire
  // On phones the visible label is short; the full name stays as the accessible name.
  const dashboardLabel = hire?.onboarding_done ? 'Open dashboard' : 'Continue with Ava'
  const dashboardShort = hire?.onboarding_done ? 'Dashboard' : 'Continue'
  const showMenu = !account
  return (
    <header className="sticky top-0 z-30 page-x pt-2 sm:pt-4">
      <nav
        aria-label="Site"
        className="glass mx-auto flex h-14 max-w-6xl items-center justify-between gap-2 rounded-full border border-white/60 py-1 pr-1 pl-3 shadow-[0_8px_30px_-18px_rgba(27,22,56,0.45)] sm:gap-4 sm:pr-2 sm:pl-5"
      >
        <Link href="/" className="flex min-h-11 min-w-11 shrink-0 items-center gap-2 font-semibold whitespace-nowrap text-ink tight">
          <BrandMark className="size-7" />
          <span className="text-base max-[379px]:sr-only sm:text-lg">{PRODUCT}</span>
        </Link>
        {links && (
          <div className="hidden items-center gap-1 rounded-full bg-mist p-1 text-sm touch:p-0.5 md:flex">
            {NAV_LINKS.map((l) => (
              <Link key={l.href} className="inline-flex items-center rounded-full px-4 py-1.5 whitespace-nowrap text-body hover:bg-white touch:min-h-11" href={l.href}>
                {l.label}
              </Link>
            ))}
          </div>
        )}
        <div className="flex min-w-0 items-center gap-1 sm:gap-2">
          {account ? (
            <>
              {hire ? (
                <Button tone="dark" icon aria-label={dashboardLabel} onClick={() => router.push(homeFor(account))}>
                  <span className="sm:hidden" aria-hidden>
                    {dashboardShort}
                  </span>
                  <span className="hidden sm:inline">{dashboardLabel}</span>
                </Button>
              ) : cta ? (
                <Button tone="dark" icon onClick={() => router.push('/agents/ava')}>
                  Meet Ava
                </Button>
              ) : null}
              <AccountMenu session={session} links={links} />
            </>
          ) : (
            <>
              <Link
                href="/login"
                className="inline-flex min-h-11 items-center rounded-full px-3 text-[length:var(--text-control)] font-medium whitespace-nowrap text-ink hover:bg-mist sm:px-4 sm:text-base"
              >
                Log in
              </Link>
              {cta && (
                <Button tone="dark" icon onClick={() => router.push('/agents/ava')}>
                  Meet Ava
                </Button>
              )}
            </>
          )}
          {showMenu && (
            <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
              <SheetTrigger asChild>
                <button
                  type="button"
                  aria-label="Menu"
                  className="grid size-11 shrink-0 place-items-center rounded-full text-ink hover:bg-mist md:hidden"
                >
                  <Menu className="size-5" aria-hidden />
                </button>
              </SheetTrigger>
              <SheetContent side="right" className="w-[min(20rem,85vw)] gap-0 pb-[env(safe-area-inset-bottom)]">
                <SheetHeader className="border-b border-line">
                  <SheetTitle>{PRODUCT}</SheetTitle>
                  <SheetDescription className="sr-only">Site navigation</SheetDescription>
                </SheetHeader>
                <div className="flex flex-col gap-1 overflow-y-auto p-3 text-base">
                  {links &&
                    NAV_LINKS.map((l) => (
                      <Link
                        key={l.href}
                        href={l.href}
                        onClick={() => setMenuOpen(false)}
                        className="flex min-h-11 items-center rounded-xl px-3 text-ink hover:bg-mist"
                      >
                        {l.label}
                      </Link>
                    ))}
                  {!account && (
                    <Link href="/login" onClick={() => setMenuOpen(false)} className="flex min-h-11 items-center rounded-xl px-3 font-medium text-ink hover:bg-mist">
                      Log in
                    </Link>
                  )}
                </div>
              </SheetContent>
            </Sheet>
          )}
        </div>
      </nav>
    </header>
  )
}

// Who is signed in, on every signed-in screen: the account's initial opens a
// small menu with the name, email and Log out.
export function AccountMenu({ session, links = false }: { session: Session; links?: boolean }) {
  const account = session.account
  if (!account) return null
  const initial = initialOf(account.user.name)
  // Radix gives the keyboard and screen-reader behaviour for free: arrows, Escape returning focus to the button,
  // focus moving into the menu on open, and a real menu role.
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Account: signed in as ${account.user.name}`}
          title={`${account.user.name} (${account.user.email})`}
          className="grid size-11 shrink-0 place-items-center rounded-full bg-lilac-100 text-sm font-semibold text-violet-700 hover:bg-lilac-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500"
        >
          {initial}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" collisionPadding={8} className="w-64 rounded-2xl border-line bg-white p-2">
        <div className="px-3 py-2">
          <p className="text-xs text-muted">Signed in as</p>
          <p className="truncate font-medium text-ink">{account.user.name}</p>
          <p className="truncate text-sm text-muted">{account.user.email}</p>
        </div>
        {links &&
          NAV_LINKS.map((l) => (
            <DropdownMenuItem key={l.href} asChild className="min-h-11 rounded-xl px-3 text-sm text-ink md:hidden">
              <Link href={l.href}>{l.label}</Link>
            </DropdownMenuItem>
          ))}
        <DropdownMenuItem onSelect={() => void session.logOut()} className="min-h-11 gap-2 rounded-xl px-3 py-2 text-sm text-ink">
          <LogOut className="size-4" />
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function Footer() {
  return (
    <footer className="page-x pb-6">
      <div className="mx-auto max-w-6xl overflow-hidden rounded-[28px] bg-[linear-gradient(180deg,#2a2160,#120e2a)] text-white">
        <div className="flex flex-col gap-6 px-5 pt-8 sm:flex-row sm:px-8 sm:pt-10 sm:items-start sm:justify-between">
          <p className="max-w-sm text-lg text-white/80">AI teammates that start as beginners and grow up by working with you.</p>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-white/60 sm:gap-x-8">
            <Link href="/agents/ava" className="inline-flex min-h-11 items-center hover:text-white">
              Meet Ava
            </Link>
            <a href="/#safety" className="inline-flex min-h-11 items-center hover:text-white">
              Safety
            </a>
            <a href="/console" className="inline-flex min-h-11 items-center hover:text-white">
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
