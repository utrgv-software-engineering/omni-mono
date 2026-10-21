/**
 * Hit-tested mouse click handler. Fires only when the click falls inside the
 * bounding box of the referenced DOM element, and passes coordinates relative
 * to that element to the handler.
 *
 * Uses ink 7's `getBoundingBox` rather than handrolled measurement.
 */

import { useCallback, useRef } from "react";
import { getBoundingBox, type DOMElement } from "ink";
import type React from "react";
import { useMouse, type MouseEvent } from "./useMouse.js";

export type MouseClickHandler = (
  event: MouseEvent,
  relativeX: number,
  relativeY: number,
) => void;

export interface UseMouseClickOptions {
  isActive?: boolean;
  /**
   * Which mouse event triggers the handler. The MouseContext currently emits
   * only left-button + scroll events, so `left-press` and `left-release` are
   * the meaningful choices.
   */
  eventName?: MouseEvent["name"];
}

export function useMouseClick(
  containerRef: React.RefObject<DOMElement | null>,
  handler: MouseClickHandler,
  { isActive = true, eventName = "left-press" }: UseMouseClickOptions = {},
) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  const onMouse = useCallback(
    (event: MouseEvent) => {
      if (event.name !== eventName) return;
      const node = containerRef.current;
      if (!node) return;

      const { x, y, width, height } = getBoundingBox(node);
      // MouseContext already converts terminal 1-based coords to 0-based,
      // so a direct subtraction yields element-relative coordinates.
      const relativeX = event.col - x;
      const relativeY = event.row - y;

      if (
        relativeX >= 0 &&
        relativeX < width &&
        relativeY >= 0 &&
        relativeY < height
      ) {
        handlerRef.current(event, relativeX, relativeY);
      }
    },
    [containerRef, eventName],
  );

  useMouse(onMouse, { isActive });
}
