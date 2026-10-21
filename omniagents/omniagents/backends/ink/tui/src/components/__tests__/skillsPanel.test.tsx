import React from "react";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { RPCClient } from "../../rpc/client.js";
import { filterSkills, nameColumnWidth, skillBody } from "../SkillsPanel.js";
import { cleanup, render } from "ink-testing-library";

const plain = (frame?: string) => stripAnsi(frame ?? "");

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

let SkillsPanel: (typeof import("../SkillsPanel.js"))["SkillsPanel"];
beforeAll(async () => {
  ({ SkillsPanel } = await import("../SkillsPanel.js"));
});
afterEach(cleanup);

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
  const instance = render(
    <KeypressProvider>
      <SkillsPanel
        focused
        onUnfocus={onUnfocus}
        client={client as unknown as RPCClient}
        sessionId="s"
      />
    </KeypressProvider>,
  );
  return instance;
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
    const { stdin, lastFrame } = mount(client);
    await waitFor(() => expect(plain(lastFrame())).toContain("acceptance"));
    expect(plain(lastFrame())).toContain("broken: bad frontmatter");
    stdin.write("dep");
    await waitFor(() => expect(plain(lastFrame())).toContain("filter dep"));
    expect(plain(lastFrame())).not.toContain("acceptance");
    stdin.write("\r");
    await waitFor(() => expect(plain(lastFrame())).toContain("line three"));
    expect(client.serverCall).toHaveBeenCalledWith(
      "skills.read",
      { name: "deploy" },
      "s",
    );
    stdin.write("\x1b");
    await waitFor(() => expect(plain(lastFrame())).toContain("filter dep"));
  });

  it("esc clears a filter first, then closes", async () => {
    const onUnfocus = vi.fn();
    const { stdin, lastFrame } = mount(fakeClient(), onUnfocus);
    await waitFor(() => expect(plain(lastFrame())).toContain("acceptance"));
    stdin.write("a");
    await waitFor(() => expect(plain(lastFrame())).toContain("filter a"));
    stdin.write("\x1b");
    await waitFor(() => expect(plain(lastFrame())).not.toContain("filter a"));
    expect(onUnfocus).not.toHaveBeenCalled();
    stdin.write("\x1b");
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
