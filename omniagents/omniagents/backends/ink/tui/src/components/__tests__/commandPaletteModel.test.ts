import { describe, expect, it } from "vitest";
import {
  BUILTIN_COMMANDS,
  buildCommandRows,
  completionFor,
  filterCommands,
} from "../command-palette-model.js";

const server = (name: string, extra: Record<string, unknown> = {}) => ({
  name,
  function: name,
  description: `${name} desc`,
  usage: "",
  args: { kind: "none" },
  aliases: [],
  during_run: true,
  order: 50,
  ...extra,
});

describe("buildCommandRows", () => {
  it("sorts by declared order then name, builtins included", () => {
    const rows = buildCommandRows([
      server("zebra", { order: 20 }),
      server("alpha", { order: 20 }),
      server("late", { order: 95 }),
    ]);
    const names = rows.map((r) => r.name);
    expect(names.indexOf("model")).toBeLessThan(names.indexOf("alpha"));
    expect(names.indexOf("alpha")).toBeLessThan(names.indexOf("zebra"));
    expect(names.indexOf("late")).toBeGreaterThan(names.indexOf("help"));
  });

  it("drops a server row that collides with a builtin name or alias", () => {
    const rows = buildCommandRows([
      server("Model"),
      server("models"),
      server("goal"),
    ]);
    expect(rows.filter((r) => r.name === "model")).toHaveLength(1);
    expect(rows.find((r) => r.name === "model")!.source).toBe("client");
    expect(rows.some((r) => r.name === "models")).toBe(false);
    expect(rows.some((r) => r.name === "goal")).toBe(true);
  });

  it("ignores malformed catalog rows", () => {
    const rows = buildCommandRows([{ name: "" }, null, 42, { nope: true }]);
    expect(rows).toHaveLength(BUILTIN_COMMANDS.length);
  });

  it("carries the argument rule and usage through", () => {
    const rows = buildCommandRows([
      server("goal", {
        usage: "<goal text>",
        args: { kind: "text", field: "goal" },
        during_run: false,
      }),
    ]);
    const goal = rows.find((r) => r.name === "goal")!;
    expect(goal.args).toEqual({ kind: "text", field: "goal" });
    expect(goal.usage).toBe("<goal text>");
    expect(goal.during_run).toBe(false);
  });
});

describe("filterCommands", () => {
  const rows = buildCommandRows([
    server("goal"),
    server("goal.stop"),
    server("good", { description: "go through the roof" }),
  ]);
  it("puts an exact name first, then prefix matches in catalog order", () => {
    expect(filterCommands(rows, "goal").map((r) => r.name)).toEqual([
      "goal",
      "goal.stop",
    ]);
    expect(filterCommands(rows, "go").map((r) => r.name)).toEqual([
      "goal",
      "goal.stop",
      "good",
    ]);
  });
  it("matches aliases but never descriptions", () => {
    expect(filterCommands(rows, "resume")[0]!.name).toBe("sessions");
    expect(filterCommands(rows, "roof")).toEqual([]);
    expect(filterCommands(rows, "through")).toEqual([]);
  });
  it("returns everything for an empty query", () => {
    expect(filterCommands(rows, "")).toHaveLength(rows.length);
  });
});

describe("completionFor", () => {
  it("adds a trailing space only when the command takes arguments", () => {
    expect(
      completionFor(
        buildCommandRows([server("recap")]).find((r) => r.name === "recap")!,
      ),
    ).toBe("/recap");
    expect(
      completionFor(
        buildCommandRows([
          server("goal", { args: { kind: "text", field: "goal" } }),
        ]).find((r) => r.name === "goal")!,
      ),
    ).toBe("/goal ");
  });
});
