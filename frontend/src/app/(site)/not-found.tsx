import Link from 'next/link'

export default function NotFound() {
  return (
    <main className="grid min-h-dvh place-items-center bg-canvas px-6">
      <div className="max-w-md text-center">
        <h1 className="text-3xl font-medium text-ink tight">There's no page here</h1>
        <p className="mt-3 text-body">The link may be old or mistyped.</p>
        <Link href="/" className="mt-8 inline-flex rounded-full bg-ink px-5 py-3 text-sm font-medium text-white hover:bg-night-800">
          See all agents
        </Link>
      </div>
    </main>
  )
}
