import type { Metadata } from 'next'
import { ChatPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Chat' }

export default function Page() {
  return <ChatPage />
}
