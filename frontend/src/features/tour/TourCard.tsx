'use client'

import { Compass } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useTour } from '@/features/tour/tour-context'

// Settings: start the walkthroughs over (#626).
export function TourCard() {
  const tour = useTour()
  const [busy, setBusy] = useState(false)
  return (
    <Card data-tour="settings-tour">
      <CardHeader>
        <CardTitle>Guided tour</CardTitle>
        <CardDescription>A short walkthrough of each page. You can also run one page&apos;s tour from the account menu.</CardDescription>
      </CardHeader>
      <CardContent>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            void tour.replay().finally(() => setBusy(false))
          }}
        >
          <Compass />
          Replay the tour
        </Button>
      </CardContent>
    </Card>
  )
}
