// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutomationsApp, health, sentence, type Automation } from "./Automations";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@omniagents/chat-ui/components/omni-avatar/AgentAvatar", () => ({ AgentAvatar: () => <span data-avatar /> }));
vi.mock("@omniagents/ui/code-editor", () => ({
  CodeMirrorEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (v: string) => void; ariaLabel: string }) => (
    <textarea aria-label={ariaLabel} value={value} onChange={e => onChange(e.target.value)} />
  ),
}));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const base = { description: "", self_improvement: false, enabled: true, conversation: "auto" as const, skipped: [] };
const automations: Automation[] = [
  {
    ...base,
    name: "nightly-review",
    description: "reviews the day and proposes factory changes",
    instruction: "Review the factory's runs.\n\nPropose changes.",
    triggers: [{ provider: "schedule", event: "cron_fired", schedule: { name: "nightly-review", cron: "0 2 * * *" } }],
    agent: "improver",
    runs: [{ id: 7, agent: "improver", session_id: "s-7", status: "completed", started_at: 1_700_000_000, chat: true }],
  },
  {
    ...base,
    name: "docs-drift-on-push",
    description: "checks the docs still match the API",
    instruction: "Check the docs.",
    triggers: [{ provider: "github", event: "push", filter: { repos: ["*/api-service"] } }],
    agent: "maintainer",
    runs: [],
    skipped: [{ id: 3, provider: "github", event: "push", thread_key: null, received_at: 1_700_000_100, status: "ignored", detail: "no automation matches" }],
  },
  {
    ...base,
    name: "slack-dms",
    instruction: "Answer.",
    triggers: [{ provider: "slack", event: "message_dm" }],
    agent: "foreman",
    runs: [],
  },
];
let root: Root;
let node: HTMLDivElement;
const fetcher = vi.fn(async (url: string, _options?: RequestInit) => ({
  ok: true,
  json: async () =>
    url.includes("/definition/automations/nightly-review") && (_options?.method ?? "GET") === "GET"
      ? {
          commit: "a".repeat(40),
          path: "automations/nightly-review/automation.md",
          fields: {
            description: "reviews the day",
            agent: "improver",
            triggers: [{ provider: "schedule", schedule: { name: "nightly-review", cron: "0 2 * * *" } }],
            instruction: "Review the factory's runs.",
            conversation: "auto",
          },
        }
      : url.includes("/definition/automations/")
        ? { commit: "b".repeat(40), published: true }
        : url.endsWith("/factory/definition")
          ? { main: "a".repeat(40) }
          : url.includes("automation-routes")
      ? { agents: ["improver", "maintainer", "foreman"] }
      : url.endsWith("/enabled")
        ? { enabled: false }
        : { automations, chat_agent: "foreman", chat_agents: ["foreman", "maintainer", "improver"], agent_details: [{ id: "foreman", name: "Foreman" }] },
}));
const button = (name: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.getAttribute("aria-label") === name || b.textContent === name)!;
beforeEach(() => {
  fetcher.mockClear();
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  node = document.createElement("div");
  document.body.append(node);
  root = createRoot(node);
});
afterEach(() => {
  act(() => root.unmount());
  node.remove();
  vi.unstubAllGlobals();
});

it("reads each automation as one sentence and its health as one word", () => {
  expect(sentence(automations[0], "Improver")).toBe("Every day at 02:00 (server time) → Improver reviews the day and proposes factory changes");
  expect(sentence(automations[2], "Foreman")).toBe("When someone messages the factory directly in Slack → Foreman slack dms");
  // Events its filters left out are not a problem: it has nothing to do yet.
  expect(automations.map(health)).toEqual(["ok", "quiet", "quiet"]);
  expect(health({ ...automations[0], enabled: false })).toBe("off");
  expect(health({ ...automations[0], blocked: "Self-improvement is off" })).toBe("blocked");
  expect(health({ ...automations[0], runs: [{ ...automations[0].runs[0], status: "failed" }] })).toBe("failing");
});

const tabs = () => [...node.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
const openTab = async (label: string) =>
  act(async () => {
    tabs().find(t => t.textContent?.startsWith(label))!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
  });

it("shows one agent's automations at a time, in Chat's order", async () => {
  await act(async () => root.render(<AutomationsApp navigate={vi.fn()} />));
  expect(tabs().map(t => t.textContent)).toEqual(["Foreman · 1", "Maintainer · 1", "Improver · 1"]);
  // The first tab opens first.
  // A row leads with what it does; the tab already says who.
  expect(button("Slack dms").textContent).toBe("Slack dms");
  expect(node.textContent).not.toContain("Checks the docs still match the API");
  await openTab("Maintainer");
  // Filters leaving events out is them working: nothing to attend to.
  expect(button("Docs drift on push").textContent).toBe("Checks the docs still match the API");
  expect(node.querySelector('section[aria-label="Needs attention"]')).toBeNull();
  expect(node.textContent).not.toContain("judged");
});

it("shows what starts each automation as the event and a chip per filter value", async () => {
  const original = automations[1];
  automations[1] = {
    ...original,
    triggers: [
      { provider: "github", event: "workflow_run_completed", filter: { repos: ["*/api", "*/web", "*/site"], branches: ["main"], conclusions: ["failure"] } },
      { provider: "github", event: "push" },
    ],
  };
  try {
    await act(async () => root.render(<AutomationsApp navigate={vi.fn()} />));
    await openTab("Maintainer");
    const line = button("Docs drift on push").nextElementSibling!;
    const chips = [...line.querySelectorAll('[data-slot="badge"]')].map(b => b.textContent);
    // Several repositories fold into one chip on the list; it names them on hover.
    expect(chips).toEqual(["3 repos", "main", "failure", "+1"]);
    expect(line.textContent).toContain("A CI workflow finishes");
    expect(line.querySelector('[title="api, web, site"]')).not.toBeNull();
    expect(line.querySelector('[data-slot="badge"][class*="text-destructive"]')!.textContent).toBe("failure");
    // On its own page, every repository is its own chip.
    await act(async () => root.render(<AutomationsApp key="detail" name="docs-drift-on-push" navigate={vi.fn()} />));
    const when = node.querySelector('section[aria-label="When"]')!;
    expect([...when.querySelectorAll('[data-slot="badge"]')].map(b => b.textContent)).toEqual(["api", "web", "site", "main", "failure"]);
  } finally {
    automations[1] = original;
  }
});

it("flags the tab with something to do and says what it is", async () => {
  const failed = { ...automations[2], runs: [{ id: 9, agent: "foreman", session_id: "s-9", status: "failed", started_at: 1_700_000_200 }] };
  const blocked = { ...automations[0], blocked: "Self-improvement is off" };
  const original = [...automations];
  automations.splice(0, 3, blocked, automations[1], failed);
  try {
    await act(async () => root.render(<AutomationsApp navigate={vi.fn()} />));
    expect(tabs().filter(t => t.querySelector('[aria-label="Needs attention"]')).map(t => t.textContent)).toEqual(["Foreman · 1", "Improver · 1"]);
    await openTab("Foreman");
    expect(node.querySelector('section[aria-label="Needs attention"]')!.textContent).toContain("Its last run failed. Open it to see why.");
    await openTab("Improver");
    expect(node.textContent).toContain("Self-improvement is off. Open it to see how to turn it on.");
  } finally {
    automations.splice(0, 3, ...original);
  }
});

it("says when self-improvement stops an automation and opens Factory changes", async () => {
  const original = automations[0];
  automations[0] = { ...original, blocked: "Self-improvement is off" };
  const settings = vi.fn();
  try {
    await act(async () => root.render(<AutomationsApp name="nightly-review" navigate={vi.fn()} onFactorySettings={settings} />));
    expect(node.querySelector('[role="status"]')!.textContent).toContain("Self-improvement is off, so this automation can't run.");
    await act(async () => button("Open Factory changes").click());
    expect(settings).toHaveBeenCalled();
  } finally {
    automations[0] = original;
  }
});

it("turns an automation off from the list", async () => {
  await act(async () => root.render(<AutomationsApp navigate={vi.fn()} />));
  await openTab("Improver");
  await act(async () => button("Turn off Nightly review").click());
  expect(fetcher).toHaveBeenCalledWith(
    "/workplace/api/factory/automations/nightly-review/enabled",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ enabled: false }) }),
  );
  expect(button("Turn on Nightly review")).toBeDefined();
});

it("opens one automation with its overview, then its history and ignored events", async () => {
  const navigate = vi.fn();
  await act(async () => root.render(<AutomationsApp name="nightly-review" navigate={navigate} />));
  expect(node.textContent).toContain("Review the factory's runs.");
  expect(node.textContent).not.toContain("Propose changes.");
  await act(async () => button("Show the full instruction").click());
  expect(node.textContent).toContain("Propose changes.");
  await act(async () => button("Run now").click());
  expect(fetcher).toHaveBeenCalledWith("/workplace/api/factory/schedules/nightly-review/run", expect.objectContaining({ method: "POST" }));
  const history = [...node.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(t => t.textContent?.startsWith("History"))!;
  await act(async () => {
    history.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
  });
  await act(async () => button("Open chat").click());
  expect(navigate).toHaveBeenCalledWith("/personal?chat=s-7");
});

it("keeps ignored events behind a toggle", async () => {
  await act(async () => root.render(<AutomationsApp name="docs-drift-on-push" navigate={vi.fn()} />));
  const history = [...node.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(t => t.textContent?.startsWith("History"))!;
  await act(async () => {
    history.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
  });
  expect(node.textContent).not.toContain("no automation matches");
  await act(async () => button("Show the event it didn't run for").click());
  expect(node.textContent).toContain("no automation matches");
});

it("edits an automation where it is, as fields", async () => {
  await act(async () => root.render(<AutomationsApp name="nightly-review" navigate={vi.fn()} />));
  await act(async () => {});
  await act(async () => button("Edit").click());
  await act(async () => {});
  await act(async () => {});
  expect(document.querySelector<HTMLInputElement>("#automation-description")!.value).toBe("reviews the day");
  expect(document.querySelector<HTMLSelectElement>('select[aria-label="How often"]')!.value).toBe("daily");
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Time"]')!.value).toBe("02:00");
  await act(async () => button("Save").click());
  const save = fetcher.mock.calls.find(([url, options]) => url.includes("/definition/automations/nightly-review") && options?.method === "PUT")!;
  expect(JSON.parse(save[1]!.body as string)).toMatchObject({
    base_commit: "a".repeat(40),
    agent: "improver",
    triggers: [{ provider: "schedule", schedule: { name: "nightly-review", cron: "0 2 * * *" } }],
  });
});

it("adds an automation from a form", async () => {
  const navigate = vi.fn();
  await act(async () => root.render(<AutomationsApp navigate={navigate} />));
  await act(async () => {});
  await act(async () => button("New automation").click());
  expect(navigate).toHaveBeenCalledWith("/automations/%2Bnew");
  await act(async () => root.render(<AutomationsApp key="new" name="+new" navigate={navigate} />));
  await act(async () => {});
  await act(async () => {});
  const type = async (selector: string, value: string) =>
    act(async () => {
      const element = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!;
      const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
  await type("#automation-name", "Weekly Report");
  await type("#automation-description", "writes the weekly report");
  await type('textarea[aria-label="What it\'s told"]', "Write it.");
  await act(async () => button("Add automation").click());
  const save = fetcher.mock.calls.find(([url, options]) => url.includes("/definition/automations/weekly-report") && options?.method === "PUT")!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({
    base_commit: "a".repeat(40),
    description: "writes the weekly report",
    agent: null,
    triggers: [{ provider: "schedule", schedule: { name: "weekly-report", cron: "0 9 * * 1-5" } }],
    instruction: "Write it.",
    conversation: "auto",
  });
  expect(navigate).toHaveBeenCalledWith("/automations/weekly-report");
});

it("reads and writes the schedules people use", async () => {
  const { readCron, writeCron } = await import("./AutomationForm");
  expect(readCron("30 8 * * 1-5")).toMatchObject({ kind: "weekdays", time: "08:30" });
  expect(readCron("0 9 * * 1")).toMatchObject({ kind: "weekly", day: 1 });
  expect(readCron("*/5 * * * *")).toMatchObject({ kind: "custom" });
  expect(writeCron({ kind: "weekly", time: "07:15", day: 3, cron: "" })).toBe("15 7 * * 3");
});
