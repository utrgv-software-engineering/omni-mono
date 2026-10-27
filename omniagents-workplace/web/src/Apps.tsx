// Apps: the identities agents act as. Register one, point its events at
// your factory, and copy the environment it connects with. Also your own
// tokens, to connect an MCP client as yourself.
import { useEffect, useState } from "react";

import { Button } from "@omniagents/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@omniagents/ui/dialog";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Copy, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { apps as appsApi, tokens as tokensApi, type AppSettings } from "./api";

const fail = (e: Error) => toast.error(e.message);

function agentYml() {
  return `mcp_servers:
  - name: github
    type: streamable_http
    params:
      url: \${GITHUB_MCP_URL:-https://api.githubcopilot.com/mcp/}
      headers:
        Authorization: Bearer \${GITHUB_MCP_TOKEN}
        X-MCP-Toolsets: default,actions
  - name: slack
    type: streamable_http
    params:
      url: \${SLACK_MCP_URL:-https://mcp.slack.com/mcp}
      headers:
        Authorization: Bearer \${SLACK_MCP_TOKEN}
  - name: linear
    type: streamable_http
    params:
      url: \${LINEAR_MCP_URL:-https://mcp.linear.app/mcp}
      headers:
        Authorization: Bearer \${LINEAR_MCP_TOKEN}`;
}

function projectYml(app: AppSettings, repositories: string[]) {
  return `integrations:
  - type: github
    repositories: [${repositories.join(", ")}]
    git_url: \${GITHUB_GIT_URL:-}
    token_env: GITHUB_MCP_TOKEN
    account: ${app.username}
    webhook_secret: \${GITHUB_WEBHOOK_SECRET:-}
  - type: slack
    delivery: ${app.slack!.delivery}
    ${app.slack?.delivery === "http" ? "signing_secret: ${SLACK_SIGNING_SECRET}" : "app_token: ${SLACK_APP_TOKEN}"}
    api_url: \${SLACK_API_URL:-https://slack.com/api}
  - type: linear
    webhook_secret: \${LINEAR_WEBHOOK_SECRET:-}
    access_token: \${LINEAR_ACCESS_TOKEN:-}
    api_url: \${LINEAR_API_URL:-https://api.linear.app/graphql}`;
}

export function AppsDialog({
  open,
  onOpenChange,
  repositories,
  admin,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  repositories: string[];
  /** Admins register apps and say where their events go. */
  admin: boolean;
}) {
  const [list, setList] = useState<AppSettings[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", username: "", description: "" });
  const load = () =>
    void appsApi.list().then(({ apps }) => {
      setList(apps);
      setSelected((id) => id ?? apps.find((a) => a.env)?.id ?? null);
    }, fail);
  useEffect(() => {
    if (open) load();
  }, [open]);
  const app = list.find((a) => a.id === selected && a.env);
  const replace = (next: AppSettings) =>
    setList((all) => all.map((a) => (a.id === next.id ? next : a)));

  const register = (e: React.FormEvent) => {
    e.preventDefault();
    appsApi.register(form).then((made) => {
      setForm({ name: "", username: "", description: "" });
      setList((all) => [...all, made]);
      setSelected(made.id);
    }, fail);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Connections</DialogTitle>
          <DialogDescription>
            An app is what an agent acts as: an account in Slack, GitHub and
            Linear, with its own tokens. A factory reaches the workplace exactly
            as it would reach the real services; only its environment differs.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-2">
          {list.map((a) => (
            <Button
              key={a.id}
              size="sm"
              variant={a.id === selected ? "secondary" : "outline"}
              disabled={!a.env}
              title={a.env ? undefined : `${a.name} is someone else's`}
              onClick={() => setSelected(a.id)}
            >
              {a.slack && (
                <span
                  className={`size-2 rounded-full ${a.slack.connected ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
                />
              )}
              {a.name}
            </Button>
          ))}
        </div>
        {app && (
          <AppPanel
            key={app.id}
            app={app}
            admin={admin}
            repositories={repositories}
            onChange={replace}
            onRemoved={() => {
              setSelected(null);
              load();
            }}
          />
        )}
        {!admin && (
          <p className="border-t pt-4 text-sm text-muted-foreground">
            An admin registers apps. Connect your own MCP client with Your
            tokens.
          </p>
        )}
        <form
          onSubmit={register}
          className={`grid grid-cols-3 gap-3 border-t pt-4 ${admin ? "" : "hidden"}`}
        >
          <div className="col-span-3 text-sm font-medium">Register an app</div>
          <div className="space-y-1">
            <Label htmlFor="app-name">Name</Label>
            <Input
              id="app-name"
              placeholder="Factory"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="app-username">Username</Label>
            <Input
              id="app-username"
              placeholder="factory"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="app-description">What it does</Label>
            <Input
              id="app-description"
              placeholder="Opens PRs for issues"
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
            />
          </div>
          <div className="col-span-3 flex justify-end">
            <Button type="submit" disabled={!form.name || !form.username}>
              Register
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AppPanel({
  app,
  admin,
  repositories,
  onChange,
  onRemoved,
}: {
  app: AppSettings;
  admin: boolean;
  repositories: string[];
  onChange: (app: AppSettings) => void;
  onRemoved: () => void;
}) {
  const [delivery, setDelivery] = useState(app.slack!.delivery);
  const [requestUrl, setRequestUrl] = useState(app.slack?.request_url ?? "");
  const [githubUrl, setGithubUrl] = useState(app.github?.webhook_url ?? "");
  const [linearUrl, setLinearUrl] = useState(app.linear?.webhook_url ?? "");
  const save = () =>
    void appsApi
      .configure(app.id, {
        slack_delivery: delivery,
        slack_request_url: requestUrl,
        github_webhook_url: githubUrl,
        linear_webhook_url: linearUrl,
      })
      .then((next) => {
        onChange(next);
        toast.success("Saved");
      }, fail);
  const rotate = (credential: string) =>
    void appsApi.rotate(app.id, credential).then((next) => {
      onChange(next);
      toast.success(
        "A new one is in the environment; the old one no longer works",
      );
    }, fail);
  const env = Object.entries(app.env ?? {})
    .map(([k, v]) => `export ${k}=${v}`)
    .join("\n");
  return (
    <div className="min-w-0 space-y-4 rounded-md border p-4">
      <div className="text-sm">
        <span className="font-semibold">{app.name}</span>{" "}
        <span className="text-muted-foreground">
          @{app.username}
          {app.owner ? ` · registered by ${app.owner}` : ""}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="delivery">Slack events</Label>
          <NativeSelect
            id="delivery"
            value={delivery}
            onChange={(e) => setDelivery(e.target.value as "socket" | "http")}
          >
            <NativeSelectOption value="socket">
              Over Socket Mode (the app connects)
            </NativeSelectOption>
            <NativeSelectOption value="http">
              To a request URL
            </NativeSelectOption>
          </NativeSelect>
        </div>
        <div className="space-y-1">
          <Label htmlFor="request-url">Slack request URL</Label>
          <Input
            id="request-url"
            disabled={delivery !== "http"}
            placeholder="https://host/integrations/slack/events"
            value={requestUrl}
            onChange={(e) => setRequestUrl(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="github-url">GitHub webhook URL</Label>
          <Input
            id="github-url"
            placeholder="https://host/integrations/github/events"
            value={githubUrl}
            onChange={(e) => setGithubUrl(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="linear-url">
            Linear webhook URL (agent sessions)
          </Label>
          <Input
            id="linear-url"
            placeholder="https://host/integrations/linear/events"
            value={linearUrl}
            onChange={(e) => setLinearUrl(e.target.value)}
          />
        </div>
      </div>
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={save}
          disabled={!admin}
          title={admin ? undefined : "An admin says where an app's events go"}
        >
          Save
        </Button>
      </div>
      <Snippet title="Environment (before omniagents run)" text={env} />
      <div className="flex flex-wrap gap-1 text-xs">
        <span className="mr-1 self-center text-muted-foreground">Rotate:</span>
        {[
          ["github_token", "GitHub token"],
          ["linear_token", "Linear token"],
          ["canvas_token", "Canvas token"],
          ["memory_token", "Memory token"],
          ["slack_user_token", "Slack user token"],
          [
            delivery === "http" ? "slack_signing_secret" : "slack_app_token",
            delivery === "http" ? "Slack signing secret" : "Slack app token",
          ],
          ...(app.github?.webhook_url
            ? [["github_webhook_secret", "GitHub webhook secret"]]
            : []),
          ...(app.linear?.webhook_url
            ? [["linear_webhook_secret", "Linear webhook secret"]]
            : []),
        ].map(([credential, label]) => (
          <Button
            key={credential}
            size="xs"
            variant="outline"
            onClick={() => rotate(credential)}
          >
            <RefreshCw /> {label}
          </Button>
        ))}
      </div>
      <Snippet title="agent.yml: the agents' MCP servers" text={agentYml()} />
      <Snippet
        title="project.yml: the server that receives the events, and the repositories its runs check out"
        text={projectYml(app, repositories)}
      />
      <div className="flex justify-end border-t pt-3">
        <Button
          size="sm"
          variant="ghost"
          className="text-destructive"
          onClick={() => {
            if (
              window.confirm(
                `Remove ${app.name}? Every token it has stops working.`,
              )
            )
              void appsApi.remove(app.id).then(onRemoved, fail);
          }}
        >
          <Trash2 /> Remove the app
        </Button>
      </div>
    </div>
  );
}

export function TokensDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [have, setHave] = useState<Record<string, boolean>>({});
  const [mcp, setMcp] = useState<Record<string, string>>({});
  const [made, setMade] = useState<{ service: string; token: string } | null>(
    null,
  );
  const load = () =>
    void tokensApi.mine().then((r) => {
      setHave(r.tokens);
      setMcp(r.mcp);
    }, fail);
  useEffect(() => {
    if (open) load();
    else setMade(null);
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Your tokens</DialogTitle>
          <DialogDescription>
            Connect your own MCP client (Claude, Codex, omni) to an MCP server
            here, as yourself: its URL, with a token as a Bearer authorization.
            A token is shown once; making a new one retires the old.
          </DialogDescription>
        </DialogHeader>
        {(["slack", "github", "linear", "canvas", "memory"] as const).map((service) => (
          <div
            key={service}
            className="space-y-2 rounded-md border p-3 text-sm"
          >
            <div className="flex items-center gap-2">
              <span className="font-semibold capitalize">{service}</span>
              <code className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                {mcp[service]}
              </code>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  void tokensApi.make(service).then(({ token }) => {
                    setMade({ service, token });
                    load();
                  }, fail)
                }
              >
                {have[service] ? "New token" : "Make a token"}
              </Button>
              {have[service] && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void tokensApi.revoke(service).then(load, fail)
                  }
                >
                  Revoke
                </Button>
              )}
            </div>
            {made?.service === service && (
              <Snippet title="Your new token (copy it now)" text={made.token} />
            )}
          </div>
        ))}
      </DialogContent>
    </Dialog>
  );
}

function Snippet({ title, text }: { title: string; text: string }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
        {title}
        <Button
          variant="ghost"
          size="icon-xs"
          title="Copy"
          onClick={() =>
            void navigator.clipboard
              .writeText(text)
              .then(() => toast.success("Copied"))
          }
        >
          <Copy />
        </Button>
      </div>
      <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs">
        {text}
      </pre>
    </div>
  );
}
