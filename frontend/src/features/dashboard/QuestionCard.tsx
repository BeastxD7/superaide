'use client'

import { MessageCircleQuestion } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useAppData } from '@/features/dashboard/data'
import { AppError, call } from '@/lib/api'

type Question = { id: string; topic: string; question: string; status: 'asked' | 'answered' | 'skipped'; answer: string }

// One Good Question: Ava may ask one thing a day. Shown only when the feature is on (the API answers 404 when off).
export function QuestionCard({ name }: { name: string }) {
  const { data, error, reload } = useAppData<{ question: Question | null }>('/question')
  const [answer, setAnswer] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  if (error || !data) return null
  const q = data.question
  if (q && q.status !== 'asked') return null

  async function go(path: string, json?: unknown) {
    setProblem(null)
    try {
      await call(path, { method: 'POST', json })
      setAnswer('')
      await reload()
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    }
  }

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageCircleQuestion className="size-5 text-violet-600" aria-hidden />
          One question from {name}
        </CardTitle>
        <CardDescription>She asks at most one a day, so she can get better at helping you.</CardDescription>
      </CardHeader>
      <CardContent>
        {!q ? (
          <Button size="sm" onClick={() => void go('/question/ask')}>
            Ask me today&apos;s question
          </Button>
        ) : (
          <div className="space-y-3">
            <p className="text-sm font-medium text-ink">{q.question}</p>
            <Input aria-label="Your answer" value={answer} onChange={(e) => setAnswer(e.target.value)} />
            {problem && <p className="text-sm text-red-700">{problem}</p>}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={!answer.trim()} onClick={() => void go(`/question/${q.id}/answer`, { answer })}>
                Save answer
              </Button>
              <Button size="sm" variant="outline" onClick={() => void go(`/question/${q.id}/skip`, { never: false })}>
                Skip
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void go(`/question/${q.id}/skip`, { never: true })}>
                Never ask about this
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
