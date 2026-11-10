import { expect, it, vi } from "vitest";
import { describeEvent } from "./automation-triggers";
import { loadScopeLabels, scopeLabel } from "./automation-scope-labels";

it("names Teams messages readably", () => {
  expect(describeEvent({ provider: "teams", event: "message" })).toBe("Someone messages the factory in Teams");
});

it("resolves scoped labels without pipeline discovery or changing IDs", async () => {
  const trigger = { provider: "azure-devops", filter: { connections: ["one"], projects: ["project"], repositories: ["repo"] } };
  const original = JSON.stringify(trigger);
  const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("connections") ? { connections: [{ id: "one", label: "School", organization: "org", scopes: [{ project: "project", repositories: ["repo"] }] }] } : url.includes("project=") ? { repositories: [{ id: "repo", name: "Application" }] } : { projects: [{ id: "project", name: "Faculty" }] } }));
  const labels = await loadScopeLabels([trigger], fetcher);
  expect(scopeLabel(labels, trigger, "connection", "one")).toBe("School (org)");
  expect(scopeLabel(labels, trigger, "project", "project")).toBe("Faculty");
  expect(scopeLabel(labels, trigger, "repo", "repo")).toBe("Application");
  expect(JSON.stringify(trigger)).toBe(original);
  expect(fetcher.mock.calls.every(([url]) => !url.includes("pipelines"))).toBe(true);
});

it("keeps a safe readable fallback when lookups fail", async () => {
  const trigger = { provider: "azure-devops", filter: { connections: ["deleted"] } };
  const labels = await loadScopeLabels([trigger], async () => { throw new Error("unavailable"); });
  expect(scopeLabel(labels, trigger, "connection", "deleted")).toBe("Unavailable connection (deleted)");
});
