'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { call, type NotionStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// The Notion capability: paste an integration secret, choose the pages Ava may see, and say whether she may draft pages and comments.
// The token goes to the server once, is checked with Notion and sealed in the vault; only its hint comes back.
export function NotionPanel({ name }: { name: string }) {
  const [status, setStatus] = useState<NotionStatus | null>(null)
  const [token, setToken] = useState('')
  const [pages, setPages] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<NotionStatus>('/notion')
      .then((s) => {
        if (!live) return
        setStatus(s)
        setPages(s.pages.join('\n'))
      })
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function act(run: () => Promise<NotionStatus>, done: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      const next = await run()
      setStatus(next)
      setPages(next.pages.join('\n'))
      setSaved(done)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  return (
    <div id="notion" className="mt-3 space-y-4 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="Notion connection">
      {problem && <Notice tone="error">{problem}</Notice>}
      {saved && <Notice tone="info">{saved}</Notice>}
      {!status.connected ? (
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () => {
              const next = await call<NotionStatus>('/notion/token', { method: 'POST', json: { token } })
              setToken('')
              return next
            }, 'Notion is connected. Now choose the pages below.')
          }}
        >
          <Label htmlFor="notion-token">Notion integration secret</Label>
          <p className="text-body">
            In Notion, create an internal integration (Settings, Connections, Develop or manage integrations), then share only the pages you want {name} to see with it (Insert content and Comment access only if you will turn drafts on). Paste its secret here. It is stored encrypted and never shown again.
          </p>
          <Input id="notion-token" type="password" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)} className="min-h-11" />
          <div>
            <Button size="sm" type="submit" disabled={busy || !token.trim()}>
              Connect Notion
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-body">
              Connected as <strong>{status.login}</strong> (token {status.hint}).
            </p>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => call<NotionStatus>('/notion/token', { method: 'DELETE' }), 'Notion is disconnected.')}>
              Disconnect
            </Button>
          </div>
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              const list = pages.split(/[\s,]+/).filter(Boolean)
              void act(() => call<NotionStatus>('/notion/pages', { method: 'PUT', json: { pages: list } }), 'Pages saved.')
            }}
          >
            <Label htmlFor="notion-pages">Pages {name} may see</Label>
            <p className="text-body">One link or id per line. {name} sees these pages and the pages beneath them, nothing else.</p>
            <Textarea id="notion-pages" rows={4} value={pages} onChange={(e) => setPages(e.target.value)} placeholder="https://www.notion.so/Team-wiki-0a1b2c3d4e5f60718293a4b5c6d7e8f9" />
            <div>
              <Button size="sm" type="submit" disabled={busy}>
                Save pagesitories
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
                  () => call<NotionStatus>('/notion/writes', { method: 'POST', json: { enabled: e.target.checked } }),
                  e.target.checked ? 'Drafts are on: every page or comment waits for your OK.' : 'Drafts are off.',
                )
              }
            />
            Let {name} propose pages and comments (each waits for your OK)
          </label>
        </>
      )}
    </div>
  )
}
