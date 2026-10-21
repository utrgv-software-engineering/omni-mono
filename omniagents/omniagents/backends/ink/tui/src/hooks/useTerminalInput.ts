import { useEffect, useRef } from "react";
import { useStdin, useStdout } from "ink";
import { TerminalInputParser } from "../terminal/input.js";
import type { Key } from "./useKeypress.js";

/** Subscribe to Ink's raw chunks, before its single-key parser loses framing. */
export function useTerminalInput(
  handler: (input: string, key: Key) => void,
  isActive = true,
): void {
  const { internal_eventEmitter: emitter, setRawMode } = useStdin();
  const { stdout } = useStdout();
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    if (!isActive) return;
    const parser = new TerminalInputParser((input, key) =>
      latest.current(input, key),
    );
    const feed = (chunk: string) => parser.feed(chunk);
    emitter.on("input", feed);
    setRawMode(true);
    stdout.write("\x1b[?2004h");
    return () => {
      emitter.off("input", feed);
      parser.dispose();
      stdout.write("\x1b[?2004l");
      setRawMode(false);
    };
  }, [emitter, setRawMode, stdout, isActive]);
}
