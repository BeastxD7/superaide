import type { Metadata } from 'next'
import { WorkspaceScreen } from '@/app/(site)/screens'

export const metadata: Metadata = { title: 'Workspace', robots: { index: false } }

export default function WorkspacePage() {
  return <WorkspaceScreen />
}
