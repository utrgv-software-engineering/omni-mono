/**
 * How the TUI introduces itself to the agent runtime. The TUI ships inside
 * the omniagents package (src/ in a checkout, dist/ when installed, both
 * four levels under it), so it carries omniagents' version.
 */

import { readFileSync } from "node:fs";
import type { Identity } from "@omniagents/client/protocol/gui-v2";

function omniagentsVersion(): string {
  const source = readFileSync(new URL("../../../../_version.py", import.meta.url), "utf8");
  const version = /__version__ = "([^"]+)"/.exec(source)?.[1];
  if (!version) {
    throw new Error("omniagents/_version.py has no __version__");
  }
  return version;
}

export const CLIENT_IDENTITY: Identity = { name: "omniagents-tui", version: omniagentsVersion() };
