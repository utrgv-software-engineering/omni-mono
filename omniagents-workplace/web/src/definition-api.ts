/** The factory's definition, as the Workplace edits it (factory_definition.py). */
import { factoryFetch } from "./factory-scope";

export type Scope =
  | { kind: "agent"; agent: string }
  | { kind: "base"; base: string; name?: string }
  | { kind: "factory" }
  | { kind: "global" }
  | { kind: "repository"; repository: string }
  | { kind: "unknown" };
export type Skill = { name: string; path: string; description: string };
export type EffectiveSkill = Skill & { scope: Scope; replaces: { path: string; scope: Scope }[] };
export type Definition = {
  key: string;
  kind: "agent" | "base";
  name: string;
  description: string;
  instructions: string | null;
  inline_instructions: boolean;
  skills_folder: string | null;
  skills: Skill[];
  extends: string | null;
  chat?: boolean;
  extends_chain?: string[];
  effective_skills?: EffectiveSkill[];
  agents?: string[];
};
export type Published = { commit: string; ok: boolean; at: number; detail?: string };
export type Tree = {
  agents: Definition[];
  bases: Definition[];
  skills: { folder: string; skills: Skill[] }[];
  automations: { name: string; path: string }[];
  repository: string;
  main: string;
  live: string | null;
  uncommitted: string[];
  require_review: boolean;
  published: Published | null;
};
export type Saved = { commit: string; published: boolean; detail?: string | null; pull_request?: string };
export type Problems = Record<string, string[]>;
export type DefinitionError = Error & { problems?: Problems; paths?: string[] };

export const API = "/workplace/api/factory/definition";

/** The response's body, or an error carrying the factory's problems with an edit. */
export async function json<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const error = new Error(typeof detail === "string" ? detail : detail?.detail || fallback) as DefinitionError;
    if (detail?.problems) error.problems = detail.problems;
    if (detail?.paths) error.paths = detail.paths;
    throw error;
  }
  return body as T;
}

export function send<T>(path: string, method: string, body: unknown, fallback: string) {
  return factoryFetch(API + path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r =>
    json<T>(r, fallback),
  );
}

export const loadTree = () => factoryFetch(API).then(r => json<Tree>(r, "Unable to load the factory"));

/** What a save did, in a sentence: published, waiting for review, or saved but not running yet. */
export function savedMessage(saved: Saved, done = "Saved. The factory uses it from its next run.") {
  if (saved.pull_request) return { kind: "success" as const, text: "Proposed for review" };
  if (saved.published) return { kind: "success" as const, text: done };
  return { kind: "warning" as const, text: saved.detail || "Saved" };
}

/** Who has a skill, in words. */
export function scopeLabel(scope: Scope, agentName: (key: string) => string) {
  switch (scope.kind) {
    case "agent":
      return `Only ${agentName(scope.agent)}`;
    case "base":
      return `Every agent built on ${scope.name ?? scope.base}`;
    case "factory":
      return "Every agent";
    case "global":
      return "The installation's global skills";
    case "repository":
      return `From ${scope.repository}`;
    default:
      return "";
  }
}
