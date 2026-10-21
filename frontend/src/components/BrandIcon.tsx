import { useId } from 'react'
import { MARKS, type Brand } from '@/components/brands/marks'
import { cn } from '@/lib/utils'

export type { Brand } from '@/components/brands/marks'

// A service's official logo. Full colour by default; `mono` draws it in the text colour for
// brands whose guidelines allow a one-colour mark (full-colour-only marks, like Google's,
// ignore it). Pass `decorative` when a visible label already names the service, so screen
// readers don't hear it twice.
export function BrandIcon({
  brand,
  size = 20,
  mono = false,
  decorative = false,
  label,
  className,
}: {
  brand: Brand
  size?: number
  mono?: boolean
  decorative?: boolean
  label?: string
  className?: string
}) {
  const uid = useId().replace(/:/g, '')
  const mark = MARKS[brand]
  const drawing = mono && mark.mono ? mark.mono : mark.colour
  const a11y = decorative
    ? { 'aria-hidden': true as const, focusable: 'false' as const }
    : { role: 'img', 'aria-label': label ?? mark.title }
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={drawing.viewBox}
      width={size}
      height={size}
      className={cn('shrink-0', className)}
      {...a11y}
    >
      {drawing.draw(uid)}
    </svg>
  )
}
