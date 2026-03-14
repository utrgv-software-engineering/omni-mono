/**
 * JSON-RPC client that communicates with the Python TUI server over a pipe (fd 3/4).
 *
 * The Python server reads from our write pipe and writes to our read pipe.
 * Protocol: newline-delimited JSON-RPC 2.0.
 */

import { createReadStream, createWriteStream } from "fs";
import { createInterface } from "readline";

export type RpcEventHandler = (event: string, data: unknown) => void;

let nextId = 1;
const pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
let eventHandler: RpcEventHandler | null = null;

// fd 3 = read from server, fd 4 = write to server
let writeStream: ReturnType<typeof createWriteStream> | null = null;
let readStream: ReturnType<typeof createReadStream> | null = null;
let rl: ReturnType<typeof createInterface> | null = null;
let initialized = false;

export function initRpc(readFd: number, writeFd: number): void {
  if (initialized) return;
  initialized = true;

  writeStream = createWriteStream("", { fd: writeFd });

  readStream = createReadStream("", { fd: readFd, encoding: "utf-8" });
  rl = createInterface({ input: readStream });

  rl.on("line", (line: string) => {
    if (!line.trim()) return;
    try {
      const msg = JSON.parse(line);
      // Event notification (no id)
      if (!("id" in msg) && typeof msg.method === "string" && msg.method.startsWith("event:")) {
        const eventName = msg.method.slice(6);
        eventHandler?.(eventName, msg.params);
        return;
      }
      // Response to a request
      const id = String(msg.id);
      const entry = pending.get(id);
      if (!entry) return;
      pending.delete(id);
      if (msg.error) {
        entry.reject(new Error(msg.error.message || "RPC error"));
      } else {
        entry.resolve(msg.result);
      }
    } catch {
      // ignore malformed lines
    }
  });

  rl.on("close", () => {
    for (const entry of pending.values()) {
      entry.reject(new Error("RPC connection closed"));
    }
    pending.clear();
  });
}

export function onEvent(handler: RpcEventHandler): void {
  eventHandler = handler;
}

export function destroyRpc(): void {
  rl?.close();
  readStream?.destroy();
  writeStream?.end();
  rl = null;
  readStream = null;
  writeStream = null;
}

export function call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  if (!writeStream) {
    return Promise.reject(new Error("RPC not initialized"));
  }
  const id = String(nextId++);
  const payload = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";

  return new Promise<T>((resolve, reject) => {
    pending.set(id, {
      resolve: resolve as (value: unknown) => void,
      reject,
    });
    writeStream!.write(payload, "utf-8");
  });
}

// --- Typed API wrappers ---

export type Project = {
  id: string;
  label: string;
  workspace_dir: string;
  created_at: number;
  updated_at: number;
  ticket_count: number;
  active_count: number;
};

export type Column = {
  id: string;
  label: string;
  max_concurrent: number | null;
  gate: boolean;
};

export type Task = {
  id: string;
  project_id: string;
  task_description: string;
  status: { type: string; timestamp: number; data?: Record<string, unknown> };
  created_at: number;
  ticket_id: string | null;
  session_id: string | null;
  run_id: string | null;
  last_urls: Record<string, string> | null;
};

export type Ticket = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  priority: string;
  blocked_by: string[];
  column_id: string;
  created_at: number;
  updated_at: number;
  branch: string | null;
  use_worktree: boolean;
  worktree_path: string | null;
  worktree_name: string | null;
  supervisor_session_id: string | null;
  phase: string | null;
  token_usage: { input_tokens?: number; output_tokens?: number; total_tokens?: number; inputTokens?: number; outputTokens?: number; totalTokens?: number } | null;
  task: Task | null;
};

export type SandboxConfig = {
  enabled: boolean;
  image?: string | null;
  dockerfile?: string | null;
};

export type Container = {
  name: string;
  status: string;
  image: string;
  ports: string;
  created: string;
  state: string;
};

export type NetworkConfig = {
  enabled?: boolean;
  allowlist?: string[];
  denylist?: string[];
  allow_private_ips?: boolean;
  enable_socks5?: boolean;
};

export type FileDiff = {
  path: string;
  additions: number;
  deletions: number;
  is_binary: boolean;
  staged?: boolean;
  untracked?: boolean;
};

export type DiffResponse = {
  total_files: number;
  total_additions: number;
  total_deletions: number;
  has_changes: boolean;
  files: FileDiff[];
};

export type OmniSession = {
  id: string;
  archived: boolean;
  created_at: string;
  message_count: number | null;
  workspace_root?: string | null;
  first_message: { role: string; content: string; timestamp: string } | null;
  last_message: { role: string; content: string; timestamp: string } | null;
};

export const api = {
  listProjects: () => call<Project[]>("projects.list"),
  addProject: (label: string, workspace_dir: string) =>
    call<Project>("projects.add", { label, workspace_dir }),
  removeProject: (project_id: string) =>
    call<void>("projects.remove", { project_id }),
  getPipeline: (project_id: string) =>
    call<{ columns: Column[] }>("pipeline.get", { project_id }),

  listTickets: (project_id: string) =>
    call<Ticket[]>("tickets.list", { project_id }),
  addTicket: (project_id: string, title: string, priority?: string) =>
    call<Ticket>("tickets.add", { project_id, title, priority }),
  moveTicket: (ticket_id: string, column_id: string, project_id?: string) =>
    call<Ticket>("tickets.move", { ticket_id, column_id, project_id }),
  deleteTicket: (ticket_id: string, project_id?: string) =>
    call<void>("tickets.delete", { ticket_id, project_id }),
  updateTicket: (ticket_id: string, patch: Record<string, unknown>, project_id?: string) =>
    call<Ticket>("tickets.update", { ticket_id, patch, project_id }),

  sandboxStart: (ticket_id: string, project_id?: string) =>
    call<{ container_name: string; ws_url: string | null; sandbox_url: string | null; code_server_url: string | null; desktop_url: string | null }>(
      "sandbox.start", { ticket_id, project_id },
    ),
  supervisorStart: (ticket_id: string, prompt?: string, project_id?: string) =>
    call<{ session_id: string; run_id: string }>(
      "supervisor.start", { ticket_id, prompt, project_id },
    ),
  supervisorStop: (ticket_id: string, project_id?: string) =>
    call<void>("supervisor.stop", { ticket_id, project_id }),
  supervisorAutoStart: (ticket_id: string, prompt?: string, project_id?: string) =>
    call<{ ticket_id: string; started: boolean }>(
      "supervisor.auto_start", { ticket_id, prompt, project_id },
    ),
  supervisorAutoStop: (ticket_id: string, project_id?: string) =>
    call<{ ticket_id: string; stopped: boolean; was_auto: boolean }>(
      "supervisor.auto_stop", { ticket_id, project_id },
    ),

  snapshot: () => call<Record<string, unknown>>("snapshot"),

  ticketHistory: (ticket_id: string, project_id?: string, limit?: number) =>
    call<Array<{ index: number; role: string; content: string }>>(
      "ticket.history", { ticket_id, project_id, limit },
    ),
  ticketArtifacts: (ticket_id: string, subdir?: string) =>
    call<Array<{ name: string; path: string; is_dir: boolean; size: number }>>(
      "ticket.artifacts", { ticket_id, subdir },
    ),
  ticketArtifactRead: (ticket_id: string, path: string) =>
    call<{ content: string }>("ticket.artifact_read", { ticket_id, path }),

  listModels: () =>
    call<Array<Record<string, unknown>>>("models.list"),
  addModel: (params: {
    provider_name: string;
    provider_type: string;
    model_name: string;
    model: string;
    label?: string;
    provider_api_key?: string;
    provider_base_url?: string;
    reasoning?: string;
    set_default?: boolean;
  }) => call<void>("models.add", params),
  removeModel: (name: string) =>
    call<boolean>("models.remove", { name }),
  setDefaultModel: (name: string) =>
    call<boolean>("models.set_default", { name }),

  listProviders: () =>
    call<Array<Record<string, unknown>>>("providers.list"),
  addProvider: (params: {
    name: string;
    type: string;
    api_key?: string;
    base_url?: string;
  }) => call<void>("providers.add", params),
  removeProvider: (name: string) =>
    call<boolean>("providers.remove", { name }),
  getNetworkConfig: () =>
    call<NetworkConfig>("network.get"),
  saveNetworkConfig: (config: NetworkConfig) =>
    call<void>("network.save", { config }),

  getEnv: () =>
    call<Record<string, string>>("env.get"),
  saveEnv: (entries: Record<string, string>) =>
    call<void>("env.save", { entries }),

  listContainers: () =>
    call<Container[]>("containers.list"),
  startContainer: (name: string) =>
    call<void>("containers.start", { name }),
  stopContainer: (name: string) =>
    call<void>("containers.stop", { name }),
  rebuildContainer: (name: string) =>
    call<Record<string, unknown>>("containers.rebuild", { name }),
  pruneContainers: () =>
    call<Record<string, string>>("containers.prune"),
  sandboxRebuildImage: () =>
    call<{ image: string; note?: string }>("sandbox.rebuild_image", {}),

  gitDiff: (ticket_id: string, project_id?: string) =>
    call<DiffResponse>("git.diff", { ticket_id, project_id }),
  gitDiffFile: (ticket_id: string, path: string, project_id?: string) =>
    call<{ patch: string }>("git.diff_file", { ticket_id, path, project_id }),

  ticketArtifactOpen: (ticket_id: string, path: string) =>
    call<void>("ticket.artifact_open", { ticket_id, path }),

  setAutoDispatch: (project_id: string, enabled: boolean) =>
    call<void>("projects.set_auto_dispatch", { project_id, enabled }),
  getAutoDispatch: (project_id: string) =>
    call<{ enabled: boolean }>("projects.get_auto_dispatch", { project_id }),

  getSandboxConfig: () =>
    call<{ sandbox: SandboxConfig }>("sandbox.get_config"),
  setSandboxConfig: (sandbox: SandboxConfig) =>
    call<void>("sandbox.set_config", { sandbox }),

  mcpList: () =>
    call<{ mcpServers: Record<string, { type?: string; command?: string; args?: string[]; url?: string; env?: Record<string, string>; headers?: Record<string, string> }> }>("mcp.list"),
  mcpSave: (config: { mcpServers: Record<string, unknown> }) =>
    call<void>("mcp.save", { config }),

  testModel: (name?: string) =>
    call<{ success: boolean; output: string }>("models.test", { name }),

  listSessions: (limit?: number, offset?: number) =>
    call<OmniSession[]>("sessions.list", { limit: limit ?? 20, offset: offset ?? 0 }),

  onboardingStatus: () =>
    call<{ models_configured: boolean }>("onboarding.status"),

  getProjectHotkeys: () =>
    call<Record<string, string>>("projects.get_hotkeys"),
  setProjectHotkey: (digit: string, project_id: string | null) =>
    call<void>("projects.set_hotkey", { digit, project_id }),

  // Terminal command resolution (for embedded PTY)
  terminalResolveSsh: (ticket_id: string, project_id?: string) =>
    call<{ command: string[]; cwd?: string; label: string }>("terminal.resolve_ssh", { ticket_id, project_id }),
  terminalResolveOmni: (ticket_id: string, project_id?: string) =>
    call<{ command: string[]; cwd?: string; label: string }>("terminal.resolve_omni", { ticket_id, project_id }),
  terminalResolveChat: (ticket_id: string, project_id?: string) =>
    call<{ command: string[]; cwd?: string; label: string }>("terminal.resolve_chat", { ticket_id, project_id }),

  // Project-level terminal resolution (no ticket required)
  projectResolveShell: (project_id: string) =>
    call<{ command: string[]; cwd?: string; label: string }>("project.resolve_shell", { project_id }),
  projectResolveOmni: (project_id: string) =>
    call<{ command: string[]; cwd?: string; label: string }>("project.resolve_omni", { project_id }),
};
