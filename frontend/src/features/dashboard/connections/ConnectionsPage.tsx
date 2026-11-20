'use client'

import { PageContainer, PageHeader } from '@/components/layout'
import { AmbientChannels } from '@/features/dashboard/connections/AmbientChannels'
import { SetupCheckCard } from '@/features/dashboard/connections/SetupCheckCard'
import { ConnectorSettings } from '@/features/dashboard/connections/ConnectorSettings'
import { HowThisWorks } from '@/features/dashboard/settings/HowThisWorks'
import { Connections } from '@/features/workspace/Connections'
import { useSession } from '@/lib/session-context'

export function ConnectionsPage() {
  const session = useSession()
  const me = session.me
  if (!me) return null
  return (
    <PageContainer width="list">
        <PageHeader
          title="Connections"
          description={`Give ${me.agent.name} a way to reach people. Her AI model is chosen on the AI model page. Keys are checked with each service, stored encrypted, and never shown again in full.`}
        />
        <div className="mt-6 grid items-start gap-6 @4xl/page:grid-cols-2">
        <div className="min-w-0 space-y-6">
        <div data-tour="connections-list">
          <Connections agentName={me.agent.name} me={me} refreshMe={session.refresh} />
        </div>
        <div>
          <ConnectorSettings path="channels" title="Reading and keeping" anchor="channels" />
        </div>
        </div>
        <div className="min-w-0 space-y-6">
        <div>
          <AmbientChannels agentName={me.agent.name} />
        </div>
        <div>
          <SetupCheckCard />
        </div>
        <div>
          <HowThisWorks
            rules={[
              'A message from someone you have not added is never treated as you: it cannot give Ava orders or see your private notes.',
              'A reply is cut to the platform limit (Telegram 4,096 characters, Slack 3,900), after Ava has written it.',
              'Keys and tokens are checked with each service, stored encrypted, and never shown again in full.',
              'In a channel or thread other people can read, Ava answers you only from what is fine for everyone there: your email, calendar, reminders and private notes are never opened in it.',
              'If you ask for something private in a channel, she says so in one neutral line and sends the answer to your direct message with her.',
              'Other people in a thread have none of your authority: what they ask, or claim you said, is never an instruction from you.',
              'To see who pinged her in channels, ask in your direct message. Unless you turned on reading a whole channel (above), she sees only messages that mention her or reply in her threads, not the rest of a channel.',
              'Messages she stores from a channel you opened never trigger anything: no reply, no model call, no memory, no action.',
            ]}
          />
        </div>
        </div>
        </div>
    </PageContainer>
  )
}
