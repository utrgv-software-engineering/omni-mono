import { describe, expect, it } from "vitest";
import {
  BUILTIN_COMMANDS,
  buildCommandRows,
  completionFor,
  declaresRequiredParams,
  filterCommands,
  type CommandRow,
  type ServerFunction,
} from "../command-palette-model.js";

function fn(overrides: Partial<ServerFunction> = {}): ServerFunction {
  return { name: "goal", description: "Set the session goal", ...overrides };
}

describe("buildCommandRows", () => {
  it("lists builtins first, then server functions alphabetically", () => {
    const rows = buildCommandRows([
      fn({ name: "zebra" }),
      fn({ name: "alpha" }),
    ]);
    expect(rows.slice(0, BUILTIN_COMMANDS.length).map((r) => r.name)).toEqual(
      BUILTIN_COMMANDS.map((b) => b.name),
    );
    expect(rows.slice(BUILTIN_COMMANDS.length).map((r) => r.name)).toEqual([
      "alpha",
      "zebra",
    ]);
  });

  it("drops a server function that collides with a builtin", () => {
    const rows = buildCommandRows([
      fn({ name: "Model" }),
      fn({ name: "goal" }),
    ]);
    expect(rows.filter((r) => r.name.toLowerCase() === "model")).toHaveLength(
      1,
    );
    expect(rows.find((r) => r.name.toLowerCase() === "model")!.source).toBe(
      "builtin",
    );
  });

  it("normalizes a blank description to null", () => {
    const rows = buildCommandRows([fn({ name: "quiet", description: "   " })]);
    expect(rows.find((r) => r.name === "quiet")!.description).toBeNull();
  });

  it("ignores entries without a usable name", () => {
    const rows = buildCommandRows([
      fn({ name: "" }),
      { name: undefined as unknown as string },
    ]);
    expect(rows).toHaveLength(BUILTIN_COMMANDS.length);
  });

  it("marks functions that declare parameters", () => {
    const rows = buildCommandRows([
      fn({ name: "withargs", params_schema: { required: ["text"] } }),
      fn({ name: "noargs", params_schema: {} }),
    ]);
    expect(rows.find((r) => r.name === "withargs")!.takesArgs).toBe(true);
    expect(rows.find((r) => r.name === "noargs")!.takesArgs).toBe(false);
  });
});

describe("declaresRequiredParams", () => {
  it("reads an explicit required list", () => {
    expect(declaresRequiredParams({ required: ["a"] })).toBe(true);
    expect(declaresRequiredParams({ required: [] })).toBe(false);
  });

  it("treats a non-empty properties map as parameters", () => {
    expect(declaresRequiredParams({ properties: { a: {} } })).toBe(true);
    expect(declaresRequiredParams({ properties: {} })).toBe(false);
  });

  it("degrades to false for an absent or unusable schema", () => {
    expect(declaresRequiredParams(undefined)).toBe(false);
    expect(declaresRequiredParams({} as Record<string, unknown>)).toBe(false);
  });
});

describe("filterCommands", () => {
  const rows: CommandRow[] = [
    {
      name: "model",
      description: "Switch model",
      source: "builtin",
      takesArgs: false,
    },
    {
      name: "goal",
      description: "Set the goal",
      source: "server",
      takesArgs: true,
    },
    {
      name: "recap",
      description: "Summarize the model session",
      source: "server",
      takesArgs: false,
    },
  ];

  it("ranks prefix matches above substring matches", () => {
    expect(filterCommands(rows, "mo").map((r) => r.name)).toEqual([
      "model",
      "recap",
    ]);
  });

  it("tolerates the leading slash the user typed", () => {
    expect(filterCommands(rows, "/go").map((r) => r.name)).toEqual(["goal"]);
  });

  it("matches descriptions as a fallback", () => {
    expect(filterCommands(rows, "summarize").map((r) => r.name)).toEqual([
      "recap",
    ]);
  });

  it("returns a copy of everything for an empty query", () => {
    const all = filterCommands(rows, "  ");
    expect(all).toHaveLength(3);
    expect(all).not.toBe(rows);
  });

  it("returns nothing when nothing matches", () => {
    expect(filterCommands(rows, "zzz")).toEqual([]);
  });
});

describe("completionFor", () => {
  it("leaves the cursor after a space for commands that take arguments", () => {
    expect(
      completionFor({
        name: "goal",
        description: null,
        source: "server",
        takesArgs: true,
      }),
    ).toBe("/goal ");
  });

  it("completes argument-free commands exactly", () => {
    expect(
      completionFor({
        name: "exit",
        description: null,
        source: "builtin",
        takesArgs: false,
      }),
    ).toBe("/exit");
  });
});
