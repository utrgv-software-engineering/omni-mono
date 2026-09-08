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
  const indexFor = (
    list: readonly T[],
    getKey: (item: T) => string,
    id: string | null,
  ) =>
    Math.max(
      0,
      list.findIndex((item) => getKey(item) === id),
    );
  const index = indexFor(items, key, selected);
  // Remember the initially displayed item too, before the first navigation.
  useLayoutEffect(() => {
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
      setSelected(list[next] ? getKey(list[next]) : null);
      return next;
    },
    reset: () => setSelected(null),
  };
}
