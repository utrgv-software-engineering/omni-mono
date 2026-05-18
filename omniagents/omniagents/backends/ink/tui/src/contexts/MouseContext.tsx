/**
 * Mouse event context for handling mouse wheel scrolling.
 *
 * Ink 7 switched stdin from flowing mode (`stdin.on('data', ...)`) to paused
 * mode (`stdin.addListener('readable', ...)` + `stdin.read()`). In paused
 * mode, once ink reads a chunk it is consumed from the stream — so a parallel
 * `stdin.on('data', ...)` listener never fires. We instead subscribe to
 * ink's `internal_eventEmitter`, which emits every chunk ink reads, so this
 * MouseProvider sees the SGR mouse sequences alongside ink's keypress parser.
 */

import React, { createContext, useContext, useEffect, useRef, useCallback, useMemo } from "react";
import { useStdin } from "ink";

export interface MouseEvent {
  name: "scroll-up" | "scroll-down" | "left-press" | "left-release";
  col: number;
  row: number;
}

export type MouseHandler = (event: MouseEvent) => void;

interface MouseContextType {
  subscribe: (handler: MouseHandler) => void;
  unsubscribe: (handler: MouseHandler) => void;
}

const MouseContext = createContext<MouseContextType | null>(null);

export const MouseProvider: React.FC<{ children: React.ReactNode; mouseEventsEnabled?: boolean }> = ({
  children,
  mouseEventsEnabled = false,
}) => {
  const stdinCtx = useStdin();
  const handlersRef = useRef<Set<MouseHandler>>(new Set());

  const subscribe = useCallback((handler: MouseHandler) => {
    handlersRef.current.add(handler);
  }, []);

  const unsubscribe = useCallback((handler: MouseHandler) => {
    handlersRef.current.delete(handler);
  }, []);

  useEffect(() => {
    if (!mouseEventsEnabled) {
      return;
    }
    // `internal_eventEmitter` is added by ink 7; gate on its presence so we
    // also work under older ink versions during a transitional install.
    const emitter = (stdinCtx as unknown as {
      internal_eventEmitter?: {
        on: (event: string, listener: (chunk: string) => void) => void;
        off: (event: string, listener: (chunk: string) => void) => void;
      };
    }).internal_eventEmitter;
    if (!emitter) return;

    process.stdout.write("\x1b[?1002h\x1b[?1006h");

    let buffer = "";

    const flushOne = (): boolean => {
      const match = buffer.match(/^\x1b\[<(\d+);(\d+);(\d+)([Mm])/);
      if (!match) return false;
      const button = parseInt(match[1], 10);
      const col = parseInt(match[2], 10) - 1;
      const row = parseInt(match[3], 10) - 1;
      const pressed = match[4] === "M";

      let name: MouseEvent["name"] | null = null;
      if (button === 64) name = "scroll-up";
      else if (button === 65) name = "scroll-down";
      else if (button === 0 && pressed) name = "left-press";
      else if (button === 0 && !pressed) name = "left-release";

      if (name) {
        const evt: MouseEvent = { name, col, row };
        handlersRef.current.forEach((h) => h(evt));
      }
      buffer = buffer.slice(match[0].length);
      return true;
    };

    const handleInput = (chunk: string) => {
      // Ink sets stdin encoding to utf8 and emits string chunks; no Buffer
      // conversion needed (older Buffer path was for the flowing-mode hook).
      buffer += chunk;

      if (buffer.length > 4096) buffer = buffer.slice(-4096);

      for (;;) {
        if (flushOne()) continue;

        if (/^\x1b\[</.test(buffer) && !/[Mm]/.test(buffer)) {
          break;
        }

        const idx = buffer.indexOf("\x1b");
        if (idx > 0) {
          buffer = buffer.slice(idx);
          continue;
        }
        if (idx === 0) break;
        buffer = "";
        break;
      }
    };

    emitter.on("input", handleInput);

    return () => {
      emitter.off("input", handleInput);
      process.stdout.write("\x1b[?1006l\x1b[?1002l");
    };
  }, [stdinCtx, mouseEventsEnabled]);

  const contextValue = useMemo(
    () => ({ subscribe, unsubscribe }),
    [subscribe, unsubscribe],
  );

  return (
    <MouseContext.Provider value={contextValue}>
      {children}
    </MouseContext.Provider>
  );
};

export const useMouseContext = () => {
  const context = useContext(MouseContext);
  if (!context) {
    throw new Error("useMouseContext must be used within a MouseProvider");
  }
  return context;
};
