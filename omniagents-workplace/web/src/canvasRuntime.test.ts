// The canvas runtime (omniagents_workplace/canvas/support.js) runs the
// fixture's boards: no binding left unrendered, and a click re-renders.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

const root = join(__dirname, "..", "..");
const runtime = readFileSync(
  join(root, "omniagents_workplace", "canvas", "support.js"),
  "utf8",
);
const fixture = join(root, "tests", "fixtures", "canvas", "studio-redesign");

function mount(html: string) {
  const dom = new JSDOM(html.replace('<script src="./support.js"></script>', ""), {
    runScripts: "outside-only",
  });
  const win = dom.window as unknown as Window & {
    __dcNoAutoMount: boolean;
    __dc: { mount: (d: Document) => unknown };
    eval: (code: string) => unknown;
  };
  win.__dcNoAutoMount = true;
  win.eval(runtime);
  win.__dc.mount(win.document);
  return win.document;
}

describe("the canvas runtime", () => {
  for (const name of readdirSync(fixture).filter((f) => f.endsWith(".dc.html"))) {
    it(`renders ${name}`, () => {
      const doc = mount(readFileSync(join(fixture, name), "utf8"));
      expect(doc.documentElement.getAttribute("data-dc-ready")).toBe("");
      expect(doc.body.innerHTML).not.toContain("{{");
      expect(doc.querySelectorAll("sc-if, sc-for, helmet").length).toBe(0);
      // The helmet's fonts and styles are in the head.
      expect(doc.head.querySelectorAll("style").length).toBeGreaterThan(0);
    });
  }

  it("re-renders on setState", () => {
    const doc = mount(`<html><head></head><body><x-dc>
      <p class="n">{{count}}</p><button onClick="{{bump}}">+</button>
      <sc-if value="{{big}}"><b>big</b></sc-if>
      <sc-for list="{{items}}" as="i"><li>{{i.label}}</li></sc-for>
    </x-dc><script type="text/x-dc">
      class Component extends DCLogic {
        constructor(p) { super(p); this.state = { count: 1 }; }
        renderVals() {
          const s = this.state;
          return { count: s.count, big: s.count > 1, bump: () => this.setState({ count: s.count + 1 }),
                   items: [{ label: "a" }, { label: "b" }] };
        }
      }
    </script></body></html>`);
    expect(doc.querySelector(".n")?.textContent).toBe("1");
    expect(doc.querySelector("b")).toBeNull();
    expect([...doc.querySelectorAll("li")].map((li) => li.textContent)).toEqual(["a", "b"]);
    (doc.querySelector("button") as HTMLButtonElement).click();
    expect(doc.querySelector(".n")?.textContent).toBe("2");
    expect(doc.querySelector("b")?.textContent).toBe("big");
  });

  it("shows the markup with a note when the script fails", () => {
    const doc = mount(`<html><head></head><body><x-dc><p>{{x}}</p></x-dc>
      <script type="text/x-dc">throw new Error("nope")</script></body></html>`);
    expect(doc.documentElement.getAttribute("data-dc-ready")).toBe("failed");
    expect(doc.body.textContent).toContain("did not run: nope");
  });
});
