// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { AgentReadiness } from "./AgentReadiness";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.unstubAllGlobals());

it("shows an agent's weeks, newest first, with lessons, runs and grades", async () => {
  const week = (start: number, lessons: number) => ({
    start,
    messages: 3,
    lessons,
    personal_lessons: 1,
    runs: 2,
    failed: 1,
    pass_rates: start > 1 ? { task_compliance: { passed: 1, graded: 2, rate: 0.5, interval: [0.1, 0.9] } } : {},
  });
  const fetcher = vi.fn(async (_url: string) => ({
    ok: true,
    json: async () => ({ agent: "designer", factory_error: null, weeks: [week(0, 5), week(604800, 1)] }),
  }));
  vi.stubGlobal("fetch", fetcher);
  const node = document.createElement("div");
  document.body.append(node);
  const root = createRoot(node);
  await act(async () => root.render(<AgentReadiness agent="designer" name="Designer" onClose={() => {}} />));
  expect(fetcher.mock.calls[0][0]).toContain("/workplace/api/personal/agents/designer/readiness");
  const rows = [...document.querySelectorAll("tbody tr")].map((row) => row.textContent);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toContain("1 (1)");
  expect(rows[0]).toContain("50% (1/2)");
  expect(rows[1]).toContain("5 (1)");
  expect(document.body.textContent).toContain("task compliance");
  act(() => root.unmount());
  node.remove();
});
