import type { Overview as OverviewData } from '../api'
import { GrowthTrack } from '../components/GrowthTrack'
import { usd } from '../format'

const STAGE_LINE: Record<string, string> = {
  Infant: 'It watches, asks and drafts. Everything it does still needs your approval.',
  Child: 'It drafts and suggests on its own. Every post still needs your approval.',
  Adolescent: 'It publishes inside agreed themes, with a window for you to stop it.',
  Adult: 'It works inside its charter and helps train the next agent.',
}

export function Overview({ data }: { data: OverviewData }) {
  const { spend, safety, counts } = data
  const usedShare = Math.min(1, (spend.settled_usd + spend.reserved_usd) / spend.cap_usd)

  const real = data.model_mode.startsWith('Claude')
  const status = [
    {
      name: 'Brain',
      ok: real,
      on: 'Real Claude replies, inside the spend cap.',
      off: 'Offline: replies are echoes. Put your key in ANTHROPIC_API_KEY in .env and restart.',
    },
    {
      name: 'Telegram',
      ok: data.channels.telegram,
      on: 'Connected. Message your bot from your phone.',
      off: 'Not connected. Create a bot with @BotFather, put its token in TELEGRAM_BOT_TOKEN in .env, restart.',
    },
    {
      name: 'Slack',
      ok: data.channels.slack,
      on: 'Connected over Socket Mode.',
      off: 'Not connected (optional). Create the app from slack-manifest.yml and add its tokens to .env.',
    },
  ]

  return (
    <div className="overview">
      <section className="live" aria-labelledby="live-title">
        <h2 id="live-title">What is live right now</h2>
        <ul>
          {status.map((s) => (
            <li key={s.name} className={s.ok ? 'live-row ok' : 'live-row off'}>
              <span className="live-dot" aria-hidden="true" />
              <span className="live-name">{s.name}</span>
              <span className="live-text">{s.ok ? s.on : s.off}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="hero" aria-labelledby="stage-title">
        <h1 id="stage-title">
          {data.agent_name} is at the <span className="stage-word">{data.stage}</span> stage
        </h1>
        <p className="lede">{STAGE_LINE[data.stage] ?? ''}</p>
        <div className="growth-frame">
          <GrowthTrack stage={data.stage} gates={data.gates} />
        </div>
        <div className="gates">
          <h2>Gates to {data.next_stage}</h2>
          <ul>
            {data.gates.map((g) => (
              <li key={g.label} className={`gate gate-${g.status}`}>
                <span className="gate-mark" aria-hidden="true" />
                <span className="gate-label">{g.label}</span>
                <span className="gate-detail">{g.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="spend-block" aria-labelledby="spend-title">
        <div className="spend-head">
          <h2 id="spend-title">Claude API spend</h2>
          <p>
            <strong className="num">{usd(spend.settled_usd)}</strong> of {usd(spend.cap_usd, 2)} cap, {spend.calls}{' '}
            {spend.calls === 1 ? 'call' : 'calls'}. The cap is enforced before every call.
          </p>
        </div>
        <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={spend.cap_usd} aria-valuenow={spend.settled_usd}>
          <div className="meter-fill" style={{ width: `${Math.max(usedShare * 100, usedShare > 0 ? 0.6 : 0)}%` }} />
        </div>
        <p className="quiet">
          {usd(spend.remaining_usd)} left. Model: {data.model_mode}.
        </p>
      </section>

      <div className="facts">
        <section aria-labelledby="checks-title">
          <h2 id="checks-title">Identity checks</h2>
          <dl>
            <div>
              <dt>Messages from unlinked accounts</dt>
              <dd className="num">{safety.stranger_messages}</dd>
            </div>
            <div>
              <dt>Codes sent to a trusted channel</dt>
              <dd className="num">{safety.link_codes_sent}</dd>
            </div>
            <div>
              <dt>Channels linked with a code</dt>
              <dd className="num">{safety.links_made}</dd>
            </div>
            <div className={safety.refused_actions ? 'flag' : ''}>
              <dt>Requests refused</dt>
              <dd className="num">{safety.refused_actions}</dd>
            </div>
            <div className={safety.rejected_webhooks ? 'flag' : ''}>
              <dt>Unverified messages dropped</dt>
              <dd className="num">{safety.rejected_webhooks}</dd>
            </div>
          </dl>
        </section>
        <section aria-labelledby="knows-title">
          <h2 id="knows-title">What {data.agent_name} knows</h2>
          <dl>
            <div>
              <dt>People</dt>
              <dd className="num">{counts.people}</dd>
            </div>
            <div>
              <dt>Linked channels</dt>
              <dd className="num">
                {counts.linked_identities} of {counts.identities}
              </dd>
            </div>
            <div>
              <dt>Conversations</dt>
              <dd className="num">{counts.conversations}</dd>
            </div>
            <div>
              <dt>Messages in the last 24 hours</dt>
              <dd className="num">{counts.messages_24h}</dd>
            </div>
            <div>
              <dt>Private memories</dt>
              <dd className="num">{counts.memories}</dd>
            </div>
            <div className={counts.pending_approvals ? 'flag' : ''}>
              <dt>Waiting for your approval</dt>
              <dd className="num">{counts.pending_approvals}</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  )
}
