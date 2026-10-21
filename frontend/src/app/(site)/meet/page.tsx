import type { Metadata } from 'next'
import { MeetScreen } from '@/app/(site)/screens'

export const metadata: Metadata = { title: 'Getting to know you', robots: { index: false } }

export default function MeetPage() {
  return <MeetScreen />
}
