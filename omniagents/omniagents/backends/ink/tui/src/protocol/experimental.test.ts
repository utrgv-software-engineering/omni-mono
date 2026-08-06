/**
 * Guards ../protocol/experimental.ts against protocol drift: the opt-in
 * manifest must track protocol/openrpc/inventory.json exactly — every
 * experimental operation the protocol defines is requested (so new
 * surface is never silently unreachable), except the launcher-privileged
 * agent_host_* control plane, and nothing stable or unknown is requested
 * (an unknown name would fail the initialize handshake on a matching
 * server generation).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  EXPERIMENTAL_NOTIFICATIONS,
  EXPERIMENTAL_REQUESTS,
  REQUESTED_EXPERIMENTAL_OPERATIONS,
} from "./experimental.js";

type Inventory = {
  requests: { stable: string[]; experimental: string[] };
  notifications: { stable: string[]; experimental: string[] };
};

const inventory: Inventory = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL(
        // tui/src/protocol → repo root
        "../../../../../../protocol/openrpc/inventory.json",
        import.meta.url,
      ),
    ),
    "utf-8",
  ),
);

describe("experimental capability manifest", () => {
  it("requests every experimental request except the agent_host_* control plane", () => {
    const expected = inventory.requests.experimental
      .filter((name) => !name.startsWith("agent_host_"))
      .sort();
    expect([...EXPERIMENTAL_REQUESTS].sort()).toEqual(expected);
  });

  it("requests every experimental notification", () => {
    expect([...EXPERIMENTAL_NOTIFICATIONS].sort()).toEqual(
      [...inventory.notifications.experimental].sort(),
    );
  });

  it("is the concatenation of requests and notifications, without duplicates", () => {
    expect(REQUESTED_EXPERIMENTAL_OPERATIONS).toEqual([
      ...EXPERIMENTAL_REQUESTS,
      ...EXPERIMENTAL_NOTIFICATIONS,
    ]);
    expect(new Set(REQUESTED_EXPERIMENTAL_OPERATIONS).size).toBe(
      REQUESTED_EXPERIMENTAL_OPERATIONS.length,
    );
  });

  it("requests nothing stable and nothing outside the inventory", () => {
    const known = new Set([
      ...inventory.requests.experimental,
      ...inventory.notifications.experimental,
    ]);
    const stable = new Set([
      ...inventory.requests.stable,
      ...inventory.notifications.stable,
    ]);
    for (const name of REQUESTED_EXPERIMENTAL_OPERATIONS) {
      expect(known.has(name), `${name} is not experimental`).toBe(true);
      expect(stable.has(name), `${name} is stable`).toBe(false);
    }
  });
});
