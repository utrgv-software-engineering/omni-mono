import { generatePath } from 'react-router-dom'
import { ROUTES } from '../app/routes'

export type QueryValue = string | number | boolean | null | undefined

interface BuildRoutePathOptions {
  pathParams?: Record<string, string | number>
  query?: Record<string, QueryValue>
  baseSearch?: URLSearchParams | string | null | undefined
}

const toQueryStringValue = (value: QueryValue): string | undefined => {
  if (value === null || value === undefined) {
    return undefined
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed.length > 0 ? trimmed : undefined
  }
  return String(value)
}

const createSearchString = (
  baseSearch: URLSearchParams | string | null | undefined,
  updates: Record<string, QueryValue> | undefined
): string => {
  const params = baseSearch instanceof URLSearchParams
    ? new URLSearchParams(baseSearch)
    : typeof baseSearch === 'string' && baseSearch.length > 0
      ? new URLSearchParams(baseSearch.startsWith('?') ? baseSearch.slice(1) : baseSearch)
      : new URLSearchParams()

  if (updates) {
    Object.entries(updates).forEach(([key, value]) => {
      const normalized = toQueryStringValue(value)
      if (normalized === undefined) {
        params.delete(key)
      } else {
        params.set(key, normalized)
      }
    })
  }

  const serialized = params.toString()
  return serialized.length > 0 ? `?${serialized}` : ''
}

export const buildRoutePath = (
  route: keyof typeof ROUTES,
  options: BuildRoutePathOptions = {}
): string => {
  const { pathParams, query, baseSearch } = options
  const pathname = pathParams ? generatePath(ROUTES[route], pathParams as any) : ROUTES[route]
  const search = createSearchString(baseSearch ?? null, query)
  return `${pathname}${search}`
}

export const mergeSearchParams = (
  baseSearch: URLSearchParams | string | null | undefined,
  updates: Record<string, QueryValue>
): string => createSearchString(baseSearch, updates)
