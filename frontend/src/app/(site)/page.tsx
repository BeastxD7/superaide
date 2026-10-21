import { MarketplaceScreen } from '@/app/(site)/screens'
import { agentCatalog } from '@/lib/server'

export default async function HomePage() {
  return <MarketplaceScreen initialAgents={await agentCatalog()} />
}
