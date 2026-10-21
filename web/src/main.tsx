import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

// Two separate apps share this page: the person-facing app (marketplace, hiring,
// onboarding, workspace) and the operator console at #/console. Each brings its
// own stylesheet, so switching between them reloads the page.
const AppRoot = lazy(() => import('./app/AppRoot.tsx'))
const ConsoleApp = lazy(() => import('./ConsoleApp.tsx'))

function family(hash: string): 'console' | 'app' {
  return hash.startsWith('#/console') || hash.startsWith('#token=') ? 'console' : 'app'
}

function Root() {
  const [current] = useState(() => family(window.location.hash))
  useEffect(() => {
    const onHash = () => {
      if (family(window.location.hash) !== current) window.location.reload()
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [current])
  return <Suspense fallback={null}>{current === 'console' ? <ConsoleApp /> : <AppRoot />}</Suspense>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
