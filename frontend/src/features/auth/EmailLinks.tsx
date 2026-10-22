'use client'

// The pages behind emailed links: confirming an address, asking for a password reset,
// and choosing a new password. The token arrives in the URL fragment (#token=…), which
// browsers never send to a server; it's read here, then cleared from the address bar.
import Link from 'next/link'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { AppError, auth } from '@/lib/api'
import { Orb } from '@/components/Orb'
import { Button, Notice, PRODUCT } from '@/components/site'

// `keepAs`: a name to remember the token under for this tab (sessionStorage), so reloading the page
// doesn't lose a link that is still good, as when a too-short password was refused. Only the reset link
// needs it; a verify link is used up the moment it is opened.
function useFragmentToken(keepAs?: string): [string | null | undefined, () => void] {
  const [token, setToken] = useState<string | null | undefined>(undefined) // undefined: not read yet
  useEffect(() => {
    const key = keepAs ? `superaide.link.${keepAs}` : null
    const found = new URLSearchParams(window.location.hash.slice(1)).get('token')
    if (found) window.history.replaceState(null, '', window.location.pathname)
    let kept: string | null = null
    try {
      if (key && found) sessionStorage.setItem(key, found)
      else if (key) kept = sessionStorage.getItem(key)
    } catch {
      // Storage can be blocked; the link then works until the page is reloaded.
    }
    setToken(found ?? kept)
  }, [keepAs])
  const forget = () => {
    try {
      if (keepAs) sessionStorage.removeItem(`superaide.link.${keepAs}`)
    } catch {
      // Nothing was kept.
    }
  }
  return [token, forget]
}

function message(err: unknown): string {
  return err instanceof AppError ? err.message : String(err)
}

function Frame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mesh flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-xl items-center justify-end px-5 pt-6">
        <Link href="/" className="text-sm font-medium text-muted hover:text-ink">
          {PRODUCT}
        </Link>
      </header>
      <main className="mx-auto flex w-full max-w-md flex-1 flex-col items-center px-5 pt-10 pb-10 text-center">
        <Orb hue="indigo" size={96} />
        <h1 className="mt-6 text-4xl font-medium tight">{title}</h1>
        <div className="mt-6 w-full space-y-4" aria-live="polite">
          {children}
        </div>
      </main>
    </div>
  )
}

const INPUT =
  'mt-2 w-full rounded-2xl border border-white bg-white px-4 py-3.5 text-ink outline-none placeholder:text-muted focus:border-violet-400'

export function VerifyEmail() {
  const [token] = useFragmentToken()
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null)
  const sent = useRef(false)

  useEffect(() => {
    if (!token || sent.current) return
    sent.current = true // once, even under React's double effects in development
    auth
      .verifyEmail(token)
      .then(() => setState({ ok: true, text: 'Your email address is confirmed. Thank you.' }))
      .catch((err) => setState({ ok: false, text: message(err) }))
  }, [token])

  return (
    <Frame title="Confirm your email">
      {token === null && <Notice tone="error">This link is missing its code. Open the link from the email again.</Notice>}
      {token && !state && <p className="text-body">Checking your link…</p>}
      {state && <Notice tone={state.ok ? 'info' : 'error'}>{state.text}</Notice>}
      {state && (
        <Link href="/" className="inline-block font-medium text-violet-700 hover:underline">
          Continue to {PRODUCT}
        </Link>
      )}
    </Frame>
  )
}

export function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setDone((await auth.requestReset(email)).status)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Frame title="Forgot your password?">
      {done ? (
        <Notice tone="info">{done}</Notice>
      ) : (
        <form onSubmit={submit} className="space-y-4 text-left" noValidate>
          <p className="text-center text-body">Enter your email and we'll send you a link to choose a new password.</p>
          <label className="block">
            <span className="text-sm font-medium text-ink">Email</span>
            <input
              autoFocus
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              placeholder="you@company.com"
              className={INPUT}
            />
          </label>
          {error && <Notice tone="error">{error}</Notice>}
          <Button tone="violet" type="submit" disabled={busy || !email} className="w-full py-4 text-base">
            {busy ? 'Sending…' : 'Email me a link'}
          </Button>
        </form>
      )}
      <Link href="/login" className="inline-block text-sm font-medium text-violet-700 hover:underline">
        Back to log in
      </Link>
    </Frame>
  )
}

export function ResetPassword() {
  const [token, forget] = useFragmentToken('reset')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!token) return
    setBusy(true)
    setError(null)
    try {
      await auth.resetPassword(token, password)
      forget() // the link is used up
      setDone(true)
    } catch (err) {
      // A refused password leaves the link good (the person can try another, even after a reload);
      // a link that is used or expired never will be.
      if (err instanceof AppError && err.status === 400) forget()
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Frame title="Choose a new password">
      {token === null && (
        <Notice tone="error">
          This link is missing its code. <Link href="/forgot-password" className="underline">Ask for a new link</Link>.
        </Notice>
      )}
      {done && (
        <>
          <Notice tone="info">Your password is changed and every device was signed out.</Notice>
          {/* A full page load, not a client-side Link: the reset ended every session, and this page may still
              hold the account the browser was signed in as. A fresh load starts from what the server says. */}
          <a href="/login" className="inline-block font-medium text-violet-700 hover:underline">
            Log in
          </a>
        </>
      )}
      {token && !done && (
        <form onSubmit={submit} className="space-y-4 text-left" noValidate>
          <label className="block">
            <span className="text-sm font-medium text-ink">New password</span>
            <input
              autoFocus
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              aria-describedby="reset-hint"
              className={INPUT}
            />
            <span id="reset-hint" className="mt-1.5 block text-sm text-muted">
              At least 10 characters. A short sentence works well.
            </span>
          </label>
          {error && <Notice tone="error">{error}</Notice>}
          <Button tone="violet" type="submit" disabled={busy || !password} className="w-full py-4 text-base">
            {busy ? 'Saving…' : 'Save new password'}
          </Button>
        </form>
      )}
    </Frame>
  )
}
