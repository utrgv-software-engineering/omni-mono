import { describe, expect, it } from "vitest";
import { CLIENT_IDENTITY } from "./client-identity.js";

describe("CLIENT_IDENTITY", () => {
  it("is the TUI at the version of the omniagents it ships in", () => {
    expect(CLIENT_IDENTITY.name).toBe("omniagents-tui");
    expect(CLIENT_IDENTITY.version).toMatch(/^\d+\.\d+\.\d+/);
  });
});
