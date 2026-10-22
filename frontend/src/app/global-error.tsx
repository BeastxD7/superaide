'use client'

import { useEffect } from 'react'

// The last resort: the page's outer layout itself failed. This replaces the whole document, so it
// carries its own <html>, <body> and styles (no stylesheet from the app is available here).
export default function GlobalError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error)
  }, [error])
  return (
    <html lang="en">
      <head>
        <title>Something went wrong | Superaide</title>
      </head>
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif', color: '#1a1a2e', background: '#fff' }}>
        <main
          style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: '0 1.5rem', textAlign: 'center' }}
          role="alert"
        >
          <div style={{ maxWidth: '28rem' }}>
            <h1 style={{ fontSize: '1.75rem', fontWeight: 500 }}>Superaide hit a problem</h1>
            <p style={{ marginTop: '0.75rem', lineHeight: 1.5 }}>
              Nothing you saved has been lost. Try again. If it keeps happening, reload the page or come back in a
              few minutes.
            </p>
            <button
              type="button"
              onClick={() => retry()}
              style={{
                marginTop: '2rem',
                padding: '0.75rem 1.25rem',
                borderRadius: '999px',
                border: 0,
                background: '#1a1a2e',
                color: '#fff',
                fontSize: '0.875rem',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Try again
            </button>
          </div>
        </main>
      </body>
    </html>
  )
}
