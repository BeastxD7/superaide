import type { ButtonHTMLAttributes } from 'react'
import clsx from 'clsx'

export type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'ok'

// Primary (violet), secondary (haze), ghost (text), danger and ok (outlined, for the pause and resume switch).
export function Button({ variant = 'primary', className, type = 'button', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return <button type={type} className={clsx('c-btn', variant !== 'primary' && `is-${variant === 'secondary' ? 'secondary' : variant}`, className)} {...rest} />
}
