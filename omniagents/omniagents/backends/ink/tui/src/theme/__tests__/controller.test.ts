import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { useAppStore } from "../../store/appStore.js";
import { DEFAULT_THEME_ID, Theme } from "../../theme.js";
import {
  applyTheme,
  disposeTheme,
  initTheme,
  resolveInitialThemeId,
  selectTheme,
} from "../controller.js";
import { readSettings } from "../settings.js";
import { listThemes, omarchyCurrentTheme, userThemes } from "../sources.js";

const dirs: string[] = [];
const scratch = () => {
  const d = mkdtempSync(join(tmpdir(), "ink-theme-"));
  dirs.push(d);
  return d;
};
afterEach(() => {
  disposeTheme();
  applyTheme(DEFAULT_THEME_ID);
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

const fakeOmarchy = (root: string, name: string, colors: string) => {
  const state = join(root, "current");
  mkdirSync(join(state, "theme"), { recursive: true });
  writeFileSync(join(state, "theme", "colors.toml"), colors);
  writeFileSync(join(state, "theme.name"), name + "\n");
  return state;
};

describe("sources", () => {
  it("reads Omarchy's current theme and names it", () => {
    const state = fakeOmarchy(
      scratch(),
      "rose-pine",
      'background = "#faf4ed"\nforeground = "#575279"\n',
    );
    const t = omarchyCurrentTheme(state)!;
    expect(t.id).toBe("omarchy");
    expect(t.detail).toBe("Rose Pine");
    expect(t.palette.mode).toBe("light");
    expect(omarchyCurrentTheme(join(scratch(), "nope"))).toBeNull();
  });
  it("lists user themes from files and directories and shadows bundled ids", () => {
    const dir = scratch();
    writeFileSync(
      join(dir, "mine.toml"),
      'background = "#101010"\nforeground = "#eeeeee"\n',
    );
    mkdirSync(join(dir, "tokyo-night"));
    writeFileSync(
      join(dir, "tokyo-night", "colors.toml"),
      'background = "#000000"\nforeground = "#ffffff"\n',
    );
    writeFileSync(join(dir, "broken.toml"), "not = ");
    const user = userThemes(dir);
    expect(user.map((t) => t.id)).toEqual(["broken", "mine", "tokyo-night"]);
    const all = listThemes({
      themesDir: dir,
      omarchyStateDir: join(dir, "no-omarchy"),
    });
    expect(all.filter((t) => t.id === "tokyo-night")).toHaveLength(1);
    expect(all.find((t) => t.id === "tokyo-night")!.origin).toBe("user");
    expect(all[0]!.origin).toBe("user");
  });
});

describe("precedence", () => {
  it("flag, then setting, then env, then Omarchy, then default", () => {
    const dir = scratch();
    const settings = join(dir, "ink.json");
    const noOmarchy = join(dir, "none");
    const env: NodeJS.ProcessEnv = {};
    expect(
      resolveInitialThemeId(undefined, {
        settingsFile: settings,
        omarchyStateDir: noOmarchy,
        env,
      }),
    ).toBe(DEFAULT_THEME_ID);
    const state = fakeOmarchy(
      dir,
      "nord",
      'background = "#2e3440"\nforeground = "#d8dee9"\n',
    );
    expect(
      resolveInitialThemeId(undefined, {
        settingsFile: settings,
        omarchyStateDir: state,
        env,
      }),
    ).toBe("omarchy");
    env.OMNI_THEME = "gruvbox";
    expect(
      resolveInitialThemeId(undefined, {
        settingsFile: settings,
        omarchyStateDir: state,
        env,
      }),
    ).toBe("gruvbox");
    writeFileSync(settings, JSON.stringify({ theme: "nord" }));
    expect(
      resolveInitialThemeId(undefined, {
        settingsFile: settings,
        omarchyStateDir: state,
        env,
      }),
    ).toBe("nord");
    expect(
      resolveInitialThemeId("kanagawa", {
        settingsFile: settings,
        omarchyStateDir: state,
        env,
      }),
    ).toBe("kanagawa");
  });
});

describe("apply and select", () => {
  it("repaints Theme, bumps the version, and remembers the choice", () => {
    const dir = scratch();
    const settings = join(dir, "ink.json");
    initTheme(undefined, {
      settingsFile: settings,
      omarchyStateDir: join(dir, "none"),
      env: {},
    });
    const before = useAppStore.getState().themeVersion;
    const tokyo = Theme.textPrimary;
    const applied = selectTheme("flexoki-light");
    expect(applied.id).toBe("flexoki-light");
    expect(Theme.textPrimary).not.toBe(tokyo);
    expect(Theme.textPrimary).toBe("#100F0F");
    expect(useAppStore.getState().themeVersion).toBe(before + 1);
    expect(readSettings(settings).theme).toBe("flexoki-light");
  });
  it("falls back to the default for an unknown id", () => {
    const dir = scratch();
    initTheme("does-not-exist", {
      omarchyStateDir: join(dir, "none"),
      env: {},
    });
    expect(Theme.textPrimary).toBe("#c0caf5");
  });
  it("follows Omarchy when its theme changes", async () => {
    const dir = scratch();
    const state = fakeOmarchy(
      dir,
      "nord",
      'background = "#2e3440"\nforeground = "#d8dee9"\n',
    );
    initTheme(undefined, { omarchyStateDir: state, env: {} });
    expect(Theme.textPrimary).toBe("#d8dee9");
    writeFileSync(
      join(state, "theme", "colors.toml"),
      'background = "#000000"\nforeground = "#ff00ff"\n',
    );
    writeFileSync(join(state, "theme.name"), "magenta\n");
    const deadline = Date.now() + 3000;
    while (Theme.textPrimary !== "#ff00ff" && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 50));
    }
    expect(Theme.textPrimary).toBe("#ff00ff");
  });
});
