import type { Metadata } from 'next'
import { McpPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Tools (MCP)' }

export default function Page() {
  return <McpPage />
}
