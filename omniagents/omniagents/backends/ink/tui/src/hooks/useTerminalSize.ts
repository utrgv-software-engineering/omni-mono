import { useCallback, useSyncExternalStore } from "react";
import { useStdout } from "ink";

/** Rows and columns when stdout is not a terminal (tests, pipes). */
const FALLBACK = { rows: 24, columns: 80 } as const;

/**
 * The terminal's size, re-rendering the caller when it changes.
 *
 * Reading `stdout.rows` / `stdout.columns` during render captures the size
 * of whatever render last ran. On a resize Ink repaints the existing tree
 * straight away, so a layout sized from a stale read paints a frame taller
 * than the shrunk terminal — the alternate screen keeps only the top rows,
 * cutting the composer and status bar off — and stays that way until some
 * unrelated state change happens to re-render it. Every size that feeds
 * layout comes from here instead, so a resize re-renders exactly the
 * components that depend on it.
 */
export function useTerminalSize(): { rows: number; columns: number } {
  const { stdout } = useStdout();
  const subscribe = useCallback(
    (onChange: () => void) => {
      stdout.on("resize", onChange);
      return () => {
        stdout.off("resize", onChange);
      };
    },
    [stdout],
  );
  const rows = useSyncExternalStore(
    subscribe,
    () => stdout.rows || FALLBACK.rows,
  );
  const columns = useSyncExternalStore(
    subscribe,
    () => stdout.columns || FALLBACK.columns,
  );
  return { rows, columns };
}
