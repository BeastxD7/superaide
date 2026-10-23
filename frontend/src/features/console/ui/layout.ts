// How many equal columns fill `n` items without leaving one alone: n itself up to 4, else 3 or 4 when they divide, else 2.
export function evenCols(n: number): 2 | 3 | 4 {
  if (n >= 2 && n <= 4) return n as 2 | 3 | 4
  if (n % 3 === 0) return 3
  if (n % 4 === 0) return 4
  return 2
}
