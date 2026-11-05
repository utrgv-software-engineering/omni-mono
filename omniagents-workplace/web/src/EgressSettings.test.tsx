import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { EgressSettings } from "./EgressSettings";
import { AllowlistEditor } from "./Governance";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const view = {
  enabled: true,
  deployment: ["pypi.org:443", "registry.npmjs.org:443"],
  own: null,
  effective: null,
  managed_by_installation: false,
};
const reply = (body: unknown) => ({ ok: true, json: async () => body }) as Response;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(view)));
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.clearAllMocks();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function form(name: "Deployment" | "Factory") {
  return container.querySelector(`form[aria-label="${name} allowlist"]`)!;
}

function checkbox(scope: Element, text: string) {
  return [...scope.querySelectorAll("label")].find((label) => label.textContent?.includes(text))!.querySelector('[role="checkbox"]') as HTMLButtonElement;
}

async function input(scope: Element, text: string) {
  const field = scope.querySelector("input") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, text);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function add(text: string) {
  const scope = [...form("Deployment").querySelectorAll("label")].find((label) => label.textContent?.includes("New destination"))!;
  await input(scope, text);
  await act(async () => ([...form("Deployment").querySelectorAll("button")].find((button) => button.textContent === "Add destination") as HTMLButtonElement).click());
}

async function submit(scope: Element) {
  await act(async () => scope.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

it("adds deployment destinations and saves checked entries only", async () => {
  await act(async () => root.render(<EgressSettings />));
  expect(container.querySelector("textarea")).toBeNull();
  expect(checkbox(form("Deployment"), "pypi.org:443").getAttribute("aria-checked")).toBe("true");
  await add(" EXAMPLE.COM:443 ");
  await act(async () => checkbox(form("Deployment"), "pypi.org:443").click());
  vi.mocked(fetch).mockResolvedValueOnce(reply({ pending: null })).mockResolvedValue(reply({ ...view, deployment: ["example.com:443", "registry.npmjs.org:443"] }));
  await submit(form("Deployment"));
  expect(fetch).toHaveBeenCalledWith("/workplace/api/deployment/egress", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ allow: ["example.com:443", "registry.npmjs.org:443"], reason: "" }),
  }));
  expect(toast.success).toHaveBeenCalledWith("Saved");
  expect(form("Deployment").textContent).not.toContain("pypi.org:443");
});

it("offers factory checkboxes only from the deployment and saves a subset", async () => {
  await act(async () => root.render(<EgressSettings />));
  expect(form("Factory").textContent).not.toContain("Add destination");
  expect(checkbox(form("Factory"), "Use all").getAttribute("aria-checked")).toBe("true");
  await act(async () => checkbox(form("Factory"), "pypi.org:443").click());
  expect(checkbox(form("Factory"), "Use all").getAttribute("aria-checked")).toBe("false");
  vi.mocked(fetch).mockResolvedValueOnce(reply({ pending: null }));
  await submit(form("Factory"));
  expect(fetch).toHaveBeenCalledWith("/workplace/api/factory/egress", expect.objectContaining({
    body: JSON.stringify({ allow: ["registry.npmjs.org:443"], reason: "" }),
  }));
});

it("distinguishes allow none from inheriting all destinations", async () => {
  await act(async () => root.render(<EgressSettings />));
  for (const host of view.deployment) await act(async () => checkbox(form("Factory"), host).click());
  expect(form("Factory").textContent).toContain("Saving blocks all destinations");
  vi.mocked(fetch).mockResolvedValueOnce(reply({ pending: null })).mockResolvedValue(reply({ ...view, own: [] }));
  await submit(form("Factory"));
  expect(fetch).toHaveBeenCalledWith("/workplace/api/factory/egress", expect.objectContaining({ body: JSON.stringify({ allow: [], reason: "" }) }));
  await act(async () => checkbox(form("Factory"), "Use all").click());
  vi.mocked(fetch).mockResolvedValueOnce(reply({ pending: null }));
  await submit(form("Factory"));
  expect(fetch).toHaveBeenCalledWith("/workplace/api/factory/egress", expect.objectContaining({ body: JSON.stringify({ allow: null, reason: "" }) }));
});

it.each(["https://example.com/path", "example.com:0", "example.com:65536", "bad host", "*.com"])("rejects an invalid new destination: %s", async (entry) => {
  await act(async () => root.render(<EgressSettings />));
  await add(entry);
  expect(form("Deployment").querySelector('[role="alert"]')?.textContent).toContain("Enter a hostname");
  expect(form("Deployment").querySelectorAll('[role="checkbox"]')).toHaveLength(2);
});

it("rejects duplicates and accepts wildcard entries", async () => {
  await act(async () => root.render(<EgressSettings />));
  await add("PYPI.ORG:443");
  expect(form("Deployment").textContent).toContain("already listed");
  await add("*.example.com:443");
  expect(checkbox(form("Deployment"), "*.example.com:443").getAttribute("aria-checked")).toBe("true");
});

it("keeps installation-managed deployment checkboxes read-only", async () => {
  vi.mocked(fetch).mockResolvedValue(reply({ ...view, managed_by_installation: true }));
  await act(async () => root.render(<EgressSettings />));
  expect((form("Deployment").querySelector("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
  expect(form("Deployment").textContent).not.toContain("Add destination");
  expect(form("Deployment").textContent).toContain("Factories page");
  expect(form("Factory").querySelector('[role="checkbox"]')).not.toBeNull();
});

it("does not expose editable controls when loading fails", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("Network unavailable"));
  await act(async () => root.render(<EgressSettings />));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Network unavailable");
  expect(container.querySelector("form")).toBeNull();
  await act(async () => (container.querySelector("button") as HTMLButtonElement).click());
  expect(form("Deployment")).not.toBeNull();
});

it("reports pending approval and restores the saved selection", async () => {
  await act(async () => root.render(<EgressSettings />));
  await add("example.com:443");
  vi.mocked(fetch).mockResolvedValueOnce(reply({ pending: { id: "decision" } }));
  await submit(form("Deployment"));
  expect(toast.success).toHaveBeenCalledWith("Saved. Widening it waits for the governance owner.");
  expect(form("Deployment").textContent).not.toContain("example.com:443");
});

it("preserves checkbox edits on save failure", async () => {
  await act(async () => root.render(<EgressSettings />));
  await act(async () => checkbox(form("Deployment"), "pypi.org:443").click());
  vi.mocked(fetch).mockRejectedValueOnce(new Error("Administrator required"));
  await submit(form("Deployment"));
  expect(toast.error).toHaveBeenCalledWith("Administrator required");
  expect(checkbox(form("Deployment"), "pypi.org:443").getAttribute("aria-checked")).toBe("false");
});

it("preserves existing narrower factory entries instead of silently broadening them", async () => {
  vi.mocked(fetch).mockResolvedValue(reply({ ...view, deployment: ["*.example.com:443"], own: ["api.example.com:443"] }));
  await act(async () => root.render(<EgressSettings />));
  expect(checkbox(form("Factory"), "api.example.com:443").getAttribute("aria-checked")).toBe("true");
  expect(checkbox(form("Factory"), "*.example.com:443").getAttribute("aria-checked")).toBe("false");
});

it("reloads the installation editor after saving an empty list", async () => {
  const load = vi.fn().mockResolvedValueOnce(["pypi.org:443"]).mockResolvedValue([]);
  const save = vi.fn().mockResolvedValue({ pending: null });
  await act(async () => root.render(<AllowlistEditor allowAdd label="Deployment allowlist" help="Deployment destinations" load={load} save={save} />));
  await act(async () => checkbox(form("Deployment"), "pypi.org:443").click());
  await submit(form("Deployment"));
  expect(save).toHaveBeenCalledWith([], "");
  expect(form("Deployment").textContent).toContain("No destinations yet");
  expect(load).toHaveBeenCalledTimes(2);
});
