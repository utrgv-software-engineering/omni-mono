// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutomationsApp, health, sentence, type Automation } from "./Automations";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@omniagents/chat-ui/components/omni-avatar/AgentAvatar", () => ({ AgentAvatar: () => <span data-avatar /> }));
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
    url.includes("automation-routes")
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
  expect(automations.map(health)).toEqual(["ok", "never", "quiet"]);
  expect(health({ ...automations[0], enabled: false })).toBe("off");
  expect(health({ ...automations[0], runs: [{ ...automations[0].runs[0], status: "failed" }] })).toBe("failing");
});

it("puts what needs attention first, then each agent's automations in Chat's order", async () => {
  await act(async () => root.render(<AutomationsApp navigate={vi.fn()} />));
  const sections = [...node.querySelectorAll("section")].map(s => s.getAttribute("aria-label"));
  expect(sections).toEqual(["Needs attention", "Foreman", "Improver"]);
  const attention = node.querySelector('section[aria-label="Needs attention"]')!;
  expect(attention.textContent).toContain("checks the docs still match the API");
  expect(attention.querySelector('[aria-label="Never runs: its filters leave out every event"]')).not.toBeNull();
  expect(node.textContent).not.toContain("judged");
});

it("turns an automation off from the list", async () => {
  await act(async () => root.render(<AutomationsApp navigate={vi.fn()} />));
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
  await act(async () => button("Show the event it ignored").click());
  expect(node.textContent).toContain("no automation matches");
});
