/**
 * Guards ../protocol/experimental.ts against protocol drift: the opt-in
 * manifest must track protocol/openrpc/inventory.json exactly — every
 * experimental operation the protocol defines is requested (so new
 * surface is never silently unreachable), except the launcher-privileged
 * agent_host_* control plane, and nothing stable or unknown is requested
 * (an unknown name would fail the initialize handshake on a matching
 * server generation).
 *
 * The Ink client carries the identical guard. The two manifests are
 * meant to stay byte-identical in content, so a capability reachable
 * from one reference client is reachable from the other; the last test
 * here is what makes divergence fail rather than drift quietly.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  EXPERIMENTAL_NOTIFICATIONS,
  EXPERIMENTAL_REQUESTS,
  REQUESTED_EXPERIMENTAL_OPERATIONS,
} from './experimental'

type Inventory = {
  requests: { stable: string[]; experimental: string[] }
  notifications: { stable: string[]; experimental: string[] }
}

/** web/ui/src/protocol → repo root */
const fromRepoRoot = (relative: string) =>
  fileURLToPath(new URL(`../../../../../../${relative}`, import.meta.url))

const inventory: Inventory = JSON.parse(readFileSync(fromRepoRoot('protocol/openrpc/inventory.json'), 'utf-8'))

describe('experimental capability manifest', () => {
  it('requests every experimental request except the agent_host_* control plane', () => {
    const expected = inventory.requests.experimental.filter((name) => !name.startsWith('agent_host_')).sort()
    expect([...EXPERIMENTAL_REQUESTS].sort()).toEqual(expected)
  })

  it('requests every experimental notification', () => {
    expect([...EXPERIMENTAL_NOTIFICATIONS].sort()).toEqual([...inventory.notifications.experimental].sort())
  })

  it('is the concatenation of requests and notifications, without duplicates', () => {
    expect(REQUESTED_EXPERIMENTAL_OPERATIONS).toEqual([...EXPERIMENTAL_REQUESTS, ...EXPERIMENTAL_NOTIFICATIONS])
    expect(new Set(REQUESTED_EXPERIMENTAL_OPERATIONS).size).toBe(REQUESTED_EXPERIMENTAL_OPERATIONS.length)
  })

  it('requests nothing stable and nothing outside the inventory', () => {
    const known = new Set([...inventory.requests.experimental, ...inventory.notifications.experimental])
    const stable = new Set([...inventory.requests.stable, ...inventory.notifications.stable])
    for (const name of REQUESTED_EXPERIMENTAL_OPERATIONS) {
      expect(known.has(name), `${name} is not experimental`).toBe(true)
      expect(stable.has(name), `${name} is stable`).toBe(false)
    }
  })

  it('matches the Ink client operation for operation', () => {
    // Parsed rather than imported: the Ink manifest is outside this
    // package's tsconfig, and the point is to compare the shipped source
    // of truth, not a re-export someone could forget to update.
    const inkSource = readFileSync(
      fromRepoRoot('omniagents/backends/ink/tui/src/protocol/experimental.ts'),
      'utf-8',
    )
    const names = (block: string) => [...block.matchAll(/^\s*"([a-z_]+)",/gm)].map((m) => m[1]).sort()
    const [inkRequests, inkNotifications] = inkSource.split('EXPERIMENTAL_NOTIFICATIONS')
    expect([...EXPERIMENTAL_REQUESTS].sort()).toEqual(names(inkRequests!))
    expect([...EXPERIMENTAL_NOTIFICATIONS].sort()).toEqual(names(inkNotifications!))
  })
})
