// Pure helpers behind the session verbs (/status, /export, /copy, /mcp,
// /skills): text in, text out, so each is unit-testable without a render.

import type {
  GoalSnapshot,
  LoopTaskSnapshot,
  Message,
  UsageInfo,
} from "../store/appStore.js";

export type StatusFacts = {
  sessionId: string | null | undefined;
  model: string | null;
  usage: UsageInfo | null;
  goal: GoalSnapshot | null;
  loops: LoopTaskSnapshot[];
  workspaceRoot: string | null;
  connected: boolean;
};

const k = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/** The `/status` card: one fact per line, nothing the status bar does not already know. */
export function statusLines(facts: StatusFacts): string[] {
  const lines: string[] = [];
  lines.push(
    `session   ${facts.sessionId ?? "(none yet — starts on first send)"}`,
  );
  lines.push(`model     ${facts.model ?? facts.usage?.model ?? "(default)"}`);
  if (facts.usage) {
    const cap = facts.usage.maxInputTokens
      ? ` of ${k(facts.usage.maxInputTokens)} context`
      : "";
    lines.push(
      `tokens    ${k(facts.usage.totalTokens)} total · ${k(facts.usage.inputTokens)} in · ${k(facts.usage.outputTokens)} out${cap}`,
    );
  } else {
    lines.push("tokens    none yet");
  }
  lines.push(`workspace ${facts.workspaceRoot ?? "(no workspace)"}`);
  if (
    facts.goal &&
    (facts.goal.status === "active" || facts.goal.status === "paused")
  ) {
    lines.push(
      `goal      ${facts.goal.status} · turn ${facts.goal.turn}/${facts.goal.max_turns} · ${facts.goal.goal}`,
    );
  }
  const activeLoops = facts.loops.filter((l) => l.status === "active").length;
  if (activeLoops > 0) lines.push(`loops     ${activeLoops} active`);
  lines.push(`connection ${facts.connected ? "connected" : "disconnected"}`);
  return lines;
}

/** The last thing the assistant said, for `/copy`. */
export function lastAssistantText(messages: readonly Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]!;
    if (m.role === "assistant" && m.content.trim()) return m.content;
  }
  return null;
}

/** OSC 52: put text on the terminal's clipboard. Works in most modern terminals. */
export function osc52(text: string): string {
  return `]52;c;${Buffer.from(text, "utf8").toString("base64")}`;
}

/** The transcript as markdown, for `/export`. Tool calls become fenced blocks. */
export function transcriptToMarkdown(
  messages: readonly Message[],
  meta: { sessionId?: string | null; model?: string | null; exportedAt: Date },
): string {
  const out: string[] = [];
  out.push(`# Conversation${meta.sessionId ? ` ${meta.sessionId}` : ""}`);
  out.push("");
  out.push(
    `Exported ${meta.exportedAt.toISOString()}${meta.model ? ` · model ${meta.model}` : ""}`,
  );
  out.push("");
  for (const m of messages) {
    switch (m.role) {
      case "user":
        out.push("## User", "", m.content, "");
        break;
      case "assistant":
        out.push("## Assistant", "", m.content, "");
        break;
      case "tool_call": {
        const call = m.toolCall;
        out.push(`### Tool: ${call?.tool ?? "unknown"}`, "");
        if (call?.input) out.push("```json", call.input, "```", "");
        if (m.content) out.push("```", m.content, "```", "");
        break;
      }
      case "system":
        out.push(`> ${m.content}`, "");
        break;
      case "command":
        out.push(
          `### /${m.command ?? "command"}`,
          "",
          "```",
          m.content,
          "```",
          "",
        );
        break;
      default:
        if (m.content) out.push(m.content, "");
    }
  }
  return out.join("\n");
}

export function exportFileName(
  sessionId: string | null | undefined,
  now: Date,
): string {
  const stamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const id = (sessionId ?? "draft").slice(0, 8);
  return `omni-session-${id}-${stamp}.md`;
}

export function mcpLines(
  servers: ReadonlyArray<{
    server_name: string;
    enabled: boolean;
    status?: unknown;
    counts?: { tools?: number; resources?: number } | null;
    disabled_reason?: string;
  }>,
): string[] {
  if (servers.length === 0) return ["No MCP servers configured."];
  return servers.map((s) => {
    const status =
      typeof s.status === "string"
        ? s.status
        : ((s.status as { state?: string } | undefined)?.state ?? "");
    const tools = s.counts?.tools != null ? `${s.counts.tools} tools` : "";
    const why =
      !s.enabled && s.disabled_reason ? ` (${s.disabled_reason})` : "";
    return [
      `${s.server_name}`,
      s.enabled ? status || "enabled" : `disabled${why}`,
      tools,
    ]
      .filter(Boolean)
      .join(" · ");
  });
}
