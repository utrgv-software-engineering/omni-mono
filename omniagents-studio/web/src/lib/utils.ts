import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms.toFixed(0)}ms`
  } else if (ms < 60000) {
    return `${(ms / 1000).toFixed(2)}s`
  } else {
    return `${(ms / 60000).toFixed(2)}m`
  }
}

export function formatTimestamp(timestamp: string | null): string {
  if (!timestamp) return 'N/A'
  return new Date(timestamp).toLocaleString()
}

export function calculateDuration(startedAt: string | null, endedAt: string | null): number | undefined {
  if (!startedAt) return undefined
  const start = new Date(startedAt).getTime()
  const end = endedAt ? new Date(endedAt).getTime() : Date.now()
  return end - start
}