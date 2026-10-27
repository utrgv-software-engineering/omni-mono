// The configuration page, as Jumpstart's /jumpstart: in development, the
// factory's workplace.yml is edited here. "Save & Reload" writes the file and
// applies it (what the file gained is made); commit it, and deploying applies
// it on the server. Only in development: a server's config comes from git.
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@omniagents/ui/tabs";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";

import { factoryConfig, type FactoryConfigFile } from "./api";

type Row = Record<string, unknown>;
type Field = {
  key: string;
  label: string;
  placeholder: string;
  width?: string;
  type?: "text" | "checkbox";
};

const fail = (e: unknown) => {
  toast.error((e as Error).message);
};

/** A value at a dotted path of the config (``app.name``). */
function at(config: FactoryConfigFile, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (value, key) =>
        value && typeof value === "object" ? (value as Row)[key] : undefined,
      config,
    );
}

/** The config with ``path`` set (empty values removed, as the file leaves them out). */
function setAt(
  config: FactoryConfigFile,
  path: string,
  value: unknown,
): FactoryConfigFile {
  const next = structuredClone(config);
  const keys = path.split(".");
  let node = next as Row;
  for (const key of keys.slice(0, -1)) {
    if (!node[key] || typeof node[key] !== "object") node[key] = {};
    node = node[key] as Row;
  }
  const last = keys[keys.length - 1];
  if (
    value === "" ||
    value === undefined ||
    (Array.isArray(value) && !value.length)
  )
    delete node[last];
  else node[last] = value;
  return next;
}

/** A list the file may write as strings (``[eng]``) or mappings (``[{name: eng}]``). */
function rows(value: unknown, key: string): Row[] {
  return Array.isArray(value)
    ? value.map((v) => (v && typeof v === "object" ? (v as Row) : { [key]: v }))
    : [];
}

export function ConfigDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [file, setFile] = useState<{
    path: string;
    environment: string;
    credentials: boolean;
  } | null>(null);
  const [config, setConfig] = useState<FactoryConfigFile | null>(null);
  const [saving, setSaving] = useState(false);
  const [made, setMade] = useState<string[] | null>(null);
  useEffect(() => {
    if (!open) return;
    setMade(null);
    void factoryConfig.get().then((r) => {
      setFile(r);
      setConfig(r.config);
    }, fail);
  }, [open]);

  const text = (path: string) => {
    const value = config ? at(config, path) : undefined;
    return typeof value === "string" || typeof value === "number"
      ? String(value)
      : "";
  };
  const set = (path: string) => (value: unknown) =>
    setConfig((c) => (c ? setAt(c, path, value) : c));
  const field = (path: string, label: string, placeholder: string) => (
    <div className="space-y-1">
      <Label htmlFor={`cfg-${path}`}>{label}</Label>
      <Input
        id={`cfg-${path}`}
        placeholder={placeholder}
        value={text(path)}
        onChange={(e) => set(path)(e.target.value)}
      />
    </div>
  );

  const save = () => {
    if (!config) return;
    setSaving(true);
    void factoryConfig
      .save(config)
      .then((r) => {
        setMade(r.made);
        toast.success("Saved and applied workplace.yml");
      }, fail)
      .finally(() => setSaving(false));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Configuration</DialogTitle>
          <DialogDescription>
            The factory's workplace, as{" "}
            <code>{file?.path ?? "workplace.yml"}</code> says it. Save &amp;
            Reload writes the file and makes what it gained here (it never
            deletes). Commit it: deploying applies it on the server.{" "}
            <code>{"${...}"}</code> values come from the environment, and{" "}
            <code>{"${credentials.…}"}</code> from the encrypted credentials (
            {file?.environment ?? "development"}:
            {file?.credentials ? " present" : " none yet: bin/setup makes them"}
            ).
          </DialogDescription>
        </DialogHeader>
        {!config ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <Tabs defaultValue="workplace">
            <TabsList>
              <TabsTrigger value="workplace">Workplace</TabsTrigger>
              <TabsTrigger value="factory">Factory</TabsTrigger>
              <TabsTrigger value="slack">Slack</TabsTrigger>
              <TabsTrigger value="github">GitHub</TabsTrigger>
              <TabsTrigger value="linear">Linear</TabsTrigger>
              <TabsTrigger value="canvases">Canvases</TabsTrigger>
              <TabsTrigger value="admins">Admins</TabsTrigger>
            </TabsList>

            <TabsContent
              value="workplace"
              className="grid grid-cols-3 gap-3 pt-3"
            >
              {field("organization.name", "Organization", "Acme")}
              {field("organization.slug", "Short name", "acme")}
              {field("organization.tz", "Time zone", "America/Chicago")}
              <div className="col-span-3">
                {field("admin", "First admin", "${credentials.admin}")}
              </div>
              <p className="col-span-3 text-xs text-muted-foreground">
                The organization's short name is its GitHub organization:
                project.yml's repositories are in it. The first admin is made
                once, from the credentials; add more under Admins.
              </p>
            </TabsContent>

            <TabsContent
              value="factory"
              className="grid grid-cols-2 gap-3 pt-3"
            >
              {field("factory.url", "Factory server", "http://127.0.0.1:8765")}
              {field(
                "factory.auth_token",
                "Its token",
                "${credentials.factory.auth_token}",
              )}
              {field("app.name", "App name", "Acme Factory")}
              {field("app.username", "App username", "acme-factory")}
              <div className="col-span-2">
                {field("app.description", "What it does", "Software factory")}
              </div>
              <div className="space-y-1">
                <Label htmlFor="cfg-delivery">Slack events</Label>
                <NativeSelect
                  id="cfg-delivery"
                  value={text("app.slack_delivery") || "socket"}
                  onChange={(e) => set("app.slack_delivery")(e.target.value)}
                >
                  <NativeSelectOption value="socket">
                    Over Socket Mode
                  </NativeSelectOption>
                  <NativeSelectOption value="http">
                    To a request URL
                  </NativeSelectOption>
                </NativeSelect>
              </div>
              {field(
                "app.slack_request_url",
                "Slack request URL",
                "https://host/integrations/slack/events",
              )}
              {field(
                "app.github_webhook",
                "GitHub webhook",
                "http://127.0.0.1:8765/integrations/github/events",
              )}
              {field(
                "app.linear_webhook",
                "Linear webhook",
                "http://127.0.0.1:8765/integrations/linear/events",
              )}
              {field("env_file", "Its credentials go to", ".env")}
              {field("start", "Start command (printed)", "bin/dev")}
            </TabsContent>

            <TabsContent value="slack" className="pt-3">
              <ListEditor
                title="Channels: everyone joins them, and the factory is in each"
                rows={rows(at(config, "slack.channels"), "name")}
                fields={[
                  {
                    key: "name",
                    label: "Name",
                    placeholder: "eng",
                    width: "w-48",
                  },
                  {
                    key: "purpose",
                    label: "Purpose",
                    placeholder: "Requests for the factory",
                  },
                ]}
                onChange={set("slack.channels")}
              />
            </TabsContent>

            <TabsContent value="github" className="pt-3">
              <ListEditor
                title="Repositories, in the organization"
                rows={rows(at(config, "github.repositories"), "name")}
                fields={[
                  {
                    key: "name",
                    label: "Name",
                    placeholder: "api-service",
                    width: "w-40",
                  },
                  {
                    key: "description",
                    label: "Description",
                    placeholder: "A JSON API for tasks",
                  },
                  {
                    key: "from",
                    label: "Files from",
                    placeholder: "evaluations/fixtures/workspaces/api_service",
                  },
                  {
                    key: "private",
                    label: "Private",
                    placeholder: "",
                    type: "checkbox",
                    width: "w-16",
                  },
                ]}
                nested={{
                  key: "labels",
                  title: "Labels",
                  fields: [
                    {
                      key: "name",
                      label: "Label",
                      placeholder: "bug",
                      width: "w-40",
                    },
                    {
                      key: "color",
                      label: "Color",
                      placeholder: "d73a4a",
                      width: "w-28",
                    },
                    {
                      key: "description",
                      label: "Description",
                      placeholder: "Something isn't working",
                    },
                  ],
                }}
                onChange={set("github.repositories")}
              />
            </TabsContent>

            <TabsContent value="linear" className="pt-3">
              <ListEditor
                title="Teams"
                rows={rows(at(config, "linear.teams"), "name")}
                fields={[
                  {
                    key: "key",
                    label: "Key",
                    placeholder: "ENG",
                    width: "w-24",
                  },
                  { key: "name", label: "Name", placeholder: "Engineering" },
                ]}
                nested={{
                  key: "labels",
                  title: "Labels",
                  fields: [
                    {
                      key: "name",
                      label: "Label",
                      placeholder: "Bug",
                      width: "w-40",
                    },
                    {
                      key: "color",
                      label: "Color",
                      placeholder: "#EB5757",
                      width: "w-28",
                    },
                  ],
                }}
                onChange={set("linear.teams")}
              />
            </TabsContent>

            <TabsContent value="canvases" className="pt-3">
              <ListEditor
                title="Canvases the workplace starts with"
                rows={rows(at(config, "canvases"), "title")}
                fields={[
                  {
                    key: "title",
                    label: "Title",
                    placeholder: "Web redesign",
                    width: "w-56",
                  },
                  {
                    key: "from",
                    label: "Files from (a canvas.json and its boards)",
                    placeholder: "designs/web",
                  },
                ]}
                onChange={set("canvases")}
              />
            </TabsContent>

            <TabsContent value="admins" className="pt-3">
              <CreateAdmin />
            </TabsContent>
          </Tabs>
        )}
        {made && (
          <div className="rounded-md border bg-muted/40 p-3 text-sm">
            {made.length ? (
              <>
                Made:{" "}
                <span className="text-muted-foreground">{made.join(", ")}</span>
              </>
            ) : (
              "Applied: everything it names was there already."
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button onClick={save} disabled={!config || saving}>
            {saving ? "Saving…" : "Save & Reload"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RowFields({
  row,
  fields,
  onChange,
}: {
  row: Row;
  fields: Field[];
  onChange: (row: Row) => void;
}) {
  return (
    <>
      {fields.map((f) =>
        f.type === "checkbox" ? (
          <label
            key={f.key}
            className={`flex items-center gap-1 text-xs ${f.width ?? ""}`}
          >
            <input
              type="checkbox"
              checked={!!row[f.key]}
              onChange={(e) => {
                const next = { ...row };
                if (e.target.checked) next[f.key] = true;
                else delete next[f.key];
                onChange(next);
              }}
            />
            {f.label}
          </label>
        ) : (
          <Input
            key={f.key}
            aria-label={f.label}
            className={`h-8 ${f.width ?? "flex-1"}`}
            placeholder={f.placeholder}
            value={
              typeof row[f.key] === "string" || typeof row[f.key] === "number"
                ? String(row[f.key])
                : ""
            }
            onChange={(e) => {
              const next = { ...row };
              if (e.target.value) next[f.key] = e.target.value;
              else delete next[f.key];
              onChange(next);
            }}
          />
        ),
      )}
    </>
  );
}

function ListEditor({
  title,
  rows: items,
  fields,
  nested,
  onChange,
}: {
  title: string;
  rows: Row[];
  fields: Field[];
  nested?: { key: string; title: string; fields: Field[] };
  onChange: (rows: Row[]) => void;
}) {
  const replace = (i: number, row: Row) =>
    onChange(items.map((r, n) => (n === i ? row : r)));
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        {title}
        <Button
          size="xs"
          variant="outline"
          className="ml-auto"
          onClick={() => onChange([...items, {}])}
        >
          <Plus /> Add
        </Button>
      </div>
      {items.length === 0 && (
        <p className="text-sm text-muted-foreground">None.</p>
      )}
      {items.map((row, i) => (
        <div key={i} className="space-y-2 rounded-md border p-2">
          <div className="flex items-center gap-2">
            <RowFields
              row={row}
              fields={fields}
              onChange={(r) => replace(i, r)}
            />
            <Button
              size="icon-sm"
              variant="ghost"
              title="Take it out of the file (what it made stays)"
              onClick={() => onChange(items.filter((_, n) => n !== i))}
            >
              <X />
            </Button>
          </div>
          {nested && (
            <div className="ml-4 space-y-1">
              {rows(row[nested.key], "name").map((sub, j, all) => (
                <div key={j} className="flex items-center gap-2">
                  <RowFields
                    row={sub}
                    fields={nested.fields}
                    onChange={(s) =>
                      replace(i, {
                        ...row,
                        [nested.key]: all.map((x, k) => (k === j ? s : x)),
                      })
                    }
                  />
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    onClick={() => {
                      const left = all.filter((_, k) => k !== j);
                      const next = { ...row };
                      if (left.length) next[nested.key] = left;
                      else delete next[nested.key];
                      replace(i, next);
                    }}
                  >
                    <X />
                  </Button>
                </div>
              ))}
              <Button
                size="xs"
                variant="ghost"
                onClick={() =>
                  replace(i, {
                    ...row,
                    [nested.key]: [...rows(row[nested.key], "name"), {}],
                  })
                }
              >
                <Plus /> {nested.title}
              </Button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function CreateAdmin() {
  const [form, setForm] = useState({
    name: "",
    username: "",
    email: "",
    password: "",
  });
  return (
    <form
      className="grid grid-cols-2 gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        void factoryConfig.createAdmin(form).then(({ person }) => {
          toast.success(
            `${person.name} (${person.username}) is an admin: they sign in with that password`,
          );
          setForm({ name: "", username: "", email: "", password: "" });
        }, fail);
      }}
    >
      <p className="col-span-2 text-sm text-muted-foreground">
        Create an admin user with a password, as Jumpstart's configuration page
        does in development. On a server, an admin adds people from People.
      </p>
      {(
        [
          ["name", "Name", "Jane Smith", "text"],
          ["username", "Username", "jsmith", "text"],
          ["email", "Email", "jane@example.com", "email"],
          ["password", "Password", "••••••••", "password"],
        ] as const
      ).map(([key, label, placeholder, type]) => (
        <div key={key} className="space-y-1">
          <Label htmlFor={`admin-${key}`}>{label}</Label>
          <Input
            id={`admin-${key}`}
            type={type}
            placeholder={placeholder}
            value={form[key]}
            onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          />
        </div>
      ))}
      <div className="col-span-2 flex justify-end">
        <Button
          type="submit"
          disabled={
            !form.name ||
            !form.username ||
            !form.email ||
            form.password.length < 8
          }
        >
          Create admin user
        </Button>
      </div>
    </form>
  );
}
