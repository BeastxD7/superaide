'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Eye, EyeOff } from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useState, type FormEvent } from 'react'
import { AppError, auth } from '@/lib/api'
import type { Session } from '@/lib/session-context'
import { Orb } from '@/components/Orb'
import { BrandIcon } from '@/components/BrandIcon'
import { homeFor, safeNext } from '@/lib/session'
import { Button, Notice, PRODUCT } from '@/components/site'

type Mode = 'signup' | 'login'

// What to tell someone a Google sign-in sent back to this page.
const GOOGLE_PROBLEMS: Record<string, string> = {
  cancelled: 'Google sign-in was cancelled. You can try again, or use your email and password.',
  expired: 'That Google sign-in took too long or was opened in another browser. Please try again.',
  unverified: "Google hasn't verified that account's email address, so we can't use it to sign you in.",
  locked: 'Too many wrong passwords on this account. Try again in a few minutes.',
  throttled: 'Too many attempts from this device. Wait a while and try again.',
  failed: "We couldn't sign you in with Google. Please try again, or use your email and password.",
}

// The reason comes from the address bar, so only our own keys count ("__proto__" or "constructor" are not reasons).
function googleProblem(reason: string): string {
  return Object.hasOwn(GOOGLE_PROBLEMS, reason) ? GOOGLE_PROBLEMS[reason] : GOOGLE_PROBLEMS.failed
}

export function AuthPage({
  mode,
  next,
  google: googleReason = null,
  session,
}: {
  mode: Mode
  next: string | null
  google?: string | null
  session: Session
}) {
  const router = useRouter()
  const target = safeNext(next)
  const hiring = Boolean(target && /^\/agents\/[^/]+\/hire$/.test(target))
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  // Why a Google sign-in came back here, if it did (/login?google=<reason>).
  const [error, setError] = useState<{ message: string; field?: string } | null>(
    googleReason ? { message: googleProblem(googleReason) } : null,
  )
  const [google, setGoogle] = useState(false)
  const [googleBusy, setGoogleBusy] = useState(false)

  // Offer Google only when this server has it switched on.
  useEffect(() => {
    let live = true
    auth
      .googleEnabled()
      .then((r) => live && setGoogle(r.enabled))
      .catch(() => live && setGoogle(false))
    return () => {
      live = false
    }
  }, [])

  async function continueWithGoogle() {
    setGoogleBusy(true)
    setError(null)
    try {
      const { url } = await auth.googleStart(target, mode)
      window.location.assign(url)
    } catch (err) {
      setError(err instanceof AppError ? { message: err.message } : { message: String(err) })
      setGoogleBusy(false)
    }
  }

  // Already signed in: go where you were heading.
  useEffect(() => {
    if (session.account) router.replace(target ?? homeFor(session.account))
  }, [session.account, target, router])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const account = mode === 'signup' ? await auth.signup(name, email, password) : await auth.login(email, password)
      await session.signedIn(account)
      router.replace(target ?? homeFor(account))
    } catch (err) {
      setError(err instanceof AppError ? { message: err.message, field: err.field } : { message: String(err) })
    } finally {
      setBusy(false)
    }
  }

  const other = mode === 'signup' ? 'login' : 'signup'
  const otherHref = `/${other}${target ? `?next=${encodeURIComponent(target)}` : ''}`
  const fieldError = (f: string) =>
    error?.field === f ? (
      <span className="mt-1.5 block text-sm text-danger" role="alert">
        {error.message}
      </span>
    ) : null

  return (
    <div className="mesh flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-xl items-center gap-4 page-x pt-4 sm:pt-6">
        <Link
          href={hiring && target ? target.replace(/\/hire$/, '') : '/'}
          className="grid size-11 shrink-0 place-items-center rounded-full bg-white text-ink shadow-sm hover:bg-mist"
          aria-label="Back"
        >
          <ChevronLeft className="size-5" />
        </Link>
        {hiring ? (
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white" aria-label="Hiring progress: step 1 of 3">
            <motion.div className="h-full rounded-full bg-gradient-to-r from-violet-400 to-violet-600" initial={{ width: 0 }} animate={{ width: '33%' }} />
          </div>
        ) : (
          <span className="flex-1" />
        )}
        <Link href="/" className="inline-flex min-h-11 items-center text-sm font-medium text-muted hover:text-ink">
          {PRODUCT}
        </Link>
      </header>

      <main className="mx-auto flex w-full max-w-md flex-1 flex-col page-x pt-6 pb-10 sm:pt-10">
        <motion.form
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
          onSubmit={submit}
          className="flex flex-1 flex-col"
          noValidate
        >
          <div className="flex flex-col items-center text-center">
            <Orb hue="indigo" size={96} track />
            <h1 className="mt-6 text-3xl font-medium tight sm:text-4xl">{mode === 'signup' ? (hiring ? 'Create your account' : 'Create an account') : 'Welcome back'}</h1>
            <p className="mt-2 text-body">
              {mode === 'signup'
                ? hiring
                  ? 'Ava will work for this account. You can log in from any browser.'
                  : `Your account on ${PRODUCT}.`
                : 'Log in to pick up where you left off.'}
            </p>
          </div>

          {google && (
            <div className="mt-8">
              <button
                type="button"
                onClick={continueWithGoogle}
                disabled={googleBusy}
                className="flex w-full items-center justify-center gap-3 rounded-full border border-[#dadce0] bg-white px-5 py-3.5 font-medium text-[#1f1f1f] transition-colors hover:bg-[#f8f9fa] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <BrandIcon brand="google" size={20} decorative />
                {googleBusy ? 'Opening Google…' : 'Continue with Google'}
              </button>
              <div className="mt-6 flex items-center gap-3 text-sm text-muted" aria-hidden="true">
                <span className="h-px flex-1 bg-ink/10" />
                or with your email
                <span className="h-px flex-1 bg-ink/10" />
              </div>
            </div>
          )}

          <div className={google ? 'mt-6 space-y-4' : 'mt-8 space-y-4'}>
            {mode === 'signup' && (
              <label className="block">
                <span className="text-sm font-medium text-ink">Your name</span>
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  maxLength={120}
                  placeholder="e.g. Shashank Rao"
                  aria-invalid={error?.field === 'name'}
                  className="mt-2 w-full rounded-2xl border border-white bg-white px-4 py-3.5 text-ink outline-none placeholder:text-muted focus:border-violet-400 focus:ring-2 focus:ring-violet-400/60"
                />
                {fieldError('name')}
              </label>
            )}
            <label className="block">
              <span className="text-sm font-medium text-ink">Email</span>
              <input
                autoFocus={mode === 'login'}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete={mode === 'signup' ? 'email' : 'username'}
                placeholder="you@company.com"
                aria-invalid={error?.field === 'email'}
                className="mt-2 w-full rounded-2xl border border-white bg-white px-4 py-3.5 text-ink outline-none placeholder:text-muted focus:border-violet-400 focus:ring-2 focus:ring-violet-400/60"
              />
              {fieldError('email')}
            </label>
            <div>
              <label htmlFor="auth-password" className="text-sm font-medium text-ink">
                Password
              </label>
              <div className="relative mt-2">
                <input
                  id="auth-password"
                  type={show ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                  minLength={mode === 'signup' ? 10 : undefined}
                  aria-invalid={error?.field === 'password'}
                  aria-describedby={mode === 'signup' ? 'auth-password-hint' : undefined}
                  className="w-full rounded-2xl border border-white bg-white py-3.5 pr-14 pl-4 text-ink outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-400/60"
                />
                <button
                  type="button"
                  onClick={() => setShow((v) => !v)}
                  className="absolute top-1/2 right-1 grid size-11 -translate-y-1/2 place-items-center rounded-full text-muted hover:bg-mist hover:text-ink"
                  aria-label={show ? 'Hide password' : 'Show password'}
                >
                  {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
              {mode === 'signup' && error?.field !== 'password' && (
                <span id="auth-password-hint" className="mt-1.5 block text-sm text-muted">
                  At least 10 characters. A short sentence works well.
                </span>
              )}
              {fieldError('password')}
              {mode === 'login' && (
                <Link href="/forgot-password" className="mt-1 inline-flex min-h-11 items-center text-sm font-medium text-violet-700 hover:underline">
                  Forgot your password?
                </Link>
              )}
            </div>
            {error && !error.field && <Notice tone="error">{error.message}</Notice>}
          </div>

          <div className="mt-auto pt-10">
            <Button
              tone="violet"
              type="submit"
              disabled={busy || !email || !password || (mode === 'signup' && !name.trim())}
              className="w-full py-4"
            >
              {busy ? (mode === 'signup' ? 'Creating your account…' : 'Logging in…') : mode === 'signup' ? 'Create account' : 'Log in'}
            </Button>
            <p className="mt-4 text-center text-sm text-muted">
              {mode === 'signup' ? 'Already have an account? ' : 'New here? '}
              <Link href={otherHref} className="font-medium text-violet-700 hover:underline">
                {mode === 'signup' ? 'Log in' : 'Create an account'}
              </Link>
            </p>
          </div>
        </motion.form>
      </main>
    </div>
  )
}
