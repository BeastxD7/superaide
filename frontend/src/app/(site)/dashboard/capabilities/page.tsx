import type { Metadata } from 'next'
import { SkillsPage } from '@/app/(site)/dashboard/pages'

// "Capabilities" in the app; the code, API and manifest keep the Agent Skills name ("skill").
export const metadata: Metadata = { title: 'Capabilities' }

// (Gmail's sign-in return, /dashboard/capabilities?gmail=..., is redirected permanently to the Gmail page in next.config.ts.)
export default function Page() {
  return <SkillsPage />
}
