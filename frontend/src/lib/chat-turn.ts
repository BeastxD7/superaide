import type { LiveTurn } from '@/components/Conversation'
import type { ChatEvent, WorkStep } from '@/lib/api'

// Fold one streamed event into the reply being shown.
export function apply(turn: LiveTurn, e: ChatEvent): LiveTurn {
  switch (e.event) {
    case 'status':
      return { ...turn, status: e.data.text }
    case 'reasoning':
      return { ...turn, reasoning: turn.reasoning + e.data.delta }
    case 'text':
      return { ...turn, text: turn.text + e.data.delta, status: 'Writing' }
    case 'narration': {
      // What she wrote before using a tool was her saying what she's doing, not the answer.
      const say: WorkStep = { kind: 'say', label: e.data.text }
      return { ...turn, text: '', steps: [...turn.steps, say] }
    }
    case 'retract':
      // The draft failed the source check; the rewrite streams in its place.
      return { ...turn, text: '' }
    case 'tool': {
      const step: WorkStep = { kind: 'tool', ...e.data }
      const at = turn.steps.findIndex((s) => s.kind === 'tool' && s.id === e.data.id)
      const steps = at >= 0 ? turn.steps.map((s, i) => (i === at ? step : s)) : [...turn.steps, step]
      return { ...turn, steps, status: e.data.state === 'running' ? 'Working' : 'Thinking' }
    }
    default:
      return turn
  }
}
