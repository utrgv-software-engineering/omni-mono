/**
 * Scroll provider for managing scrollable regions and mouse wheel events
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { getBoundingBox, type DOMElement } from "ink";
import { useMouse, type MouseEvent } from "../hooks/useMouse.js";

export interface ScrollState {
  scrollTop: number;
  scrollHeight: number;
  innerHeight: number;
}

export interface ScrollableEntry {
  id: string;
  ref: React.RefObject<DOMElement>;
  getScrollState: () => ScrollState;
  scrollBy: (delta: number) => void;
  hasFocus: () => boolean;
  flashScrollbar: () => void;
}

interface ScrollContextType {
  register: (entry: ScrollableEntry) => void;
  unregister: (id: string) => void;
}

const ScrollContext = createContext<ScrollContextType | null>(null);

const findScrollableCandidates = (
  mouseEvent: MouseEvent,
  scrollables: Map<string, ScrollableEntry>,
) => {
  const candidates: Array<ScrollableEntry & { area: number }> = [];

  for (const entry of scrollables.values()) {
    if (!entry.ref.current || !entry.hasFocus()) {
      continue;
    }

    const boundingBox = getBoundingBox(entry.ref.current);
    if (!boundingBox) continue;

    const { x, y, width, height } = boundingBox;

    const isInside =
      mouseEvent.col >= x &&
      mouseEvent.col < x + width + 1 && // Intentionally add one to width to include scrollbar.
      mouseEvent.row >= y &&
      mouseEvent.row < y + height;

    if (isInside) {
      candidates.push({ ...entry, area: width * height });
    }
  }

  // Sort by smallest area first (innermost element)
  candidates.sort((a, b) => a.area - b.area);
  return candidates;
};

export const ScrollProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const scrollablesRef = useRef(new Map<string, ScrollableEntry>());

  const register = useCallback((entry: ScrollableEntry) => {
    scrollablesRef.current.set(entry.id, entry);
  }, []);

  const unregister = useCallback((id: string) => {
    scrollablesRef.current.delete(id);
  }, []);

  const handleScroll = (direction: "up" | "down", mouseEvent: MouseEvent) => {
    const delta = direction === "up" ? -1 : 1;
    let candidates = findScrollableCandidates(
      mouseEvent,
      scrollablesRef.current,
    );

    if (candidates.length === 0) {
      const fallback: Array<ScrollableEntry & { area: number }> = [];
      for (const entry of scrollablesRef.current.values()) {
        if (!entry.ref.current || !entry.hasFocus()) continue;
        const bbox = getBoundingBox(entry.ref.current);
        if (!bbox) continue;
        const area = bbox.width * bbox.height;
        fallback.push({ ...entry, area });
      }
      fallback.sort((a, b) => b.area - a.area);
      candidates = fallback;
    }

    for (const candidate of candidates) {
      const { scrollTop, scrollHeight, innerHeight } =
        candidate.getScrollState();

      // Epsilon to handle floating point inaccuracies.
      const canScrollUp = scrollTop > 0.001;
      const canScrollDown = scrollTop < scrollHeight - innerHeight - 0.001;

      if (direction === "up" && canScrollUp) {
        candidate.scrollBy(delta);
        return;
      }

      if (direction === "down" && canScrollDown) {
        candidate.scrollBy(delta);
        return;
      }
    }

  };

  const handleClick = (mouseEvent: MouseEvent) => {
    const candidates = findScrollableCandidates(
      mouseEvent,
      scrollablesRef.current,
    );

    if (candidates.length > 0) {
      // The first candidate is the innermost one.
      candidates[0].flashScrollbar();
    }
  };

  useMouse(
    (event: MouseEvent) => {
      if (event.name === "scroll-up") {
        handleScroll("up", event);
      } else if (event.name === "scroll-down") {
        handleScroll("down", event);
      } else if (event.name === "left-press") {
        handleClick(event);
      }
    },
    { isActive: true },
  );

  const contextValue = useMemo(
    () => ({ register, unregister }),
    [register, unregister],
  );

  return (
    <ScrollContext.Provider value={contextValue}>
      {children}
    </ScrollContext.Provider>
  );
};

let nextId = 0;

export const useScrollable = (
  entry: Omit<ScrollableEntry, "id">,
  isActive: boolean,
) => {
  const context = useContext(ScrollContext);
  if (!context) {
    throw new Error("useScrollable must be used within a ScrollProvider");
  }

  const [id] = useState(() => `scrollable-${nextId++}`);
  const entryRef = useRef(entry);

  // Keep entry ref up to date (runs on every render to keep ref synchronized)
  // This doesn't cause re-renders since it only updates a ref
  useEffect(() => {
    entryRef.current = entry;
  }, [entry]);

  useEffect(() => {
    if (isActive) {
      // Always register with latest entry
      context.register({ ...entryRef.current, id });
      return () => {
        context.unregister(id);
      };
    }
    return;
  }, [context, id, isActive]);
};
