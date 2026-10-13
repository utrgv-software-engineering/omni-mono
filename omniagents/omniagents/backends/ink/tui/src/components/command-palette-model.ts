// Presentation logic for the slash-command palette and the inline `/`
// popup, kept out of the components so it is unit-testable without an
// Ink render.
//
// The rows are the slash catalog: what the server declares typeable
// (`server_call("slash.list")`) plus this client's own commands, in the
// same shape. Nothing else is ever listed — a server function without a
// slash declaration is an RPC, not a command.

import {
  completionFor as slashCompletion,
  normalizeSlashCommand,
  takesArgs,
  type SlashCommand,
} from "../rpc/slash.js";

export type CommandRow = SlashCommand;

/** Commands this client runs itself. They win over a server row of the
 * same name: `/model` opens the picker here rather than switching
 * blind. Order is presentation order. */
export const BUILTIN_COMMANDS: readonly CommandRow[] = [
  {
    name: "model",
    function: null,
    description: "Switch the session's model",
    usage: "",
    args: { kind: "none" },
    aliases: ["models"],
    during_run: false,
    order: 10,
    source: "client",
  },
  {
    name: "new",
    function: null,
    description: "Start a fresh conversation",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: false,
    order: 20,
    source: "client",
  },
  {
    name: "rename",
    function: null,
    description: "Rename this conversation",
    usage: "<title>",
    args: { kind: "text", field: "title" },
    aliases: [],
    during_run: true,
    order: 21,
    source: "client",
  },
  {
    name: "fork",
    function: null,
    description: "Fork this conversation and continue in the copy",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: false,
    order: 22,
    source: "client",
  },
  {
    name: "diff",
    function: null,
    description: "Review this session's changes (the review screen)",
    usage: "",
    args: { kind: "none" },
    aliases: ["review"],
    during_run: true,
    order: 23,
    source: "client",
  },
  {
    name: "status",
    function: null,
    description: "Session, model, tokens, workspace, goal",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 24,
    source: "client",
  },
  {
    name: "mcp",
    function: null,
    description: "Configured MCP servers and their tools",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 25,
    source: "client",
  },
  {
    name: "theme",
    function: null,
    description: "Pick a color theme (Omarchy palettes): open the theme panel",
    usage: "",
    args: { kind: "none" },
    aliases: ["themes"],
    during_run: true,
    order: 26.5,
    source: "client",
  },
  {
    name: "skills",
    function: null,
    description: "Skills available to this session: open the skills panel",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 26,
    source: "client",
  },
  {
    name: "copy",
    function: null,
    description: "Copy the last reply to the clipboard",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 27,
    source: "client",
  },
  {
    name: "export",
    function: null,
    description: "Save the conversation as markdown",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 28,
    source: "client",
  },
  {
    name: "pwd",
    function: null,
    description: "Show the workspace root",
    usage: "",
    args: { kind: "none" },
    aliases: ["cwd"],
    during_run: true,
    order: 29,
    source: "client",
  },
  {
    name: "jobs",
    function: null,
    description: "Background bash jobs: open the jobs panel (ctrl+b)",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 70,
    source: "client",
  },
  {
    name: "artifacts",
    function: null,
    description: "Artifacts the agent has shown: open the artifacts panel",
    usage: "",
    args: { kind: "none" },
    aliases: ["artifact"],
    during_run: true,
    order: 60,
    source: "client",
  },
  {
    name: "tasks",
    function: null,
    description: "The agent's task list: open the tasks panel",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 62,
    source: "client",
  },
  {
    name: "queue",
    function: null,
    description: "Messages waiting for the current run: open the queue panel",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 63,
    source: "client",
  },
  {
    name: "notifications",
    function: null,
    description: "Open the notifications panel (ctrl+n)",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 64,
    source: "client",
  },
  {
    name: "git",
    function: null,
    description:
      "Stage, commit, branches, worktrees: open the git panel (ctrl+g)",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 65,
    source: "client",
  },
  {
    name: "workers",
    function: null,
    description: "Running subagents: go to the agents screen (alt+3)",
    usage: "",
    args: { kind: "none" },
    aliases: ["agents"],
    during_run: true,
    order: 66,
    source: "client",
  },
  {
    name: "sessions",
    function: null,
    description: "Browse, search, fork or switch conversations (ctrl+p)",
    usage: "",
    args: { kind: "none" },
    aliases: ["resume"],
    during_run: true,
    order: 80,
    source: "client",
  },
  {
    name: "keys",
    function: null,
    description: "Every keyboard shortcut (? on an empty composer)",
    usage: "",
    args: { kind: "none" },
    aliases: ["shortcuts"],
    during_run: true,
    order: 81,
    source: "client",
  },
  {
    name: "approvals",
    function: null,
    description: "Who answers tool approvals — you, or the guardian",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 82,
    source: "client",
  },
  {
    name: "checks",
    function: null,
    description: "Who reviews plan-step completions against exit criteria",
    usage: "",
    args: { kind: "none" },
    aliases: [],
    during_run: true,
    order: 83,
    source: "client",
  },
  {
    name: "help",
    function: null,
    description: "Show this command palette",
    usage: "",
    args: { kind: "none" },
    aliases: ["?"],
    during_run: true,
    order: 90,
    source: "client",
  },
  {
    name: "exit",
    function: null,
    description: "Quit the TUI",
    usage: "",
    args: { kind: "none" },
    aliases: ["quit"],
    during_run: true,
    order: 99,
    source: "client",
  },
] as const;

/**
 * Merge the server catalog with the client builtins. A server row that
 * collides with a builtin name (or alias) is dropped: the builtin is
 * what `handleSlashCommand` runs. Sorted by declared order, then name.
 */
export function buildCommandRows(
  serverRows: readonly unknown[],
  builtins: readonly CommandRow[] = BUILTIN_COMMANDS,
): CommandRow[] {
  const reserved = new Set<string>();
  for (const builtin of builtins) {
    reserved.add(builtin.name);
    for (const alias of builtin.aliases) reserved.add(alias);
  }
  const server: CommandRow[] = [];
  for (const raw of serverRows) {
    const row = normalizeSlashCommand(raw);
    if (!row || reserved.has(row.name)) continue;
    server.push(row);
  }
  return [...builtins, ...server].sort(
    (a, b) => a.order - b.order || a.name.localeCompare(b.name),
  );
}

/**
 * Filter by the text typed after the slash, the way Codex does: an exact
 * name (or alias) first, then names that start with the text, each
 * group in catalog order. Descriptions are never matched — a query is
 * the start of a command name, not a search.
 */
export function filterCommands(
  rows: readonly CommandRow[],
  query: string,
): CommandRow[] {
  const needle = query.trim().toLowerCase().replace(/^\//, "");
  if (needle === "") return [...rows];
  const exact: CommandRow[] = [];
  const prefix: CommandRow[] = [];
  for (const row of rows) {
    const names = [row.name, ...row.aliases];
    if (names.includes(needle)) {
      exact.push(row);
    } else if (names.some((name) => name.startsWith(needle))) {
      prefix.push(row);
    }
  }
  return [...exact, ...prefix];
}

export const completionFor = slashCompletion;
export { takesArgs };
