/**
 * Scrollable container component with keyboard and mouse support
 */

import React, {
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

interface ScrollableProps {
  children?: React.ReactNode;
  width?: number;
  height?: number | string;
  maxWidth?: number;
  maxHeight?: number;
  hasFocus: boolean;
  scrollToBottom?: boolean;
  flexGrow?: number;
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

    // Only update if dimensions actually changed
    const dimsChanged =
      previousSizeRef.current.innerHeight !== innerHeight ||
      previousSizeRef.current.scrollHeight !== scrollHeight;

    if (dimsChanged) {
      const contentGrew = scrollHeight > previousSizeRef.current.scrollHeight;

      previousSizeRef.current = { innerHeight, scrollHeight };
      setSize({ innerHeight, scrollHeight });

      if (contentGrew) {
        setScrollTop(Math.max(0, scrollHeight - innerHeight));
        pinToBottomRef.current = true;
      }
    }
  });

  const scrollBy = useCallback(
    (delta: number) => {
      const { scrollHeight, innerHeight } = sizeRef.current;
      setScrollTop((prev: number) => {
        const bottom = Math.max(0, scrollHeight - innerHeight);
        const next = Math.min(Math.max(0, prev + delta), bottom);
        return next;
      });
    },
    [sizeRef],
  );

  const { scrollbarColor, flashScrollbar, scrollByWithAnimation } =
    useAnimatedScrollbar(hasFocus, scrollBy);

  useKeypress(
    (key: Key) => {
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
        {children}
      </Box>
    </Box>
  );
};
