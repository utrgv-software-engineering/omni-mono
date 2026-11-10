// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { factoryPath, factoryUrl, factoryStorageKey } from "./factory-scope";
const a = "/factories/" + "a".repeat(32);
const b = "/factories/" + "b".repeat(32);
describe("explicit factory scope", () => {
  it("scopes APIs, socket URLs, files and browser state to the current tab", () => {
    window.history.replaceState(null, "", a + "/personal");
    expect(factoryPath()).toBe("/personal");
    expect(factoryUrl("/workplace/api/session")).toBe(
      a + "/workplace/api/session",
    );
    const ws = new URL(window.location.href);
    ws.protocol = "ws:";
    ws.pathname = "/workplace/api/personal/runtime/ws";
    expect(new URL(factoryUrl(ws.href)).pathname).toBe(
      a + "/workplace/api/personal/runtime/ws",
    );
    expect(factoryUrl("/library/view/one/key/file.png")).toBe(
      a + "/library/view/one/key/file.png",
    );
    expect(factoryUrl(b + "/personal")).toBe(b + "/personal");
    const key = factoryStorageKey("selected-chat");
    window.history.replaceState(null, "", b + "/personal");
    expect(factoryStorageKey("selected-chat")).not.toBe(key);
    expect(factoryUrl("/workplace/api/session")).toBe(
      b + "/workplace/api/session",
    );
  });
  it("preserves external URLs and single-factory behavior", () => {
    expect(factoryUrl("https://github.com/org/repo")).toBe(
      "https://github.com/org/repo",
    );
    window.history.replaceState(null, "", "/personal");
    expect(factoryUrl("/workplace/api/session")).toBe("/workplace/api/session");
    expect(factoryStorageKey("selected-chat")).toBe("selected-chat");
  });
});
