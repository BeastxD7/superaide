'use client'

import { useEffect } from 'react'
import { Button } from '@/features/console/ui'

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
        <Button onClick={() => retry()}>Try again</Button>
      </div>
    </main>
  )
}
