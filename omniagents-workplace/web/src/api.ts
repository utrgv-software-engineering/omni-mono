// The workplace server's UI API (/workplace/api) and its live stream.

export interface SlackUser {
  id: string;
  name: string;
  real_name: string;
  is_bot: boolean;
  is_admin: boolean;
  deleted: boolean;
  /** Their own time zone, else the workspace's. */
  tz: string;
  profile: {
    real_name: string;
    display_name: string;
    email: string;
    title: string;
    status_text: string;
    /** `:emoji:`, or empty. */
    status_emoji: string;
  };
}

export interface SlackChannel {
  id: string;
  name: string;
  is_im: boolean;
  is_mpim: boolean;
  is_private: boolean;
  is_archived: boolean;
  is_general: boolean;
  /** New members join it (only in the workplace's own state). */
  is_default?: boolean;
  creator: string;
  members: string[];
  purpose: { value: string };
  topic: { value: string };
}

export interface SlackBookmark {
  id: string;
  title: string;
  link: string;
  created_by?: string;
}

export interface SlackDraft {
  id: string;
  channel: string;
  text: string;
  thread_ts: string | null;
}

export interface SlackScheduled extends SlackDraft {
  post_at: number;
}

export interface SlackApp {
  id: string;
  name: string;
  user: string;
  delivery: "socket" | "http";
  request_url: string | null;
  connected: boolean;
}

export interface SlackMessage {
  type: "message";
  user: string;
  text: string;
  ts: string;
  subtype?: string;
  thread_ts?: string;
  app_id?: string;
  reply_count?: number;
  reply_users?: string[];
  latest_reply?: string;
  reactions?: { name: string; users: string[]; count: number }[];
  files?: SlackFile[];
  edited?: { user: string; ts: string };
  pinned_to?: string[];
  /** Search results: the conversation it is in. */
  channel?: string;
}

export interface SlackFile {
  id: string;
  name: string;
  title: string;
  mimetype: string;
  size: number;
  url_private: string;
  alt_txt: string;
}

export interface Repository {
  full_name: string;
  description: string;
  default_branch: string;
  clone_url: string;
  branches: { name: string; sha: string; subject?: string }[];
  open_issues: number;
  open_pulls: number;
}

export interface GitHubUser {
  login: string;
  name: string;
  slack: string | null;
}

export interface Person {
  id: string;
  username: string;
  name: string;
  email: string;
  admin: boolean;
  disabled: boolean;
  slack_id: string;
  github_login: string;
  linear_id: string;
  has_password?: boolean;
}

export interface Session {
  account: Person | null;
  setup: boolean;
  organization: string | null;
  /** A new workplace, opened on its own machine: set up without the link. */
  setup_here?: boolean;
  /** A workplace `up` made, opened on its own machine: its admin chooses a password. */
  claim_here?: Invite | null;
  /** development, test, staging or production. */
  environment?: string;
  /** Development, served by `up`: the factory's workplace.yml is edited in the UI. */
  configurable?: boolean;
  /** Served with a factory's workplace.yml: its model is configured in the UI. */
  factory?: boolean;
  architecture?: "personal" | "specialist";
}

export interface Invite {
  username: string;
  name: string;
  organization: string;
}

export interface WorkplaceState {
  me: Person;
  github: {
    factory: string | null;
    users: GitHubUser[];
    repositories: Repository[];
  };
  slack: {
    team: { id: string; name: string; domain: string; tz: string };
    users: SlackUser[];
    channels: SlackChannel[];
    apps: SlackApp[];
    /** Per conversation you are in: [unread messages, mentions of you]. */
    unread: Record<string, [number, number]>;
    /** Your drafts and scheduled messages. */
    drafts: number;
  };
}

export interface WireEntry {
  id: number;
  at: number;
  service: string;
  direction: "in" | "out";
  kind: "mcp" | "api" | "event" | "socket" | "git";
  title: string;
  actor: string | null;
  status: "ok" | "error" | "pending";
  request: unknown;
  response: unknown;
  duration_ms: number | null;
  detail: Record<string, unknown>;
}

export interface AppSettings {
  id: string;
  name: string;
  username: string;
  description: string;
  owner?: string | null;
  removed?: boolean;
  slack?: {
    app_id: string;
    user_id: string;
    delivery: "socket" | "http";
    request_url: string | null;
    connected: boolean;
  };
  github?: { login: string; webhook_url: string | null };
  linear?: { user_id: string; webhook_url: string | null };
  /** Only for an app you manage (yours, or as an admin). */
  env?: Record<string, string>;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/workplace/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface Commit {
  sha: string;
  message: string;
  subject: string;
  author: string;
  email: string;
  date: string;
  parents: string[];
}

export interface FileChange {
  filename: string;
  status: string;
  additions: number;
  deletions: number;
  patch: string;
  previous_filename: string | null;
}

export interface TreeEntry {
  type: "file" | "dir" | "symlink" | "submodule";
  name: string;
  path: string;
  size: number;
  /** The last commit that changed it. */
  last_commit: Commit | null;
}

export type Tree =
  | {
      type: "dir";
      ref: string;
      path: string;
      entries: TreeEntry[];
      readme: string | null;
      last_commit: Commit | null;
    }
  | {
      type: "file";
      ref: string;
      path: string;
      size: number;
      binary: boolean;
      content: string | null;
      last_commit: Commit | null;
    };

export interface IssueSummary {
  number: number;
  title: string;
  state: "open" | "closed";
  user: string;
  labels: string[];
  assignees: string[];
  comments: number;
  created_at: string;
  updated_at: string;
  is_pull: boolean;
  html_url: string;
  draft?: boolean;
  merged?: boolean;
  head?: string;
  base?: string;
}

export interface ReviewComment {
  id: number;
  user: string;
  body: string;
  path?: string;
  line: number | null;
  side: string;
  in_reply_to?: number | null;
  thread?: string;
  at: string;
}

export type TimelineItem =
  | {
      type: "comment";
      id: number;
      user: string;
      body: string;
      at: string;
      edited?: boolean;
      reactions: Record<string, number>;
      /** The reactions the signed-in person gave. */
      reacted: string[];
    }
  | {
      type: "review";
      id: number;
      user: string;
      state: string;
      body: string;
      at: string;
      comments: ReviewComment[];
    };

export interface IssueDetail extends IssueSummary {
  body: string;
  reactions: Record<string, number>;
  reacted: string[];
  state_reason: string | null;
  closed_by: string | null;
  closed_at: string | null;
  timeline: TimelineItem[];
  head_sha?: string;
  /** Whether the pull request's branch is still there (deleted after a merge, say). */
  head_exists?: boolean;
  /** Commits on the base the pull request's branch lacks. */
  behind_by?: number;
  mergeable_state?: string;
  /** The base's required checks this head has not passed. */
  unmet_checks?: { name: string; state: string }[];
  merged_by?: string | null;
  requested_reviewers?: string[];
  files?: FileChange[];
  commits?: Commit[];
  checks?: {
    run: number;
    name: string;
    status: string;
    conclusion: string | null;
    at: string;
  }[];
  threads?: {
    id: string;
    path: string;
    resolved: boolean;
    comments: ReviewComment[];
  }[];
}

export interface WorkflowRun {
  id: number;
  name: string;
  run_number: number;
  event: string;
  status: string;
  conclusion: string | null;
  head_branch: string;
  head_sha: string;
  title: string;
  actor: string;
  at: string;
  pull_requests: number[];
  workflow_id: number;
  path: string;
  run_attempt: number;
  jobs: {
    id: number;
    name: string;
    status: string;
    conclusion: string | null;
    log: string;
    steps: {
      number: number;
      name: string;
      status: string;
      conclusion: string | null;
    }[];
  }[];
}

export interface WorkflowInfo {
  id: number;
  name: string;
  path: string;
  dispatch: boolean;
}

export interface RepoDetails {
  full_name: string;
  description: string;
  default_branch: string;
  clone_url: string;
  branches: { name: string; sha: string; subject: string }[];
  open_issues: number;
  open_pulls: number;
  labels: { name: string; color: string; description?: string }[];
  protected: string[];
  self_hosted: boolean;
  required_checks: Record<string, string[]>;
  check_names: string[];
}

export interface Branch {
  name: string;
  sha: string;
  commit: Commit | null;
  ahead: number;
  behind: number;
  default: boolean;
  protected: boolean;
  pull: {
    number: number;
    state: string;
    merged: boolean;
    draft: boolean;
  } | null;
}

const gh = (repo: string) => `/github/repos/${repo}`;
const patch = <T>(path: string, body: unknown) =>
  call<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  call<T>(path, { method: "PUT", body: JSON.stringify(body) });

export interface Compare {
  base: string;
  head: string;
  commits: Commit[];
  files: FileChange[];
  mergeable: boolean;
  existing: number | null;
}

export const github = {
  recentPushes: (repo: string) =>
    call<{ pushes: { branch: string; at: string }[] }>(
      `${gh(repo)}/recent-pushes`,
    ),
  compare: (repo: string, base: string, head: string) =>
    call<Compare>(
      `${gh(repo)}/compare?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`,
    ),
  createPull: (
    repo: string,
    body: {
      title: string;
      body: string;
      head: string;
      base: string;
      draft: boolean;
    },
  ) => post<IssueSummary>(`${gh(repo)}/pulls`, body),
  createRepo: (body: {
    name: string;
    description: string;
    private: boolean;
    auto_init: boolean;
    protect_default_branch: boolean;
  }) => post<{ full_name: string; clone_url: string }>("/github/repos", body),
  repos: () =>
    call<{
      repositories: RepoDetails[];
      users: GitHubUser[];
      factory: string | null;
      webhooks: { url: string }[];
    }>("/github/repos"),
  tree: (repo: string, ref: string, path: string) =>
    call<Tree>(
      `${gh(repo)}/tree?ref=${encodeURIComponent(ref)}&path=${encodeURIComponent(path)}`,
    ),
  commits: (repo: string, ref: string) =>
    call<{ commits: Commit[] }>(
      `${gh(repo)}/commits?ref=${encodeURIComponent(ref)}`,
    ),
  commit: (repo: string, sha: string) =>
    call<Commit & { files: FileChange[] }>(`${gh(repo)}/commits/${sha}`),
  issues: (repo: string, kind: "issue" | "pull", state: string) =>
    call<{ items: IssueSummary[] }>(
      `${gh(repo)}/issues?kind=${kind}&state_filter=${state}`,
    ),
  issue: (repo: string, number: number) =>
    call<IssueDetail>(`${gh(repo)}/issues/${number}`),
  createIssue: (
    repo: string,
    body: {
      title: string;
      body: string;
      labels?: string[];
      assignees?: string[];
    },
  ) => post<IssueSummary>(`${gh(repo)}/issues`, body),
  setReviewers: (repo: string, number: number, reviewers: string[]) =>
    put<{ requested_reviewers: string[] }>(
      `${gh(repo)}/pulls/${number}/reviewers`,
      { reviewers },
    ),
  editIssue: (
    repo: string,
    number: number,
    body: {
      title?: string;
      body?: string;
      draft?: boolean;
      state?: string;
      state_reason?: string;
      labels?: string[];
      assignees?: string[];
    },
  ) => patch<IssueSummary>(`${gh(repo)}/issues/${number}`, body),
  comment: (repo: string, number: number, body: { body: string }) =>
    post<{ ok: boolean }>(`${gh(repo)}/issues/${number}/comments`, body),
  editComment: (repo: string, id: number, body: string) =>
    patch<{ ok: boolean }>(`${gh(repo)}/issues/comments/${id}`, { body }),
  deleteComment: (repo: string, id: number) =>
    call<{ ok: boolean }>(`${gh(repo)}/issues/comments/${id}`, {
      method: "DELETE",
    }),
  reactToIssue: (repo: string, number: number, content: string) =>
    post<{ ok: boolean }>(`${gh(repo)}/issues/${number}/reactions`, {
      content,
    }),
  reactToComment: (repo: string, id: number, content: string) =>
    post<{ ok: boolean }>(`${gh(repo)}/issues/comments/${id}/reactions`, {
      content,
    }),
  resolveThread: (
    repo: string,
    number: number,
    thread: string,
    resolved: boolean,
  ) =>
    put<{ ok: boolean }>(`${gh(repo)}/pulls/${number}/threads/${thread}`, {
      resolved,
    }),
  review: (
    repo: string,
    number: number,
    body: {
      event: string;
      body: string;
      comments: { path: string; line: number; side: string; body: string }[];
    },
  ) => post<{ ok: boolean }>(`${gh(repo)}/pulls/${number}/reviews`, body),
  reply: (
    repo: string,
    number: number,
    commentId: number,
    body: { body: string },
  ) =>
    post<{ ok: boolean }>(
      `${gh(repo)}/pulls/${number}/comments/${commentId}/replies`,
      body,
    ),
  merge: (
    repo: string,
    number: number,
    body: { method: string; bypass?: boolean },
  ) => post<{ sha: string }>(`${gh(repo)}/pulls/${number}/merge`, body),
  branches: (repo: string) =>
    call<{ branches: Branch[] }>(`${gh(repo)}/branches`),
  updateBranch: (repo: string, number: number) =>
    post<{ sha: string }>(`${gh(repo)}/pulls/${number}/update-branch`, {}),
  settings: (
    repo: string,
    body: {
      description?: string;
      default_branch?: string;
      protected?: string[];
      self_hosted?: boolean;
      required_checks?: Record<string, string[]>;
    },
  ) => patch<{ ok: boolean }>(`${gh(repo)}/settings`, body),
  deleteRepo: (repo: string, confirm: string) =>
    call<{ ok: boolean }>(gh(repo), {
      method: "DELETE",
      body: JSON.stringify({ confirm }),
    }),
  createLabel: (
    repo: string,
    body: { name: string; color: string; description: string },
  ) => post<{ name: string }>(`${gh(repo)}/labels`, body),
  editLabel: (
    repo: string,
    name: string,
    body: { name?: string; color?: string; description?: string },
  ) =>
    patch<{ name: string }>(
      `${gh(repo)}/labels/${encodeURIComponent(name)}`,
      body,
    ),
  deleteLabel: (repo: string, name: string) =>
    call<{ ok: boolean }>(`${gh(repo)}/labels/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  deleteBranch: (repo: string, branch: string) =>
    call<{ branch: string; sha: string }>(
      `${gh(repo)}/branches/${encodeURIComponent(branch)}`,
      { method: "DELETE" },
    ),
  restoreBranch: (repo: string, branch: string, sha: string) =>
    post<{ branch: string; sha: string }>(`${gh(repo)}/branches`, {
      branch,
      sha,
    }),
  runs: (repo: string) => call<{ runs: WorkflowRun[] }>(`${gh(repo)}/runs`),
  workflows: (repo: string) =>
    call<{ workflows: WorkflowInfo[] }>(`${gh(repo)}/workflows`),
  dispatch: (repo: string, workflow: number, ref: string) =>
    post<{ run: number }>(`${gh(repo)}/workflows/${workflow}/dispatches`, {
      ref,
    }),
  rerun: (repo: string, run: number) =>
    post<{ ok: boolean }>(`${gh(repo)}/runs/${run}/rerun`, {}),
  cancelRun: (repo: string, run: number) =>
    post<{ ok: boolean }>(`${gh(repo)}/runs/${run}/cancel`, {}),
  setWebhook: (url: string) => put<{ ok: boolean }>("/github/webhook", { url }),
};

export interface LinearUser {
  id: string;
  name: string;
  display_name: string;
  is_app: boolean;
  slack: string | null;
  email: string;
}

export interface LinearStatus {
  id: string;
  name: string;
  type: string;
  color: string;
  position: number;
}

export interface LinearLabel {
  id: string;
  name: string;
  color: string;
  description: string | null;
  retired: boolean;
  /** A team's own label (else the workspace's). */
  team: boolean;
  issues: number;
}

export interface LinearCycle {
  id: string;
  number: number;
  name: string | null;
  starts_at: string;
  ends_at: string;
  current: boolean;
}

export interface LinearTeam {
  id: string;
  key: string;
  name: string;
  description: string;
  member: boolean;
  members: string[];
  states: LinearStatus[];
  labels: LinearLabel[];
  cycles: LinearCycle[];
}

export interface LinearProjectSummary {
  id: string;
  identifier: string;
  name: string;
  summary: string;
  status: string;
  status_name: string;
  priority: number;
  lead: string | null;
  lead_id: string | null;
  teams: string[];
  start_date: string | null;
  target_date: string | null;
  color: string;
  progress: number;
  issues: number;
  archived: boolean;
  updated_at: string;
}

export interface LinearWorkspace {
  organization: { name: string; url_key: string };
  users: LinearUser[];
  teams: LinearTeam[];
  projects: LinearProjectSummary[];
  project_statuses: { name: string; type: string; color: string }[];
  unread: number;
}

export interface LinearIssueRef {
  identifier: string;
  title: string;
  state_type?: string;
}

export interface LinearIssueSummary {
  id: string;
  identifier: string;
  title: string;
  priority: number;
  priority_name: string;
  state: string;
  state_type: string;
  state_color: string;
  labels: string[];
  assignee: string | null;
  assignee_name: string | null;
  delegate: string | null;
  creator: string | null;
  team: string;
  updated_at: string;
  created_at: string;
  comments: number;
  url: string;
  due_date: string | null;
  estimate: number | null;
  project: { id: string; name: string } | null;
  milestone: { id: string; name: string } | null;
  cycle: { id: string; number: number; name: string | null } | null;
  parent: LinearIssueRef | null;
  sub_issues: number;
  archived: boolean;
}

export interface LinearActivity {
  id: string;
  type: string;
  content: {
    type: string;
    body?: string;
    action?: string;
    parameter?: string;
    result?: string;
  };
  at: string;
  user: string | null;
  ephemeral: boolean;
  signal: string | null;
}

export interface LinearSession {
  id: string;
  app: string | null;
  status: string;
  comment_id: string | null;
  created_at: string;
  external_urls: { label: string; url: string }[];
  activities: LinearActivity[];
  stopped: boolean;
}

export interface LinearComment {
  id: string;
  body: string;
  parent_id: string | null;
  at: string;
  edited: boolean;
  user_id: string;
  author: string | null;
  author_name: string | null;
  is_app: boolean;
  session: string | null;
}

export interface LinearIssue extends Omit<
  LinearIssueSummary,
  "comments" | "sub_issues"
> {
  description: string;
  creator: string | null;
  created_at: string;
  attachments: {
    id: string;
    title: string;
    url: string;
    content_type: string | null;
    subtitle: string | null;
    file: boolean;
  }[];
  sub_issues: LinearIssueSummary[];
  relations: {
    blocks: LinearIssueRef[];
    blocked_by: LinearIssueRef[];
    related: LinearIssueRef[];
    duplicate_of: LinearIssueRef | null;
    duplicates: LinearIssueRef[];
  };
  subscribed: boolean;
  documents: { id: string; title: string }[];
  comments: LinearComment[];
  sessions: LinearSession[];
}

export interface LinearProject extends Omit<LinearProjectSummary, "issues"> {
  description: string;
  members: string[];
  links: { id: string; title: string; url: string }[];
  milestones: {
    id: string;
    name: string;
    description: string;
    target_date: string | null;
    issues: number;
    done: number;
  }[];
  issues: LinearIssueSummary[];
  documents: { id: string; title: string; updated_at: string }[];
  updates: {
    id: string;
    body: string;
    health: string;
    at: string;
    user_id: string;
    author: string | null;
  }[];
}

export interface LinearDocument {
  id: string;
  title: string;
  content: string;
  created_at: string;
  updated_at: string;
  creator: string | null;
  updated_by: string | null;
  project: { id: string; name: string } | null;
  issue: { identifier: string; title: string } | null;
  team: string | null;
}

export interface LinearNotification {
  id: string;
  type: string;
  text: string;
  at: string;
  read: boolean;
  actor: string | null;
  issue: { identifier: string; title: string } | null;
  project: { id: string; name: string } | null;
  comment: string | null;
}

export interface LinearIssueEdit {
  title?: string;
  description?: string;
  state?: string;
  labels?: string[];
  assignee?: string;
  unassign?: boolean;
  delegate?: string;
  undelegate?: boolean;
  priority?: number;
  due_date?: string | null;
  estimate?: number | null;
  parent?: string | null;
  project?: string | null;
  milestone?: string | null;
  cycle?: string | null;
  duplicate_of?: string | null;
  blocks?: string[];
  blocked_by?: string[];
  related?: string[];
  remove_blocks?: string[];
  remove_blocked_by?: string[];
  remove_related?: string[];
  archived?: boolean;
  subscribed?: boolean;
}

const remove = <T = { ok: boolean }>(path: string) =>
  call<T>(path, { method: "DELETE" });

export const linear = {
  workspace: () => call<LinearWorkspace>("/linear/workspace"),
  createTeam: (body: { key: string; name: string }) =>
    post<{ id: string; key: string; name: string }>("/linear/teams", body),
  editTeam: (key: string, body: { name?: string; description?: string }) =>
    patch<LinearTeam>(`/linear/teams/${key}`, body),
  addStatus: (
    key: string,
    body: { name: string; type: string; color: string },
  ) => post<{ id: string }>(`/linear/teams/${key}/states`, body),
  editStatus: (
    key: string,
    id: string,
    body: { name?: string; color?: string; position?: number },
  ) => patch<{ ok: boolean }>(`/linear/teams/${key}/states/${id}`, body),
  deleteStatus: (key: string, id: string, moveTo?: string) =>
    remove(
      `/linear/teams/${key}/states/${id}${moveTo ? `?move_to=${moveTo}` : ""}`,
    ),
  addMember: (key: string, user: string) =>
    post<{ ok: boolean }>(`/linear/teams/${key}/members`, { user }),
  removeMember: (key: string, user: string) =>
    remove(`/linear/teams/${key}/members/${user}`),
  addLabel: (
    key: string,
    body: { name: string; color: string; description?: string },
  ) => post<{ id: string }>(`/linear/teams/${key}/labels`, body),
  editLabel: (
    id: string,
    body: {
      name?: string;
      color?: string;
      description?: string;
      retired?: boolean;
    },
  ) => patch<{ ok: boolean }>(`/linear/labels/${id}`, body),
  deleteLabel: (id: string) => remove(`/linear/labels/${id}`),
  addCycle: (
    key: string,
    body: { starts_at: string; ends_at: string; name?: string },
  ) =>
    post<{ id: string; number: number }>(`/linear/teams/${key}/cycles`, body),
  editCycle: (
    id: string,
    body: { starts_at?: string; ends_at?: string; name?: string | null },
  ) => patch<{ ok: boolean }>(`/linear/cycles/${id}`, body),
  deleteCycle: (id: string) => remove(`/linear/cycles/${id}`),
  issues: (
    filter: {
      team?: string;
      project?: string;
      cycle?: string;
      archived?: boolean;
    } = {},
  ) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(filter))
      if (v !== undefined) q.set(k, String(v));
    return call<{ issues: LinearIssueSummary[] }>(`/linear/issues?${q}`);
  },
  issue: (identifier: string) =>
    call<LinearIssue>(`/linear/issues/${identifier}`),
  create: (body: {
    team: string;
    title: string;
    description: string;
    priority: number;
    labels: string[];
  }) => post<LinearIssueSummary>("/linear/issues", body),
  edit: (identifier: string, body: LinearIssueEdit) =>
    patch<LinearIssueSummary>(`/linear/issues/${identifier}`, body),
  deleteIssue: (identifier: string) => remove(`/linear/issues/${identifier}`),
  attachLink: (identifier: string, title: string, url: string) =>
    post<{ id: string }>(`/linear/issues/${identifier}/attachments`, {
      title,
      url,
    }),
  attachFile: async (identifier: string, file: File) => {
    const form = new FormData();
    form.set("file", file);
    form.set("title", file.name);
    const response = await fetch(
      `/workplace/api/linear/issues/${identifier}/attachments`,
      { method: "POST", body: form },
    );
    if (!response.ok) {
      const detail = (
        (await response.json().catch(() => ({}))) as { detail?: string }
      ).detail;
      throw new Error(detail || `${response.status} ${response.statusText}`);
    }
    return (await response.json()) as { id: string; url: string };
  },
  detach: (id: string) => remove(`/linear/attachments/${id}`),
  comment: (
    identifier: string,
    body: { body: string; parent_id?: string | null },
  ) => post<{ ok: boolean }>(`/linear/issues/${identifier}/comments`, body),
  editComment: (id: string, body: string) =>
    patch<{ ok: boolean }>(`/linear/comments/${id}`, { body }),
  deleteComment: (id: string) => remove(`/linear/comments/${id}`),
  stopSession: (id: string) =>
    post<{ ok: boolean }>(`/linear/sessions/${id}/stop`, {}),
  projects: (archived = false) =>
    call<{ projects: LinearProjectSummary[] }>(
      `/linear/projects?archived=${archived}`,
    ),
  project: (id: string) => call<LinearProject>(`/linear/projects/${id}`),
  createProject: (body: {
    name: string;
    teams: string[];
    summary?: string;
    status?: string;
    lead?: string | null;
    target_date?: string | null;
  }) => post<LinearProjectSummary>("/linear/projects", body),
  editProject: (
    id: string,
    body: {
      name?: string;
      summary?: string;
      description?: string;
      status?: string;
      priority?: number;
      lead?: string | null;
      start_date?: string | null;
      target_date?: string | null;
      teams?: string[];
      archived?: boolean;
      add_members?: string[];
      remove_members?: string[];
      links?: { title: string; url: string }[];
    },
  ) => patch<LinearProjectSummary>(`/linear/projects/${id}`, body),
  deleteProject: (id: string) => remove(`/linear/projects/${id}`),
  addMilestone: (
    project: string,
    body: { name: string; description?: string; target_date?: string | null },
  ) => post<{ id: string }>(`/linear/projects/${project}/milestones`, body),
  editMilestone: (
    id: string,
    body: { name?: string; description?: string; target_date?: string | null },
  ) => patch<{ ok: boolean }>(`/linear/milestones/${id}`, body),
  deleteMilestone: (id: string) => remove(`/linear/milestones/${id}`),
  postUpdate: (project: string, body: { body: string; health: string }) =>
    post<{ id: string }>(`/linear/projects/${project}/updates`, body),
  deleteUpdate: (id: string) => remove(`/linear/updates/${id}`),
  documents: () =>
    call<{ documents: Omit<LinearDocument, "content">[] }>("/linear/documents"),
  document: (id: string) => call<LinearDocument>(`/linear/documents/${id}`),
  createDocument: (body: {
    title: string;
    content?: string;
    project?: string;
    issue?: string;
    team?: string;
  }) => post<LinearDocument>("/linear/documents", body),
  editDocument: (id: string, body: { title?: string; content?: string }) =>
    patch<LinearDocument>(`/linear/documents/${id}`, body),
  deleteDocument: (id: string) => remove(`/linear/documents/${id}`),
  inbox: () => call<{ notifications: LinearNotification[] }>("/linear/inbox"),
  mark: (id: string, body: { read?: boolean; snoozed_until?: number | null }) =>
    patch<{ ok: boolean }>(`/linear/inbox/${id}`, body),
  dismiss: (id: string) => remove(`/linear/inbox/${id}`),
  readAll: () => post<{ ok: boolean }>("/linear/inbox/read", {}),
};

export const api = {
  state: () => call<WorkplaceState>("/state"),
  messages: (channel: string) =>
    call<{ messages: SlackMessage[] }>(`/slack/channels/${channel}/messages`),
  thread: (channel: string, ts: string) =>
    call<{ parent: SlackMessage; replies: SlackMessage[] }>(
      `/slack/channels/${channel}/threads/${ts}`,
    ),
  post: (body: { channel: string; text: string; thread_ts?: string }) =>
    post<{ message: SlackMessage }>("/slack/messages", body),
  react: (body: {
    channel: string;
    ts: string;
    emoji: string;
    remove?: boolean;
  }) => post<{ message: SlackMessage }>("/slack/reactions", body),
  createChannel: (body: { name: string; is_private: boolean }) =>
    post<{ channel: SlackChannel }>("/slack/channels", body),
  openDm: (others: string[]) =>
    post<{ channel: SlackChannel }>("/slack/dms", { others }),
  editMessage: (channel: string, ts: string, text: string) =>
    call<{ message: SlackMessage }>(`/slack/messages/${channel}/${ts}`, {
      method: "PATCH",
      body: JSON.stringify({ text }),
    }),
  deleteMessage: (channel: string, ts: string) =>
    call<{ ok: boolean }>(`/slack/messages/${channel}/${ts}`, {
      method: "DELETE",
    }),
  pin: (channel: string, ts: string, pinned: boolean) =>
    call<{ message: SlackMessage }>(`/slack/messages/${channel}/${ts}/pin`, {
      method: pinned ? "POST" : "DELETE",
    }),
  channel: (channel: string) =>
    call<{
      channel: SlackChannel;
      pins: (SlackMessage & { pinned_by: string })[];
      bookmarks: SlackBookmark[];
    }>(`/slack/channels/${channel}`),
  editChannel: (
    channel: string,
    body: {
      name?: string;
      topic?: string;
      purpose?: string;
      archived?: boolean;
      private?: boolean;
      default?: boolean;
    },
  ) =>
    call<{ channel: SlackChannel }>(`/slack/channels/${channel}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  join: (channel: string) =>
    post<{ channel: SlackChannel }>(`/slack/channels/${channel}/join`, {}),
  leave: (channel: string) =>
    post<{ channel: SlackChannel }>(`/slack/channels/${channel}/leave`, {}),
  invite: (channel: string, users: string[]) =>
    post<{ channel: SlackChannel }>(`/slack/channels/${channel}/members`, {
      users,
    }),
  removeMember: (channel: string, user: string) =>
    call<{ channel: SlackChannel }>(
      `/slack/channels/${channel}/members/${user}`,
      { method: "DELETE" },
    ),
  read: (channel: string, ts: string) =>
    post<{ ok: boolean }>(`/slack/channels/${channel}/read`, { ts }),
  addBookmark: (channel: string, title: string, link: string) =>
    post<{ bookmark: SlackBookmark }>(`/slack/channels/${channel}/bookmarks`, {
      title,
      link,
    }),
  removeBookmark: (channel: string, id: string) =>
    call<{ ok: boolean }>(`/slack/channels/${channel}/bookmarks/${id}`, {
      method: "DELETE",
    }),
  upload: async (body: {
    channel: string;
    file: File;
    comment: string;
    thread_ts?: string;
  }) => {
    const form = new FormData();
    form.set("channel", body.channel);
    form.set("file", body.file);
    form.set("comment", body.comment);
    if (body.thread_ts) form.set("thread_ts", body.thread_ts);
    const response = await fetch("/workplace/api/slack/files", {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const detail = (
        (await response.json().catch(() => ({}))) as { detail?: string }
      ).detail;
      throw new Error(detail || `${response.status} ${response.statusText}`);
    }
    return (await response.json()) as { file: { id: string; name: string } };
  },
  search: (q: string) =>
    call<{
      messages: SlackMessage[];
      channels: SlackChannel[];
      people: SlackUser[];
    }>(`/slack/search?q=${encodeURIComponent(q)}`),
  drafts: () =>
    call<{ drafts: SlackDraft[]; scheduled: SlackScheduled[] }>(
      "/slack/drafts",
    ),
  editDraft: (id: string, text: string) =>
    call<{ draft: SlackDraft }>(`/slack/drafts/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ text }),
    }),
  sendDraft: (id: string) =>
    post<{ message: SlackMessage }>(`/slack/drafts/${id}/send`, {}),
  deleteDraft: (id: string) =>
    call<{ ok: boolean }>(`/slack/drafts/${id}`, { method: "DELETE" }),
  cancelScheduled: (id: string) =>
    call<{ ok: boolean }>(`/slack/scheduled/${id}`, { method: "DELETE" }),
  editProfile: (body: {
    name?: string;
    display_name?: string;
    title?: string;
    tz?: string;
    status_text?: string;
    status_emoji?: string;
  }) =>
    call<{ user: SlackUser; account: Person }>("/me/profile", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  editTeam: (body: { name?: string; tz?: string }) =>
    call<{ team: { name: string; tz: string } }>("/team", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  wire: () => call<{ entries: WireEntry[] }>("/wire"),
  clearWire: () => call<{ ok: boolean }>("/wire", { method: "DELETE" }),
};

export const auth = {
  session: () => call<Session>("/session"),
  setup: (body: {
    token: string;
    organization: string;
    slug: string;
    username: string;
    name: string;
    email: string;
    password: string;
    tz: string;
  }) => post<{ account: Person }>("/setup", body),
  signIn: (username: string, password: string, tz: string) =>
    post<{ account: Person }>("/signin", { username, password, tz }),
  signOut: () => post<{ ok: boolean }>("/signout", {}),
  invite: (token: string) => call<Invite>(`/invites/${token}`),
  acceptInvite: (token: string, password: string, tz: string) =>
    post<{ account: Person }>(`/invites/${token}`, { password, tz }),
  claim: (password: string, tz: string) =>
    post<{ account: Person }>("/claim", { password, tz }),
  changePassword: (current: string, next: string) =>
    post<{ ok: boolean }>("/me/password", { current, new: next }),
};

export const people = {
  list: () => call<{ people: Person[] }>("/people"),
  add: (body: {
    username: string;
    name: string;
    email: string;
    admin: boolean;
  }) => post<{ person: Person; invite_url: string }>("/people", body),
  invite: (id: string) =>
    post<{ invite_url: string }>(`/people/${id}/invite`, {}),
  edit: (
    id: string,
    body: {
      name?: string;
      email?: string;
      admin?: boolean;
      disabled?: boolean;
    },
  ) => patch<{ person: Person }>(`/people/${id}`, body),
};

export const apps = {
  list: () => call<{ apps: AppSettings[] }>("/apps"),
  register: (body: { name: string; username: string; description: string }) =>
    post<AppSettings>("/apps", body),
  get: (id: string) => call<AppSettings>(`/apps/${id}`),
  configure: (
    id: string,
    body: {
      slack_delivery?: string;
      slack_request_url?: string;
      github_webhook_url?: string;
      linear_webhook_url?: string;
    },
  ) => patch<AppSettings>(`/apps/${id}`, body),
  rotate: (id: string, credential: string) =>
    post<AppSettings>(`/apps/${id}/rotate/${credential}`, {}),
  remove: (id: string) =>
    call<{ ok: boolean }>(`/apps/${id}`, { method: "DELETE" }),
};

export const tokens = {
  mine: () =>
    call<{
      tokens: {
        github: boolean;
        linear: boolean;
        slack: boolean;
        canvas: boolean;
      };
      mcp: { github: string; linear: string; slack: string; canvas: string };
    }>("/me/tokens"),
  make: (service: string) =>
    post<{ token: string }>(`/me/tokens/${service}`, {}),
  revoke: (service: string) =>
    call<{ ok: boolean }>(`/me/tokens/${service}`, { method: "DELETE" }),
};

export type StreamEvent =
  | {
      type: "slack";
      data: { kind: string; channel?: SlackChannel; message?: SlackMessage };
    }
  | { type: "wire"; data: WireEntry }
  | {
      type: "github";
      data: { kind: string; repo: string | null; number: number | null };
    }
  | { type: "linear"; data: { kind: string; issue: string | null } }
  | { type: "canvas"; data: { kind: string; canvas: string | null } }
  | { type: "ready" };

/** Subscribe to the workplace's changes; reconnects on its own. */
export function subscribe(onEvent: (event: StreamEvent) => void): () => void {
  const source = new EventSource("/workplace/api/stream");
  source.addEventListener("ready", () => onEvent({ type: "ready" }));
  source.addEventListener("slack", (e) =>
    onEvent({ type: "slack", data: JSON.parse((e as MessageEvent).data) }),
  );
  source.addEventListener("linear", (e) =>
    onEvent({ type: "linear", data: JSON.parse((e as MessageEvent).data) }),
  );
  source.addEventListener("github", (e) =>
    onEvent({ type: "github", data: JSON.parse((e as MessageEvent).data) }),
  );
  source.addEventListener("canvas", (e) =>
    onEvent({ type: "canvas", data: JSON.parse((e as MessageEvent).data) }),
  );
  source.addEventListener("wire", (e) =>
    onEvent({ type: "wire", data: JSON.parse((e as MessageEvent).data) }),
  );
  return () => source.close();
}

// -- canvases ----------------------------------------------------------------------

export interface CanvasBoard {
  x: number;
  y: number;
  w: number;
  h: number;
  title?: string;
  expand?: string;
  is_interactive?: boolean;
}

export interface CanvasIndex {
  v: number;
  title: string;
  boards: Record<string, CanvasBoard>;
  order: string[];
  notes?: Record<
    string,
    {
      kind?: string;
      text: string;
      x: number;
      y: number;
      w?: number;
      maxW?: number;
    }
  >;
  [key: string]: unknown;
}

export interface CanvasSummary {
  preview?: {src: string; width: number; height: number} | null;
  id: string;
  title: string;
  version: number;
  owner: string | null;
  owner_id: string;
  updated_at: number;
  boards: number;
  open_comments: number;
}

export interface CanvasComment {
  id: string;
  body: string;
  author: string | null;
  author_id: string;
  at: number;
  board: string | null;
  x: number | null;
  y: number | null;
  parent_id: string | null;
  resolved: boolean;
  version: number;
}

export interface CanvasDetail extends CanvasSummary {
  shown: number;
  index: CanvasIndex;
  files: { path: string; size: number }[];
  /** Where its files are served (sandboxed), at the version shown. */
  view: string;
  versions: {
    version: number;
    author: string | null;
    at: number;
    label: string;
    changed: string[];
  }[];
  comments: CanvasComment[];
}

export const canvas = {
  list: () => call<{ canvases: CanvasSummary[] }>("/canvas/canvases"),
  create: (title: string) => post<CanvasSummary>("/canvas/canvases", { title }),
  get: (id: string, version?: number) =>
    call<CanvasDetail>(
      `/canvas/canvases/${id}${version ? `?version=${version}` : ""}`,
    ),
  rename: (id: string, title: string) =>
    patch<CanvasSummary>(`/canvas/canvases/${id}`, { title }),
  remove: (id: string) => remove(`/canvas/canvases/${id}`),
  publish: (
    id: string,
    body: {
      files: Record<string, string | null>;
      base_version: number;
      label?: string;
    },
  ) =>
    post<{ version: number; changed: string[] }>(
      `/canvas/canvases/${id}/publish`,
      body,
    ),
  restore: (id: string, version: number) =>
    post<{ version: number }>(`/canvas/canvases/${id}/restore`, { version }),
  comment: (
    id: string,
    body: {
      body: string;
      board?: string | null;
      x?: number | null;
      y?: number | null;
      parent_id?: string | null;
    },
  ) => post<CanvasComment>(`/canvas/canvases/${id}/comments`, body),
  resolve: (comment: string, resolved: boolean) =>
    post<CanvasComment>(`/canvas/comments/${comment}/resolve`, { resolved }),
  deleteComment: (comment: string) => remove(`/canvas/comments/${comment}`),
};

// -- the factory's workplace.yml (development's configuration page) ------------------

export type FactoryConfigFile = Record<string, unknown>;

export const factoryConfig = {
  get: () =>
    call<{
      path: string;
      environment: string;
      config: FactoryConfigFile;
      credentials: boolean;
    }>("/config"),
  save: (config: FactoryConfigFile) =>
    call<{ made: string[]; env_file: string | null }>("/config", {
      method: "PUT",
      body: JSON.stringify({ config }),
    }),
  createAdmin: (body: {
    name: string;
    username: string;
    email: string;
    password: string;
  }) => post<{ person: Person }>("/config/admins", body),
};

// -- the factory's model (as omni-desktop's AI settings) ------------------------------

export type ProviderKind =
  "openai" | "anthropic" | "ollama" | "openai-compatible";

export interface ModelChoice {
  id: string;
  label: string;
  blurb: string;
  recommended?: boolean;
  maxInput: number;
  maxOutput: number;
  verified: boolean;
}

export interface FactoryModelState {
  path: string;
  default: string | null;
  voice_default: string | null;
  providers: {
    name: string;
    type: string;
    base_url: string | null;
    key: string | null;
    models: { ref: string; label: string; realtime: boolean }[];
  }[];
  chatgpt: { state: string; email: string | null; plan: string | null } | null;
  runtime: {
    default: string | null;
    models: {
      ref: string;
      label: string;
      provider: string | null;
      realtime: boolean;
      available: boolean;
      why: string;
      default: boolean;
    }[];
  } | null;
  factory_error: string | null;
}

export const factoryModel = {
  get: () => call<FactoryModelState>("/factory/model"),
  validate: (body: {
    kind: ProviderKind;
    api_key?: string;
    base_url?: string;
  }) =>
    post<{
      ok: boolean;
      code?: string;
      detail?: string;
      models?: string[];
      choices?: ModelChoice[];
    }>("/factory/model/validate", body),
  addProvider: (body: {
    kind: ProviderKind;
    api_key?: string;
    base_url?: string;
    model: ModelChoice;
    make_default: boolean;
  }) =>
    post<FactoryModelState & { ref: string }>("/factory/model/providers", body),
  removeProvider: (name: string) =>
    call<FactoryModelState>(`/factory/model/providers/${name}`, {
      method: "DELETE",
    }),
  setDefault: (model: string) =>
    call<FactoryModelState>("/factory/model/default", {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),
  chatgptStart: () =>
    post<{
      state: string;
      login_id: string | null;
      user_code: string | null;
      verification_url: string | null;
      interval: number | null;
    }>("/factory/model/chatgpt/start", {}),
  chatgptComplete: (login_id: string) =>
    post<{ state: string; default?: string | null }>(
      "/factory/model/chatgpt/complete",
      { login_id },
    ),
  chatgptLogout: () =>
    post<{ ok: boolean }>("/factory/model/chatgpt/logout", {}),
};

export interface FactorySchedule {
  name: string;
  automation: string;
  cron: string;
  timezone: string | null;
  review_runs: number | null;
  next: string;
}

export const factorySchedules = {
  get: () =>
    call<{ schedules: FactorySchedule[]; factory_error: string | null }>(
      "/factory/schedules",
    ),
  run: (name: string) =>
    post<{ fired: string }>(
      `/factory/schedules/${encodeURIComponent(name)}/run`,
      {},
    ),
};

export interface UsageRow {
  agent: string | null;
  model: string | null;
  requests: number;
  input_tokens: number;
  cached_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost: number | null;
}

export interface FactoryRun {
  id: number;
  automation: string;
  agent: string;
  status: string;
  error: string | null;
  started_at: number;
  finished_at: number | null;
  source: {
    provider: string;
    event: string;
    person: string | null;
    link: string | null;
  };
  grades: { judge: string; agent: string; passed: boolean | null; reason: string | null }[];
  usage: UsageRow[];
  cost: number | null;
  pull_requests: { repository: string; number: number; relation: string }[];
}

export interface FactoryPull {
  repository: string;
  number: number;
  title: string;
  url: string;
  state: string;
  requested_at: number;
  requested_by: string | null;
  merged_at: number | null;
  time_to_ship: number | null;
  human_touches: number;
  runs: number[];
  cost: number | null;
  reverted: boolean;
  size: number | null;
}

export interface PassRate {
  passed: number;
  graded: number;
  rate: number;
  interval: [number, number] | null;
}

export interface Period {
  start: number;
  runs: number;
  spend: number;
  merged: number;
  cost_per_merged_pr: number | null;
  human_touches_per_merged_pr: number | null;
  median_time_to_ship: number | null;
  reverted: number;
  median_size: number | null;
  pass_rates: Record<string, PassRate>;
}

export interface Promotion {
  number: number;
  title: string;
  merged_at: number;
  commit: string;
  url: string;
}

export interface Slice {
  requests: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost: number;
  unpriced_requests: number;
}

export interface FactoryReport {
  factory_error: string | null;
  since?: number;
  priced?: boolean;
  summary?: {
    runs: number;
    running: number;
    pull_requests: number;
    merged: number;
    spend: number;
    unpriced_runs: number;
    cost_per_merged_pr: number | null;
    human_touches_per_merged_pr: number | null;
    median_time_to_ship: number | null;
    reverted: number;
  };
  bucket?: "day" | "week";
  over_time?: Period[];
  by_version?: {
    version: string;
    first_run: number;
    runs: number;
    spend: number;
    pass_rates: Record<string, PassRate>;
  }[];
  promotions?: Promotion[];
  runs?: FactoryRun[];
  pull_requests?: FactoryPull[];
  by?: Record<"source" | "agent" | "model" | "person", Record<string, Slice>>;
}

export const factoryReport = {
  get: (days: number) => call<FactoryReport>(`/factory/report?days=${days}`),
};

export interface BenchmarkConfig {
  name: string;
  model: string;
  reasoning: string | null;
  runs: number;
  passed: number;
  correctness: number;
  requests: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost: number | null;
  cost_per_task: number | null;
  seconds_per_task: number | null;
  by_task: Record<string, { runs: number; passed: number }>;
}

export interface BenchmarkResult {
  suite: string;
  at: number;
  runs_per_task: number;
  configs: BenchmarkConfig[];
  frontier: string[];
}

export const factoryBenchmarks = {
  get: () =>
    call<{
      suites: { name: string; path: string; tasks: number }[];
      results: BenchmarkResult[];
      archive: boolean;
      repository: string | null;
    }>("/factory/benchmarks"),
  add: (run_id: number, suite: string) =>
    post<{ number: number; url: string; scenario: string }>(
      "/factory/benchmarks",
      { run_id, suite },
    ),
  run: (repo: string, suite: string) =>
    post<{ run: number }>(
      `/github/repos/${repo}/workflows/benchmarks.yml/dispatches`,
      { ref: "main", inputs: { suite } },
    ),
};

export interface FactoryRunCase {
  id: number;
  automation: string;
  agent: string;
  started_at: number;
  provider: string;
  event: string;
  version: string | null;
  pull_requests: { repository: string; number: number; relation: string }[];
  sessions: {
    agent: string;
    session_id: string;
    started_at?: number;
    prompt: string | null;
    opened?: string[];
  }[];
}

export const factoryRun = {
  get: (id: number) => call<FactoryRunCase>(`/factory/runs/${id}`),
};

export interface LibraryArtifact {
  id: string;
  title: string;
  kind: "page" | "document";
  owner_id: string;
  updated_at: number;
  url: string;
  view: string;
  markdown?: string;
}
export const library = {
  list: () => call<{ artifacts: LibraryArtifact[] }>("/library/artifacts"),
  get: (id: string) => call<LibraryArtifact>(`/library/artifacts/${encodeURIComponent(id)}`),
  create: (title: string, kind: "page" | "document") => post<LibraryArtifact>("/library/artifacts", {title, kind}),
  rename: (id: string, title: string) => patch<LibraryArtifact>(`/library/artifacts/${encodeURIComponent(id)}`, {title}),
  delete: (id: string) => remove(`/library/artifacts/${encodeURIComponent(id)}`),
};
