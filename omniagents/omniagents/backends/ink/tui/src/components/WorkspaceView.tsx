import React from "react";
import type { RPCClient } from "@omniagents/client/rpc/client";
import { defaultEnvironmentId } from "../rpc/host.js";

const identities = new WeakMap<RPCClient, number>();
let nextIdentity = 0;

/** A fresh view owns fresh requests and watches after a client/environment change. */
export function WorkspaceView({
  client,
  active,
  children,
}: {
  client: RPCClient | null;
  active: boolean;
  children: React.ReactNode;
}) {
  if (client && !identities.has(client)) identities.set(client, ++nextIdentity);
  const key = JSON.stringify([
    client ? identities.get(client) : null,
    client ? defaultEnvironmentId(client) : null,
  ]);
  return active ? <React.Fragment key={key}>{children}</React.Fragment> : null;
}
