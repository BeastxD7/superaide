import { Skeleton } from '@/components/ui/skeleton'

// What the transcript looks like while it loads, drawn in the shape of the real one (see Conversation.tsx): Ava's
// bubbles on the left behind a 32px avatar column, the owner's on the right, the same corner radii, the same
// gap and run spacing. Heights are one, two or three lines of body text (24px each) plus the bubble's 20px of
// padding, so the messages replace it without the page jumping. Ava replies last, as in a real chat.
const ROWS: { from: 'agent' | 'you'; width: string; lines: 1 | 2 | 3; startsRun: boolean; endsRun: boolean }[] = [
  { from: 'agent', width: '62%', lines: 3, startsRun: true, endsRun: false },
  { from: 'agent', width: '38%', lines: 1, startsRun: false, endsRun: true },
  { from: 'you', width: '34%', lines: 1, startsRun: true, endsRun: true },
  { from: 'agent', width: '70%', lines: 2, startsRun: true, endsRun: true },
]

const bubbleHeight = (lines: number) => lines * 24 + 20

export function ChatSkeleton({ what = 'your conversation' }: { what?: string }) {
  return (
    // role="status" is a polite live region by itself; an explicit aria-live would make the transcript announce twice.
    <div role="status" aria-busy="true" data-testid="chat-skeleton" className="flex flex-col gap-2.5">
      <span className="sr-only">{`Loading ${what}`}</span>
      {ROWS.map((row, i) =>
        row.from === 'agent' ? (
          <div key={i} aria-hidden="true" className={`flex items-end gap-2.5 ${row.startsRun ? 'mt-3' : ''}`}>
            <div className="w-8 shrink-0">{row.endsRun && <Skeleton className="size-8 rounded-full" />}</div>
            <Skeleton data-testid="skeleton-agent-bubble" className="max-w-[85%] rounded-3xl rounded-bl-md" style={{ width: row.width, height: bubbleHeight(row.lines) }} />
          </div>
        ) : (
          <div key={i} aria-hidden="true" className={`flex justify-end ${row.startsRun ? 'mt-3' : ''}`}>
            <Skeleton className="max-w-[80%] rounded-3xl rounded-br-md" style={{ width: row.width, height: bubbleHeight(row.lines) }} />
          </div>
        ),
      )}
    </div>
  )
}
