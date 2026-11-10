import { useEffect, useState } from "react";
import { factoryFetch } from "./factory-scope";
import type { Condition, Trigger } from "./automation-triggers";

export type ScopeLabels = Record<string, string>;
type Fetcher = (url: string) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;
type Resource = { id: string; name: string };
type Connection = { id: string; label: string; organization: string; scopes: { project: string; repositories: string[] }[] };
const values = (value: unknown): string[] => Array.isArray(value) ? value.map(String) : value == null ? [] : [String(value)];
const key = (...parts: string[]) => JSON.stringify(parts);

async function lookup(fetcher: Fetcher, url: string): Promise<Record<string, unknown>> {
  try {
    const response = await fetcher(url);
    if (!response.ok) return {};
    const body = await response.json();
    return body && typeof body === "object" ? body as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export async function loadScopeLabels(triggers: Trigger[], fetcher: Fetcher = factoryFetch): Promise<ScopeLabels> {
  const azure = triggers.filter(trigger => trigger.provider === "azure-devops");
  if (!azure.length) return {};
  const labels: ScopeLabels = {};
  const body = await lookup(fetcher, "/workplace/api/azure-devops/connections");
  const connections = Array.isArray(body.connections) ? body.connections as Connection[] : [];
  await Promise.all(connections.filter(connection => azure.some(trigger => !values(trigger.filter?.connections).length || values(trigger.filter?.connections).includes(connection.id))).map(async connection => {
    labels[key("connection", connection.id)] = `${connection.label} (${connection.organization})`;
    const path = `/workplace/api/azure-devops/connections/${encodeURIComponent(connection.id)}/available`;
    const projects = await lookup(fetcher, path);
    if (Array.isArray(projects.projects)) for (const project of projects.projects as Resource[]) labels[key("project", connection.id, project.id)] = project.name;
    await Promise.all((connection.scopes ?? []).filter(scope => azure.some(trigger => (!values(trigger.filter?.connections).length || values(trigger.filter?.connections).includes(connection.id)) && (!values(trigger.filter?.projects).length || values(trigger.filter?.projects).includes(scope.project)))).map(async scope => {
      const repositories = await lookup(fetcher, `${path}?project=${encodeURIComponent(scope.project)}&resource=repositories`);
      if (Array.isArray(repositories.repositories)) for (const repository of repositories.repositories as Resource[]) {
        if (scope.repositories.includes(repository.id)) labels[key("repo", connection.id, scope.project, repository.id)] = repository.name;
      }
    }));
  }));
  return labels;
}

export function scopeLabel(labels: ScopeLabels, trigger: Trigger, kind: Condition["kind"], id: string): string {
  if (trigger.provider !== "azure-devops" || !["connection", "project", "repo"].includes(kind)) return id;
  const connections = values(trigger.filter?.connections);
  const projects = values(trigger.filter?.projects);
  const found = Object.entries(labels).filter(([serialized]) => {
    const parts = JSON.parse(serialized) as string[];
    if (parts[0] !== kind || parts[parts.length - 1] !== id) return false;
    if (kind !== "connection" && connections.length && !connections.includes(parts[1])) return false;
    return kind !== "repo" || !projects.length || projects.includes(parts[2]);
  }).map(([serialized, label]) => {
    const parts = JSON.parse(serialized) as string[];
    return { label, connection: labels[key("connection", parts[1])] ?? parts[1] };
  });
  if (found.length === 1) return found[0].label;
  if (found.length > 1) return found.map(value => `${value.label} (${value.connection})`).join(" / ");
  return `Unavailable ${kind === "repo" ? "repository" : kind} (${id})`;
}

export function useScopeLabels(triggers: Trigger[]): ScopeLabels {
  const [labels, setLabels] = useState<ScopeLabels>({});
  const signature = JSON.stringify(triggers);
  useEffect(() => {
    let active = true;
    void loadScopeLabels(JSON.parse(signature) as Trigger[]).then(result => { if (active) setLabels(result); });
    return () => { active = false; };
  }, [signature]);
  return labels;
}
