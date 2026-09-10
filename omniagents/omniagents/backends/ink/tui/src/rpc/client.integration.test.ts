import { afterEach, expect, it, vi } from "vitest";
import { WebSocketServer } from "ws";
import { RPCClient } from "./client.js";

const clients: RPCClient[] = [];
const servers: WebSocketServer[] = [];
afterEach(async () => {
  for (const client of clients.splice(0)) client.disconnect();
  for (const server of servers.splice(0)) {
    for (const socket of server.clients) socket.terminate();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

it("restores an idle session after a real socket reconnect without requiring a live event", async () => {
  const server = new WebSocketServer({ port: 0 });
  servers.push(server);
  await new Promise<void>((resolve) => server.once("listening", resolve));
  let connections = 0;
  const methods: string[] = [];
  server.on("connection", (socket) => {
    const connection = ++connections;
    socket.on("message", (raw) => {
      const request = JSON.parse(String(raw));
      methods.push(request.method);
      let result: unknown;
      if (request.method === "initialize") {
        result = {
          protocol_version: "2.0.0",
          identity: { name: "test", version: "1" },
          platform: { os: "linux", arch: "x64" },
          capabilities: request.params.capabilities,
          agent_host: {
            agent_host_id: "host",
            default_workspace_id: "workspace",
            default_environment_id: "environment",
          },
        };
      } else if (request.method === "queue_status") {
        result = {
          run_active: connection === 1,
          active_run_id: connection === 1 ? "run" : null,
          snapshot: {
            stream_id: "epoch",
            last_seq: connection,
            items: [],
            queue: [],
          },
        };
      } else if (request.method === "resume_session") {
        result = {
          session_id: "s",
          stream_id: "epoch",
          last_seq: connection,
          events: [],
        };
      } else if (request.id === undefined) return;
      else result = {};
      socket.send(JSON.stringify({ jsonrpc: "2.0", id: request.id, result }));
    });
  });
  const address = server.address();
  if (typeof address !== "object" || !address)
    throw new Error("no listen address");
  const client = new RPCClient({ url: `ws://127.0.0.1:${address.port}` });
  clients.push(client);
  const states: boolean[] = [];
  const errors: unknown[] = [];
  client.on("error", (error) => errors.push(error));
  client.on("rpc_ready", () => {
    void client
      .synchronizeSession("s", (snapshot) => states.push(snapshot.run_active))
      .catch((error) => errors.push(error));
  });
  await client.connect();
  await vi.waitFor(() => expect(states).toEqual([true]));
  for (const socket of server.clients) socket.close(1012, "restart");
  await vi.waitFor(() => expect(states).toEqual([true, false]), {
    timeout: 3000,
  });
  expect(methods.filter((method) => method === "resume_session")).toHaveLength(
    2,
  );
  expect(errors).toEqual([]);
});
