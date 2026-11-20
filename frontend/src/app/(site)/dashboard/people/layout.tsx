import type { ReactNode } from 'react'
import { PeopleLayout } from '@/app/(site)/dashboard/pages'

export default function Layout({ children }: { children: ReactNode }) {
  return <PeopleLayout>{children}</PeopleLayout>
}
