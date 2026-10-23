'use client'

import type { Overview as OverviewData } from '@/features/console/api'
import { BrandIcon } from '@/components/BrandIcon'
import { GrowthTrack } from '@/features/console/components/GrowthTrack'
import { usd } from '@/features/console/format'
import { Badge, Card, evenCols, Grid, Meter, PageHeader, Stat, Stats, Tile } from '@/features/console/ui'

const STAGE_LINE: Record<string, string> = {
  Infant: 'It watches, asks and drafts. Everything it does still needs your approval.',
  Child: 'It drafts and suggests on its own. Every post still needs your approval.',
  Adolescent: 'It publishes inside agreed themes, with a window for you to stop it.',
  Adult: 'It works inside its charter and helps train the next agent.',
}

const GATE_BADGE = { met: { tone: 'ok', text: 'Met' }, unmet: { tone: 'danger', text: 'Not met' }, later: { tone: 'neutral', text: 'Later' } } as const

export function Overview({ data }: { data: OverviewData }) {
  const { spend, safety, counts } = data

  const real = data.model_mode.startsWith('Claude')
  const status = [
    {
      name: 'Brain',
      brand: 'claude',
      ok: real,
      on: 'Real Claude replies, inside the spend cap.',
      off: "Offline: replies are echoes. Connect a model in the app under AI model, or put a key in ANTHROPIC_API_KEY in .env and restart.",
    },
    {
      name: 'Telegram',
      brand: 'telegram',
      ok: data.channels.telegram,
      on: 'Connected. Message your bot from your phone.',
      off: 'Not connected. Connect Telegram in the app under Connections (or put a bot token in TELEGRAM_BOT_TOKEN in .env and restart).',
    },
    {
      name: 'Slack',
      brand: 'slack',
      ok: data.channels.slack,
      on: 'Connected over Socket Mode.',
      off: 'Not connected (optional). Connect Slack in the app under Connections.',
    },
  ] as const

  return (
    <>
      <PageHeader
        title={
          <>
            {data.agent_name} is at the <em>{data.stage}</em> stage
          </>
        }
        description={STAGE_LINE[data.stage]}
      />

      <Card title="What is live right now" titleId="live-title">
        <Grid cols={3}>
          {status.map((s) => (
            <Tile key={s.name}>
              <div className="c-tile-head">
                <span className="c-tile-name">
                  <BrandIcon brand={s.brand} size={16} decorative />
                  {s.name}
                </span>
                <Badge tone={s.ok ? 'ok' : 'warn'} dot>
                  {s.ok ? 'On' : 'Off'}
                </Badge>
              </div>
              <p className="c-tile-text">{s.ok ? s.on : s.off}</p>
            </Tile>
          ))}
        </Grid>
      </Card>

      <Card title="Growth" titleId="growth-title">
        <div className="growth-frame" tabIndex={0} role="group" aria-label="Growth track, scrolls sideways on a small screen">
          <GrowthTrack stage={data.stage} gates={data.gates} />
        </div>
        <h3 className="c-subtitle">Gates to {data.next_stage}</h3>
        <Grid cols={evenCols(data.gates.length)}>
          {data.gates.map((g) => (
            <Tile key={g.label}>
              <Badge tone={GATE_BADGE[g.status].tone} dot>
                {GATE_BADGE[g.status].text}
              </Badge>
              <p className="c-tile-name">{g.label}</p>
              <p className="c-tile-text">{g.detail}</p>
            </Tile>
          ))}
        </Grid>
      </Card>

      <Card
        title="Claude API spend"
        titleId="spend-title"
        description={
          <>
            {usd(spend.settled_usd)} of {usd(spend.cap_usd, 2)} cap, {spend.calls} {spend.calls === 1 ? 'call' : 'calls'}. The cap is enforced before every call.
          </>
        }
      >
        <Meter label="Claude API spend against the cap" value={spend.settled_usd} max={spend.cap_usd} />
        <p className="quiet">
          {usd(spend.remaining_usd)} left. Model: {data.model_mode}.
        </p>
      </Card>

      <Grid cols={2}>
        <Card title="Identity checks" titleId="checks-title">
          <Stats cols={3} label="Identity checks">
            <Stat label="Messages from unlinked accounts" value={safety.stranger_messages} />
            <Stat label="Codes sent to a trusted channel" value={safety.link_codes_sent} />
            <Stat label="Channels linked with a code" value={safety.links_made} />
            <Stat label="Requests refused" value={safety.refused_actions} tone={safety.refused_actions ? 'danger' : 'neutral'} />
            <Stat label="Unverified messages dropped" value={safety.rejected_webhooks} tone={safety.rejected_webhooks ? 'danger' : 'neutral'} />
            <Stat label="Model calls blocked" value={safety.refused_model_calls} tone={safety.refused_model_calls ? 'warn' : 'neutral'} />
          </Stats>
        </Card>
        <Card title={`What ${data.agent_name} knows`} titleId="knows-title">
          <Stats cols={3} label={`What ${data.agent_name} knows`}>
            <Stat label="People" value={counts.people} />
            <Stat label="Linked channels" value={`${counts.linked_identities} of ${counts.identities}`} />
            <Stat label="Conversations" value={counts.conversations} />
            <Stat label="Messages in the last 24 hours" value={counts.messages_24h} />
            <Stat label="Private memories" value={counts.memories} />
            <Stat label="Waiting for your approval" value={counts.pending_approvals} tone={counts.pending_approvals ? 'warn' : 'neutral'} />
          </Stats>
        </Card>
      </Grid>
    </>
  )
}
