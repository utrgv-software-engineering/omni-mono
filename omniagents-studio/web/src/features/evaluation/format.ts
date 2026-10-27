/** A rate as a percentage, e.g. `62%`. */
export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`
}

/** A rate with its 95% interval, e.g. `62% (48–74%)`: a rate without its
 * interval is how thirty cases look like a result. */
export function rateWithInterval(value: number | null | undefined, interval: [number, number] | null | undefined): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (!interval) {
    return percent(value)
  }
  return `${percent(value)} (${Math.round(interval[0] * 100)}–${Math.round(interval[1] * 100)}%)`
}

export function when(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/** A path shown from the project down. */
export function relativePath(path: string, root: string | null | undefined): string {
  if (root && path.startsWith(root)) {
    return path.slice(root.length).replace(/^\//, '')
  }
  return path.split('/').slice(-3).join('/')
}

/** The first line of a text, cut to `length`. */
export function excerpt(text: string | null | undefined, length = 120): string {
  const line = (text ?? '').trim().split('\n')[0] ?? ''
  return line.length > length ? `${line.slice(0, length - 1)}…` : line
}
