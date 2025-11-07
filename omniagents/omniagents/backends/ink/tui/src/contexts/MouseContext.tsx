/**
 * Mouse event context for handling mouse wheel scrolling
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";
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

export const MouseProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const { stdin, setRawMode } = useStdin();
  const handlersRef = useRef<Set<MouseHandler>>(new Set());

  const subscribe = useCallback((handler: MouseHandler) => {
    handlersRef.current.add(handler);
  }, []);

  const unsubscribe = useCallback((handler: MouseHandler) => {
    handlersRef.current.delete(handler);
  }, []);

  useEffect(() => {
    if (!stdin || !setRawMode) {
      return;
    }

    // Enable mouse tracking
    stdin.write("\x1b[?1000h"); // Enable mouse button events
    stdin.write("\x1b[?1006h"); // Enable SGR extended mouse mode

    const handleData = (data: Buffer) => {
      const str = data.toString();

      // Parse SGR mouse events: \x1b[<b;x;y(M|m)
      const sgrMatch = str.match(/\x1b\[<(\d+);(\d+);(\d+)([Mm])/);
      if (sgrMatch) {
        const button = parseInt(sgrMatch[1], 10);
        const col = parseInt(sgrMatch[2], 10) - 1; // Convert to 0-indexed
        const row = parseInt(sgrMatch[3], 10) - 1; // Convert to 0-indexed
        const pressed = sgrMatch[4] === "M";

        let eventName: MouseEvent["name"] | null = null;

        if (button === 64) {
          eventName = "scroll-up";
        } else if (button === 65) {
          eventName = "scroll-down";
        } else if (button === 0 && pressed) {
          eventName = "left-press";
        } else if (button === 0 && !pressed) {
          eventName = "left-release";
        }

        if (eventName) {
          const event: MouseEvent = { name: eventName, col, row };
          handlersRef.current.forEach((handler) => handler(event));
        }
      }
    };

    stdin.on("data", handleData);

    return () => {
      stdin.off("data", handleData);
      // Disable mouse tracking
      stdin.write("\x1b[?1000l");
      stdin.write("\x1b[?1006l");
    };
  }, [stdin, setRawMode]);

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
