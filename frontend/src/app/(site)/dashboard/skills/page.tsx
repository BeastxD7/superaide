import type { Metadata } from 'next'
import { SkillsPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Skills' }

export default function Page() {
  return <SkillsPage />
}
