import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { BrandMark } from '@/components/site'
import { principalCard } from '@/lib/server'

type Props = { params: Promise<{ token: string }> }

// The card an AI assistant's message links to. Public and signed: a forged, expired or revoked card (and the
// feature switched off) all get the site's ordinary "There's no page here". It shows nothing private.
export const metadata: Metadata = {
  title: 'An AI assistant wrote to you',
  robots: { index: false, follow: false },
}

export default async function CardPage({ params }: Props) {
  const { token } = await params
  const card = await principalCard(token)
  if (!card) notFound()
  const until = new Date(card.valid_until * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col gap-6 px-4 py-12">
      <BrandMark />
      <h1 className="text-2xl font-semibold text-ink">{card.agent} is an AI assistant</h1>
      <p className="text-body">{card.notice}</p>
      <section aria-labelledby="can">
        <h2 id="can" className="text-sm font-medium text-ink">She can</h2>
        <ul className="mt-1 list-disc pl-5 text-body">
          {card.can.map((c) => <li key={c}>{c}</li>)}
        </ul>
      </section>
      <section aria-labelledby="cannot">
        <h2 id="cannot" className="text-sm font-medium text-ink">She cannot</h2>
        <ul className="mt-1 list-disc pl-5 text-body">
          {card.cannot.map((c) => <li key={c}>{c}</li>)}
        </ul>
      </section>
      <p className="text-sm text-muted">This card is signed and valid until {until}. If something looks wrong, reply to the message and ask for a person.</p>
    </main>
  )
}
