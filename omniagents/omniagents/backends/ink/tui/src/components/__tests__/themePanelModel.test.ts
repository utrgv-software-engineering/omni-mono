import { describe, expect, it } from "vitest";
import { bundledThemes } from "../../theme/sources.js";
import {
  filterThemes,
  initialThemeIndex,
  originLabel,
} from "../theme-panel-model.js";

const rows = bundledThemes();

describe("theme panel model", () => {
  it("filters by name prefix then substring", () => {
    expect(filterThemes(rows, "tok").map((r) => r.id)).toEqual(["tokyo-night"]);
    expect(filterThemes(rows, "night").map((r) => r.id)).toEqual([
      "tokyo-night",
    ]);
    expect(filterThemes(rows, "")).toHaveLength(rows.length);
  });
  it("starts on the active theme", () => {
    expect(rows[initialThemeIndex(rows, "nord")]!.id).toBe("nord");
    expect(initialThemeIndex(rows, "missing")).toBe(0);
  });
  it("labels bundled themes by mode and Omarchy by what it follows", () => {
    expect(originLabel(rows.find((r) => r.id === "flexoki-light")!)).toBe(
      "light",
    );
    expect(
      originLabel({ ...rows[0]!, origin: "omarchy", detail: "Nord" }),
    ).toBe("follows the desktop · Nord");
  });
});
