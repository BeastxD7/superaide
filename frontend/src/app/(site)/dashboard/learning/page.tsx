import type { Metadata } from 'next'
import { LearningPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Learning' }

export default function Page() {
  return <LearningPage />
}
