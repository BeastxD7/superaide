'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { call, type GithubStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// The GitHub capability: paste an access token, choose the repositories Ava may see, and say whether she may draft issues and comments.
// The token goes to the server once, is checked with GitHub and sealed in the vault; only its hint comes back.
export function GithubPanel({ name }: { name: string }) {
  const [status, setStatus] = useState<GithubStatus | null>(null)
  const [token, setToken] = useState('')
  const [repos, setRepos] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<GithubStatus>('/github')
      .then((s) => {
        if (!live) return
        setStatus(s)
        setRepos(s.repos.join('\n'))
      })
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function act(run: () => Promise<GithubStatus>, done: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      const next = await run()
      setStatus(next)
      setRepos(next.repos.join('\n'))
      setSaved(done)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  return (
    <div id="github" className="mt-3 space-y-4 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="GitHub connection">
      {problem && <Notice tone="error">{problem}</Notice>}
      {saved && <Notice tone="info">{saved}</Notice>}
      {!status.connected ? (
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () => {
              const next = await call<GithubStatus>('/github/token', { method: 'POST', json: { token } })
              setToken('')
              return next
            }, 'GitHub is connected. Now choose the repositories below.')
          }}
        >
          <Label htmlFor="github-token">GitHub access token</Label>
          <p className="text-body">
            Create a fine-grained token on GitHub limited to the repositories you want {name} to see, with read access to issues and pull requests (and write access to issues only if you will turn drafts on). It is stored encrypted and never shown again.
          </p>
          <Input id="github-token" type="password" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)} className="min-h-11" />
          <div>
            <Button size="sm" type="submit" disabled={busy || !token.trim()}>
              Connect GitHub
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-body">
              Connected as <strong>{status.login}</strong> (token {status.hint}).
            </p>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => call<GithubStatus>('/github/token', { method: 'DELETE' }), 'GitHub is disconnected.')}>
              Disconnect
            </Button>
          </div>
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              const list = repos.split(/[\s,]+/).filter(Boolean)
              void act(() => call<GithubStatus>('/github/repos', { method: 'PUT', json: { repos: list } }), 'Repositories saved.')
            }}
          >
            <Label htmlFor="github-repos">Repositories {name} may see</Label>
            <p className="text-body">One per line, as owner/name. {name} sees nothing outside this list.</p>
            <Textarea id="github-repos" rows={4} value={repos} onChange={(e) => setRepos(e.target.value)} placeholder="acme/billing" />
            <div>
              <Button size="sm" type="submit" disabled={busy}>
                Save repositories
              </Button>
            </div>
          </form>
          <label className="flex min-h-11 items-center gap-2 text-body">
            <input
              type="checkbox"
              role="switch"
              className="size-5 accent-violet-600"
              checked={status.writes}
              disabled={busy}
              onChange={(e) =>
                void act(
                  () => call<GithubStatus>('/github/writes', { method: 'POST', json: { enabled: e.target.checked } }),
                  e.target.checked ? 'Drafts are on: every issue or comment waits for your OK.' : 'Drafts are off.',
                )
              }
            />
            Let {name} propose issues and comments (each waits for your OK)
          </label>
        </>
      )}
    </div>
  )
}
