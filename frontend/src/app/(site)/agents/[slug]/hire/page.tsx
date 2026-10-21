import type { Metadata } from 'next'
import { HireScreen } from '@/app/(site)/screens'

export const metadata: Metadata = { title: 'Hire', robots: { index: false } }

export default async function HirePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  return <HireScreen slug={slug} />
}
