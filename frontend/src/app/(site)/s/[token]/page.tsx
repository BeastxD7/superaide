import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { BrandMark } from '@/components/site'
import { sharedBrain } from '@/lib/server'

type Props = { params: Promise<{ token: string }> }

// A picture of the shape of someone's assistant's memory, shared by its owner by an unlisted link that expires.
// Counts only: no names and no notes. An unknown, expired or revoked link (and the feature switched off) all
// get the site's ordinary "There's no page here", so a link can't be probed.
export const metadata: Metadata = {
  title: 'The shape of an assistant’s memory',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
}

// The fixed topic words (the dashboard's labels live in a client module, which a server page can't read).
const CATEGORY_LABEL: Record<string, string> = {
  about: 'About the owner',
  work: 'Work',
  person: 'People',
  preference: 'Preferences',
  urgency: 'Urgent things',
  availability: 'Availability',
  sharing: 'Sharing rules',
  other: 'Other notes',
}
const ORIGIN_LABEL: Record<string, string> = { you: 'Told by the owner', file: 'From a file or link', channel: 'From a channel', imported: 'Imported' }

export default async function SharedBrainPage({ params }: Props) {
  const { token } = await params
  const shared = await sharedBrain(token)
  if (!shared) notFound()
  const until = shared.expires_at
    ? new Date(shared.expires_at).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
    : null
  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col gap-6 px-4 py-12">
      <BrandMark />
      <h1 className="text-2xl font-semibold text-ink">The shape of an assistant’s memory</h1>
      <p className="text-body">
        Its owner shared this picture of how much an AI assistant knows and what kind of things. It shows counts only: no names, no notes,
        no file names.
      </p>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/api/p/brain/${encodeURIComponent(token)}/picture.svg`}
        alt={`Shape of the memory: ${shared.things} things, grouped by topic`}
        className="w-full rounded-xl border border-line bg-white"
      />
      <section aria-labelledby="by-topic">
        <h2 id="by-topic" className="text-sm font-medium text-ink">
          {shared.things} things, by topic
        </h2>
        <ul className="mt-1 list-disc pl-5 text-body">
          {shared.topics.map((t) => (
            <li key={t.topic}>
              {CATEGORY_LABEL[t.topic] ?? t.topic}: {t.count}
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="by-origin">
        <h2 id="by-origin" className="text-sm font-medium text-ink">
          Where it came from
        </h2>
        <ul className="mt-1 list-disc pl-5 text-body">
          {Object.entries(shared.origins)
            .filter(([, n]) => n > 0)
            .map(([k, n]) => (
              <li key={k}>
                {ORIGIN_LABEL[k] ?? k}: {n}
              </li>
            ))}
        </ul>
      </section>
      {until && <p className="text-sm text-muted">This link stops working on {until}, or sooner if its owner turns it off.</p>}
    </main>
  )
}
