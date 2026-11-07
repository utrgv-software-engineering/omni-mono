import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FactoryModelDialog } from "./FactoryModel";
import { factoryModel } from "./api";

vi.mock("./api", () => ({ factoryModel: { get: vi.fn(), validate: vi.fn(), addProvider: vi.fn() } }));
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
  const field = document.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement;
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
