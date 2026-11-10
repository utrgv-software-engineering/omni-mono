import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { factoryFetch } from "./factory-scope";

type Route = { name: string; description: string; agent: string; conversation: string };
export function AutomationRoutes() {
  const [agents, setAgents] = useState<string[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string>();
  useEffect(() => {
    void factoryFetch("/workplace/api/factory/automation-routes").then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to load automations");
      setAgents(data.agents); setRoutes(data.automations);
    }).catch(error => setError(error.message));
  }, []);
  const update = (name: string, change: Partial<Route>) => setRoutes(rows => rows.map(row => row.name === name ? {...row, ...change} : row));
  async function save(route: Route) {
    setBusy(route.name); setError("");
    try {
      const response = await factoryFetch(`/workplace/api/factory/automation-routes/${encodeURIComponent(route.name)}`, {
        method: "PUT", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({agent: route.agent, conversation: route.conversation}),
      });
      if (!response.ok) throw new Error((await response.json()).detail || "Unable to save routing");
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(undefined); }
  }
  return <div className="flex max-h-[65vh] flex-col gap-4 overflow-auto">
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {routes.map(route => <div key={route.name} className="flex flex-col gap-2 border-b pb-4">
      <p className="text-sm font-medium">{route.name}</p>
      {route.description && <p className="text-sm text-muted-foreground">{route.description}</p>}
      <NativeSelect aria-label={`Agent for ${route.name}`} value={route.agent} onChange={event => update(route.name, {agent: event.target.value})}>
        {agents.map(agent => <NativeSelectOption key={agent} value={agent}>{agent}</NativeSelectOption>)}
      </NativeSelect>
      <NativeSelect aria-label={`Conversation for ${route.name}`} value={route.conversation} onChange={event => update(route.name, {conversation: event.target.value})}>
        <NativeSelectOption value="auto">Follow the work item; new chat for scheduled tasks</NativeSelectOption>
        <NativeSelectOption value="new">New conversation for each event</NativeSelectOption>
        <NativeSelectOption value="thread">Continue the source thread</NativeSelectOption>
      </NativeSelect>
      <Button variant="outline" disabled={!!busy} onClick={() => void save(route)}>{busy === route.name ? "Saving…" : "Save"}</Button>
    </div>)}
  </div>;
}
