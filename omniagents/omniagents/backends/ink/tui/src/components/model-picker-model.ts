// Presentation logic for the model picker, kept out of the component so it
// is unit-testable without an Ink render (same split as ./task-model).

import type {
  ListModelsResult,
  ModelDescriptor,
} from "@omniagents/client/rpc/model-catalog";

export type ModelRow = {
  id: string;
  label: string;
  provider: string | null;
  active: boolean;
  isDefault: boolean;
  available: boolean;
  /** First blocking/informational note: availability reason, deprecation. */
  note: string | null;
  /** Reasoning efforts this model accepts (empty = none). */
  reasoningOptions: string[];
};

/**
 * Project a list_models result onto picker rows. Hidden models stay
 * hidden; order is the server's (provider-grouped). The active model is
 * resolved session-first, then the client's last-known session model,
 * then the catalog default.
 */
export function buildModelRows(
  result: ListModelsResult,
  fallbackActive: string | null,
): ModelRow[] {
  const active =
    result.session?.active_model ?? fallbackActive ?? result.default_model;
  return result.models.filter((m) => !m.hidden).map((m) => row(m, active));
}

function row(m: ModelDescriptor, active: string | null): ModelRow {
  const note = m.deprecation.deprecated
    ? (m.deprecation.message ?? "deprecated")
    : (m.availability.reasons[0]?.message ?? null);
  return {
    id: m.id,
    label: m.label,
    provider: m.provider.name,
    active: active !== null && (m.id === active || m.model === active),
    isDefault: m.is_default,
    available: m.availability.available,
    note,
    reasoningOptions: m.reasoning.options,
  };
}

/** Index the cursor should start on: the active row, else the first. */
export function initialIndex(rows: ModelRow[]): number {
  const idx = rows.findIndex((r) => r.active);
  return idx >= 0 ? idx : 0;
}

/** Window [start, start+size) that keeps `index` visible with context. */
export function windowStart(
  index: number,
  total: number,
  size: number,
): number {
  return Math.max(0, Math.min(index - Math.floor(size / 2), total - size));
}
