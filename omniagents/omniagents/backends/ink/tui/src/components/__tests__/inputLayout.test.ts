import { expect, it } from "vitest";
import stringWidth from "string-width";
import { layoutInput, columnAtCell } from "../input-layout.js";

it("wraps by terminal cells without splitting emoji or combining sequences", () => {
  const text = "界界e\u0301👩‍💻xyz";
  const layout = layoutInput([text], 0, Array.from(text).length, 4);
  expect(layout.rows.every((row) => stringWidth(row.text) <= 4)).toBe(true);
  expect(layout.rows.map((row) => row.text).join("")).toBe(text + " ");
  expect(layout.rows.some((row) => row.text.includes("👩‍💻"))).toBe(true);
  expect(layout.cursorRow).toBe(layout.rows.length - 1);
});

it("maps visual cell offsets back to logical cursor positions", () => {
  const layout = layoutInput(["界abc"], 0, 1, 3);
  expect(layout.cursorCell).toBe(2);
  expect(columnAtCell(layout.rows[0], 2)).toBe(1);
});
