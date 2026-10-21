import { useCallback, useRef, useState, type SetStateAction } from "react";

/** Keyboard bursts must observe earlier keys before React commits the next frame. */
export function useImmediateState<T>(initial: T) {
  const [value, setValue] = useState(initial);
  const ref = useRef(initial);
  const set = useCallback((action: SetStateAction<T>) => {
    const next =
      typeof action === "function"
        ? (action as (value: T) => T)(ref.current)
        : action;
    ref.current = next;
    setValue(next);
  }, []);
  return [value, set, ref] as const;
}
