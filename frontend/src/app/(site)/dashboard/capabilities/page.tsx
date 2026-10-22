import type { Metadata } from 'next'
import { SkillsPage } from '@/app/(site)/dashboard/pages'

// "Capabilities" in the app; the code, API and manifest keep the Agent Skills name ("skill").
export const metadata: Metadata = { title: 'Capabilities' }

export default function Page() {
  return <SkillsPage />
}
