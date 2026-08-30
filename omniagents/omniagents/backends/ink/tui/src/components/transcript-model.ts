/**
 * Semantic transcript projection ("beat renderer").
 *
 * The transcript renders agent activity at the verb level — "read 6
 * files", not six `read_file(path=…)` rows with previews. Pure functions
 * here; the rendering lives in MessageListOptimized. Rules:
 *
 * - consecutive completed tool calls form a SPAN; within a span,
 *   consecutive same-verb calls merge into counted segments, and the
 *   span renders as one dim line (`✎ edited 3 files · ⌕ read 2`);
 * - a prose/system message flushes the span;
 * - NOTHING breaks out of the span. A failed call, a cancelled one, a
 *   guardian denial: they are all still machinery, and a run that tries
 *   something, fails and retries is ordinary agent behaviour rather than
 *   an event the transcript should stop for. They fold in as toned
 *   counts — `Read 3 files · 1 failed` — and ctrl+o is where the detail
 *   lives. This mirrors Omni Desktop, whose activity chain counts
 *   failures and rejections into its collapsed header and keeps the
 *   per-call detail behind the expander;
 * - the currently-running call breaks out with its salient argument so
 *   live progress stays visible.
 */

import type { Message, ToolCallInfo } from "../store/appStore.js";

export type ActivityVerb =
  | "read"
  | "edit"
  | "search"
  | "command"
  | "web"
  | "worker"
  | "mcp"
  | "plan"
  | "other";

/** display_type is the server's own classification — strongest signal. */
const DISPLAY_TYPE_VERB: Record<string, ActivityVerb> = {
  file_content: "read",
  directory_listing: "read",
  diff: "edit",
  command: "command",
  search_results: "search",
  web_content: "web",
};

/** Item-model tool_kind fallback (store/conversation.ts toolKind). */
const KIND_VERB: Record<string, ActivityVerb> = {
  command: "command",
  file: "edit",
  search: "search",
  web: "web",
  mcp: "mcp",
  plan: "plan",
  artifact: "other",
  worker: "worker",
  other: "other",
};

const NAME_PATTERNS: Array<[RegExp, ActivityVerb]> = [
  [/read|\bcat\b|open|view|glob|list_dir|list_files|ls\b/, "read"],
  [
    /edit|write|create|patch|apply|append|mkdir|move|copy|rename|delete|remove/,
    "edit",
  ],
  [/grep|search|find|rg\b/, "search"],
  [/bash|shell|exec|command|run_/, "command"],
  [/web|fetch|browse|http|url|download/, "web"],
  [/worker|subagent|spawn/, "worker"],
  [/task_|plan/, "plan"],
];

export function classifyToolCall(toolCall: ToolCallInfo): ActivityVerb {
  const displayType = toolCall.metadata?.display_type;
  if (typeof displayType === "string" && DISPLAY_TYPE_VERB[displayType]) {
    return DISPLAY_TYPE_VERB[displayType];
  }
  const name = toolCall.tool.toLowerCase();
  for (const [pattern, verb] of NAME_PATTERNS) {
    if (pattern.test(name)) return verb;
  }
  if (toolCall.kind && KIND_VERB[toolCall.kind]) {
    return KIND_VERB[toolCall.kind];
  }
  return "other";
}

export function isErrorToolCall(toolCall: ToolCallInfo): boolean {
  const meta = toolCall.metadata;
  if (!meta) return false;
  if (meta.display_type === "error") return true;
  return (
    meta.display_type === "command" &&
    typeof meta.exit_code === "number" &&
    meta.exit_code !== 0
  );
}

/** Argument keys worth showing, most-salient first. */
const SALIENT_KEYS = [
  "path",
  "file_path",
  "filename",
  "command",
  "pattern",
  "query",
  "url",
  "branch",
  "name",
];

/** The one argument worth a row: a path, a command, a query. */
export function salientArg(input: string, max = 60): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  const record = parsed as Record<string, unknown>;
  let value: string | null = null;
  for (const key of SALIENT_KEYS) {
    if (typeof record[key] === "string" && (record[key] as string).trim()) {
      value = record[key] as string;
      break;
    }
  }
  if (value === null) {
    for (const candidate of Object.values(record)) {
      if (typeof candidate === "string" && candidate.trim()) {
        value = candidate;
        break;
      }
    }
  }
  if (value === null) return null;
  const oneLine = value.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

export type BeatSegment = { verb: ActivityVerb; count: number };

export type TranscriptRow =
  | { type: "message"; key: string; message: Message }
  | {
      type: "beats";
      key: string;
      segments: BeatSegment[];
      /** Calls still in flight — the span renders live while non-empty. */
      running: ToolCallInfo[];
      /**
       * Reviews that happened inside this span. Rendered as one trailing
       * count, never as rows: under "Approve for me" there is one per
       * tool call, and the whole point of the span is that a run's
       * machinery costs a line, not a line each.
       */
      reviewed: number;
      /** Calls that failed or were cancelled — toned, never hidden. */
      errors: number;
      /** Guardian denials and completion rejections. */
      rejections: number;
    };

const VERB_NOUN: Record<ActivityVerb, [string, string]> = {
  read: ["read %n file", "read %n files"],
  edit: ["edited %n file", "edited %n files"],
  search: ["%n search", "%n searches"],
  command: ["ran %n command", "ran %n commands"],
  web: ["%n web fetch", "%n web fetches"],
  worker: ["%n worker task", "%n worker tasks"],
  mcp: ["%n mcp call", "%n mcp calls"],
  plan: ["%n plan update", "%n plan updates"],
  other: ["%n tool call", "%n tool calls"],
};

/**
 * Every segment renders its full plain-language phrase — `read 2 files`.
 * No glyphs: the words carry it. Segments are per-verb totals (at most
 * one per verb), so there is no abbreviation tier; an abbreviated
 * grammar here once produced the literal string "· · calls 1" and is
 * not coming back.
 */
export function segmentText(segment: BeatSegment): string {
  const template =
    segment.count === 1
      ? VERB_NOUN[segment.verb][0]
      : VERB_NOUN[segment.verb][1];
  return template.replace("%n", String(segment.count));
}

/** `Read 2 files, edited 1 file, 1 search` — sentence case, comma-joined. */
export function beatsLine(segments: BeatSegment[]): string {
  const joined = segments.map((segment) => segmentText(segment)).join(", ");
  return joined.charAt(0).toUpperCase() + joined.slice(1);
}

const VERB_PRESENT: Record<ActivityVerb, string> = {
  read: "reading",
  edit: "editing",
  search: "searching",
  command: "running",
  web: "fetching",
  worker: "running worker",
  mcp: "calling",
  plan: "updating plan",
  other: "running",
};

/**
 * Present-tense label for a call in flight: `reading src/app.ts`,
 * `running npm test`, `searching "useMessages"`. For mcp/unclassified
 * calls the tool name IS the information, so it joins the detail.
 */
export function runningLabel(toolCall: ToolCallInfo): {
  action: string;
  detail: string | null;
} {
  const verb = classifyToolCall(toolCall);
  const arg = salientArg(toolCall.input);
  if (verb === "mcp" || verb === "other") {
    return {
      action: VERB_PRESENT[verb],
      detail: arg ? `${toolCall.tool} ${arg}` : toolCall.tool,
    };
  }
  return { action: VERB_PRESENT[verb], detail: arg };
}

/**
 * The whole span as one sentence. Live spans lead with what's happening
 * now appended to what already landed:
 *   `Reading src/app.ts…`                        (nothing landed yet)
 *   `Read 2 files, 1 search · reading src/app.ts…`
 *   `Read 2 files · running 3 tools…`            (parallel calls)
 * Settled spans are just the past-tense summary.
 */
export function spanLine(
  segments: BeatSegment[],
  running: ToolCallInfo[],
  reviewed = 0,
): string {
  const reviewNote = reviewedSuffix(reviewed);
  const withReview = (text: string) =>
    reviewNote ? (text ? `${text} · ${reviewNote}` : reviewNote) : text;
  const past = segments.length > 0 ? beatsLine(segments) : "";
  if (running.length === 0) {
    const settled = withReview(past);
    return settled.charAt(0).toUpperCase() + settled.slice(1);
  }
  let live: string;
  if (running.length === 1) {
    const label = runningLabel(running[0]!);
    live = label.detail
      ? `${label.action} ${label.detail}…`
      : `${label.action}…`;
  } else {
    live = `running ${running.length} tools…`;
  }
  const head = withReview(past);
  if (!head) return live.charAt(0).toUpperCase() + live.slice(1);
  return `${head} · ${live}`;
}

/** `3 reviewed` — the guardian's ordinary work, as one token. */
export function reviewedSuffix(reviewed: number): string {
  if (reviewed <= 0) return "";
  return `${reviewed} reviewed`;
}

/**
 * The toned half of a span line: what went wrong inside it. Kept
 * separate from `spanLine` because it renders in a different colour —
 * the same split Desktop draws between its group summary and its
 * `formatGroupFailures`.
 */
export function failureNote(errors: number, rejections: number): string {
  const parts: string[] = [];
  if (errors > 0) parts.push(`${errors} failed`);
  if (rejections > 0) parts.push(`${rejections} rejected`);
  return parts.join(" · ");
}

function toolCallKey(message: Message, index: number): string {
  return message.toolCall?.call_id || `tool-${index}`;
}

/**
 * Messages that render zero rows in every mode. They must be filtered
 * BEFORE row construction — a row wrapper around a null body would still
 * paint its spacing as a stray blank line.
 */
export function rendersNothing(message: Message): boolean {
  if (message.role === "approval_request") return true;
  if (
    message.role === "tool_call" &&
    message.toolCall?.metadata?.hidden === true
  ) {
    return true;
  }
  if (message.role === "system" && !(message.content ?? "").trim()) return true;
  return false;
}

/**
 * Fold a message list into transcript rows. Pure; called per render from
 * a useMemo, so it must stay cheap (single pass, no parsing of outputs).
 */
export function groupTranscript(messages: Message[]): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  // Per-verb TOTALS for the span, in first-appearance order — NOT
  // run-length encoding. Interleaved work (read, run, read, read) is the
  // normal case; encoding runs fragments it into count-1 stutter.
  let spanCounts = new Map<ActivityVerb, number>();
  // In-flight calls stay part of the span: the whole span renders as one
  // live line (`⠋ …`) while any call is running, and settles into the
  // past-tense summary when the last one lands.
  let spanRunning: ToolCallInfo[] = [];
  let spanKey: string | null = null;
  let spanReviewed = 0;
  let spanErrors = 0;
  let spanRejections = 0;

  const flush = () => {
    const hasContent =
      spanCounts.size > 0 ||
      spanRunning.length > 0 ||
      spanReviewed > 0 ||
      spanErrors > 0 ||
      spanRejections > 0;
    if (hasContent && spanKey !== null) {
      rows.push({
        type: "beats",
        key: `beats-${spanKey}`,
        segments: [...spanCounts.entries()].map(([verb, count]) => ({
          verb,
          count,
        })),
        running: spanRunning,
        reviewed: spanReviewed,
        errors: spanErrors,
        rejections: spanRejections,
      });
    }
    spanCounts = new Map();
    spanRunning = [];
    spanReviewed = 0;
    spanErrors = 0;
    spanRejections = 0;
    spanKey = null;
  };

  messages.forEach((message, index) => {
    if (rendersNothing(message)) return;

    if (message.role === "review" && message.review) {
      // Never flushes: a verdict belongs to the same beat as the call it
      // judged, whichever way it went.
      if (message.review.blocking) {
        spanRejections += 1;
      } else {
        spanReviewed += 1;
      }
      spanKey ??= `review-${index}`;
      return;
    }

    if (message.role !== "tool_call" || !message.toolCall) {
      flush();
      rows.push({
        type: "message",
        key: `msg-${message.role}-${index}`,
        message,
      });
      return;
    }
    const toolCall = message.toolCall;

    if (isErrorToolCall(toolCall) || toolCall.output === "[Cancelled]") {
      // Counted, not broken out. The failure still shows — toned, in the
      // span line — but a retry loop no longer costs three rows a try.
      spanErrors += 1;
      spanKey ??= toolCallKey(message, index);
      return;
    }
    if (spanKey === null) spanKey = toolCallKey(message, index);
    if (toolCall.running) {
      spanRunning.push(toolCall);
      return;
    }
    const verb = classifyToolCall(toolCall);
    spanCounts.set(verb, (spanCounts.get(verb) ?? 0) + 1);
  });
  flush();
  return rows;
}
