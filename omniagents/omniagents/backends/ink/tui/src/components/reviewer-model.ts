// Presentation logic for the two reviewer switches (`/approvals` and
// `/checks`), kept out of the components so it is unit-testable without
// an Ink render (same split as ./model-picker-model).
//
// Two INDEPENDENT switches, deliberately not one panel:
//
//   approvals — who answers a tool/MCP approval. `user` prompts the
//               human (the server default); `auto` routes to the
//               guardian, which may itself be skipped when the sandbox
//               tier says the environment is contained.
//   checks    — who reviews a plan-step completion against its exit
//               criteria. `guardian` is the server DEFAULT, so these
//               reviews are already running whether or not a client
//               exposes the switch; `off` waives them.
//
// Each switch is paired with the notification that records what its
// reviewer did (`tool_approval_reviewed` / `plan_completion_reviewed`).
// The pairing is the point: a toggle without the record hides what the
// guardian did, which is the exact state this TUI was in.

export type ApprovalsReviewer = "user" | "auto";
export type WorkflowReviewer = "off" | "guardian";

export type ReviewerOption<V extends string> = {
  value: V;
  label: string;
  hint: string;
};

/**
 * Labels are Omni Desktop's, verbatim (`ModelSessionControls.tsx`). A
 * user moving between the two clients should not have to learn that
 * "checks" and "review" are the same feature.
 */
export const APPROVALS_OPTIONS: readonly ReviewerOption<ApprovalsReviewer>[] = [
  {
    value: "user",
    label: "Ask me first",
    hint: "every tool call waits for you",
  },
  {
    value: "auto",
    label: "Approve for me",
    // Says what happens when the guardian cannot answer, in words a
    // person reading a switch can act on. The behavior is that a
    // timeout or an error returns None and falls through to the
    // ordinary prompt (guardian.py) — i.e. it never silently denies.
    hint: "the guardian decides; you are asked if it cannot",
  },
] as const;

export const CHECKS_OPTIONS: readonly ReviewerOption<WorkflowReviewer>[] = [
  {
    value: "guardian",
    label: "Task checks on",
    hint: "a finished step is checked against its exit criteria",
  },
  {
    value: "off",
    label: "Checks off",
    hint: "a step is done as soon as the agent says it is",
  },
] as const;

/** Server defaults, from approval_policy.py / workflow_policy.py. */
export const DEFAULT_APPROVALS_REVIEWER: ApprovalsReviewer = "user";
export const DEFAULT_WORKFLOW_REVIEWER: WorkflowReviewer = "guardian";

/** Config keys the layered store resolves these from. */
export const APPROVALS_CONFIG_KEY = "approvals.reviewer";
export const CHECKS_CONFIG_KEY = "workflow.completion_reviewer";

/** Where an effective reviewer value came from. */
export type ReviewerSource = "session" | "config" | "default";

export type EffectiveReviewer<V extends string> = {
  value: V;
  source: ReviewerSource;
};

/**
 * Resolve what is actually in force, mirroring the server's own
 * precedence (`effective_reviewer`): the per-session override wins, then
 * the layered-config value, then the built-in default.
 *
 * Resolved rather than assumed on purpose. Omni Desktop seeds its
 * dropdown with a hardcoded `useState('user')`, so a deployment whose
 * config sets `approvals.reviewer: auto` renders "Ask me first" while
 * the guardian is in fact answering — a switch that lies about its own
 * position. The value is readable (`list_models(session_id).session`
 * plus `get_config`), so there is no reason to guess it.
 */
export function resolveReviewer<V extends string>(
  sessionOverride: unknown,
  configValue: unknown,
  fallback: V,
  allowed: readonly V[],
): EffectiveReviewer<V> {
  if (isAllowed(sessionOverride, allowed)) {
    return { value: sessionOverride, source: "session" };
  }
  if (isAllowed(configValue, allowed)) {
    return { value: configValue, source: "config" };
  }
  return { value: fallback, source: "default" };
}

function isAllowed<V extends string>(
  value: unknown,
  allowed: readonly V[],
): value is V {
  return (
    typeof value === "string" && (allowed as readonly string[]).includes(value)
  );
}

/**
 * Pull one field's effective value out of a `get_config` document
 * (`{fields: [{key, value, is_set, …}]}`). Returns undefined when the
 * field is absent or unset, so the caller falls through to the default
 * rather than treating a missing field as a value.
 */
export function configFieldValue(
  config: unknown,
  key: string,
): unknown | undefined {
  const fields = (config as { fields?: unknown })?.fields;
  if (!Array.isArray(fields)) return undefined;
  for (const field of fields) {
    if (!field || typeof field !== "object") continue;
    const record = field as Record<string, unknown>;
    if (record.key !== key) continue;
    if (record.is_set === false) return undefined;
    return record.value;
  }
  return undefined;
}

/** Trailing note for the panel: where the current value comes from. */
export function sourceNote(source: ReviewerSource): string {
  if (source === "session") return "set for this conversation";
  if (source === "config") return "from your configuration";
  return "default";
}

// ---------------------------------------------------------------------
// Review records — the transcript chips
// ---------------------------------------------------------------------

export type GuardianReview = {
  callId: string;
  tool: string;
  reviewer: string;
  outcome: "allow" | "deny";
  kind?: "tool" | "mcp";
  serverLabel?: string;
  riskLevel?: string;
  rationale?: string;
};

export type WorkflowReview = {
  taskId: string;
  subject: string;
  reviewer: string;
  outcome: "reject" | "accept_unverified" | "escalated" | "accept_verified";
  rationale?: string;
};

/**
 * One line for a reviewed tool approval. Wording matches Desktop's
 * `guardianReviewSummary` so the same event reads the same in both.
 */
export function guardianReviewLine(review: GuardianReview): string {
  const reviewer =
    review.reviewer === "sandbox-policy" ? "sandbox policy" : review.reviewer;
  const tool = review.serverLabel
    ? `${review.serverLabel} · ${review.tool}`
    : review.tool;
  const verdict = review.outcome === "deny" ? "denied" : "approved";
  const base = `${tool} ${verdict} by ${reviewer}`;
  return review.rationale ? `${base} — ${review.rationale}` : base;
}

/** One line for a reviewed plan-step completion (Desktop's wording). */
export function workflowReviewLine(review: WorkflowReview): string {
  const step = `step #${review.taskId} '${review.subject}'`;
  const verdict =
    review.outcome === "reject"
      ? "completion rejected"
      : review.outcome === "accept_verified"
        ? "completion verified"
        : review.outcome === "accept_unverified"
          ? "completion accepted (unverified)"
          : "completion contested — accepted after repeated disagreement";
  const base = `${step} — ${verdict}`;
  // The rationale is the whole value of a rejection: without it the user
  // sees a step that will not stick and no reason why.
  return review.rationale ? `${base}: ${review.rationale}` : base;
}

/** Parse a `tool_approval_reviewed` payload, or null when unusable. */
export function parseGuardianReview(params: unknown): GuardianReview | null {
  const p = (params ?? {}) as Record<string, unknown>;
  const callId = typeof p.call_id === "string" ? p.call_id : "";
  const outcome =
    p.outcome === "deny" ? "deny" : p.outcome === "allow" ? "allow" : null;
  if (!callId || !outcome) return null;
  return {
    callId,
    tool: typeof p.tool_name === "string" ? p.tool_name : "tool",
    reviewer: typeof p.reviewer === "string" ? p.reviewer : "reviewer",
    outcome,
    kind: p.kind === "mcp" ? "mcp" : "tool",
    serverLabel:
      typeof p.server_label === "string" ? p.server_label : undefined,
    riskLevel: typeof p.risk_level === "string" ? p.risk_level : undefined,
    rationale: typeof p.rationale === "string" ? p.rationale : undefined,
  };
}

const WORKFLOW_OUTCOMES = [
  "reject",
  "accept_unverified",
  "escalated",
  "accept_verified",
] as const;

/** Parse a `plan_completion_reviewed` payload, or null when unusable. */
export function parseWorkflowReview(params: unknown): WorkflowReview | null {
  const p = (params ?? {}) as Record<string, unknown>;
  const taskId = typeof p.task_id === "string" ? p.task_id : "";
  const outcome = (WORKFLOW_OUTCOMES as readonly string[]).includes(
    p.outcome as string,
  )
    ? (p.outcome as WorkflowReview["outcome"])
    : null;
  if (!taskId || !outcome) return null;
  return {
    taskId,
    subject: typeof p.subject === "string" ? p.subject : "",
    reviewer: typeof p.reviewer === "string" ? p.reviewer : "reviewer",
    outcome,
    rationale: typeof p.rationale === "string" ? p.rationale : undefined,
  };
}
