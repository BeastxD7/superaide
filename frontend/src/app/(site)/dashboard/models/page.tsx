import type { Metadata } from 'next'
import { ModelsPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'AI model' }

export default function Page() {
  return <ModelsPage />
}
