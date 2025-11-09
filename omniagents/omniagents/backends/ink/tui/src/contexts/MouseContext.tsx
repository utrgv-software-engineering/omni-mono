/**
 * Mouse event context for handling mouse wheel scrolling
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
  const { stdin } = useStdin();
  const handlersRef = useRef<Set<MouseHandler>>(new Set());

  const subscribe = useCallback((handler: MouseHandler) => {
    handlersRef.current.add(handler);
  }, []);

  const unsubscribe = useCallback((handler: MouseHandler) => {
    handlersRef.current.delete(handler);
  }, []);

  useEffect(() => {
    if (!mouseEventsEnabled || !stdin) {
      return;
    }

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

    const handleData = (data: Buffer) => {
      buffer += data.toString("utf-8");

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

    stdin.on("data", handleData);

    return () => {
      stdin.off("data", handleData);
      process.stdout.write("\x1b[?1006l\x1b[?1002l");
    };
  }, [stdin, mouseEventsEnabled]);

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
