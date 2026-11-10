// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FactoryStopBanner, FactoryStopControl } from "./FactoryStop";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const running = { stopped: null, message: "", own: null };
const stopped = {
  stopped: { stopped_by: "eric", reason: "drill", scope: "factory" },
  message: "This factory is stopped by eric: drill. An administrator can resume it.",
  own: { stopped_by: "eric", reason: "drill", scope: "factory" },
};
const reply = (body: unknown) => ({ ok: true, json: async () => body }) as Response;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function button(name: string) {
  const found = [...document.querySelectorAll("button")].find((b) => b.textContent === name);
  if (!found) throw new Error(`No button ${name}`);
  return found;
}

it("stops the factory with a reason", async () => {
  vi.mocked(fetch).mockResolvedValue(reply(running));
  await act(async () => root.render(<FactoryStopControl />));
  await act(async () => button("Stop factory").click());
  const input = document.querySelector("input") as HTMLInputElement;
  expect(button("Stop").disabled).toBe(true);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, "drill");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  vi.mocked(fetch).mockResolvedValue(reply(stopped));
  await act(async () => button("Stop").click());
  expect(fetch).toHaveBeenCalledWith(
    "/workplace/api/factory/stop",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ reason: "drill" }) }),
  );
  expect(container.textContent).toContain("stopped by eric: drill");
  expect(button("Resume factory")).toBeTruthy();
});

it("shows the stop over chats and nothing while running", async () => {
  vi.mocked(fetch).mockResolvedValue(reply(running));
  await act(async () => root.render(<FactoryStopBanner />));
  expect(container.textContent).toBe("");
  vi.mocked(fetch).mockResolvedValue(reply({ ...stopped, own: null }));
  await act(async () => root.render(<FactoryStopBanner key="again" />));
  expect(container.textContent).toContain("stopped by eric");
});
