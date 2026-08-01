/**
 * Canonical conversation domain objects (tkt_a-QF2Oc4OAsD).
 *
 * Mirrors the server's `omniagents/core/conversation/model.py` wire shapes.
 * `content` stays an open record on purpose: its fields follow the item
 * kind and grow with the tools, so pinning a closed schema here would turn
 * every tool change into a protocol break.
 */

export type ConversationItemKind =
  | "user_message"
  | "agent_message"
  | "reasoning"
  | "tool_call"
  | "approval"
  | "elicitation"
  | "artifact"
  | "compaction"
  /** Durable plan / run-level aggregated diff — see protocol.md
   * § Durable Structured Plans & Aggregated Run Diffs. */
  | "plan"
  | "run_diff";

/** Item lifecycle. Exactly one terminal state is ever reached. */
export type ConversationItemStatus =
  | "started"
  | "completed"
  | "failed"
  | "cancelled";

export type ConversationTurnStatus =
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ConversationToolKind =
  | "command"
  | "file"
  | "search"
  | "web"
  | "mcp"
  | "plan"
  | "artifact"
  | "worker"
  | "other";

/** `recorded` = written live by the runtime; `adapter` = projected from
 * legacy history by the compatibility adapter. */
export type ConversationSource = "recorded" | "adapter";

export interface ConversationThread {
  thread_id: string;
  user_id: string | null;
  status: "active" | "archived";
  title: string | null;
  created_at: number;
  updated_at: number;
  last_seq: number;
  turn_count: number;
  item_count: number;
  parent_thread_id: string | null;
  branched_from_item_id: string | null;
  usage: Record<string, unknown>;
  compaction: Record<string, unknown>;
  source: ConversationSource;
  /** When pre-existing history was materialized; null = not yet settled. */
  projected_at: number | null;
  schema_version: number;
}

export interface ConversationTurn {
  turn_id: string;
  thread_id: string;
  ordinal: number;
  status: ConversationTurnStatus;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  prompt: string | null;
  prompt_role: string | null;
  end_reason: string | null;
  error: Record<string, unknown> | null;
  usage: Record<string, unknown>;
  model: string | null;
  model_ref: string | null;
  item_count: number;
  first_seq: number | null;
  last_seq: number | null;
  attempts: number;
  source: ConversationSource;
  schema_version: number;
}

export interface ConversationItem {
  item_id: string;
  thread_id: string;
  turn_id: string | null;
  /** Thread-monotonic ordinal. The ONLY ordering clients should rely on. */
  seq: number;
  kind: ConversationItemKind;
  status: ConversationItemStatus;
  role: string | null;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  revision: number;
  content: Record<string, unknown>;
  source_ref: Record<string, unknown>;
  /** Exempt from run-end finalization; the owning subsystem lands it. */
  long_lived: boolean;
  source: ConversationSource;
  schema_version: number;
}

export interface ConversationTurnPage {
  thread_id: string;
  turns: ConversationTurn[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
}

export interface ConversationItemPage {
  thread_id: string;
  turn_id: string | null;
  items: ConversationItem[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
}

// ----- Durable plans & aggregated run diffs (experimental) -----
// Convenience projections of the same `plan` / `run_diff` items `listItems`
// returns: the server owns the scope and generation rules, so "what is the
// plan NOW" is answered once there rather than re-derived in every client.

/** Closed set. `blocked` is set by the agent, never inferred from edges. */
export type PlanStepStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "blocked";

export type PlanStep = {
  id: string;
  subject: string;
  description: string;
  active_form: string;
  status: PlanStepStatus;
  owner: string;
  blocks: string[];
  blocked_by: string[];
};

export type ConversationPlan = {
  plan_id: string;
  item_id: string;
  thread_id: string;
  turn_id: string | null;
  scope: string;
  generation: number;
  steps: PlanStep[];
  counts: Record<PlanStepStatus, number>;
  status: ConversationItemStatus;
  /** Why a terminal plan landed (`thread_archived`); null while live. */
  finalized_by: string | null;
  revision: number;
  updated_at: number;
};

export type GetPlanResult = {
  thread_id: string;
  scope: string;
  /** `null` when the conversation has never planned — not an error. */
  plan: ConversationPlan | null;
  /** Every plan the thread has held, oldest first, across scopes. */
  plans: ConversationPlan[];
};

export type RunDiffFile = {
  path: string;
  change_type: "added" | "modified" | "deleted";
  additions: number;
  deletions: number;
  /** Binary or oversize: reported, but carries no hunks. */
  opaque: boolean;
  /** The run's baseline for this path was never observed (the observer
   * bound mid-run, or the pre-image read failed). Neither creation nor
   * deletion is asserted and no hunks are carried. */
  baseline_unknown: boolean;
};

export type ConversationRunDiff = {
  item_id: string;
  run_id: string;
  /** One unified diff covering every changed path. Truncated on a FILE
   * boundary when it exceeds the server cap, so it always parses; `files`
   * and `stats` stay complete. */
  diff: string;
  files: RunDiffFile[];
  stats: { files_changed: number; additions: number; deletions: number };
  /** The rendered `diff` hit the size ceiling. `files` and `stats` did not. */
  truncated: boolean;
  /** The `files` list itself hit its ceiling (500 entries); `stats` still
   * reports the true totals. */
  files_truncated: boolean;
  status: ConversationItemStatus;
  revision: number;
  updated_at: number;
};

export type GetRunDiffResult = {
  thread_id: string;
  turn_id: string | null;
  /** `null` when the turn changed no files — not an error. */
  run_diff: ConversationRunDiff | null;
};
