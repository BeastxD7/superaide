import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { AskForm } from '@/features/ask/AskForm'
import { askPage } from '@/lib/server'

type Props = { params: Promise<{ handle: string }> }

// An owner's "Ask my Ava" link. Public: no account. An unknown, unpublished or revoked handle (and the
// feature switched off) all get the site's ordinary "There's no page here", so a handle can't be probed.
export const metadata: Metadata = {
  title: 'Leave a message with Ava',
  description: 'Ava is an AI assistant. Leave a short message and a person will read it.',
  // Shared on purpose by its owner, but never something a search engine should list.
  robots: { index: false, follow: false },
}

export default async function AskPage({ params }: Props) {
  const { handle } = await params
  const page = await askPage(handle)
  if (!page) notFound()
  return <AskForm page={page} />
}
