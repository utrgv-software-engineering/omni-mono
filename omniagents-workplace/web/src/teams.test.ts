import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  initialize: vi.fn(), context: vi.fn(), supported: vi.fn(), create: vi.fn(),
  silent: vi.fn(), popup: vi.fn(), getAccount: vi.fn(), provider: vi.fn(), fetch: vi.fn(),
}));
vi.mock("@microsoft/teams-js", () => ({ app: { initialize: mocks.initialize, getContext: mocks.context }, nestedAppAuth: { isNAAChannelRecommended: mocks.supported } }));
vi.mock("@azure/msal-browser", () => ({ createNestablePublicClientApplication: mocks.create, InteractionRequiredAuthError: class extends Error {} }));
vi.mock("@omniagents/client/rpc/ws-ticket", () => ({ setHostTicketProvider: mocks.provider }));

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mocks.initialize.mockResolvedValue(undefined);
  mocks.context.mockResolvedValue({ user: { loginHint: "alice@example.test" } });
  mocks.supported.mockReturnValue(true);
  mocks.getAccount.mockReturnValue({ tenantId: "tenant" });
  mocks.silent.mockResolvedValue({ account: { tenantId: "tenant" }, accessToken: "access-secret" });
  mocks.popup.mockResolvedValue({ account: { tenantId: "tenant" }, accessToken: "access-secret" });
  mocks.create.mockResolvedValue({ acquireTokenSilent: mocks.silent, acquireTokenPopup: mocks.popup, getAccount: mocks.getAccount });
  mocks.fetch.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.endsWith("/config")) return Response.json({ tenantId: "tenant", clientId: "client", scope: "api://client/access_as_user" });
    if (url.endsWith("/session")) return Response.json({ session: "session-secret", expiresAt: Date.now() / 1000 + 300 });
    if (url.endsWith("/ticket")) return Response.json({ ticket: "one-time-ticket" });
    return Response.json({ ok: true });
  });
  window.fetch = mocks.fetch;
});

describe("Teams authentication", () => {
  it("initializes the host before MSAL and exchanges without cookies", async () => {
    const teams = await import("./teams");
    await teams.initializeTeams();
    expect(mocks.initialize.mock.invocationCallOrder[0]).toBeLessThan(mocks.create.mock.invocationCallOrder[0]);
    expect(mocks.fetch).toHaveBeenCalledWith("/workplace/auth/teams/session", expect.objectContaining({ credentials: "omit", headers: expect.objectContaining({ Authorization: "Bearer access-secret" }) }));
    expect(mocks.provider).toHaveBeenCalled();
    expect(teams.inTeams()).toBe(true);
  });

  it("never sends credentials to other origins or unrelated services", async () => {
    const teams = await import("./teams");
    await teams.initializeTeams();
    await window.fetch("https://other.example/workplace/api/state");
    expect(mocks.fetch).toHaveBeenLastCalledWith("https://other.example/workplace/api/state", undefined);
    await window.fetch("/github/mcp");
    expect(mocks.fetch).toHaveBeenLastCalledWith("/github/mcp", undefined);
    await window.fetch("/workplace/api/state");
    const request = mocks.fetch.mock.lastCall![0] as Request;
    expect(request.headers.get("Authorization")).toBe("Bearer access-secret");
    expect(request.headers.get("X-Workplace-Session")).toBe("session-secret");
    expect(request.redirect).toBe("error");
    expect(request.credentials).toBe("omit");
  });

  it("puts only a scoped ticket in socket URLs", async () => {
    const teams = await import("./teams");
    await teams.initializeTeams();
    const url = await teams.ticketUrl(location.origin.replace(/^http/, "ws") + "/workplace/api/desktop/ws");
    expect(url).toContain("workplace_ticket=one-time-ticket");
    expect(url).not.toContain("access-secret");
    expect(url).not.toContain("session-secret");
    await expect(teams.ticketUrl("wss://other.example/ws")).rejects.toThrow("different origin");
  });

  it("does not automatically open a consent popup", async () => {
    const teams = await import("./teams");
    mocks.silent.mockRejectedValueOnce(new Error("Consent required"));
    await expect(teams.initializeTeams()).rejects.toThrow("Consent required");
    expect(mocks.popup).not.toHaveBeenCalled();
    expect(teams.inTeams()).toBe(false);
    await teams.initializeTeams(true);
    expect(mocks.popup).toHaveBeenCalledTimes(1);
  });

  it("rejects unsupported hosts and another tenant", async () => {
    const teams = await import("./teams");
    mocks.supported.mockReturnValueOnce(false);
    await expect(teams.initializeTeams()).rejects.toThrow("does not support");
    mocks.silent.mockResolvedValueOnce({ account: { tenantId: "wrong" }, accessToken: "wrong-token" });
    await expect(teams.initializeTeams()).rejects.toThrow("organization");
    expect(teams.inTeams()).toBe(false);
  });

  it("does not silently sign in as a different Teams user", async () => {
    const teams = await import("./teams");
    mocks.context.mockResolvedValue({ user: { id: "teams-user", loginHint: "alice@example.test" } });
    mocks.silent.mockResolvedValue({ account: { tenantId: "tenant", localAccountId: "other-user" }, accessToken: "other-token" });
    await expect(teams.initializeTeams()).rejects.toThrow("same account");
    expect(teams.inTeams()).toBe(false);
  });
});
