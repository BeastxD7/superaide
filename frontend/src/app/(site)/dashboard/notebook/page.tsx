import type { Metadata } from 'next'
import { NotebookPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Notebook' }

export default function Page() {
  return <NotebookPage />
}
