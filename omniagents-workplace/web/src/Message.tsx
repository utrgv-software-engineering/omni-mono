import { useState } from "react";

import { Button } from "@omniagents/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@omniagents/ui/popover";
import { Textarea } from "@omniagents/ui/textarea";
import {
  MessageSquare,
  MessageSquareReply,
  Pencil,
  Pin,
  PinOff,
  Smile,
  Trash2,
} from "lucide-react";

import type { SlackApp, SlackChannel, SlackMessage, SlackUser } from "./api";
import { emoji, Mrkdwn } from "./mrkdwn";

/** Messages Slack shows as a line about the channel, not as someone's message. */
const NOTICES = new Set([
  "channel_join",
  "channel_leave",
  "channel_topic",
  "channel_purpose",
  "channel_name",
  "channel_archive",
  "channel_unarchive",
]);

const QUICK = ["eyes", "ticket", "white_check_mark", "+1", "tada"];

const COLORS = [
  "bg-sky-600",
  "bg-emerald-600",
  "bg-amber-600",
  "bg-violet-600",
  "bg-rose-600",
  "bg-teal-600",
];

function color(id: string) {
  let n = 0;
  for (const ch of id) n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  return COLORS[n % COLORS.length];
}

export function time(ts: string) {
  return new Date(Number(ts) * 1000).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

interface Props {
  message: SlackMessage;
  lookup: {
    users: Map<string, SlackUser>;
    channels: Map<string, SlackChannel>;
    onChannel?: (id: string) => void;
  };
  apps: Map<string, SlackApp>;
  actingAs: string;
  onReact: (name: string) => void;
  onThread?: () => void;
  /** Change the text (offered on your own messages). */
  onEdit?: (text: string) => Promise<void>;
  /** Offered on your own messages, and to admins on anyone's. */
  onDelete?: () => void;
  onPin?: (pinned: boolean) => void;
  /** Open a DM with someone (their profile card's Message button). */
  onMessage?: (userId: string) => void;
}

export function MessageRow({
  message,
  lookup,
  apps,
  actingAs,
  onReact,
  onThread,
  onEdit,
  onDelete,
  onPin,
  onMessage,
}: Props) {
  const user = lookup.users.get(message.user);
  const app = message.app_id ? apps.get(message.app_id) : undefined;
  const isFactory = [...apps.values()].some((a) => a.user === message.user);
  const [editing, setEditing] = useState<string | null>(null);
  const pinned = !!message.pinned_to?.length;

  if (message.subtype && NOTICES.has(message.subtype)) {
    return (
      <div className="px-4 py-1 text-xs text-muted-foreground">
        <Mrkdwn text={message.text} lookup={lookup} /> · {time(message.ts)}
      </div>
    );
  }
  if (message.subtype === "tombstone") {
    return (
      <div className="flex gap-3 px-4 py-1.5">
        <div className="size-9 shrink-0 rounded-md bg-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-sm text-muted-foreground italic">
            This message was deleted.
          </div>
          {onThread && !!message.reply_count && (
            <button
              type="button"
              onClick={onThread}
              className="mt-1 text-xs font-medium text-sky-600 hover:underline dark:text-sky-400"
            >
              {message.reply_count}{" "}
              {message.reply_count === 1 ? "reply" : "replies"}
            </button>
          )}
        </div>
      </div>
    );
  }

  const save = async () => {
    if (editing === null || !onEdit) return;
    const text = editing.trim();
    if (!text) return;
    await onEdit(text);
    setEditing(null);
  };

  return (
    <div className="group relative flex gap-3 px-4 py-1.5 hover:bg-muted/40">
      <ProfileCard user={user} isApp={isFactory} onMessage={onMessage}>
        <button
          type="button"
          className={`flex size-9 shrink-0 items-center justify-center rounded-md text-sm font-semibold text-white ${color(message.user)}`}
        >
          {isFactory ? "🤖" : (user?.real_name ?? "?").slice(0, 1)}
        </button>
      </ProfileCard>
      <div className="min-w-0 flex-1">
        {pinned && (
          <div className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
            <Pin className="size-3" /> Pinned
          </div>
        )}
        <div className="flex items-baseline gap-2">
          <ProfileCard user={user} isApp={isFactory} onMessage={onMessage}>
            <button type="button" className="font-semibold hover:underline">
              {user?.real_name ?? message.user}
            </button>
          </ProfileCard>
          {user?.profile.status_emoji && (
            <span title={user.profile.status_text}>
              {emoji(user.profile.status_emoji.replace(/:/g, ""))}
            </span>
          )}
          <span className="text-xs text-muted-foreground">
            {time(message.ts)}
          </span>
        </div>
        {editing !== null ? (
          <div className="mt-1 space-y-2">
            <Textarea
              value={editing}
              autoFocus
              rows={3}
              onChange={(e) => setEditing(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void save();
                }
                if (e.key === "Escape") setEditing(null);
              }}
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditing(null)}
              >
                Cancel
              </Button>
              <Button size="sm" onClick={() => void save()}>
                Save
              </Button>
            </div>
          </div>
        ) : null}
        <div
          className={`text-sm leading-relaxed break-words ${editing !== null ? "hidden" : ""}`}
        >
          <Mrkdwn text={message.text} lookup={lookup} />
          {message.edited && (
            <span className="ml-1 text-xs text-muted-foreground">(edited)</span>
          )}
          {message.files?.map((file) =>
            file.mimetype.startsWith("image/") ? (
              <a
                key={file.id}
                href={file.url_private}
                target="_blank"
                rel="noreferrer"
                className="mt-1 block w-fit"
              >
                <img
                  src={file.url_private}
                  alt={file.alt_txt || file.title}
                  className="max-h-72 max-w-md rounded-md border"
                />
              </a>
            ) : file.mimetype.startsWith("video/") ? (
              <video
                key={file.id}
                src={file.url_private}
                controls
                className="mt-1 max-h-72 max-w-md rounded-md border"
              />
            ) : (
              <a
                key={file.id}
                href={file.url_private}
                target="_blank"
                rel="noreferrer"
                className="mt-1 block text-sm text-sky-600 underline"
              >
                {file.title}
              </a>
            ),
          )}
        </div>
        {app && (
          <div className="mt-0.5 text-xs text-muted-foreground">
            Sent using {app.name}
          </div>
        )}
        {!!message.reactions?.length && (
          <div className="mt-1 flex flex-wrap gap-1">
            {message.reactions.map((r) => (
              <button
                key={r.name}
                type="button"
                title={r.users
                  .map((u) => lookup.users.get(u)?.real_name ?? u)
                  .join(", ")}
                onClick={() => onReact(r.name)}
                className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${
                  r.users.includes(actingAs)
                    ? "border-sky-500 bg-sky-500/10"
                    : "hover:bg-accent"
                }`}
              >
                {emoji(r.name)} {r.count}
              </button>
            ))}
          </div>
        )}
        {onThread && !!message.reply_count && (
          <button
            type="button"
            onClick={onThread}
            className="mt-1 flex items-center gap-2 text-xs font-medium text-sky-600 hover:underline dark:text-sky-400"
          >
            <span className="flex -space-x-1">
              {(message.reply_users ?? []).slice(0, 3).map((u) => (
                <span
                  key={u}
                  className={`size-4 rounded-sm ring-1 ring-background ${color(u)}`}
                />
              ))}
            </span>
            {message.reply_count}{" "}
            {message.reply_count === 1 ? "reply" : "replies"}
            {message.latest_reply && (
              <span className="font-normal text-muted-foreground">
                Last reply {time(message.latest_reply)}
              </span>
            )}
          </button>
        )}
      </div>
      <div className="absolute -top-3 right-4 hidden items-center gap-0.5 rounded-md border bg-background p-0.5 shadow-sm group-hover:flex">
        {QUICK.map((name) => (
          <button
            key={name}
            type="button"
            title={`:${name}:`}
            onClick={() => onReact(name)}
            className="rounded px-1 text-sm hover:bg-accent"
          >
            {emoji(name)}
          </button>
        ))}
        <Smile className="mx-1 size-3.5 text-muted-foreground" />
        {onThread && (
          <button
            type="button"
            title="Reply in thread"
            onClick={onThread}
            className="rounded p-1 hover:bg-accent"
          >
            <MessageSquareReply className="size-3.5" />
          </button>
        )}
        {onPin && (
          <button
            type="button"
            title={pinned ? "Unpin from the channel" : "Pin to the channel"}
            onClick={() => onPin(!pinned)}
            className="rounded p-1 hover:bg-accent"
          >
            {pinned ? (
              <PinOff className="size-3.5" />
            ) : (
              <Pin className="size-3.5" />
            )}
          </button>
        )}
        {onEdit && message.user === actingAs && (
          <button
            type="button"
            title="Edit"
            onClick={() => setEditing(message.text)}
            className="rounded p-1 hover:bg-accent"
          >
            <Pencil className="size-3.5" />
          </button>
        )}
        {onDelete && (
          <button
            type="button"
            title="Delete"
            onClick={() => {
              if (window.confirm("Delete this message? This can't be undone."))
                onDelete();
            }}
            className="rounded p-1 text-destructive hover:bg-accent"
          >
            <Trash2 className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

/** Who someone is: their name, title, status and local time, and a way to message them. */
export function ProfileCard({
  user,
  isApp,
  onMessage,
  children,
}: {
  user: SlackUser | undefined;
  isApp?: boolean;
  onMessage?: (userId: string) => void;
  children: React.ReactNode;
}) {
  if (!user) return <>{children}</>;
  let local = "";
  try {
    local = new Date().toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
      timeZone: user.tz || undefined,
    });
  } catch {
    local = "";
  }
  return (
    <Popover>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-2">
        <div className="flex items-center gap-3">
          <div
            className={`flex size-12 shrink-0 items-center justify-center rounded-md text-lg font-semibold text-white ${color(user.id)}`}
          >
            {isApp ? "🤖" : user.real_name.slice(0, 1)}
          </div>
          <div className="min-w-0">
            <div className="truncate font-semibold">{user.real_name}</div>
            <div className="truncate text-sm text-muted-foreground">
              @{user.profile.display_name || user.name}
              {isApp ? " · app" : ""}
            </div>
          </div>
        </div>
        {user.profile.title && (
          <div className="text-sm">{user.profile.title}</div>
        )}
        {(user.profile.status_emoji || user.profile.status_text) && (
          <div className="text-sm">
            {user.profile.status_emoji &&
              emoji(user.profile.status_emoji.replace(/:/g, ""))}{" "}
            {user.profile.status_text}
          </div>
        )}
        {local && (
          <div className="text-xs text-muted-foreground">
            {local} local time{user.tz ? ` (${user.tz})` : ""}
          </div>
        )}
        {onMessage && !user.deleted && (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => onMessage(user.id)}
          >
            <MessageSquare /> Message
          </Button>
        )}
      </PopoverContent>
    </Popover>
  );
}
