'use client'

import dynamic from 'next/dynamic'

// The console keeps its admin token in this browser's storage, so it renders only in the browser.
const ConsoleApp = dynamic(() => import('@/features/console/ConsoleApp'), { ssr: false })

export function ConsoleScreen() {
  return <ConsoleApp />
}
