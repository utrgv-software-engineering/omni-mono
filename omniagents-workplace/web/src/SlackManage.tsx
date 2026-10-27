// Managing Slack: browsing and joining channels, a channel's details (about,
// members, settings), drafts and scheduled messages, search, your profile and
// the workspace's settings.
import { useEffect, useState } from "react";

import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
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
import { Textarea } from "@omniagents/ui/textarea";
import { Archive, Hash, Lock, X } from "lucide-react";
import { toast } from "sonner";

import {
  api,
  type Person,
  type SlackBookmark,
  type SlackChannel,
  type SlackDraft,
  type SlackMessage,
  type SlackScheduled,
  type SlackUser,
} from "./api";
import { time } from "./Message";
import { emoji, Mrkdwn } from "./mrkdwn";

const failed = (e: Error) => toast.error(`Slack said: ${e.message}`);

/** Every IANA time zone this browser knows. */
const ZONES: string[] = (() => {
  try {
    return (
      Intl as unknown as { supportedValuesOf: (k: string) => string[] }
    ).supportedValuesOf("timeZone");
  } catch {
    return [];
  }
})();

function ZoneSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (tz: string) => void;
}) {
  const zones = ZONES.includes(value) || !value ? ZONES : [value, ...ZONES];
  return (
    <NativeSelect
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {zones.map((z) => (
        <NativeSelectOption key={z} value={z}>
          {z}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  );
}

// -- browsing channels ------------------------------------------------------------

export function BrowseChannels({
  open,
  onOpenChange,
  channels,
  actingAs,
  onOpen,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channels: SlackChannel[];
  actingAs: string;
  onOpen: (id: string) => void;
  onChanged: () => Promise<void>;
}) {
  const [filter, setFilter] = useState("");
  const [archived, setArchived] = useState(false);
  const shown = channels
    .filter((c) => !c.is_im && !c.is_mpim && c.is_archived === archived)
    .filter((c) =>
      `${c.name} ${c.purpose.value}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
    )
    .sort((a, b) => a.name.localeCompare(b.name));
  const join = (c: SlackChannel) =>
    api.join(c.id).then(async () => {
      await onChanged();
      onOpen(c.id);
      onOpenChange(false);
    }, failed);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Channels</DialogTitle>
          <DialogDescription>
            Every public channel, and the private ones you are in.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-3">
          <Input
            placeholder="Search channels"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label className="flex shrink-0 items-center gap-2 text-sm">
            <Checkbox
              checked={archived}
              onCheckedChange={(v) => setArchived(!!v)}
            />
            Archived
          </label>
        </div>
        <div className="max-h-96 divide-y overflow-y-auto rounded-md border">
          {shown.length === 0 && (
            <div className="p-4 text-center text-sm text-muted-foreground">
              No channels
            </div>
          )}
          {shown.map((c) => {
            const member = c.members.includes(actingAs);
            return (
              <div key={c.id} className="flex items-center gap-3 px-3 py-2">
                {c.is_private ? (
                  <Lock className="size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <Hash className="size-4 shrink-0 text-muted-foreground" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{c.name}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {member && "Joined · "}
                    {c.members.length}{" "}
                    {c.members.length === 1 ? "member" : "members"}
                    {c.purpose.value && ` · ${c.purpose.value}`}
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    onOpen(c.id);
                    onOpenChange(false);
                  }}
                >
                  View
                </Button>
                {!member && !c.is_archived && !c.is_private && (
                  <Button size="sm" onClick={() => void join(c)}>
                    Join
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// -- a channel's details --------------------------------------------------------------

export function ChannelDialog({
  open,
  onOpenChange,
  channel,
  users,
  actingAs,
  isAdmin,
  tab,
  onTab,
  onChanged,
  onMessage,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channel: SlackChannel;
  users: Map<string, SlackUser>;
  actingAs: string;
  isAdmin: boolean;
  tab: string;
  onTab: (tab: string) => void;
  onChanged: () => Promise<void>;
  onMessage: (userId: string) => void;
}) {
  const [name, setName] = useState(channel.name);
  const [topic, setTopic] = useState(channel.topic.value);
  const [purpose, setPurpose] = useState(channel.purpose.value);
  const [adding, setAdding] = useState("");
  const member = channel.members.includes(actingAs);
  useEffect(() => {
    setName(channel.name);
    setTopic(channel.topic.value);
    setPurpose(channel.purpose.value);
  }, [channel.id, channel.name, channel.topic.value, channel.purpose.value]);

  const edit = (body: Parameters<typeof api.editChannel>[1]) =>
    api.editChannel(channel.id, body).then(onChanged, failed);
  const canRename =
    member && (channel.creator === actingAs || isAdmin) && !channel.is_general;
  const candidates = [...users.values()]
    .filter((u) => !u.deleted && !u.is_bot && !channel.members.includes(u.id))
    .sort((a, b) => a.real_name.localeCompare(b.real_name));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {channel.is_private ? "🔒" : "#"} {channel.name}
          </DialogTitle>
          {channel.is_archived && (
            <DialogDescription>This channel is archived.</DialogDescription>
          )}
        </DialogHeader>
        <Tabs value={tab} onValueChange={onTab}>
          <TabsList>
            <TabsTrigger value="about">About</TabsTrigger>
            <TabsTrigger value="members">
              Members ({channel.members.length})
            </TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>

          <TabsContent value="about" className="space-y-4 pt-2">
            <div className="space-y-1">
              <Label htmlFor="ch-topic">Topic</Label>
              <div className="flex gap-2">
                <Input
                  id="ch-topic"
                  placeholder="Release on Friday"
                  value={topic}
                  disabled={!member || channel.is_archived}
                  onChange={(e) => setTopic(e.target.value)}
                />
                <Button
                  variant="outline"
                  disabled={
                    !member ||
                    channel.is_archived ||
                    topic === channel.topic.value
                  }
                  onClick={() => void edit({ topic })}
                >
                  Save
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="ch-purpose">Description</Label>
              <Textarea
                id="ch-purpose"
                rows={2}
                placeholder="Requests for the factory"
                value={purpose}
                disabled={!member || channel.is_archived}
                onChange={(e) => setPurpose(e.target.value)}
              />
              <div className="flex justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    !member ||
                    channel.is_archived ||
                    purpose === channel.purpose.value
                  }
                  onClick={() => void edit({ purpose })}
                >
                  Save
                </Button>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              Created by{" "}
              {users.get(channel.creator)?.real_name ??
                "someone no longer here"}
            </div>
          </TabsContent>

          <TabsContent value="members" className="space-y-3 pt-2">
            {member && !channel.is_archived && (
              <div className="flex gap-2">
                <NativeSelect
                  value={adding}
                  onChange={(e) => setAdding(e.target.value)}
                  className="flex-1"
                >
                  <NativeSelectOption value="">Add someone…</NativeSelectOption>
                  {candidates.map((u) => (
                    <NativeSelectOption key={u.id} value={u.id}>
                      {u.real_name} (@{u.name})
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
                <Button
                  disabled={!adding}
                  onClick={() =>
                    void api.invite(channel.id, [adding]).then(async () => {
                      setAdding("");
                      await onChanged();
                    }, failed)
                  }
                >
                  Add
                </Button>
              </div>
            )}
            <div className="max-h-72 divide-y overflow-y-auto rounded-md border">
              {channel.members.map((id) => {
                const u = users.get(id);
                return (
                  <div key={id} className="flex items-center gap-2 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {u?.real_name ?? id}
                        {id === actingAs && (
                          <span className="font-normal text-muted-foreground">
                            {" "}
                            (you)
                          </span>
                        )}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        @{u?.name}
                        {u?.profile.title && ` · ${u.profile.title}`}
                      </div>
                    </div>
                    {id !== actingAs && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          onMessage(id);
                          onOpenChange(false);
                        }}
                      >
                        Message
                      </Button>
                    )}
                    {member &&
                      id !== actingAs &&
                      !channel.is_general &&
                      !channel.is_archived && (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          title={`Remove ${u?.real_name ?? id}`}
                          onClick={() =>
                            void api
                              .removeMember(channel.id, id)
                              .then(onChanged, failed)
                          }
                        >
                          <X />
                        </Button>
                      )}
                  </div>
                );
              })}
            </div>
          </TabsContent>

          <TabsContent value="settings" className="space-y-4 pt-2">
            <div className="space-y-1">
              <Label htmlFor="ch-name">Name</Label>
              <div className="flex gap-2">
                <Input
                  id="ch-name"
                  placeholder="channel-name"
                  value={name}
                  disabled={!canRename || channel.is_archived}
                  onChange={(e) => setName(e.target.value)}
                />
                <Button
                  variant="outline"
                  disabled={
                    !canRename || channel.is_archived || name === channel.name
                  }
                  onClick={() => void edit({ name })}
                >
                  Rename
                </Button>
              </div>
              {!canRename && (
                <p className="text-xs text-muted-foreground">
                  Its creator or an admin renames a channel.
                </p>
              )}
            </div>
            {isAdmin && (
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={channel.is_private}
                    disabled={channel.is_general}
                    onCheckedChange={(v) => void edit({ private: !!v })}
                  />
                  Private: only its members see it
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={!!channel.is_default || channel.is_general}
                    disabled={
                      channel.is_general ||
                      channel.is_private ||
                      channel.is_archived
                    }
                    onCheckedChange={(v) => void edit({ default: !!v })}
                  />
                  Default: everyone who joins the workspace joins it
                </label>
              </div>
            )}
            <div className="flex flex-wrap gap-2 border-t pt-4">
              {member && !channel.is_general && (
                <Button
                  variant="outline"
                  onClick={() =>
                    void api.leave(channel.id).then(async () => {
                      onOpenChange(false);
                      await onChanged();
                    }, failed)
                  }
                >
                  Leave #{channel.name}
                </Button>
              )}
              {!channel.is_general &&
                (channel.is_archived
                  ? (member || isAdmin) && (
                      <Button
                        variant="outline"
                        onClick={() => void edit({ archived: false })}
                      >
                        Unarchive
                      </Button>
                    )
                  : member && (
                      <Button
                        variant="destructive"
                        onClick={() => {
                          if (
                            window.confirm(
                              `Archive #${channel.name}? No one can post in it until it is unarchived.`,
                            )
                          )
                            void edit({ archived: true });
                        }}
                      >
                        <Archive /> Archive
                      </Button>
                    ))}
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

/** A channel's pinned messages and bookmarks, under its header. */
export function ChannelBar({
  channel,
  member,
  version,
  lookup,
  onJump,
}: {
  channel: SlackChannel;
  member: boolean;
  version: number;
  lookup: {
    users: Map<string, SlackUser>;
    channels: Map<string, SlackChannel>;
  };
  onJump: (ts: string) => void;
}) {
  const [pins, setPins] = useState<SlackMessage[]>([]);
  const [bookmarks, setBookmarks] = useState<SlackBookmark[]>([]);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ title: "", link: "" });
  const [showPins, setShowPins] = useState(false);
  const load = () =>
    void api.channel(channel.id).then((d) => {
      setPins(d.pins);
      setBookmarks(d.bookmarks);
    }, failed);
  useEffect(load, [channel.id, version]);
  if (channel.is_im || channel.is_mpim) {
    if (!pins.length) return null;
  }
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-1 text-xs">
      {pins.length > 0 && (
        <div className="relative">
          <button
            type="button"
            onClick={() => setShowPins((v) => !v)}
            className="rounded-md border px-2 py-0.5 hover:bg-accent"
          >
            📌 {pins.length} pinned
          </button>
          {showPins && (
            <div className="absolute top-full left-0 z-40 mt-1 w-96 space-y-1 rounded-md border bg-popover p-2 shadow-md">
              {pins.map((m) => (
                <button
                  key={m.ts}
                  type="button"
                  onClick={() => {
                    setShowPins(false);
                    onJump(m.ts);
                  }}
                  className="block w-full rounded p-2 text-left text-sm hover:bg-accent"
                >
                  <div className="text-xs font-semibold">
                    {lookup.users.get(m.user)?.real_name ?? m.user} ·{" "}
                    {time(m.ts)}
                  </div>
                  <div className="line-clamp-2">
                    <Mrkdwn text={m.text} lookup={lookup} />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {bookmarks.map((b) => (
        <span
          key={b.id}
          className="group flex items-center gap-1 rounded-md border px-2 py-0.5"
        >
          <a
            href={b.link}
            target="_blank"
            rel="noreferrer"
            className="hover:underline"
          >
            🔗 {b.title}
          </a>
          {member && (
            <button
              type="button"
              title="Remove the bookmark"
              className="hidden group-hover:inline"
              onClick={() =>
                void api.removeBookmark(channel.id, b.id).then(load, failed)
              }
            >
              <X className="size-3" />
            </button>
          )}
        </span>
      ))}
      {member &&
        !channel.is_archived &&
        !channel.is_im &&
        !channel.is_mpim &&
        (adding ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              void api
                .addBookmark(channel.id, form.title, form.link)
                .then(() => {
                  setForm({ title: "", link: "" });
                  setAdding(false);
                  load();
                }, failed);
            }}
          >
            <Input
              className="h-6 w-32 text-xs"
              placeholder="Runbook"
              value={form.title}
              autoFocus
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
            <Input
              className="h-6 w-56 text-xs"
              placeholder="https://example.com"
              value={form.link}
              onChange={(e) => setForm({ ...form, link: e.target.value })}
            />
            <Button size="xs" type="submit">
              Add
            </Button>
            <Button
              size="xs"
              variant="ghost"
              type="button"
              onClick={() => setAdding(false)}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="text-muted-foreground hover:text-foreground"
          >
            + Add a bookmark
          </button>
        ))}
    </div>
  );
}

// -- drafts and scheduled messages ------------------------------------------------------

export function DraftsDialog({
  open,
  onOpenChange,
  label,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: (channelId: string) => string;
  onChanged: () => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<SlackDraft[]>([]);
  const [scheduled, setScheduled] = useState<SlackScheduled[]>([]);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(
    null,
  );
  const load = () =>
    void api.drafts().then((d) => {
      setDrafts(d.drafts);
      setScheduled(d.scheduled);
    }, failed);
  useEffect(() => {
    if (open) load();
  }, [open]);
  const done = async () => {
    load();
    await onChanged();
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Drafts and scheduled messages</DialogTitle>
          <DialogDescription>
            Yours, including what your agents drafted or scheduled as you.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Drafts</h3>
            {drafts.length === 0 && (
              <p className="text-sm text-muted-foreground">No drafts.</p>
            )}
            {drafts.map((d) => (
              <div key={d.id} className="space-y-2 rounded-md border p-3">
                <div className="text-xs text-muted-foreground">
                  To {label(d.channel)}
                  {d.thread_ts && " · in a thread"}
                </div>
                {editing?.id === d.id ? (
                  <Textarea
                    rows={3}
                    value={editing.text}
                    onChange={(e) =>
                      setEditing({ id: d.id, text: e.target.value })
                    }
                  />
                ) : (
                  <div className="text-sm whitespace-pre-wrap">{d.text}</div>
                )}
                <div className="flex justify-end gap-2">
                  {editing?.id === d.id ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        void api.editDraft(d.id, editing.text).then(() => {
                          setEditing(null);
                          load();
                        }, failed)
                      }
                    >
                      Save
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditing({ id: d.id, text: d.text })}
                    >
                      Edit
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void api.deleteDraft(d.id).then(done, failed)
                    }
                  >
                    Delete
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => void api.sendDraft(d.id).then(done, failed)}
                  >
                    Send
                  </Button>
                </div>
              </div>
            ))}
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Scheduled</h3>
            {scheduled.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Nothing scheduled.
              </p>
            )}
            {scheduled.map((q) => (
              <div
                key={q.id}
                className="flex items-start gap-3 rounded-md border p-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-muted-foreground">
                    To {label(q.channel)} ·{" "}
                    {new Date(q.post_at * 1000).toLocaleString()}
                  </div>
                  <div className="text-sm whitespace-pre-wrap">{q.text}</div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void api.cancelScheduled(q.id).then(done, failed)
                  }
                >
                  Cancel
                </Button>
              </div>
            ))}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// -- search ---------------------------------------------------------------------------------

export function SearchDialog({
  query,
  onClose,
  lookup,
  label,
  onOpenMessage,
  onOpenChannel,
  onMessage,
}: {
  query: string;
  onClose: () => void;
  lookup: {
    users: Map<string, SlackUser>;
    channels: Map<string, SlackChannel>;
  };
  label: (channelId: string) => string;
  onOpenMessage: (m: SlackMessage) => void;
  onOpenChannel: (id: string) => void;
  onMessage: (userId: string) => void;
}) {
  const [results, setResults] = useState<Awaited<
    ReturnType<typeof api.search>
  > | null>(null);
  useEffect(() => {
    setResults(null);
    if (query.trim()) void api.search(query).then(setResults, failed);
  }, [query]);
  return (
    <Dialog open={!!query} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Results for “{query}”</DialogTitle>
        </DialogHeader>
        {!results ? (
          <p className="text-sm text-muted-foreground">Searching…</p>
        ) : (
          <div className="max-h-[60vh] space-y-4 overflow-y-auto">
            {results.channels.length > 0 && (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold text-muted-foreground uppercase">
                  Channels
                </h3>
                {results.channels.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => {
                      onOpenChannel(c.id);
                      onClose();
                    }}
                    className="block w-full rounded px-2 py-1 text-left text-sm hover:bg-accent"
                  >
                    {c.is_private ? "🔒" : "#"} {c.name}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {c.purpose.value}
                    </span>
                  </button>
                ))}
              </section>
            )}
            {results.people.length > 0 && (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold text-muted-foreground uppercase">
                  People
                </h3>
                {results.people.map((u) => (
                  <button
                    key={u.id}
                    type="button"
                    onClick={() => {
                      onMessage(u.id);
                      onClose();
                    }}
                    className="block w-full rounded px-2 py-1 text-left text-sm hover:bg-accent"
                  >
                    {u.real_name}
                    <span className="ml-2 text-xs text-muted-foreground">
                      @{u.name}
                      {u.profile.title && ` · ${u.profile.title}`}
                    </span>
                  </button>
                ))}
              </section>
            )}
            <section className="space-y-1">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase">
                Messages
              </h3>
              {results.messages.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No messages match.
                </p>
              )}
              {results.messages.map((m) => (
                <button
                  key={`${m.channel}:${m.ts}`}
                  type="button"
                  onClick={() => {
                    onOpenMessage(m);
                    onClose();
                  }}
                  className="block w-full rounded-md border p-2 text-left text-sm hover:bg-accent"
                >
                  <div className="text-xs text-muted-foreground">
                    {label(m.channel ?? "")} ·{" "}
                    {lookup.users.get(m.user)?.real_name ?? m.user} ·{" "}
                    {new Date(Number(m.ts) * 1000).toLocaleString()}
                  </div>
                  <div className="line-clamp-3">
                    <Mrkdwn text={m.text} lookup={lookup} />
                  </div>
                </button>
              ))}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// -- your profile ------------------------------------------------------------------------

const STATUSES = [
  { emoji: "calendar", text: "In a meeting" },
  { emoji: "car", text: "Commuting" },
  { emoji: "face_with_thermometer", text: "Out sick" },
  { emoji: "palm_tree", text: "Vacationing" },
  { emoji: "house_with_garden", text: "Working remotely" },
];

export function ProfileDialog({
  open,
  onOpenChange,
  me,
  account,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  me: SlackUser;
  account: Person;
  onSaved: () => Promise<void>;
}) {
  const initial = () => ({
    name: account.name,
    display_name: me.profile.display_name,
    title: me.profile.title,
    tz: me.tz,
    status_emoji: me.profile.status_emoji.replace(/:/g, ""),
    status_text: me.profile.status_text,
  });
  const [form, setForm] = useState(initial);
  useEffect(() => {
    if (open) setForm(initial());
  }, [open]);
  const set = (key: keyof typeof form) => (value: string) =>
    setForm((f) => ({ ...f, [key]: value }));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Your profile</DialogTitle>
          <DialogDescription>
            Your name is the same in Slack, GitHub and Linear.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void api.editProfile(form).then(async () => {
              await onSaved();
              onOpenChange(false);
            }, failed);
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="pr-name">Full name</Label>
            <Input
              id="pr-name"
              placeholder="Jane Smith"
              value={form.name}
              onChange={(e) => set("name")(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="pr-display">Display name</Label>
            <Input
              id="pr-display"
              placeholder="jane"
              value={form.display_name}
              onChange={(e) => set("display_name")(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="pr-title">Title</Label>
            <Input
              id="pr-title"
              placeholder="Software engineer"
              value={form.title}
              onChange={(e) => set("title")(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="pr-tz">Time zone</Label>
            <ZoneSelect id="pr-tz" value={form.tz} onChange={set("tz")} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="pr-status">Status</Label>
            <div className="flex gap-2">
              <Input
                className="w-32"
                placeholder="calendar"
                value={form.status_emoji}
                onChange={(e) => set("status_emoji")(e.target.value)}
                aria-label="Status emoji"
              />
              <Input
                id="pr-status"
                placeholder="In a meeting"
                value={form.status_text}
                onChange={(e) => set("status_text")(e.target.value)}
              />
            </div>
            <div className="flex flex-wrap gap-1 pt-1">
              {STATUSES.map((s) => (
                <button
                  key={s.text}
                  type="button"
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      status_emoji: s.emoji,
                      status_text: s.text,
                    }))
                  }
                  className="rounded-full border px-2 py-0.5 text-xs hover:bg-accent"
                >
                  {emoji(s.emoji)} {s.text}
                </button>
              ))}
              <button
                type="button"
                onClick={() =>
                  setForm((f) => ({ ...f, status_emoji: "", status_text: "" }))
                }
                className="rounded-full border px-2 py-0.5 text-xs hover:bg-accent"
              >
                Clear
              </button>
            </div>
          </div>
          <div className="flex justify-end">
            <Button type="submit">Save</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// -- the workspace (admins) -------------------------------------------------------------

export function TeamDialog({
  open,
  onOpenChange,
  team,
  channels,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: { name: string; domain: string; tz: string };
  channels: SlackChannel[];
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(team.name);
  const [tz, setTz] = useState(team.tz);
  useEffect(() => {
    if (open) {
      setName(team.name);
      setTz(team.tz);
    }
  }, [open]);
  const publicChannels = channels
    .filter((c) => !c.is_im && !c.is_mpim && !c.is_private && !c.is_archived)
    .sort((a, b) => a.name.localeCompare(b.name));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Workspace settings</DialogTitle>
          <DialogDescription>
            {team.domain}: its Slack domain, GitHub organization and Linear URL.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void api.editTeam({ name, tz }).then(onSaved, failed);
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="team-name">Name</Label>
            <Input
              id="team-name"
              placeholder="Acme Inc."
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="team-tz">Time zone</Label>
            <ZoneSelect id="team-tz" value={tz} onChange={setTz} />
            <p className="text-xs text-muted-foreground">
              For people who have not set their own.
            </p>
          </div>
          <div className="flex justify-end">
            <Button
              type="submit"
              disabled={name === team.name && tz === team.tz}
            >
              Save
            </Button>
          </div>
        </form>
        <div className="space-y-2 border-t pt-3">
          <div className="text-sm font-medium">Default channels</div>
          <p className="text-xs text-muted-foreground">
            Everyone who joins the workspace joins these.
          </p>
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {publicChannels.map((c) => (
              <label key={c.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={!!c.is_default || c.is_general}
                  disabled={c.is_general}
                  onCheckedChange={(v) =>
                    void api
                      .editChannel(c.id, { default: !!v })
                      .then(onSaved, failed)
                  }
                />
                #{c.name}
              </label>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
