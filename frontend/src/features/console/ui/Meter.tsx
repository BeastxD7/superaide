// A horizontal meter (spend against a cap). The name is required: a meter without one is announced as nothing.
export function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const share = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  return (
    <div className="c-meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <div className="c-meter-fill" style={{ width: `${Math.max(share * 100, share > 0 ? 0.6 : 0)}%` }} />
    </div>
  )
}
