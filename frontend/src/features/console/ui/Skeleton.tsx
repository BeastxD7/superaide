// A loading placeholder that says what is loading to screen readers.
export function LoadingBlock({ label, lines = 4 }: { label: string; lines?: number }) {
  return (
    <div className="c-skeleton" role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="c-skeleton-line" aria-hidden="true" />
      ))}
    </div>
  )
}
