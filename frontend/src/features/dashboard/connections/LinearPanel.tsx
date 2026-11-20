'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { call, type LinearStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// The Linear capability: paste an API key, choose the teams Ava may see, and say whether she may draft issues and comments.
// The token goes to the server once, is checked with Linear and sealed in the vault; only its hint comes back.
export function LinearPanel({ name }: { name: string }) {
  const [status, setStatus] = useState<LinearStatus | null>(null)
  const [token, setToken] = useState('')
  const [teams, setTeams] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<LinearStatus>('/linear')
      .then((s) => {
        if (!live) return
        setStatus(s)
        setTeams(s.teams.join('\n'))
      })
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function act(run: () => Promise<LinearStatus>, done: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      const next = await run()
      setStatus(next)
      setTeams(next.teams.join('\n'))
      setSaved(done)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  return (
    <div id="linear" className="mt-3 space-y-4 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="Linear connection">
      {problem && <Notice tone="error">{problem}</Notice>}
      {saved && <Notice tone="info">{saved}</Notice>}
      {!status.connected ? (
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () => {
              const next = await call<LinearStatus>('/linear/token', { method: 'POST', json: { token } })
              setToken('')
              return next
            }, 'Linear is connected. Now choose the teams below.')
          }}
        >
          <Label htmlFor="linear-token">Linear API key</Label>
          <p className="text-body">
            In Linear, create a personal API key (Settings, Security and access). It can do whatever you can, so {name} is limited by the teams you choose below and by your approval of every draft. Paste it here. It is stored encrypted and never shown again.
          </p>
          <Input id="linear-token" type="password" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)} className="min-h-11" />
          <div>
            <Button size="sm" type="submit" disabled={busy || !token.trim()}>
              Connect Linear
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-body">
              Connected as <strong>{status.login}</strong> (token {status.hint}).
            </p>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => call<LinearStatus>('/linear/token', { method: 'DELETE' }), 'Linear is disconnected.')}>
              Disconnect
            </Button>
          </div>
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              const list = teams.split(/[\s,]+/).filter(Boolean)
              void act(() => call<LinearStatus>('/linear/teams', { method: 'PUT', json: { teams: list } }), 'Teams saved.')
            }}
          >
            <Label htmlFor="linear-teams">Teams {name} may see</Label>
            <p className="text-body">One team key per line, such as ENG. {name} sees issues in these teams, nothing else.</p>
            <Textarea id="linear-teams" rows={4} value={teams} onChange={(e) => setTeams(e.target.value)} placeholder="ENG" />
            <div>
              <Button size="sm" type="submit" disabled={busy}>
                Save teams
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
                  () => call<LinearStatus>('/linear/writes', { method: 'POST', json: { enabled: e.target.checked } }),
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
