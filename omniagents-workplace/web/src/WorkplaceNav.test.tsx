// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Hash } from "lucide-react";
import { WorkplaceNav } from "./WorkplaceNav";

vi.mock("@omniagents/chat-ui/components/OmniRailAvatar", () => ({OmniRailAvatar: () => <canvas data-testid="rail-avatar" />}));
let mobile = false;
vi.mock("@omniagents/ui/hooks/use-mobile", () => ({
  useIsMobile: () => mobile,
}));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let root: Root;
let container: HTMLDivElement;
const navigate = vi.fn();
const sections = ["Chat", "Slack", "GitHub", "Linear", "Memory"].map(
  (name) => ({
    name,
    to: name === "Chat" ? "/personal" : "/" + name.toLowerCase(),
    icon: Hash,
    active: name === "Memory",
  }),
);
const render = () =>
  act(() =>
    root.render(
      <WorkplaceNav
        sections={sections}
        brand="Workplace"
        navigate={navigate}
        account={() => <button>Account</button>}
      />,
    ),
  );
beforeEach(() => {
  mobile = false;
  navigate.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

it("keeps desktop destinations as accessible links and preserves modified clicks", () => {
  render();
  expect(container.querySelectorAll("nav a")).toHaveLength(5);
  expect(container.querySelector('[aria-current="page"]')?.textContent).toBe(
    "Memory",
  );
  const link = container.querySelector("a")!;
  // JSDOM can follow hash links; a real modified click is left to the browser.
  link.href = "#personal";
  act(() =>
    link.dispatchEvent(
      new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        ctrlKey: true,
      }),
    ),
  );
  expect(navigate).not.toHaveBeenCalled();
  act(() =>
    link.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    ),
  );
  expect(navigate).toHaveBeenCalledWith("/personal");
});

it("makes remaining mobile destinations reachable through More", async () => {
  mobile = true;
  render();
  expect(container.querySelectorAll("nav a")).toHaveLength(3);
  const more = container.querySelector<HTMLButtonElement>(
    '[aria-label="More apps and settings"]',
  )!;
  expect(more.dataset.variant).toBe("secondary");
  await act(async () => more.click());
  const link = document.querySelector<HTMLAnchorElement>(
    '[aria-label="More apps"] a[href="/memory"]',
  )!;
  expect(link).not.toBeNull();
  await act(async () => link.click());
  expect(navigate).toHaveBeenCalledWith("/memory");
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

it("hides phone navigation for a soft keyboard but not a hardware-keyboard focus", () => {
  mobile = true;
  const viewport = new EventTarget() as EventTarget & { height: number };
  viewport.height = window.innerHeight;
  vi.stubGlobal("visualViewport", viewport);
  render();
  const input = document.createElement("textarea");
  container.appendChild(input);
  act(() => input.focus());
  const nav = container.querySelector("nav")!;
  expect(nav.classList.contains("hidden")).toBe(false);
  act(() => {
    viewport.height -= 300;
    viewport.dispatchEvent(new Event("resize"));
  });
  expect(nav.classList.contains("hidden")).toBe(true);
  expect(
    document.documentElement.style.getPropertyValue(
      "--workplace-viewport-height",
    ),
  ).toBe(`${viewport.height}px`);
  act(() => {
    viewport.height = window.innerHeight;
    viewport.dispatchEvent(new Event("resize"));
  });
  expect(nav.classList.contains("hidden")).toBe(false);
});


it("shows the Omni brand link only outside Personal in personal mode", () => {
  const show = (active: boolean, personalMode: boolean) => act(() => root.render(
    <WorkplaceNav sections={sections.map(s => ({...s, active: s.to === "/personal" ? active : false}))}
      brand="Workplace" personalMode={personalMode} navigate={navigate} account={() => <button>Account</button>} />
  ));
  show(false, true);
  expect(container.querySelector('[data-testid="rail-avatar"]')).not.toBeNull();
  act(() => container.querySelector<HTMLAnchorElement>('[aria-label="Open Omni"]')!.click());
  expect(navigate).toHaveBeenCalledWith("/personal");
  show(true, true);
  expect(container.querySelector('[aria-label="Open Omni"]')).toBeNull();
  expect(container.querySelector('[data-testid="rail-avatar"]')).toBeNull();
  show(false, false);
  expect(container.querySelector('[aria-label="Workplace"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="rail-avatar"]')).toBeNull();
});
