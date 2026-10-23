import { useId } from 'react'

// The product's mark: an agent's face, the same one as the browser tab icon (src/app/icon.svg).
export function BrandMark({ className = 'size-6' }: { className?: string }) {
  const id = useId()
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden>
      <defs>
        <linearGradient id={`${id}b`} x1="0.1" y1="0" x2="0.9" y2="1">
          <stop offset="0" stopColor="#d9c9ff" />
          <stop offset="0.38" stopColor="#b9a6ff" />
          <stop offset="0.68" stopColor="#f3b6f5" />
          <stop offset="1" stopColor="#9cc7ff" />
        </linearGradient>
        <radialGradient id={`${id}s`} cx="0.32" cy="0.26" r="0.48">
          <stop offset="0" stopColor="#fff" stopOpacity="0.95" />
          <stop offset="0.45" stopColor="#fff" stopOpacity="0.32" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="30" fill={`url(#${id}b)`} />
      <circle cx="32" cy="32" r="30" fill={`url(#${id}s)`} />
      <rect x="21.5" y="25.5" width="7" height="13" rx="3.5" fill="#1d1640" />
      <rect x="35.5" y="25.5" width="7" height="13" rx="3.5" fill="#1d1640" />
    </svg>
  )
}
