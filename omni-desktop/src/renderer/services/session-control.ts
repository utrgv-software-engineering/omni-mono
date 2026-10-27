/**
 * Cross-session control registry: each open column registers its
 * `SessionController` here keyed by its `tabId`, and the `column_*` client
 * tools resolve a `tab_id` to it.
 */

import type { SessionController } from '@omniagents/chat-ui/session-controller';

const registry = new Map<string, SessionController>();

/** Register a column's controller. Returns an unregister fn for cleanup. */
export function registerSessionController(tabId: string, controller: SessionController): () => void {
  registry.set(tabId, controller);
  return () => {
    if (registry.get(tabId) === controller) {
      registry.delete(tabId);
    }
  };
}

/** Look up a column's controller by tabId. */
export function getSessionController(tabId: string): SessionController | undefined {
  return registry.get(tabId);
}

// ─── Column run-end events ──────────────────────────────────────────────────
// A push channel so consumers (the orchestrator watcher) can react the moment a
// column's run finishes, instead of polling `list_workspace`. The column's App
// emits via `emitColumnRunEnd` (threaded through `onRunEnd`); the launcher fans
// out to listeners.

export type ColumnRunEndInfo = { runId?: string; reason?: string };
type RunEndListener = (tabId: string, info: ColumnRunEndInfo) => void;
type RunStartedListener = (tabId: string, runId: string) => void;

const runEndListeners = new Set<RunEndListener>();
const runStartedListeners = new Set<RunStartedListener>();

/** Subscribe to column run-end events. Returns an unsubscribe fn. */
export function onColumnRunEnd(listener: RunEndListener): () => void {
  runEndListeners.add(listener);
  return () => {
    runEndListeners.delete(listener);
  };
}

/** Emit a column run-end event to all listeners (best-effort; never throws). */
export function emitColumnRunEnd(tabId: string, info: ColumnRunEndInfo): void {
  for (const listener of runEndListeners) {
    try {
      listener(tabId, info);
    } catch {
      /* a misbehaving listener must not break the emit */
    }
  }
}

/** Emit a column run-started event to all listeners (best-effort; never throws). */
export function emitColumnRunStarted(tabId: string, runId: string): void {
  for (const listener of runStartedListeners) {
    try {
      listener(tabId, runId);
    } catch {
      /* a misbehaving listener must not break the emit */
    }
  }
}
