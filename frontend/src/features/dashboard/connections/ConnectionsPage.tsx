'use client'

import { ConnectorSettings } from '@/features/dashboard/connections/ConnectorSettings'
import { HowThisWorks } from '@/features/dashboard/settings/HowThisWorks'
import { Connections } from '@/features/workspace/Connections'
import { useSession } from '@/lib/session-context'

export function ConnectionsPage() {
  const session = useSession()
  const me = session.me
  if (!me) return null
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Connections</h1>
        <p className="mt-1 text-body">
          Give {me.agent.name} a way to reach people. Her AI model is chosen on the AI model page. Keys are checked with each service, stored encrypted, and
          never shown again in full.
        </p>
        <div className="mt-6">
          <Connections agentName={me.agent.name} me={me} refreshMe={session.refresh} />
        </div>
        <div className="mt-6">
          <ConnectorSettings path="channels" title="Reading and keeping" anchor="channels" />
        </div>
        <div className="mt-6">
          <HowThisWorks
            rules={[
              'A message from someone you have not added is never treated as you: it cannot give Ava orders or see your private notes.',
              'A reply is cut to the platform limit (Telegram 4,096 characters, Slack 3,900), after Ava has written it.',
              'Keys and tokens are checked with each service, stored encrypted, and never shown again in full.',
            ]}
          />
        </div>
      </div>
    </div>
  )
}
