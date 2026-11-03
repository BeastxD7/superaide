'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { SettingRow } from '@/features/dashboard/settings/SettingRow'
import { SettingsSection } from '@/features/dashboard/settings/SettingsSection'
import { HowThisWorks } from '@/features/dashboard/settings/HowThisWorks'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type Channel = {
  channel_id: string
  title: string
  private: boolean
  enabled: boolean
  retention_days: number
  enabled_at: string | null
  last_heard_at: string | null
  paused: boolean
  stored: number
}
type View = {
  channels: Channel[]
  listing_error: string
  missing_scopes: string[]
  retention_choices: number[]
  default_retention: number
  any_enabled: boolean
}

const RULES = [
  'It is off for every channel until you turn it on, one channel at a time. Ava must already be in the channel (invite her with /invite).',
  'When it is on, Ava stores each message of that channel in her log with the audience of the room: everyone who can read the channel. It never reaches a conversation with anyone else.',
  'Nothing happens when a message arrives: no reply, no model call, no memory, no reminder, no action. Words in a channel are never instructions to her.',
  'You read it back in your direct message ("what did people say in the channels?"). Ava quotes it as other people’s words, and a turn that used it cannot propose an action.',
  'Stored messages are deleted after the number of days you choose, and you can delete them all at any time. Messages that mention Ava are conversations and are not touched.',
]

function label(c: Channel) {
  return c.title.startsWith('#') || c.title === c.channel_id ? c.title : `#${c.title}`
}

// A native <dialog>, like the other confirmations: Cancel has the focus, the safe choice.
function Confirm({
  title,
  children,
  confirmLabel,
  needsUnderstood,
  extraAction,
  onConfirm,
  onCancel,
}: {
  title: string
  children: React.ReactNode
  confirmLabel: string
  needsUnderstood: boolean
  extraAction?: { label: string; run: () => void }
  onConfirm: () => void
  onCancel: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const [understood, setUnderstood] = useState(!needsUnderstood)
  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
    d?.querySelector<HTMLButtonElement>('[data-cancel]')?.focus()
    return () => d?.close()
  }, [])
  return (
    <dialog
      ref={ref}
      aria-labelledby="ambient-confirm-title"
      className="m-auto w-[min(92vw,30rem)] rounded-xl border border-amber-300 bg-white p-4 text-sm text-ink shadow-xl backdrop:bg-black/40"
      onCancel={(e) => {
        e.preventDefault()
        onCancel()
      }}
    >
      <h2 id="ambient-confirm-title" className="text-base font-semibold">
        {title}
      </h2>
      <div className="mt-2 space-y-2 text-body">{children}</div>
      {needsUnderstood && (
        <label className="mt-3 flex items-start gap-2">
          <input type="checkbox" className="mt-0.5 size-5 accent-violet-600" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
          <span>I understand</span>
        </label>
      )}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button data-cancel variant="outline" className="min-h-11" onClick={onCancel}>
          Cancel
        </Button>
        {extraAction && (
          <Button variant="outline" className="min-h-11" onClick={extraAction.run}>
            {extraAction.label}
          </Button>
        )}
        <Button className="min-h-11" disabled={!understood} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  )
}

// The per-channel switch for reading a whole channel (#509): off by default, a confirmation to turn it on, and a way
// to stop and to delete what was stored. The rules are enforced in code; this page only asks.
export function AmbientChannels({ agentName }: { agentName: string }) {
  const [view, setView] = useState<View | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [turningOn, setTurningOn] = useState<Channel | null>(null)
  const [turningOff, setTurningOff] = useState<Channel | null>(null)

  const load = useCallback(async () => {
    try {
      setView(await call<View>('/ambient/channels'))
    } catch (e) {
      setProblem(errorMessage(e))
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  async function change(c: Channel, json: Record<string, unknown>, done: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      await call('/ambient/channels', { method: 'PUT', json: { channel_id: c.channel_id, title: label(c).replace(/^#/, ''), ...json } })
      await load()
      setSaved(done)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
      setTurningOn(null)
      setTurningOff(null)
    }
  }

  async function erase(c: Channel) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      await call(`/ambient/channels/${encodeURIComponent(c.channel_id)}/messages`, { method: 'DELETE' })
      await load()
      setSaved(`Deleted what was stored from ${label(c)}.`)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!view) return problem ? <Notice tone="error">{problem}</Notice> : null
  const listing =
    view.listing_error === 'not_connected'
      ? 'Connect Slack first (above), then the channels Ava is in are listed here.'
      : view.listing_error === 'missing_scope'
        ? 'Slack would not list the channels. Add the channels:read and groups:read permissions to the Slack app, reinstall it and paste the new token.'
        : view.listing_error
          ? 'Slack could not be asked for its channels just now. Channels Ava already knows are listed.'
          : ''

  return (
    <section id="ambient" className="min-w-0" role="group" aria-label="Reading whole channels">
      {problem && (
        <div className="mb-3">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}
      {saved && (
        <p role="status" className="mb-3 text-xs text-emerald-700">
          {saved}
        </p>
      )}
      {view.missing_scopes.length > 0 && (
        <div className="mb-3" data-testid="ambient-missing-scopes">
          <Notice tone="warn">
            The Slack app is missing permissions for this: {view.missing_scopes.join(', ')}. Add them under OAuth &amp; Permissions, reinstall the app and paste the new
            token on this page.
          </Notice>
        </div>
      )}
      {listing && (
        <div className="mb-3">
          <Notice tone="info">{listing}</Notice>
        </div>
      )}
      <SettingsSection
        title="Reading whole channels"
        description={`By default ${agentName} reads a channel message only when it mentions her. Turn this on for a channel to let her store everything said in it. Off for every channel.`}
      >
        {view.channels.length === 0 && (
          <li className="px-4 py-4 text-sm text-body" data-testid="ambient-empty">
            No Slack channels yet. Invite {agentName} to a channel in Slack and it will show up here.
          </li>
        )}
        {view.channels.map((c) => (
          <SettingRow
            key={c.channel_id}
            title={label(c)}
            why={
              c.enabled
                ? `Reading the whole channel${c.enabled_at ? ` since ${new Date(c.enabled_at).toLocaleDateString()}` : ''}. ${c.stored} message${c.stored === 1 ? '' : 's'} stored${c.paused ? '; paused until the hour is over because the channel is very busy' : ''}.`
                : c.stored > 0
                  ? `Off. ${c.stored} earlier message${c.stored === 1 ? '' : 's'} still stored until they age out or you delete them.`
                  : `Off. ${agentName} reads only the messages that mention her.`
            }
            status={c.enabled ? 'On' : 'Off (default)'}
            extra={
              c.enabled || c.stored > 0 ? (
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  {c.enabled && (
                    <label className="flex items-center gap-2 text-body">
                      Keep for
                      <select
                        aria-label={`Keep messages from ${label(c)} for`}
                        className="min-h-11 rounded-md border border-line bg-white px-2 py-1.5 text-base md:text-sm"
                        value={c.retention_days}
                        disabled={busy}
                        onChange={(e) => void change(c, { enabled: true, confirmed: true, retention_days: Number(e.target.value) }, 'Retention saved.')}
                      >
                        {view.retention_choices.map((d) => (
                          <option key={d} value={d}>
                            {d} days{d === view.default_retention ? ' (default)' : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {c.stored > 0 && (
                    <Button variant="outline" className="min-h-11" disabled={busy} onClick={() => void erase(c)}>
                      Delete the {c.stored} stored message{c.stored === 1 ? '' : 's'}
                    </Button>
                  )}
                </div>
              ) : undefined
            }
            control={({ id, labelId, describedBy }) => (
              <button
                id={id}
                type="button"
                role="switch"
                aria-checked={c.enabled}
                aria-labelledby={labelId}
                aria-describedby={describedBy}
                disabled={busy}
                onClick={() => (c.enabled ? (c.stored > 0 ? setTurningOff(c) : void change(c, { enabled: false }, `Stopped reading ${label(c)}.`)) : setTurningOn(c))}
                className={`relative inline-flex h-11 w-16 shrink-0 items-center rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-600 ${c.enabled ? 'border-violet-600 bg-violet-600' : 'border-line bg-mist'}`}
              >
                <span className={`inline-block size-8 rounded-full bg-white shadow transition-transform ${c.enabled ? 'translate-x-[1.9rem]' : 'translate-x-1'}`} />
              </button>
            )}
          />
        ))}
      </SettingsSection>
      <HowThisWorks title="How reading whole channels works" rules={RULES} />
      {turningOn && (
        <Confirm
          title={`Let ${agentName} read all of ${label(turningOn)}?`}
          confirmLabel={`Turn on for ${label(turningOn)}`}
          needsUnderstood
          onConfirm={() => void change(turningOn, { enabled: true, confirmed: true }, `${agentName} now reads ${label(turningOn)}.`)}
          onCancel={() => setTurningOn(null)}
        >
          <p>
            {agentName} will store every message people write in {label(turningOn)}, not only the ones that mention her. She will not answer them, and they
            are kept for {view.default_retention} days unless you choose otherwise. Anyone in the channel can have their words stored.
          </p>
          <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900">
            Example: a teammate writes &quot;the new price is 40&quot; in {label(turningOn)}. {agentName} stores it, and you can ask her later, in a private message,
            what was said there.
          </p>
        </Confirm>
      )}
      {turningOff && (
        <Confirm
          title={`Stop reading ${label(turningOff)}?`}
          confirmLabel="Stop and keep what is stored"
          needsUnderstood={false}
          extraAction={{
            label: `Stop and delete ${turningOff.stored}`,
            run: () => void change(turningOff, { enabled: false, erase: true }, `Stopped reading ${label(turningOff)} and deleted what was stored.`),
          }}
          onConfirm={() => void change(turningOff, { enabled: false }, `Stopped reading ${label(turningOff)}.`)}
          onCancel={() => setTurningOff(null)}
        >
          <p>
            {turningOff.stored} message{turningOff.stored === 1 ? ' is' : 's are'} stored from this channel. You can keep them until they age out, or delete them now.
          </p>
        </Confirm>
      )}
    </section>
  )
}
