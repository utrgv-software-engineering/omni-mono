import { describe, expect, it } from "vitest";
import { BUNDLED_THEMES } from "../bundled.js";
import {
  isLightBackground,
  mix,
  parseColorsToml,
  resolvePalette,
} from "../palette.js";
import { rolesFromPalette } from "../roles.js";

const bundled = (id: string) => BUNDLED_THEMES.find((t) => t.id === id)!.colors;

describe("parseColorsToml", () => {
  it('reads key = "value" lines and ignores comments', () => {
    expect(
      parseColorsToml(
        '# a comment\nmode = "dark"\naccent = "#7aa2f7" # trailing\nbare = value\n\n',
      ),
    ).toEqual({ mode: "dark", accent: "#7aa2f7", bare: "value" });
  });
});

describe("mix", () => {
  it("rounds like Omarchy's awk", () => {
    expect(mix("#1a1b26", "#000000", 0.25)).toBe("#14141d");
    expect(mix("#ff0000", "#ffffff", 0.2)).toBe("#ff3333");
  });
});

describe("resolvePalette", () => {
  // Values below are what `omarchy-theme-color --all` prints for the
  // same files, so the port stays faithful to Omarchy's resolver.
  it("matches Omarchy's resolution of tokyo-night", () => {
    const p = resolvePalette(bundled("tokyo-night"));
    expect(p.mode).toBe("dark");
    expect(p.cursor).toBe("#c0caf5");
    expect(p.selection_background).toBe("#292e42");
    expect(p.selection_foreground).toBe("#c0caf5");
    expect(p.brown).toBe("#75493d");
    expect(p.bright_cyan).toBe("#0db9d7");
  });
  it("matches Omarchy's resolution of flexoki-light", () => {
    const p = resolvePalette(bundled("flexoki-light"));
    expect(p.mode).toBe("light");
    expect(p.muted).toBe("#B7B5AC");
    expect(p.selection).toBe("#CECDC3");
    expect(p.brown).toBe("#683b15");
    expect(p.dark_background).toBe("#f2efe4");
  });
  it("derives everything from an ANSI-only legacy theme", () => {
    const p = resolvePalette({
      color0: "#000000",
      color7: "#cccccc",
      color1: "#ff0000",
      color2: "#00ff00",
      color3: "#ffff00",
      color4: "#0000ff",
      color5: "#ff00ff",
      color6: "#00ffff",
    });
    expect(p.background).toBe("#000000");
    expect(p.foreground).toBe("#cccccc");
    expect(p.red).toBe("#ff0000");
    expect(p.bright_red).toBe("#ff3333");
    expect(p.accent).toBe("#0000ff");
    expect(p.mode).toBe("dark");
  });
  it("detects a light background when mode is not declared", () => {
    expect(isLightBackground("#fffcf0")).toBe(true);
    expect(
      resolvePalette({ background: "#fffcf0", foreground: "#100f0f" }).mode,
    ).toBe("light");
  });
  it("resolves every bundled theme without throwing", () => {
    for (const t of BUNDLED_THEMES) {
      const p = resolvePalette(t.colors);
      for (const [k, v] of Object.entries(p)) {
        if (k === "mode") continue;
        expect(v, `${t.id}.${k}`).toMatch(/^#[0-9a-fA-F]{6}$/);
      }
    }
  });
});

describe("rolesFromPalette", () => {
  it("lands tokyo-night on the palette the TUI shipped with", () => {
    const r = rolesFromPalette(resolvePalette(bundled("tokyo-night")));
    expect(r.textPrimary).toBe("#c0caf5");
    expect(r.textChrome).toBe("#565f89");
    expect(r.stateDone).toBe("#9ece6a");
    expect(r.stateFailed).toBe("#f7768e");
    expect(r.stateBlocked).toBe("#e0af68");
    expect(r.accentAgent).toBe("#7aa2f7");
    expect(r.accentUser).toBe("#bb9af7");
    expect(r.focus).toBe("#bb9af7");
    expect(r.barBg).toBe("#24283b");
    expect(r.barActiveFg).toBe("#1a1b26");
  });
  it("keeps light themes readable: primary text is the dark foreground", () => {
    const r = rolesFromPalette(resolvePalette(bundled("flexoki-light")));
    expect(r.textPrimary).toBe("#100F0F");
    expect(r.barActiveFg).toBe("#FFFCF0");
  });
});
