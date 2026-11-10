import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FactoryModelDialog } from "./FactoryModel";
import { factoryModel, governance, type FactoryModelState } from "./api";

vi.mock("./api", () => ({ governance: { get: vi.fn(), saveOwners: vi.fn() }, factoryModel: { get: vi.fn(), validate: vi.fn(), addProvider: vi.fn(), updateProvider: vi.fn(), addModel: vi.fn(), updateModel: vi.fn(), removeModel: vi.fn(), removeProvider: vi.fn(), setDefault: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@omniagents/ui/tabs", () => {
  const Wrapper = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  return { Tabs: Wrapper, TabsContent: Wrapper, TabsList: Wrapper, TabsTrigger: Wrapper };
});
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let container: HTMLDivElement;
let root: Root;
const state = { path: "models.json", default: null, voice_default: null, providers: [], chatgpt: null, runtime: null, factory_error: null };

beforeEach(async () => {
  vi.clearAllMocks();
  vi.mocked(governance.get).mockResolvedValue({ decisions: [], owner: true, gated: true, owners: [], can_manage_owners: true, managed_by_installation: false, people: [] });
  vi.mocked(factoryModel.get).mockResolvedValue(state);
  vi.mocked(factoryModel.addProvider).mockResolvedValue({ ...state, ref: "local/my-deployment" });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<FactoryModelDialog open onOpenChange={() => {}} />));
  const select = [...document.querySelectorAll("select")].find((field) => field.value === "ollama")!;
  await act(async () => {
    select.value = "openai-compatible";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await input("Address", "https://azure.example/openai/v1");
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});
async function input(label: string, value: string) {
  const scope = label === "Model or deployment name" ? localForm() : document;
  const field = scope.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement;
  expect(field).not.toBeNull();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function localForm() {
  return document.querySelector('input[aria-label="Address"]')!.parentElement!.parentElement!;
}
async function click(text: string) {
  const button = [...localForm().querySelectorAll("button")].find((item) => item.textContent === text)!;
  expect(button).toBeDefined();
  await act(async () => button.click());
}
it("saves a manually entered deployment without checking discovery", async () => {
  await input("Model or deployment name", " my-deployment ");
  await click("Save");
  expect(factoryModel.validate).not.toHaveBeenCalled();
  expect(factoryModel.addProvider).toHaveBeenCalledWith(expect.objectContaining({
    kind: "openai-compatible", base_url: "https://azure.example/openai/v1",
    model: expect.objectContaining({ id: "my-deployment", verified: false }),
  }));
});
it.each(["failure", "empty"])("allows manual saving after %s discovery", async (outcome) => {
  vi.mocked(factoryModel.validate).mockResolvedValue(outcome === "failure" ? { ok: false, code: "network" } : { ok: true, choices: [] });
  await click("Check");
  await input("Model or deployment name", "my-deployment");
  await click("Save");
  expect(factoryModel.addProvider).toHaveBeenCalledTimes(1);
});

it("discards discovery when the endpoint changes during Check", async () => {
  let resolve!: (result: { ok: boolean; choices: never[] }) => void;
  vi.mocked(factoryModel.validate).mockReturnValue(new Promise((done) => { resolve = done; }));
  await click("Check");
  await input("Address", "https://other.example/v1");
  await input("Model or deployment name", "my-deployment");
  await act(async () => resolve({ ok: true, choices: [] }));
  expect(localForm().textContent).not.toContain("It answered, but lists no models.");
  await click("Save");
  expect(factoryModel.addProvider).toHaveBeenCalledWith(expect.objectContaining({ base_url: "https://other.example/v1" }));
});
it("keeps Save disabled for a blank deployment or address", async () => {
  await input("Model or deployment name", "   ");
  await click("Save");
  expect(factoryModel.addProvider).not.toHaveBeenCalled();
  await input("Address", "");
  await input("Model or deployment name", "my-deployment");
  await click("Save");
  expect(factoryModel.addProvider).not.toHaveBeenCalled();
});
it("saves a discovered choice with its metadata", async () => {
  const choice = { id: "discovered", label: "Discovered model", blurb: "", maxInput: 1000, maxOutput: 100, verified: true };
  vi.mocked(factoryModel.validate).mockResolvedValue({ ok: true, choices: [choice] });
  await click("Check");
  await click("Save");
  expect(factoryModel.addProvider).toHaveBeenCalledWith(expect.objectContaining({ model: choice }));
});

it("blocks editing before a non-owner fills in model credentials", async () => {
  vi.mocked(governance.get).mockResolvedValue({ decisions: [], owner: false, gated: true, owners: [], can_manage_owners: true, managed_by_installation: false, people: [] });
  await act(async () => root.render(<FactoryModelDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<FactoryModelDialog open onOpenChange={() => {}} />));
  expect(document.body.textContent).toContain("Only governance owners can change models");
  expect(document.querySelector('input[aria-label="Address"]')?.matches(":disabled") ?? true).toBe(true);
  expect(document.body.textContent).toContain("Save governance owners");
});

it("lets an administrator assign ownership and then edit models", async () => {
  const view = { decisions: [], owner: false, gated: true, owners: [], can_manage_owners: true, managed_by_installation: false, people: [{ id: "bill", name: "Bill", username: "bill", admin: true, disabled: false }] };
  vi.mocked(governance.get).mockResolvedValue(view);
  await act(async () => root.render(<FactoryModelDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<FactoryModelDialog open onOpenChange={() => {}} />));
  await act(async () => (document.querySelector('input[type="checkbox"]') as HTMLInputElement).click());
  vi.mocked(governance.saveOwners).mockResolvedValue({ owners: ["bill"] });
  vi.mocked(governance.get).mockResolvedValue({ ...view, owners: ["bill"], owner: true });
  const save = [...document.querySelectorAll("button")].find((button) => button.textContent === "Save governance owners")!;
  await act(async () => save.click());
  expect(governance.saveOwners).toHaveBeenCalledWith(["bill"]);
  expect(document.body.textContent).not.toContain("Only governance owners can change models");
  expect(document.querySelector('input[aria-label="Address"]')?.matches(":disabled")).toBe(false);
});

it("fails closed and offers retry when permissions cannot load", async () => {
  vi.mocked(governance.get).mockRejectedValue(new Error("Permission lookup failed"));
  await act(async () => root.render(<FactoryModelDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<FactoryModelDialog open onOpenChange={() => {}} />));
  expect(document.body.textContent).toContain("Permission lookup failed");
  expect(document.querySelector('input[aria-label="Address"]')).toBeNull();
  vi.mocked(governance.get).mockResolvedValue({ decisions: [], owner: true, gated: true, owners: [], can_manage_owners: true, managed_by_installation: false, people: [] });
  await act(async () => [...document.querySelectorAll("button")].find((button) => button.textContent === "Retry")!.click());
  expect(document.querySelector('input[aria-label="Address"]')).not.toBeNull();
});

const provider = {
  name: "azure-prod", type: "openai-compatible", base_url: "https://old.example/v1", key: "••••",
  models: [{ ref: "azure-prod/deployment", id: "deployment", model: "deployment", label: "Production", realtime: false, maxInput: 1000, maxOutput: 100 }],
};
async function existingProvider(defaultModel: string | null = null) {
  vi.mocked(factoryModel.get).mockResolvedValue({ ...state, providers: [provider], default: defaultModel });
  await act(async () => root.render(<FactoryModelDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<FactoryModelDialog open onOpenChange={() => {}} />));
}
async function action(label: string) {
  const button = document.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement;
  expect(button).not.toBeNull();
  await act(async () => button.click());
}
async function textAction(text: string) {
  const button = [...document.querySelectorAll("button")].find((b) => b.textContent === text)!;
  expect(button).toBeDefined();
  await act(async () => button.click());
}
it("edits an existing provider without resubmitting a masked credential or changing its ID", async () => {
  await existingProvider();
  await action("Edit provider azure-prod");
  expect((document.querySelector('input[aria-label="Replacement API key"]') as HTMLInputElement).value).toBe("");
  await input("Provider address", "https://new.example/v1");
  vi.mocked(factoryModel.updateProvider).mockResolvedValue({ ...state, providers: [{ ...provider, base_url: "https://new.example/v1" }] });
  await textAction("Save provider");
  expect(factoryModel.updateProvider).toHaveBeenCalledWith("azure-prod", { base_url: "https://new.example/v1" });
  expect(factoryModel.validate).not.toHaveBeenCalled();
  expect(document.body.textContent).toContain("https://new.example/v1");
});
it("cancels edits and keeps a failed provider edit open without altering the saved card", async () => {
  await existingProvider();
  await action("Edit provider azure-prod");
  await input("Provider address", "https://cancelled.example/v1");
  await textAction("Cancel");
  expect(factoryModel.updateProvider).not.toHaveBeenCalled();
  await action("Edit provider azure-prod");
  expect((document.querySelector('input[aria-label="Provider address"]') as HTMLInputElement).value).toBe(provider.base_url);
  vi.mocked(factoryModel.updateProvider).mockRejectedValue(new Error("Save denied"));
  await textAction("Save provider");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Save denied");
  expect(document.querySelector('input[aria-label="Provider address"]')).not.toBeNull();
});
it("adds, edits and removes models under the selected provider", async () => {
  await existingProvider();
  await action("Add model to azure-prod");
  await input("Model ID", "new-model");
  await input("Model label", "New model");
  vi.mocked(factoryModel.addModel).mockResolvedValue({ ...state, providers: [provider] });
  await textAction("Save model");
  expect(factoryModel.addModel).toHaveBeenCalledWith("azure-prod", expect.objectContaining({ id: "new-model", label: "New model" }));
  await action("Edit model azure-prod/deployment");
  expect((document.querySelector('input[aria-label="Model ID"]') as HTMLInputElement).disabled).toBe(true);
  await input("Model label", "Renamed label");
  vi.mocked(factoryModel.updateModel).mockResolvedValue({ ...state, providers: [provider] });
  await textAction("Save model");
  expect(factoryModel.updateModel).toHaveBeenCalledWith("azure-prod", "deployment", expect.objectContaining({ label: "Renamed label" }));
  await action("Remove model azure-prod/deployment");
  expect(factoryModel.removeModel).not.toHaveBeenCalled();
  vi.mocked(factoryModel.removeModel).mockResolvedValue({ ...state, providers: [{ ...provider, models: [] }] });
  await textAction("Confirm removal");
  expect(factoryModel.removeModel).toHaveBeenCalledWith("azure-prod", "deployment");
});
it("disables default model removal and provider/model mutations for non-owners", async () => {
  await existingProvider("azure-prod/deployment");
  expect(document.querySelector('button[aria-label="Remove model azure-prod/deployment"]')?.matches(":disabled")).toBe(true);
  expect(document.querySelector('button[aria-label="Remove provider azure-prod"]')?.matches(":disabled")).toBe(true);
  vi.mocked(governance.get).mockResolvedValue({ decisions: [], owner: false, gated: true, owners: [], can_manage_owners: false, managed_by_installation: false, people: [] });
  await existingProvider();
  await action("Edit provider azure-prod");
  await action("Add model to azure-prod");
  expect(document.querySelector('input[aria-label="Replacement API key"]')).toBeNull();
  expect(factoryModel.updateProvider).not.toHaveBeenCalled();
  expect(factoryModel.addModel).not.toHaveBeenCalled();
});

it("rotates a key explicitly and prevents repeated submission while saving", async () => {
  await existingProvider();
  await action("Edit provider azure-prod");
  await input("Replacement API key", "test-new-key");
  let resolve!: (saved: FactoryModelState) => void;
  vi.mocked(factoryModel.updateProvider).mockReturnValue(new Promise((done) => { resolve = done; }));
  await textAction("Save provider");
  expect(factoryModel.updateProvider).toHaveBeenCalledWith("azure-prod", { api_key: "test-new-key" });
  expect(document.querySelector('input[aria-label="Replacement API key"]')?.matches(":disabled")).toBe(true);
  await textAction("Saving…");
  expect(factoryModel.updateProvider).toHaveBeenCalledTimes(1);
  await act(async () => resolve({ ...state, providers: [provider] }));
  expect(document.querySelector('input[aria-label="Replacement API key"]')).toBeNull();
  expect(document.body.textContent).not.toContain("test-new-key");
});
it("cancels a model draft and retains an unsuccessful draft for correction", async () => {
  await existingProvider();
  await action("Add model to azure-prod");
  await input("Model ID", "cancelled");
  await textAction("Cancel");
  expect(factoryModel.addModel).not.toHaveBeenCalled();
  await action("Edit model azure-prod/deployment");
  await input("Model label", "Unsaved label");
  vi.mocked(factoryModel.updateModel).mockRejectedValue(new Error("Invalid model settings"));
  await textAction("Save model");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Invalid model settings");
  expect((document.querySelector('input[aria-label="Model label"]') as HTMLInputElement).value).toBe("Unsaved label");
  expect(document.body.textContent).toContain("Production");
  await textAction("Cancel");
  expect(document.querySelector('input[aria-label="Model label"]')).toBeNull();
});
it("preserves the ChatGPT runtime choices when provider settings are saved", async () => {
  vi.mocked(factoryModel.get).mockResolvedValue({ ...state, providers: [provider], chatgpt: { state: "signed_in", email: "test@example.test", plan: "Plus" }, runtime: { default: "codex/text", models: [{ ref: "codex/text", label: "ChatGPT model", provider: "codex", realtime: false, available: true, why: "", default: true }] } });
  await act(async () => root.render(<FactoryModelDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<FactoryModelDialog open onOpenChange={() => {}} />));
  await action("Edit provider azure-prod");
  vi.mocked(factoryModel.updateProvider).mockResolvedValue({ ...state, providers: [provider] });
  await textAction("Save provider");
  expect(document.body.textContent).toContain("Signed in as test@example.test");
  expect([...document.querySelectorAll("option")].some((o) => o.value === "codex/text")).toBe(true);
});
it("restores the project default and keeps the selection unchanged on failure", async () => {
  await existingProvider("azure-prod/deployment");
  const select = document.querySelector('select[aria-label="Default model"]') as HTMLSelectElement;
  vi.mocked(factoryModel.setDefault).mockRejectedValue(new Error("Default denied"));
  await act(async () => { select.value = ""; select.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(factoryModel.setDefault).toHaveBeenCalledWith("");
  expect(select.value).toBe("azure-prod/deployment");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Default denied");
  vi.mocked(factoryModel.setDefault).mockResolvedValue({ ...state, providers: [provider] });
  await act(async () => { select.value = ""; select.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(select.value).toBe("");
  expect(document.querySelector('button[aria-label="Remove model azure-prod/deployment"]')?.matches(":disabled")).toBe(false);
});
it("accepts a manual API-provider model without discovery", async () => {
  const form = document.querySelector('input[id="model-key"]')!.parentElement!.parentElement!;
  const key = form.querySelector('input[type="password"]') as HTMLInputElement;
  const model = form.querySelector('input[aria-label="Model or deployment name"]') as HTMLInputElement;
  await act(async () => {
    for (const [field, value] of [[key, "test-key"], [model, "manual-api-model"]] as const) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
      field.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  await act(async () => [...form.querySelectorAll("button")].find((b) => b.textContent === "Save")!.click());
  expect(factoryModel.addProvider).toHaveBeenCalledWith(expect.objectContaining({ kind: "openai", model: expect.objectContaining({ id: "manual-api-model" }) }));
  expect(factoryModel.validate).not.toHaveBeenCalled();
});
