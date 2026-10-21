import { useLayoutEffect, useRef } from "react";
import { useImmediateState } from "./useImmediateState.js";

/** Follow an item by identity through sorting/refreshes, including same-frame navigation. */
export function useKeyedSelection<T>(
  items: readonly T[],
  key: (item: T) => string,
) {
  const [selected, setSelected, selectedRef] = useImmediateState<string | null>(
    null,
  );
  const committed = useRef({ items, key });
  useLayoutEffect(() => {
    committed.current = { items, key };
  });
  // Where the selected item last sat. When it leaves the list (deleted,
  // archived, filtered out) the cursor stays put — clamped to the new
  // length — instead of jumping back to the top.
  const lastIndex = useRef(0);
  const indexFor = (
    list: readonly T[],
    getKey: (item: T) => string,
    id: string | null,
  ) => {
    if (id === null) return 0;
    const found = list.findIndex((item) => getKey(item) === id);
    if (found >= 0) return found;
    return Math.max(0, Math.min(list.length - 1, lastIndex.current));
  };
  const index = indexFor(items, key, selected);
  // Remember the initially displayed item too, before the first navigation.
  useLayoutEffect(() => {
    lastIndex.current = index;
    const id = items[index] ? key(items[index]) : null;
    if (selectedRef.current !== id) setSelected(id);
  }, [items, index, key, selectedRef, setSelected]);
  return {
    index,
    current: () => {
      const { items: list, key: getKey } = committed.current;
      return list[indexFor(list, getKey, selectedRef.current)];
    },
    move: (delta: number) => {
      const { items: list, key: getKey } = committed.current;
      const next = Math.max(
        0,
        Math.min(
          list.length - 1,
          indexFor(list, getKey, selectedRef.current) + delta,
        ),
      );
      lastIndex.current = next;
      setSelected(list[next] ? getKey(list[next]) : null);
      return next;
    },
    reset: () => {
      lastIndex.current = 0;
      setSelected(null);
    },
  };
}
