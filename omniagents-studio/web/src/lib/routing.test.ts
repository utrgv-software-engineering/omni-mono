import { describe, expect, it } from 'vitest'

import { buildRoutePath, mergeSearchParams } from './routing'

describe('buildRoutePath', () => {
  it('substitutes route params and encodes normalized query values', () => {
    expect(
      buildRoutePath('agentDetail', {
        pathParams: { agentKey: 'agent-one' },
        query: { tab: ' details ', page: 2, archived: false },
      })
    ).toBe('/agents/agent-one?tab=details&page=2&archived=false')
  })

  it('removes blank and null values while preserving base search params', () => {
    expect(
      buildRoutePath('logs', {
        baseSearch: '?project=alpha&search=old',
        query: { search: '   ', project: null, errors: true },
      })
    ).toBe('/logs?errors=true')
  })
})

describe('mergeSearchParams', () => {
  it('merges updates into an existing URLSearchParams without mutating it', () => {
    const existing = new URLSearchParams('project=alpha&offset=20')

    expect(
      mergeSearchParams(existing, { offset: 40, query: 'hello world' })
    ).toBe('?project=alpha&offset=40&query=hello+world')
    expect(existing.toString()).toBe('project=alpha&offset=20')
  })
})
