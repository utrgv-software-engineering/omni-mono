/**
 * Priority-based keypress dispatch.
 *
 * Wraps ink's `useInput` once at the root and broadcasts to subscribers in
 * priority order. A handler returning `true` stops propagation, so a modal
 * dialog registered with `Critical` priority always wins Escape over a
 * background scroll handler registered with `Normal`.
 *
 * Within a single priority level, subscribers fire in LIFO order (the most
 * recently mounted handler gets first crack) — this matches the convention
 * that the topmost dialog/panel handles keys before its ancestors.
 *
 * Ported from gemini-cli's KeypressContext (the priority-subscription pattern
 * only — gemini-cli additionally re-parses raw stdin for paste / Kitty proto
 * support, which we do not yet do here).
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
} from "react";
import { useInput } from "ink";
import type { Key } from "../hooks/useKeypress.js";

export enum KeypressPriority {
  Low = -100,
  Normal = 0,
  High = 100,
  Critical = 200,
}

export type KeypressHandler = (input: string, key: Key) => boolean | void;

interface KeypressContextValue {
  subscribe: (handler: KeypressHandler, priority?: KeypressPriority) => void;
  unsubscribe: (handler: KeypressHandler) => void;
}

const KeypressContext = createContext<KeypressContextValue | null>(null);

export function useKeypressContext(): KeypressContextValue | null {
  return useContext(KeypressContext);
}

export function KeypressProvider({ children }: { children: React.ReactNode }) {
  // handler -> priority. Map preserves insertion order, which we use as the
  // tie-breaker (later-inserted handlers fire first within the same priority).
  const subscribers = useRef<Map<KeypressHandler, number>>(new Map()).current;
  const sortedPriorities = useRef<number[]>([]);
  const prioritiesDirty = useRef(false);

  const subscribe = useCallback(
    (
      handler: KeypressHandler,
      priority: KeypressPriority = KeypressPriority.Normal,
    ) => {
      const hadPriority = Array.from(subscribers.values()).includes(priority);
      // Re-insert so this handler is "newest" at its priority level.
      subscribers.delete(handler);
      subscribers.set(handler, priority);
      if (!hadPriority) {
        prioritiesDirty.current = true;
      }
    },
    [subscribers],
  );

  const unsubscribe = useCallback(
    (handler: KeypressHandler) => {
      const priority = subscribers.get(handler);
      if (priority === undefined) return;
      subscribers.delete(handler);
      const stillPresent = Array.from(subscribers.values()).includes(priority);
      if (!stillPresent) {
        prioritiesDirty.current = true;
      }
    },
    [subscribers],
  );

  useInput((input, rawKey) => {
    if (subscribers.size === 0) return;

    if (prioritiesDirty.current) {
      sortedPriorities.current = Array.from(new Set(subscribers.values())).sort(
        (a, b) => b - a,
      );
      prioritiesDirty.current = false;
    }

    const key = rawKey as Key;
    for (const priority of sortedPriorities.current) {
      // Collect handlers at this priority, newest-first.
      const handlers: KeypressHandler[] = [];
      for (const [h, p] of subscribers) {
        if (p === priority) handlers.push(h);
      }
      for (let i = handlers.length - 1; i >= 0; i--) {
        if (handlers[i](input, key) === true) return;
      }
    }
  });

  const value = useMemo(
    () => ({ subscribe, unsubscribe }),
    [subscribe, unsubscribe],
  );

  return (
    <KeypressContext.Provider value={value}>
      {children}
    </KeypressContext.Provider>
  );
}
