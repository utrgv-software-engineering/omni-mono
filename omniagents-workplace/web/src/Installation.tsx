import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { type Person } from "./api";
import { StopDialog } from "./FactoryStop";
import { AllowedServicesEditor } from "./Governance";
import type { AllowedService } from "./api";

type Factory = {
  id: string;
  name: string;
  project: string | null;
  role: string | null;
  runtime: { running: boolean; ready?: boolean; error?: string | null };
  stopped?: { stopped_by?: string; reason?: string; scope?: string } | null;
};
type Member = { identity_id: string; role: string };
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/workplace/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || "Request failed");
  return value;
}

type Decision = {
  id: string;
  summary: string;
  status: string;
  proposed_by: string | null;
  reason: string;
  decided_by: string | null;
  reasoning: string;
};
type Review = {
  kind: string;
  title: string;
  factory: string | null;
  performed_by: string;
  due_at: number;
  overdue: boolean;
  last: { at: number; by: string; result: string } | null;
};

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Health Affairs' governance: its owner, decisions, reviews and reports. */
function GovernancePanel({ admin, people }: { admin: boolean; people: Person[] }) {
  const [view, setView] = useState<{
    owner: boolean;
    owners: string[];
    gated: boolean;
    decisions: Decision[];
    factories: { id: string; name: string; decisions: Decision[] }[];
  } | null>(null);
  const [calendar, setCalendar] = useState<Review[]>([]);
  const [error, setError] = useState("");
  const now = new Date();
  const [quarter, setQuarter] = useState(`${now.getUTCFullYear()}Q${Math.floor(now.getUTCMonth() / 3) + 1}`);
  async function load() {
    setView(await request("/installation/governance"));
    setCalendar((await request<{ calendar: Review[] }>("/installation/reviews")).calendar);
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, []);
  async function act(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  if (!view) return null;
  const pending = [
    ...view.decisions.filter((d) => d.status === "pending").map((d) => ({ ...d, where: "All factories" })),
    ...view.factories.flatMap((f) =>
      f.decisions.filter((d) => d.status === "pending").map((d) => ({ ...d, where: f.name })),
    ),
  ];
  const decide = (d: Decision, approve: boolean) =>
    void act(() =>
      request(`/installation/governance/decisions/${d.id}`, "POST", {
        approve,
        reasoning: window.prompt(approve ? "Your reasoning" : "Why decline?") ?? "",
      }),
    );
  return (
    <section className="space-y-4 rounded-lg border p-4">
      <h2 className="font-medium">Governance</h2>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="space-y-1 text-sm">
        <span className="font-medium">Governance owner: </span>
        {admin ? (
          <select
            aria-label="Governance owner"
            className="rounded border p-1"
            multiple
            value={view.owners}
            onChange={(e) =>
              void act(() =>
                request("/installation/governance-owners", "PUT", {
                  account_ids: [...e.target.selectedOptions].map((o) => o.value),
                }),
              )
            }
          >
            {people.map((p) => (
              <option key={p.id} value={p.id}>{p.name || p.username}</option>
            ))}
          </select>
        ) : (
          people.filter((p) => view.owners.includes(p.id)).map((p) => p.name || p.username).join(", ") || "—"
        )}
        {view.gated && !view.owners.length && (
          <p className="text-destructive">Name a governance owner: under the strict policy, lowering a classification and changing allowed services wait for one.</p>
        )}
      </div>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Waiting for the governance owner</h3>
        {pending.length ? (
          <ul className="divide-y rounded-md border text-sm">
            {pending.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-2 p-2">
                <span className="flex-1">
                  {d.where}: {d.summary}
                  <span className="block text-xs text-muted-foreground">
                    Proposed by {d.proposed_by ?? "—"}{d.reason ? `: ${d.reason}` : ""}
                  </span>
                </span>
                {view.owner && (
                  <>
                    <Button size="sm" onClick={() => decide(d, true)}>Approve</Button>
                    <Button size="sm" variant="outline" onClick={() => decide(d, false)}>Decline</Button>
                  </>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing is waiting.</p>
        )}
      </div>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Scheduled reviews</h3>
        <ul className="divide-y rounded-md border text-sm">
          {calendar.map((r) => (
            <li key={r.kind + (r.factory ?? "")} className="flex flex-wrap items-center gap-2 p-2">
              <span className="flex-1">
                {r.title}
                <span className={`block text-xs ${r.overdue ? "text-destructive" : "text-muted-foreground"}`}>
                  {r.overdue ? "Due now" : `Next due ${new Date(r.due_at * 1000).toLocaleDateString()}`}
                  {r.last ? ` · last ${new Date(r.last.at * 1000).toLocaleDateString()} by ${r.last.by}: ${r.last.result}` : " · never done"}
                  {` · ${r.performed_by}`}
                </span>
              </span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  const result = window.prompt(`Record the ${r.title.toLowerCase()}: its result`);
                  if (result)
                    void act(() =>
                      request("/installation/reviews", "POST", { kind: r.kind, factory: r.factory, result }),
                    );
                }}
              >
                Record
              </Button>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          onClick={() =>
            void act(async () => {
              const r = await request<{ markdown: string }>("/installation/inventory");
              download("ai-inventory.md", r.markdown, "text/markdown");
            })
          }
        >
          Download the AI Inventory
        </Button>
        <input aria-label="Quarter" className="w-24 rounded border p-1 text-sm" value={quarter} onChange={(e) => setQuarter(e.target.value)} />
        <Button
          variant="outline"
          onClick={() =>
            void act(async () => {
              const response = await fetch(`/workplace/api/installation/quarterly-report?quarter=${encodeURIComponent(quarter)}`);
              if (!response.ok) throw new Error((await response.json()).detail || "Report failed");
              download(`governance-${quarter}.md`, await response.text(), "text/markdown");
            })
          }
        >
          Quarterly report
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            void act(async () => {
              const evidence = await request("/installation/evidence");
              download("governance-evidence.json", JSON.stringify(evidence, null, 2), "application/json");
            })
          }
        >
          Evidence export
        </Button>
      </div>
    </section>
  );
}

export function InstallationApp({
  account,
  onSignOut,
}: {
  account: Person;
  onSignOut: () => void;
}) {
  const [templateAvailable, setTemplateAvailable] = useState(false);
  const [factories, setFactories] = useState<Factory[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [invite, setInvite] = useState("");
  const [allStopped, setAllStopped] = useState<{ stopped: unknown; message: string } | null>(null);
  const [stopping, setStopping] = useState(false);
  const [servicesOpen, setServicesOpen] = useState(false);
  async function load() {
    setAllStopped(
      await request<{ stopped: unknown; message: string }>("/installation/stop"),
    );
    const result = await request<{
      factories: Factory[];
      template_available?: boolean;
    }>("/factories");
    setFactories(result.factories);
    setTemplateAvailable(!!result.template_available);
    if (account.admin)
      setPeople(
        (await request<{ people: Person[] }>("/installation/people")).people,
      );
  }
  useEffect(() => {
    const refresh = () => {
      void load().catch((e) => setError(e.message));
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, []);
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function openAccess(id: string) {
    setMembers(
      (await request<{ members: Member[] }>(`/factories/${id}/members`))
        .members,
    );
    setSelected(id);
  }
  return (
    <main className="workplace-page flex flex-col gap-6">
      <header className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Factories</h1>
        <div className="flex gap-2">
          {account.admin && (
            <Button onClick={() => setCreating(!creating)}>New factory</Button>
          )}
          {account.admin && (
            <Button variant="outline" onClick={() => setServicesOpen(!servicesOpen)}>
              Allowed services
            </Button>
          )}
          {account.admin && (
            <Button
              variant={allStopped?.stopped ? "default" : "destructive"}
              onClick={() => setStopping(true)}
            >
              {allStopped?.stopped ? "Resume all factories" : "Stop all factories"}
            </Button>
          )}
          <Button variant="ghost" onClick={onSignOut}>
            Sign out
          </Button>
        </div>
      </header>
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {!!allStopped?.stopped && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
          {allStopped.message}
        </p>
      )}
      <GovernancePanel admin={!!account.admin} people={people} />
      {servicesOpen && (
        <section className="space-y-2 rounded-lg border p-4">
          <h2 className="font-medium">Allowed services</h2>
          <AllowedServicesEditor
            load={() =>
              request<{ services: AllowedService[] }>("/installation/allowed-services").then(
                (r) => r.services,
              )
            }
            save={(services) =>
              request<{ services: AllowedService[] }>("/installation/allowed-services", "PUT", {
                services,
              }).then((r) => r.services)
            }
          />
        </section>
      )}
      <StopDialog
        open={stopping}
        onOpenChange={setStopping}
        resuming={!!allStopped?.stopped}
        scope="installation"
        onSubmit={async (reason) => {
          await request(
            allStopped?.stopped ? "/installation/resume" : "/installation/stop",
            "POST",
            { reason },
          );
          await load();
        }}
      />
      {creating && (
        <form
          className="space-y-4 rounded-lg border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void act(async () => {
              await request("/factories", "POST", Object.fromEntries(data));
              setCreating(false);
            });
          }}
        >
          <label className="block space-y-1">
            <span>Name</span>
            <Input name="name" required maxLength={100} />
          </label>
          {!templateAvailable && (
            <label className="block space-y-1">
              <span>Project directory</span>
              <Input name="project" required placeholder="/path/to/factory" />
            </label>
          )}
          <Button disabled={busy} type="submit">
            Create factory
          </Button>
        </form>
      )}
      <div className="divide-y rounded-lg border">
        {factories.map((factory) => (
          <section
            key={factory.id}
            className="flex flex-wrap items-center justify-between gap-4 p-4"
          >
            <div>
              <h2 className="font-medium">{factory.name}</h2>
              <p className="text-sm text-muted-foreground">
                {factory.stopped
                  ? `Stopped by the kill switch${factory.stopped.stopped_by ? ` (${factory.stopped.stopped_by})` : ""}${factory.stopped.reason ? `: ${factory.stopped.reason}` : ""}`
                  : factory.runtime.running
                    ? factory.runtime.ready
                      ? "Running"
                      : "Starting"
                    : "Stopped"}
              </p>
              {factory.runtime.error && (
                <p className="text-sm text-destructive">
                  {factory.runtime.error}
                </p>
              )}
            </div>
            <div className="flex gap-2">
              {account.admin && (
                <>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void act(() => openAccess(factory.id))}
                  >
                    Access
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        request(
                          `/factories/${factory.id}/${factory.runtime.running ? "stop" : "start"}`,
                          "POST",
                        ),
                      )
                    }
                  >
                    {factory.runtime.running ? "Stop" : "Start"}
                  </Button>
                </>
              )}
              {factory.role && (
                <Button asChild>
                  <a
                    href={`/factories/${factory.id}/personal`}
                  >
                    Open
                  </a>
                </Button>
              )}
            </div>
          </section>
        ))}
        {!factories.length && (
          <p className="p-4 text-muted-foreground">No factories yet.</p>
        )}
      </div>
      {selected && (
        <section className="space-y-4 rounded-lg border p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">
              {factories.find((f) => f.id === selected)?.name} access
            </h2>
            <Button variant="ghost" onClick={() => setSelected(null)}>
              Close
            </Button>
          </div>
          {people.map((person) => (
            <div
              key={person.id}
              className="flex items-center justify-between gap-4"
            >
              <span>{person.name || person.username}</span>
              <select
                aria-label={`Access for ${person.username}`}
                className="rounded-md border bg-background p-2"
                disabled={busy}
                value={
                  members.find((m) => m.identity_id === person.id)?.role ??
                  "none"
                }
                onChange={(event) => {
                  const role = event.target.value;
                  void act(async () => {
                    await request(`/factories/${selected}/members`, "PUT", {
                      account_id: person.id,
                      role: role === "none" ? null : role,
                    });
                    await openAccess(selected);
                  });
                }}
              >
                <option value="none">No access</option>
                <option value="member">Member</option>
                <option value="admin">Administrator</option>
              </select>
            </div>
          ))}
          <form
            className="space-y-3 border-t pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              const form = event.currentTarget;
              const data = Object.fromEntries(new FormData(form));
              void act(async () => {
                const result = await request<{
                  person: Person;
                  invite_url: string;
                }>("/installation/people", "POST", data);
                await request(`/factories/${selected}/members`, "PUT", {
                  account_id: result.person.id,
                  role: "member",
                });
                setInvite(result.invite_url);
                await openAccess(selected);
                form.reset();
              });
            }}
          >
            <h3 className="font-medium">Invite someone</h3>
            <Input
              name="username"
              aria-label="Username"
              placeholder="Username"
              required
            />
            <Input name="name" aria-label="Name" placeholder="Name" required />
            <Input
              name="email"
              type="email"
              aria-label="Email"
              placeholder="Email"
              required
            />
            <Button disabled={busy}>Create invitation</Button>
          </form>
          {invite && (
            <label className="block space-y-1">
              <span>Invitation link</span>
              <Input
                readOnly
                value={invite}
                onFocus={(e) => e.target.select()}
              />
            </label>
          )}
        </section>
      )}
    </main>
  );
}
