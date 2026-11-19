// A stopwatch for the time-to-first-useful-reply measurement (#411): named marks from one start, written as JSON.
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

export type Mark = { stage: string; at_ms: number; step_ms: number }

export class Stopwatch {
  private readonly t0 = performance.now()
  private last = 0
  readonly marks: Mark[] = [{ stage: 'start', at_ms: 0, step_ms: 0 }]

  /** Record that `stage` has just happened. */
  mark(stage: string): Mark {
    const at = Math.round(performance.now() - this.t0)
    const m = { stage, at_ms: at, step_ms: at - this.last }
    this.last = at
    this.marks.push(m)
    return m
  }

  get total_ms(): number {
    return this.marks[this.marks.length - 1].at_ms
  }
}

export type Target = { name: string; seconds: number }

/** The measurement as a result file: numbers only (no names, emails or tokens). */
export function writeResult(path: string, sw: Stopwatch, extra: { mode: string; target: Target; commit?: string }): void {
  const seconds = Math.round(sw.total_ms / 100) / 10
  const body = {
    measured_at: new Date().toISOString(),
    mode: extra.mode,
    commit: extra.commit ?? process.env.GITHUB_SHA ?? null,
    target_seconds: extra.target.seconds,
    total_seconds: seconds,
    within_target: seconds <= extra.target.seconds,
    stages: sw.marks,
  }
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(body, null, 2) + '\n')
}
