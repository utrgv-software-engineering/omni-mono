import { afterEach, expect, it, vi } from "vitest";

const sdkLoaded = vi.hoisted(() => vi.fn());
vi.mock("@microsoft/teams-js", () => {
  sdkLoaded();
  return {
    app: { initialize: async () => { throw new Error("Explicit Teams initialization"); } },
    nestedAppAuth: {},
  };
});

afterEach(() => vi.unstubAllGlobals());

it("keeps browser and MCP helpers free of Teams SDK import side effects", async () => {
  vi.stubGlobal("window", { fetch: vi.fn() });
  const teams = await import("./teams");
  expect(teams.inTeams()).toBe(false);
  expect(await teams.ticketUrl("/workplace/api/desktop/ws")).toBe("/workplace/api/desktop/ws");
  expect(sdkLoaded).not.toHaveBeenCalled();
  await expect(teams.initializeTeams()).rejects.toThrow("Explicit Teams initialization");
  expect(sdkLoaded).toHaveBeenCalledTimes(1);
});
