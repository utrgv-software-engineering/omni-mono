/**
 * Hook for animated scrollbar that flashes on interaction
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { TokyoNightTheme } from "../theme.js";

/**
 * Simple color interpolation between two hex colors
 */
function interpolateColor(
  color1: string,
  color2: string,
  progress: number,
): string {
  // Parse hex colors
  const r1 = parseInt(color1.slice(1, 3), 16);
  const g1 = parseInt(color1.slice(3, 5), 16);
  const b1 = parseInt(color1.slice(5, 7), 16);

  const r2 = parseInt(color2.slice(1, 3), 16);
  const g2 = parseInt(color2.slice(3, 5), 16);
  const b2 = parseInt(color2.slice(5, 7), 16);

  // Interpolate
  const r = Math.round(r1 + (r2 - r1) * progress);
  const g = Math.round(g1 + (g2 - g1) * progress);
  const b = Math.round(b1 + (b2 - b1) * progress);

  // Convert back to hex
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
}

export function useAnimatedScrollbar(
  isFocused: boolean,
  scrollBy: (delta: number) => void,
) {
  const darkColor = "#3b4261"; // Subtle gray (matching border)
  const focusedColor = TokyoNightTheme.assistantMessage; // Soft blue

  const [scrollbarColor, setScrollbarColor] = useState(darkColor);
  const colorRef = useRef(scrollbarColor);
  colorRef.current = scrollbarColor;

  const animationFrame = useRef<NodeJS.Timeout | null>(null);
  const timeout = useRef<NodeJS.Timeout | null>(null);
  const isAnimatingRef = useRef(false);

  const cleanup = useCallback(() => {
    isAnimatingRef.current = false;
    if (animationFrame.current) {
      clearInterval(animationFrame.current);
      animationFrame.current = null;
    }
    if (timeout.current) {
      clearTimeout(timeout.current);
      timeout.current = null;
    }
  }, []);

  const flashScrollbar = useCallback(() => {
    cleanup();
    isAnimatingRef.current = true;

    const fadeInDuration = 200;
    const visibleDuration = 1000;
    const fadeOutDuration = 300;

    const startColor = colorRef.current;

    // Phase 1: Fade In
    let start = Date.now();
    const animateFadeIn = () => {
      const elapsed = Date.now() - start;
      const progress = Math.min(elapsed / fadeInDuration, 1);

      setScrollbarColor(interpolateColor(startColor, focusedColor, progress));

      if (progress === 1) {
        if (animationFrame.current) {
          clearInterval(animationFrame.current);
          animationFrame.current = null;
        }

        // Phase 2: Wait
        timeout.current = setTimeout(() => {
          // Phase 3: Fade Out
          start = Date.now();
          const animateFadeOut = () => {
            const elapsed = Date.now() - start;
            const progress = Math.min(elapsed / fadeOutDuration, 1);
            setScrollbarColor(
              interpolateColor(focusedColor, darkColor, progress),
            );

            if (progress === 1) {
              cleanup();
            }
          };

          animationFrame.current = setInterval(animateFadeOut, 33);
        }, visibleDuration);
      }
    };

    animationFrame.current = setInterval(animateFadeIn, 33);
  }, [cleanup, focusedColor, darkColor]);

  const wasFocused = useRef(isFocused);
  useEffect(() => {
    if (isFocused && !wasFocused.current) {
      flashScrollbar();
    } else if (!isFocused && wasFocused.current) {
      cleanup();
      setScrollbarColor(darkColor);
    }
    wasFocused.current = isFocused;
    return cleanup;
  }, [isFocused, flashScrollbar, cleanup, darkColor]);

  const scrollByWithAnimation = useCallback(
    (delta: number) => {
      scrollBy(delta);
      flashScrollbar();
    },
    [scrollBy, flashScrollbar],
  );

  return { scrollbarColor, flashScrollbar, scrollByWithAnimation };
}
