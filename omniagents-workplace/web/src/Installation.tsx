import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { type Person } from "./api";

type Factory = {
  id: string;
  name: string;
  project: string | null;
  role: string | null;
  runtime: { running: boolean; ready?: boolean; error?: string | null };
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
  async function load() {
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
                {factory.runtime.running
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
