import React from "react";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { RPCClient } from "../../rpc/client.js";
import { filterSkills, nameColumnWidth, skillBody } from "../SkillsPanel.js";

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 100;
  rows = 30;
  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (c) => {
      if (typeof c === "string") this.frames.push(c);
    });
  }
  lastFrame(): string {
    for (let i = this.frames.length - 1; i >= 0; i -= 1) {
      const f = stripAnsi(this.frames[i] ?? "");
      if (f.trim()) return f;
    }
    return "";
  }
}
class FakeStdin extends EventEmitter {
  isTTY = true;
  private queue: string[] = [];
  setRawMode() {}
  setEncoding() {}
  resume() {}
  pause() {}
  read() {
    return this.queue.shift() ?? null;
  }
  unref() {}
  ref() {}
  press(s: string) {
    this.queue.push(s);
    this.emit("readable");
  }
}
const settle = (ms = 40) => new Promise((r) => setTimeout(r, ms));
async function waitFor(check: () => void, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      check();
      return;
    } catch (e) {
      if (Date.now() > deadline) throw e;
      await settle(10);
    }
  }
}

type Render = (typeof import("ink"))["render"];
let inkRender: Render;
let SkillsPanel: (typeof import("../SkillsPanel.js"))["SkillsPanel"];
const instances: Array<ReturnType<Render>> = [];
beforeAll(async () => {
  inkRender = (await import("ink")).render;
  ({ SkillsPanel } = await import("../SkillsPanel.js"));
});
afterEach(() => {
  while (instances.length) {
    const i = instances.pop();
    i?.unmount();
    i?.cleanup();
  }
});

const skills = [
  {
    name: "acceptance",
    description: "run acceptance tests",
    path: "/s/acceptance",
  },
  { name: "deploy", description: "ship to prod", path: "/s/deploy" },
  {
    name: "broken",
    description: "",
    path: "/s/broken",
    error: "bad frontmatter",
  },
];

function fakeClient() {
  return {
    serverCall: vi.fn(async (fn: string, args: Record<string, unknown>) => {
      if (fn === "skills.list") return { skills };
      if (fn === "skills.read")
        return {
          ok: true,
          name: args.name,
          path: "/x",
          text: "# Deploy\n\nline two\nline three",
        };
      throw new Error("unexpected " + fn);
    }),
  };
}

function mount(
  client: ReturnType<typeof fakeClient>,
  onUnfocus = () => undefined,
) {
  const stdout = new TestStdout();
  const stdin = new FakeStdin();
  const instance = inkRender(
    <KeypressProvider>
      <SkillsPanel
        focused
        onUnfocus={onUnfocus}
        client={client as unknown as RPCClient}
        sessionId="s"
      />
    </KeypressProvider>,
    {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  instances.push(instance);
  return { stdout, stdin };
}

describe("filterSkills", () => {
  it("prefers name prefixes, then names and descriptions containing the query", () => {
    expect(filterSkills(skills, "de").map((s) => s.name)).toEqual(["deploy"]);
    expect(filterSkills(skills, "test").map((s) => s.name)).toEqual([
      "acceptance",
    ]);
    expect(filterSkills(skills, "")).toHaveLength(3);
  });
});

describe("SkillsPanel", () => {
  it("lists skills, flags broken ones, filters as you type, and reads on enter", async () => {
    const client = fakeClient();
    const { stdin, stdout } = mount(client);
    await waitFor(() => expect(stdout.lastFrame()).toContain("acceptance"));
    expect(stdout.lastFrame()).toContain("broken: bad frontmatter");
    stdin.press("dep");
    await waitFor(() => expect(stdout.lastFrame()).toContain("filter dep"));
    expect(stdout.lastFrame()).not.toContain("acceptance");
    stdin.press("\r");
    await waitFor(() => expect(stdout.lastFrame()).toContain("line three"));
    expect(client.serverCall).toHaveBeenCalledWith(
      "skills.read",
      { name: "deploy" },
      "s",
    );
    stdin.press("\x1b");
    await waitFor(() => expect(stdout.lastFrame()).toContain("filter dep"));
  });

  it("esc clears a filter first, then closes", async () => {
    const onUnfocus = vi.fn();
    const { stdin, stdout } = mount(fakeClient(), onUnfocus);
    await waitFor(() => expect(stdout.lastFrame()).toContain("acceptance"));
    stdin.press("a");
    await waitFor(() => expect(stdout.lastFrame()).toContain("filter a"));
    stdin.press("\x1b");
    await waitFor(() => expect(stdout.lastFrame()).not.toContain("filter a"));
    expect(onUnfocus).not.toHaveBeenCalled();
    stdin.press("\x1b");
    await waitFor(() => expect(onUnfocus).toHaveBeenCalled());
  });
});

describe("nameColumnWidth", () => {
  it("fits the longest name within sane bounds", () => {
    expect(nameColumnWidth([])).toBe(8);
    expect(
      nameColumnWidth([{ name: "deploy", description: "", path: "a" }]),
    ).toBe(8);
    expect(
      nameColumnWidth([
        { name: "clinical-decision-support", description: "", path: "b" },
      ]),
    ).toBe(25);
    expect(
      nameColumnWidth([{ name: "x".repeat(60), description: "", path: "c" }]),
    ).toBe(28);
  });
});

describe("skillBody", () => {
  it("drops the frontmatter the header already shows", () => {
    expect(skillBody("---\nname: x\ndescription: y\n---\n\n# X\nbody\n")).toBe(
      "# X\nbody\n",
    );
    expect(skillBody("# X\nbody")).toBe("# X\nbody");
    expect(skillBody("---\nunterminated")).toBe("---\nunterminated");
  });
});
