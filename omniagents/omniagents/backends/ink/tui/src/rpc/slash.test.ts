import { describe, expect, it } from "vitest";
import {
  enterCompletion,
  findSlashCommand,
  parseSlashArgs,
  parseSlashLine,
  type SlashCommand,
} from "./slash.js";

const cmd = (
  name: string,
  extra: Partial<SlashCommand> = {},
): SlashCommand => ({
  name,
  function: name,
  description: "",
  usage: "",
  args: { kind: "none" },
  aliases: [],
  during_run: true,
  order: 100,
  source: "server",
  ...extra,
});

describe("parseSlashLine", () => {
  it("splits name and rest", () => {
    expect(parseSlashLine("/goal fix the tests")).toEqual({
      name: "goal",
      rest: "fix the tests",
    });
    expect(parseSlashLine("/Recap")).toEqual({ name: "recap", rest: "" });
  });
  it("treats a leading space or a path as plain text", () => {
    expect(parseSlashLine(" /goal x")).toBeNull();
    expect(parseSlashLine("/usr/bin/ls is a path")).toBeNull();
    expect(parseSlashLine("hello")).toBeNull();
    expect(parseSlashLine("/")).toBeNull();
  });
});

describe("findSlashCommand", () => {
  it("matches names and aliases case-insensitively", () => {
    const rows = [cmd("sessions", { aliases: ["resume"] })];
    expect(findSlashCommand(rows, "RESUME")?.name).toBe("sessions");
    expect(findSlashCommand(rows, "nope")).toBeUndefined();
  });
});

describe("parseSlashArgs", () => {
  it("refuses text for a no-argument command", () => {
    expect(parseSlashArgs({ kind: "none" }, "extra")).toEqual({
      ok: false,
      error: "This command takes no arguments.",
    });
    expect(parseSlashArgs({ kind: "none" }, "  ")).toEqual({
      ok: true,
      args: {},
    });
  });
  it("maps the whole rest into one field", () => {
    expect(
      parseSlashArgs({ kind: "text", field: "goal" }, "fix the tests"),
    ).toEqual({
      ok: true,
      args: { goal: "fix the tests" },
    });
    expect(parseSlashArgs({ kind: "text", field: "goal" }, "")).toEqual({
      ok: true,
      args: {},
    });
  });
  it("splits positional words with the last field taking the remainder", () => {
    expect(
      parseSlashArgs(
        { kind: "words", fields: ["interval", "prompt"] },
        "5m  fix the tests",
      ),
    ).toEqual({ ok: true, args: { interval: "5m", prompt: "fix the tests" } });
    expect(
      parseSlashArgs({ kind: "words", fields: ["interval", "prompt"] }, "5m"),
    ).toEqual({
      ok: true,
      args: { interval: "5m" },
    });
  });
});

describe("enterCompletion", () => {
  it("runs a fully typed verb as typed, even one that could take text", () => {
    const goal = cmd("goal", { args: { kind: "text", field: "goal" } });
    expect(enterCompletion(goal, "goal")).toEqual({
      text: "/goal",
      submit: true,
    });
    expect(enterCompletion(goal, "GOAL")).toEqual({
      text: "/goal",
      submit: true,
    });
  });
  it("runs an alias as typed", () => {
    const loop = cmd("loop", {
      aliases: ["loops"],
      args: { kind: "text", field: "text" },
    });
    expect(enterCompletion(loop, "loops")).toEqual({
      text: "/loops",
      submit: true,
    });
  });
  it("completes a partial name and waits when arguments are expected", () => {
    expect(
      enterCompletion(
        cmd("goal", { args: { kind: "text", field: "goal" } }),
        "go",
      ),
    ).toEqual({
      text: "/goal ",
      submit: false,
    });
    expect(enterCompletion(cmd("recap"), "rec")).toEqual({
      text: "/recap",
      submit: true,
    });
  });
});
