/** What starts an automation, said the way a person would say it. */

export type Trigger = {
  provider?: string;
  event?: string;
  filter?: Record<string, unknown>;
  schedule?: { name?: string; cron?: string; review_runs?: string };
};

export const EVENTS: Record<string, string> = {
  "github:pull_request_opened": "A pull request is opened",
  "github:pull_request_ready": "A draft pull request is marked ready",
  "github:pull_request_synchronized": "A pull request gets new commits",
  "github:pull_request_merged": "A pull request is merged",
  "github:pull_request_closed": "A pull request is closed without merging",
  "github:pull_request_labeled": "A pull request is labelled",
  "github:pull_request_assigned": "The factory is assigned a pull request",
  "github:pull_request_mentioned": "The factory is mentioned on a pull request",
  "github:pull_request_review_requested": "A review is requested on a pull request",
  "github:issue_labeled": "An issue is labelled",
  "github:issue_assigned": "The factory is assigned an issue",
  "github:issue_mentioned": "The factory is mentioned on an issue",
  "github:issue_closed": "An issue is closed",
  "github:push": "Commits are pushed",
  "github:workflow_run_completed": "A CI workflow finishes",
  "slack:app_mention": "Someone mentions the factory in Slack",
  "slack:message_dm": "Someone messages the factory directly in Slack",
  "slack:reaction_added": "Someone reacts to a Slack message",
  "linear:agent_session_created": "An issue is delegated to the factory in Linear",
  "linear:agent_session_prompted": "Someone replies in the factory's Linear session",
  "shared:shared.files_uploaded": "Files are added to a Shared folder",
  "memory:memory_created": "A memory is added",
  "memory:memory_updated": "A memory is changed",
  "memory:memory_forgotten": "A memory is forgotten",
};

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** Common crons in words ("Every day at 02:00", "Every Monday at 09:00"); others as written. */
export function describeCron(cron: string) {
  const [minute, hour, dom, month, dow] = cron.trim().split(/\s+/);
  const time = /^\d+$/.test(minute ?? "") && /^\d+$/.test(hour ?? "")
    ? `${hour!.padStart(2, "0")}:${minute!.padStart(2, "0")}`
    : null;
  if (time && dom === "*" && month === "*") {
    if (dow === "*") return `Every day at ${time} (server time)`;
    if (/^[0-6]$/.test(dow ?? "")) return `Every ${DAYS[Number(dow)]} at ${time} (server time)`;
    if (dow === "1-5") return `Every weekday at ${time} (server time)`;
  }
  return `On the schedule ${cron}`;
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : value == null ? [] : [String(value)];
}

function and(items: string[]) {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

/** One thing a trigger's filter narrows to, and the values it allows. */
export type Condition = {
  kind: "repo" | "base" | "branch" | "path" | "label" | "not_label" | "mention" | "assignee" | "outcome" | "channel" | "emoji" | "team";
  values: string[];
};

/** The trigger's filter as its parts, in the order a sentence says them: where, which labels, who. */
export function conditionParts(filter: Record<string, unknown> = {}): Condition[] {
  const out: Condition[] = [];
  const add = (kind: Condition["kind"], value: unknown) => {
    const values = list(value);
    if (values.length) out.push({ kind, values });
  };
  add("repo", filter.repos);
  add("base", filter.base_branches);
  add("branch", filter.branches);
  add("path", filter.paths);
  const labels = filter.labels;
  if (labels && typeof labels === "object" && !Array.isArray(labels)) add("not_label", (labels as { not_in?: unknown }).not_in);
  else add("label", labels);
  add("mention", filter.mentioned);
  add("assignee", filter.assignees);
  add("outcome", filter.conclusions);
  add("channel", filter.channels);
  add("emoji", filter.emojis);
  add("team", filter.teams);
  return out;
}

const WORDS: Record<Condition["kind"], (values: string[]) => string> = {
  repo: v => `in ${and(v)}`,
  base: v => `into ${and(v)}`,
  branch: v => `on ${and(v)}`,
  path: v => `touching ${and(v)}`,
  label: v => `labelled ${and(v)}`,
  not_label: v => `without the labels ${v.join(", ")}`,
  mention: v => `mentioning @${and(v)}`,
  assignee: v => `assigned to @${and(v)}`,
  outcome: v => `that ended in ${and(v)}`,
  channel: v => `in #${and(v)}`,
  emoji: v => `with ${and(v.map(e => `:${e}:`))}`,
  team: v => `for the ${and(v)} team`,
};

/** The trigger's filter in words: where, which labels, who. */
function conditions(filter: Record<string, unknown> = {}) {
  return conditionParts(filter).map(c => WORDS[c.kind](c.values));
}

/** What happens, without the filter: "A CI workflow finishes", or the schedule in words. */
export function describeEvent(trigger: Trigger) {
  if (trigger.provider === "schedule") return describeCron(trigger.schedule?.cron ?? "");
  return EVENTS[`${trigger.provider}:${trigger.event}`] ?? `${trigger.provider}: ${trigger.event}`;
}

export function describeTrigger(trigger: Trigger) {
  const what = describeEvent(trigger);
  if (trigger.provider === "schedule") return what;
  const where = conditions(trigger.filter);
  return where.length ? `${what} ${where.join(", ")}` : what;
}

/** The name a schedule runs under (for Run now), when the automation has one. */
export function scheduleName(triggers: Trigger[]) {
  return triggers.find(t => t.provider === "schedule")?.schedule?.name;
}
