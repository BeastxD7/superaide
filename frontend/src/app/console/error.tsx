'use client'

import { useEffect } from 'react'

// A crash inside the operator console. It has its own look (console.css), so it does not use the site's.
export default function ConsoleError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error)
  }, [error])
  return (
    <main className="connect">
      <div role="alert">
        <h1>The console hit a problem</h1>
        <p className="quiet">
          Nothing was changed. Try again. If it keeps happening, check that the API is running (start.command or
          `make api`), then reload this page.
        </p>
        <button className="primary" type="button" onClick={() => retry()}>
          Try again
        </button>
      </div>
    </main>
  )
}
