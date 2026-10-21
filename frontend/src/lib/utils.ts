import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

// Join class names and let later Tailwind classes win (shadcn/ui's helper).
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
