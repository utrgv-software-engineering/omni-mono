/**
 * Scrollable container component with keyboard and mouse support
 */

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useLayoutEffect,
  useCallback,
  useMemo,
} from "react";
import { Box, getInnerHeight, getScrollHeight, type DOMElement } from "ink";
import { useKeypress, type Key } from "../hooks/useKeypress.js";
import { useScrollable } from "../contexts/ScrollProvider.js";
import { useAnimatedScrollbar } from "../hooks/useAnimatedScrollbar.js";

/**
 * Surfaces the enclosing Scrollable's live `scrollTop` and `innerHeight` to
 * descendants. Components like VirtualizedList read this to decide which
 * items fall inside the viewport without needing to thread scroll geometry
 * through props.
 */
export interface ScrollViewport {
  scrollTop: number;
  innerHeight: number;
}

const ScrollViewportContext = createContext<ScrollViewport | null>(null);

export function useScrollViewport(): ScrollViewport | null {
  return useContext(ScrollViewportContext);
}

interface ScrollableProps {
  children?: React.ReactNode;
  width?: number;
  height?: number | string;
  maxWidth?: number;
  maxHeight?: number;
  hasFocus: boolean;
  scrollToBottom?: boolean;
  flexGrow?: number;
  contentVersion?: number;
}

export const Scrollable: React.FC<ScrollableProps> = ({
  children,
  width,
  height,
  maxWidth,
  maxHeight,
  hasFocus,
  scrollToBottom,
  flexGrow,
  contentVersion,
}) => {
  const [scrollTop, setScrollTop] = useState(0);
  const ref = useRef<DOMElement>(null);
  const [size, setSize] = useState({
    innerHeight: 0,
    scrollHeight: 0,
  });
  const sizeRef = useRef(size);
  useEffect(() => {
    sizeRef.current = size;
  }, [size]);

  const previousSizeRef = useRef({ innerHeight: 0, scrollHeight: 0 });
  const pinToBottomRef = useRef(true);

  // This effect needs to run on every render to correctly measure the container
  // and scroll to the bottom if new children are added. The if conditions
  // prevent infinite loops.
  useLayoutEffect(() => {
    if (!ref.current) {
      return;
    }
    const innerHeight = getInnerHeight(ref.current);
    const scrollHeight = getScrollHeight(ref.current);

    const EPS = 0.001;
    const prev = previousSizeRef.current;
    const dimsChanged =
      Math.abs(prev.innerHeight - innerHeight) > EPS ||
      Math.abs(prev.scrollHeight - scrollHeight) > EPS;

    const contentGrew = scrollHeight - prev.scrollHeight > EPS;
    const containerShrank = prev.innerHeight - innerHeight > EPS;

    if (dimsChanged) {
      previousSizeRef.current = { innerHeight, scrollHeight };
      setSize({ innerHeight, scrollHeight });
    }

    // Only snap to bottom while the user is pinned there. scrollBy
    // clears the pin when the user scrolls up via mouse wheel or
    // shift+arrow so streamed events don't yank them back; once they
    // return to bottom the pin re-engages.
    if ((contentGrew || containerShrank) && pinToBottomRef.current) {
      const bottom = Math.max(0, scrollHeight - innerHeight);
      setScrollTop(bottom);
    }
  });

  const scrollBy = useCallback(
    (delta: number) => {
      const { scrollHeight, innerHeight } = sizeRef.current;
      setScrollTop((prev: number) => {
        const bottom = Math.max(0, scrollHeight - innerHeight);
        const next = Math.min(Math.max(0, prev + delta), bottom);
        // Track whether the user is at the bottom. Once they scroll
        // away, content-growth no longer auto-snaps; the moment they
        // return to bottom the pin re-engages.
        pinToBottomRef.current = next >= bottom - 0.001;
        return next;
      });
    },
    [sizeRef],
  );

  const { scrollbarColor, flashScrollbar, scrollByWithAnimation } =
    useAnimatedScrollbar(hasFocus, scrollBy);

  useKeypress(
    (_input: string, key: Key) => {
      if (key.shift) {
        if (key.upArrow) {
          scrollByWithAnimation(-1);
        }
        if (key.downArrow) {
          scrollByWithAnimation(1);
        }
      }
    },
    { isActive: hasFocus },
  );

  // Use refs to keep functions stable
  const scrollTopRef = useRef(scrollTop);
  const sizeStableRef = useRef(size);

  useEffect(() => {
    scrollTopRef.current = scrollTop;
    sizeStableRef.current = size;
  }, [scrollTop, size]);

  const getScrollState = useCallback(
    () => ({
      scrollTop: scrollTopRef.current,
      scrollHeight: sizeStableRef.current.scrollHeight,
      innerHeight: sizeStableRef.current.innerHeight,
    }),
    [],
  );

  const hasFocusRef = useRef(hasFocus);
  useEffect(() => {
    hasFocusRef.current = hasFocus;
  }, [hasFocus]);

  const hasFocusCallback = useCallback(() => hasFocusRef.current, []);

  const [isRefReady, setIsRefReady] = useState(false);

  useEffect(() => {
    if (ref.current && !isRefReady) {
      setIsRefReady(true);
    }
  }, [isRefReady]);

  const scrollableEntry = useMemo(
    () => ({
      ref: ref as React.RefObject<DOMElement>,
      getScrollState,
      scrollBy: scrollByWithAnimation,
      hasFocus: hasFocusCallback,
      flashScrollbar,
    }),
    [getScrollState, scrollByWithAnimation, hasFocusCallback, flashScrollbar],
  );

  useScrollable(scrollableEntry, hasFocus && isRefReady);

  const viewport = useMemo<ScrollViewport>(
    () => ({ scrollTop, innerHeight: size.innerHeight }),
    [scrollTop, size.innerHeight],
  );

  return (
    <Box
      ref={ref}
      maxHeight={maxHeight}
      width={width ?? maxWidth}
      height={height}
      flexDirection="column"
      overflowY="scroll"
      overflowX="hidden"
      scrollTop={scrollTop}
      flexGrow={flexGrow}
      scrollbarThumbColor={scrollbarColor}
    >
      {/*
        This inner box is necessary to prevent the parent from shrinking
        based on the children's content. It also adds a right padding to
        make room for the scrollbar.
      */}
      <Box flexShrink={0} paddingRight={1} flexDirection="column">
        <ScrollViewportContext.Provider value={viewport}>
          {children}
        </ScrollViewportContext.Provider>
      </Box>
    </Box>
  );
};
