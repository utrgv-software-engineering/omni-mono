// Presentation logic for the Agents screen. Pure and unit-testable
// (same split as ./review-model / ./strip-model).

import wrapAnsi from "wrap-ansi";
import type {
  ToolCallInfo,
  SubagentEvent,
  SubagentSummary,
} from "../store/appStore.js";
import { renderMarkdownCached } from "../utils/markdownCache.js";
import { runningLabel } from "./transcript-model.js";
import { Theme } from "../theme.js";

export type DetailLine = { text: string; color: string; bold?: boolean };

/**
 * Hard-wrap plain text at `width`, breaking on spaces where possible.
 * The detail pane scrolls over PHYSICAL rows, so wrapping must happen
 * here — Ink's own wrapping would make one logical line occupy several
 * rows and break the windowed slice math.
 */
export function wrapPlain(text: string, width: number): string[] {
  const safe = Math.max(8, width);
  const out: string[] = [];
  for (const raw of text.split("\n")) {
    let line = raw;
    while (line.length > safe) {
      let cut = line.lastIndexOf(" ", safe);
      if (cut <= 0) cut = safe;
      out.push(line.slice(0, cut));
      line = line.slice(cut).trimStart();
    }
    out.push(line);
  }
  return out;
}

/**
 * Wrap ANSI-styled text at visual `width` — escape codes carry no
 * width and style continues across the break. Same physical-row
 * contract as wrapPlain.
 */
export function wrapStyled(text: string, width: number): string[] {
  return wrapAnsi(text, Math.max(8, width), {
    hard: true,
    trim: false,
  }).split("\n");
}

/**
 * A running worker's live activity feed, built from the relayed
 * ui.subagent.event buffer. Tool calls render in the transcript's beat
 * language (present-tense verb + salient argument); assistant prose
 * renders wrapped; failed tool results surface in red. One entry per
 * event — the buffer is already ring-capped by the store.
 */
export function buildLiveActivity(
  events: SubagentEvent[],
  width: number,
  includeProse = true,
): DetailLine[] {
  const lines: DetailLine[] = [];
  for (const event of events) {
    const params = event.params ?? {};
    if (event.method === "tool_called") {
      const call: ToolCallInfo = {
        tool: String(params.tool ?? ""),
        input: String(params.input ?? ""),
        call_id: String(params.call_id ?? ""),
        running: true,
      };
      const label = runningLabel(call);
      const text = label.detail
        ? `${label.action} ${label.detail}`
        : label.action;
      lines.push({ text: `· ${text}`, color: Theme.textChrome });
    } else if (event.method === "tool_result") {
      const metadata = params.metadata as Record<string, unknown> | undefined;
      if (metadata && metadata.success === false) {
        lines.push({
          text: `✗ ${String(params.tool ?? "tool")} failed`,
          color: Theme.stateFailed,
        });
      }
    } else if (event.method === "message_output" && includeProse) {
      const content = String(params.content ?? "").trim();
      if (content) {
        for (const row of wrapPlain(content, width)) {
          lines.push({ text: row, color: Theme.textSecondary });
        }
      }
    }
  }
  return lines;
}

/**
 * The selected subagent's detail pane: the full task (wrapped, never
 * truncated), a quiet meta line, the activity transcript (what it did —
 * kept after the run settles, not just while live), then the result or
 * error. Results are agent prose, so they render as markdown (styled
 * via ANSI, wrapped by visual width); errors are tracebacks and stay
 * plain red.
 */
export function buildWorkerDetail(
  worker: SubagentSummary,
  width: number,
  events: SubagentEvent[] = [],
): DetailLine[] {
  const lines: DetailLine[] = [];
  for (const row of wrapPlain(worker.task.trim(), width)) {
    lines.push({ text: row, color: Theme.textPrimary, bold: true });
  }
  const meta: string[] = [worker.status];
  if (worker.name) meta.push(worker.name);
  if (worker.role) meta.push(worker.role);
  if (worker.recovered) meta.push("recovered");
  if (worker.kind === "agent_tool" && worker.agent) meta.push(worker.agent);
  if (worker.isolation) meta.push(worker.isolation);
  meta.push(worker.subagent_id);
  lines.push({ text: meta.join(" · "), color: Theme.textChrome });
  if (worker.recovery_reason && worker.recovery_reason !== worker.error) {
    for (const row of wrapPlain(worker.recovery_reason, width)) {
      lines.push({ text: row, color: Theme.stateBlocked });
    }
  }
  if (worker.report_status) {
    lines.push({
      text: `Parent notification: ${worker.report_status}`,
      color: Theme.textChrome,
    });
  }
  // Once a result/error exists, the sub-agent's prose lives there — the
  // feed keeps only the actions, so nothing reads twice.
  const activity = buildLiveActivity(
    events,
    width,
    !(worker.error || worker.result),
  );
  if (activity.length > 0) {
    lines.push({ text: "", color: Theme.textChrome });
    lines.push(...activity);
  }
  if (worker.error) {
    lines.push({ text: "", color: Theme.textChrome });
    for (const row of wrapPlain(worker.error.trim(), width)) {
      lines.push({ text: row, color: Theme.stateFailed });
    }
  } else if (worker.result) {
    lines.push({ text: "", color: Theme.textChrome });
    const rendered = renderMarkdownCached(worker.result.trim()).trimEnd();
    for (const row of wrapStyled(rendered, width)) {
      lines.push({ text: row, color: Theme.textSecondary });
    }
  } else if (worker.status === "running" && activity.length === 0) {
    lines.push({ text: "", color: Theme.textChrome });
    lines.push({
      text: "working — activity streams here",
      color: Theme.textChrome,
    });
  }
  return lines;
}
