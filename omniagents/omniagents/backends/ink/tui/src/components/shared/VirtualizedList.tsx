/**
 * Virtualized list using ink 7's ResizeObserver + StaticRender.
 *
 * Renders only items that fall inside the visible viewport (plus `overscan`
 * items on either side). Off-viewport items are replaced by a spacer Box of
 * the same height so the parent Scrollable still sees correct total height
 * and scrollbar geometry.
 *
 * Visible items are wrapped in <StaticRender> when `isStaticItem(item)` is
 * true — typically when the item is "frozen" (e.g. a completed message), so
 * its Yoga layout is cached and a re-render of the list doesn't re-run
 * layout on every visible item.
 *
 * Caller is responsible for:
 *   - Providing `containerHeight` (the height of the visible viewport in
 *     terminal rows).
 *   - Providing `scrollTop` (current scroll offset of the parent Scrollable).
 *   - Providing `width` (the inner width available for items; required by
 *     StaticRender to pre-compute layout).
 *
 * If `containerHeight` is 0 we cannot classify items by visibility; in that
 * case we render everything (no virtualization). This avoids the catastrophic
 * case of mis-classifying all items as "off-screen" and permanently freezing
 * them inside StaticRender before their first measurement.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";
import { Box, ResizeObserver, StaticRender, type DOMElement } from "ink";

export interface VirtualizedListProps<T> {
  data: ReadonlyArray<T>;
  renderItem: (item: T, index: number) => React.ReactNode;
  keyExtractor: (item: T, index: number) => string;
  /** Used for items whose heights haven't been measured yet. */
  estimatedItemHeight?: number;
  /** Visible viewport height in rows. */
  containerHeight: number;
  /** Current scroll offset of the parent Scrollable (rows from top of content). */
  scrollTop: number;
  /** Width in cells for StaticRender / Box layout. */
  width: number;
  /** Items above/below the visible range to keep rendered. Default 5. */
  overscan?: number;
  /**
   * Returns true if the item's rendered output is stable and can be cached
   * via <StaticRender>. Defaults to false (no caching).
   */
  isStaticItem?: (item: T, index: number) => boolean;
}

export function VirtualizedList<T>({
  data,
  renderItem,
  keyExtractor,
  estimatedItemHeight = 1,
  containerHeight,
  scrollTop,
  width,
  overscan = 5,
  isStaticItem,
}: VirtualizedListProps<T>) {
  // Measured heights keyed by the same key keyExtractor returns. Survives
  // re-renders so once an item scrolls off we still know how tall it was.
  const heights = useRef<Map<string, number>>(new Map()).current;
  const itemRefs = useRef<Map<string, DOMElement>>(new Map()).current;
  // Bump to force a re-render when measurements change. `measureVersion` is
  // also a dependency of the offsets memo below — `heights` is a mutated-in-
  // place Map, so a re-render alone would hand back stale cached offsets.
  const [measureVersion, forceUpdate] = useReducer((x: number) => x + 1, 0);

  // Drain pending measurement deltas in a microtask so a single layout pass
  // triggers a single re-render even if multiple items resize.
  const pendingMeasureRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleMeasureFlush = useCallback(() => {
    if (pendingMeasureRef.current !== null) return;
    pendingMeasureRef.current = setTimeout(() => {
      pendingMeasureRef.current = null;
      forceUpdate();
    }, 0);
  }, []);

  useEffect(() => {
    return () => {
      if (pendingMeasureRef.current !== null) {
        clearTimeout(pendingMeasureRef.current);
      }
    };
  }, []);

  // One observer for the whole list.
  const observer = useMemo(
    () =>
      new ResizeObserver((entries) => {
        let changed = false;
        for (const entry of entries) {
          // Reverse-lookup the key for this DOM node.
          for (const [key, node] of itemRefs) {
            if (node === entry.target) {
              const next = entry.contentRect.height;
              if (heights.get(key) !== next) {
                heights.set(key, next);
                changed = true;
              }
              break;
            }
          }
        }
        if (changed) scheduleMeasureFlush();
      }),
    // Observer lives for the lifetime of the list.
    [],
  );

  useEffect(() => () => observer.disconnect(), [observer]);

  // Build cumulative offsets using measured-or-estimated heights.
  const { offsets, totalHeight, itemHeights } = useMemo(() => {
    const off: number[] = new Array(data.length + 1);
    const heightsArr: number[] = new Array(data.length);
    off[0] = 0;
    for (let i = 0; i < data.length; i++) {
      const key = keyExtractor(data[i], i);
      const h = heights.get(key) ?? estimatedItemHeight;
      heightsArr[i] = h;
      off[i + 1] = off[i] + h;
    }
    return {
      offsets: off,
      totalHeight: off[data.length],
      itemHeights: heightsArr,
    };
    // Re-run when items change or any measured height changes. `measureVersion`
    // is what carries the latter: `heights` is mutated in place, so it can
    // never invalidate this memo on its own. Dropping it strands the offsets
    // at `estimatedItemHeight` for every row measured since the last `data`
    // change — which puts this list's coordinate space out of sync with the
    // parent Scrollable's measured `scrollTop` and blanks the viewport.
  }, [data, keyExtractor, estimatedItemHeight, measureVersion]);

  // Visible range. With containerHeight <= 0, fall back to rendering all
  // items (see top-of-file comment on the 0-height pitfall).
  const noViewport = containerHeight <= 0;
  const [firstIdx, lastIdx] = useMemo(() => {
    if (noViewport || data.length === 0) {
      return [0, data.length - 1] as const;
    }
    const top = Math.max(0, scrollTop - overscan);
    const bottom = scrollTop + containerHeight + overscan;
    // Binary search for first item whose end > top.
    let first = 0;
    {
      let lo = 0;
      let hi = data.length;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (offsets[mid + 1] > top) hi = mid;
        else lo = mid + 1;
      }
      // Clamp: a `scrollTop` past the end of the offset table (a transient
      // the parent Scrollable can produce while a tall row is still being
      // measured) would otherwise yield first > last, an empty slice, and a
      // viewport containing nothing but a spacer. Showing the last row is
      // always a better failure mode than showing blank.
      first = Math.min(lo, data.length - 1);
    }
    // Linear walk for last (cheap, fan-out is small).
    let last = first;
    for (let i = first; i < data.length; i++) {
      if (offsets[i] >= bottom) break;
      last = i;
    }
    return [first, Math.min(last, data.length - 1)] as const;
  }, [noViewport, data.length, scrollTop, containerHeight, overscan, offsets]);

  const setItemRef = useCallback(
    (key: string) => (node: DOMElement | null) => {
      const prev = itemRefs.get(key);
      if (prev && prev !== node) {
        observer.unobserve(prev);
        itemRefs.delete(key);
      }
      if (node) {
        itemRefs.set(key, node);
        observer.observe(node);
      }
    },
    [observer, itemRefs],
  );

  if (data.length === 0) return null;

  const headHeight = offsets[firstIdx];
  const tailHeight = totalHeight - offsets[lastIdx + 1];

  return (
    <Box flexDirection="column" width="100%">
      {headHeight > 0 && (
        <Box flexShrink={0} height={headHeight} width="100%" />
      )}
      {data.slice(firstIdx, lastIdx + 1).map((item, sliceIdx) => {
        const index = firstIdx + sliceIdx;
        const key = keyExtractor(item, index);
        const staticThisItem = isStaticItem?.(item, index) ?? false;
        const content = renderItem(item, index);

        return (
          <Box
            key={key}
            ref={setItemRef(key)}
            width="100%"
            flexDirection="column"
            flexShrink={0}
          >
            {staticThisItem ? (
              <StaticRender
                width={width}
                deps={[key, width, itemHeights[index]]}
              >
                {() => <>{content}</>}
              </StaticRender>
            ) : (
              content
            )}
          </Box>
        );
      })}
      {tailHeight > 0 && (
        <Box flexShrink={0} height={tailHeight} width="100%" />
      )}
    </Box>
  );
}
