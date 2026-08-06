/**
 * Hook for handling keyboard input.
 *
 * If a <KeypressProvider> is mounted upstream, the handler subscribes to the
 * priority-based dispatch context. Otherwise it falls back to ink's `useInput`
 * directly so the hook is safe to use in tests or in isolated component trees.
 */

import { useEffect, useRef } from "react";
import { useInput } from "ink";
import {
  useKeypressContext,
  type KeypressPriority,
} from "../contexts/KeypressContext.js";

export interface Key {
  upArrow: boolean;
  downArrow: boolean;
  leftArrow: boolean;
  rightArrow: boolean;
  pageDown: boolean;
  pageUp: boolean;
  return: boolean;
  escape: boolean;
  ctrl: boolean;
  shift: boolean;
  tab: boolean;
  backspace: boolean;
  delete: boolean;
  meta: boolean;
  name?: string;
}

export interface UseKeypressOptions {
  isActive: boolean;
  /**
   * Dispatch priority when subscribed via <KeypressProvider>. Ignored in the
   * fallback (no-provider) path. A handler returning `true` from `onKeypress`
   * stops propagation to lower-priority handlers.
   */
  priority?: KeypressPriority;
}

/**
 * A hook that listens for keypress events.
 *
 * The callback receives `(input, key)` matching ink's `useInput` signature:
 * `input` is the raw character (e.g. "x", "1"); `key` carries modifier flags
 * and special-key booleans.
 *
 * Returning `true` from the callback stops propagation to lower-priority
 * subscribers when running under <KeypressProvider>. Returning `void` (or
 * any falsy value) lets the event continue propagating.
 *
 * @param onKeypress - The callback function to execute on each keypress.
 * @param options - `isActive` toggles the subscription; `priority` controls
 *   dispatch order when running under <KeypressProvider>.
 */
export function useKeypress(
  onKeypress: (input: string, key: Key) => boolean | void,
  { isActive, priority }: UseKeypressOptions,
) {
  const ctx = useKeypressContext();

  // The live callback rides in a ref so the subscription survives
  // re-renders. Subscribing per-render (the old shape, with onKeypress in
  // the effect deps) re-inserted the handler at the END of the dispatch
  // map on every render — same-priority ordering became "whoever
  // re-rendered last", i.e. nondeterministic. With the ref, subscription
  // order is mount order, stable for the component's lifetime.
  const handlerRef = useRef(onKeypress);
  handlerRef.current = onKeypress;

  useEffect(() => {
    if (!ctx || !isActive) return;
    const handler = (input: string, key: Key) =>
      handlerRef.current(input, key);
    ctx.subscribe(handler, priority);
    return () => {
      ctx.unsubscribe(handler);
    };
  }, [ctx, isActive, priority]);

  // Fallback path when no provider is mounted (tests, partial trees).
  // Re-uses ink's useInput which is a no-op when isActive is false.
  useInput(
    (input, key) => {
      onKeypress(input, key as Key);
    },
    { isActive: !ctx && isActive },
  );
}
