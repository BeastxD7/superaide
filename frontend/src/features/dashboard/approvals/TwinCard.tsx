'use client'

import { Scale } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppData } from '@/features/dashboard/data'

type Twin = {
  decisions: number
  accuracy: number | null
  baseline: number | null
  calibration_error: number | null
  bins: { from: number; to: number; n: number; predicted: number; observed: number }[]
  per_kind: Record<string, { decisions: number; yes_rate: number; accuracy: number }>
  gate: { ready: boolean; reasons: string[] }
}

const pct = (x: number | null) => (x === null ? 'n/a' : `${Math.round(x * 100)}%`)

// Owner Twin calibration: how well a simple predictor guesses your yes or no. Shown only when the feature is on
// (the API answers 404 when off). It changes nothing by itself.
export function TwinCard({ name }: { name: string }) {
  const { data, error } = useAppData<Twin>('/twin')
  if (error || !data) return null
  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Scale className="size-5 text-violet-600" aria-hidden />
          How well {name} can predict your answers
          <Badge variant={data.gate.ready ? 'default' : 'secondary'}>{data.gate.ready ? 'Reliable' : 'Not yet'}</Badge>
        </CardTitle>
        <CardDescription>
          Scored on your past decisions, each guessed before it was known. Nothing is done automatically from this.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-body">
        <p>
          {data.decisions} decisions · right {pct(data.accuracy)} of the time (always guessing the common answer: {pct(data.baseline)}) ·
          calibration error {data.calibration_error ?? 'n/a'}
        </p>
        {data.gate.reasons.length > 0 && (
          <ul className="list-disc pl-5">
            {data.gate.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {data.bins.length > 0 && (
          <table className="w-full text-left">
            <caption className="sr-only">Predicted against observed yes rate</caption>
            <thead>
              <tr className="text-muted">
                <th scope="col">Guessed yes</th>
                <th scope="col">Cards</th>
                <th scope="col">Said yes</th>
              </tr>
            </thead>
            <tbody>
              {data.bins.map((b) => (
                <tr key={b.from}>
                  <td>{pct(b.predicted)}</td>
                  <td>{b.n}</td>
                  <td>{pct(b.observed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}
