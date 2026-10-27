'use client'

import { ArrowRight, Drama, GraduationCap } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { useRehearsalEnabled } from '@/features/rehearsal/useRehearsal'
import { useSchoolEnabled } from '@/features/school/useSchool'
import { useSession } from '@/lib/session-context'

// Where Ava learns and practises: one menu entry (docs/design/dashboard-ia.md, section 3) instead of one per feature.
export function LearningPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const school = useSchoolEnabled()
  const rehearsal = useRehearsalEnabled()
  const items = [
    school && { href: '/dashboard/learning/school', label: 'Ava School', icon: GraduationCap, text: `Lessons you may share, never your data, so every assistant improves. Nothing is shared without your yes.` },
    rehearsal && { href: '/dashboard/learning/rehearsal', label: 'Rehearsal room', icon: Drama, text: `Practise a hard conversation with ${name} playing the other side. Nothing is sent.` },
  ].filter(Boolean) as { href: string; label: string; icon: typeof Drama; text: string }[]
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Learning</h1>
        <p className="mt-1 text-body">How {name} learns and practises.</p>
        {items.length === 0 ? (
          <p className="mt-6 text-body">This isn&rsquo;t turned on yet.</p>
        ) : (
          <ul className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="Learning">
            {items.map(({ href, label, icon: Icon, text }) => (
              <li key={href} className="flex flex-col rounded-xl border border-line bg-white p-4">
                <div className="flex items-center gap-3">
                  <span className="grid size-9 place-items-center rounded-lg bg-lilac-100 text-violet-600">
                    <Icon className="size-4.5" aria-hidden />
                  </span>
                  <h2 className="font-medium text-ink">{label}</h2>
                </div>
                <p className="mt-3 text-sm text-body">{text}</p>
                <Button asChild variant="outline" className="mt-3 self-start touch:min-h-11">
                  <Link href={href}>
                    Open {label} <ArrowRight aria-hidden />
                  </Link>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
