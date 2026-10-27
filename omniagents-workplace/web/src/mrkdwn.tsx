// Slack mrkdwn as React: *bold*, _italic_, ~strike~, `code`, ```blocks```,
// > quotes, <url|text> links, <@U…> and <#C…|name> mentions, :emoji:.
import type { ReactNode } from "react";

import type { SlackChannel, SlackUser } from "./api";

export const EMOJI: Record<string, string> = {
  eyes: "👀",
  ticket: "🎫",
  white_check_mark: "✅",
  heavy_check_mark: "✔️",
  "+1": "👍",
  thumbsup: "👍",
  "-1": "👎",
  tada: "🎉",
  rocket: "🚀",
  heart: "❤️",
  warning: "⚠️",
  x: "❌",
  hourglass: "⌛",
  hourglass_flowing_sand: "⏳",
  bug: "🐛",
  memo: "📝",
  wave: "👋",
  pray: "🙏",
  fire: "🔥",
  thinking_face: "🤔",
  raised_hands: "🙌",
  construction: "🚧",
  link: "🔗",
  robot_face: "🤖",
  white_circle: "⚪",
  large_green_circle: "🟢",
  red_circle: "🔴",
  arrows_counterclockwise: "🔄",
  calendar: "📆",
  car: "🚗",
  face_with_thermometer: "🤒",
  palm_tree: "🌴",
  house_with_garden: "🏡",
  pushpin: "📌",
};

export const emoji = (name: string) => EMOJI[name] ?? `:${name}:`;

interface Lookup {
  users: Map<string, SlackUser>;
  channels: Map<string, SlackChannel>;
  onChannel?: (id: string) => void;
}

export function Mrkdwn({ text, lookup }: { text: string; lookup: Lookup }) {
  const blocks = text.split(/(```[\s\S]*?```)/g);
  return (
    <>
      {blocks.map((block, i) =>
        block.startsWith("```") &&
        block.endsWith("```") &&
        block.length >= 6 ? (
          <pre
            key={i}
            className="my-1 overflow-x-auto rounded-md border bg-muted/50 p-2 font-mono text-xs"
          >
            {block.slice(3, -3).replace(/^\w*\n/, "")}
          </pre>
        ) : (
          <Lines key={i} text={block} lookup={lookup} />
        ),
      )}
    </>
  );
}

function Lines({ text, lookup }: { text: string; lookup: Lookup }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        const quote = /^(>|&gt;)\s?/.exec(line);
        const content = inline(
          quote ? line.slice(quote[0].length) : line,
          lookup,
        );
        return (
          <span key={i}>
            {quote ? (
              <span className="border-l-4 border-muted-foreground/30 pl-2 text-muted-foreground">
                {content}
              </span>
            ) : (
              content
            )}
            {i < lines.length - 1 && <br />}
          </span>
        );
      })}
    </>
  );
}

const TOKEN =
  /(`[^`]+`|<[^>\s][^>]*>|\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~|:[a-z0-9_+'-]+:|https?:\/\/[^\s<>]+)/g;

function inline(text: string, lookup: Lookup): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(TOKEN)) {
    const token = match[0];
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    last = at + token.length;
    out.push(<Token key={key++} token={token} lookup={lookup} />);
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function Token({ token, lookup }: { token: string; lookup: Lookup }) {
  if (token.startsWith("`")) {
    return (
      <code className="rounded border bg-muted/60 px-1 py-0.5 font-mono text-[0.85em] text-rose-600 dark:text-rose-400">
        {token.slice(1, -1)}
      </code>
    );
  }
  if (token.startsWith("<")) {
    const inner = token.slice(1, -1);
    const [target, label] = inner.split("|", 2) as [string, string | undefined];
    if (target.startsWith("@")) {
      const user = lookup.users.get(target.slice(1));
      return <Mention>@{user?.name ?? label ?? target.slice(1)}</Mention>;
    }
    if (target.startsWith("#")) {
      const id = target.slice(1);
      const name = lookup.channels.get(id)?.name ?? label ?? id;
      return (
        <button
          type="button"
          className="text-left"
          onClick={() => lookup.onChannel?.(id)}
        >
          <Mention>#{name}</Mention>
        </button>
      );
    }
    if (target.startsWith("!")) return <Mention>@{target.slice(1)}</Mention>;
    return <Link href={target}>{label ?? target}</Link>;
  }
  if (token.startsWith("http")) return <Link href={token}>{token}</Link>;
  if (token.startsWith(":")) return <>{emoji(token.slice(1, -1))}</>;
  const body = token.slice(1, -1);
  if (token.startsWith("*")) return <strong>{body}</strong>;
  if (token.startsWith("_")) return <em>{body}</em>;
  return <s>{body}</s>;
}

function Mention({ children }: { children: ReactNode }) {
  return (
    <span className="rounded bg-sky-500/15 px-0.5 font-medium text-sky-700 dark:text-sky-300">
      {children}
    </span>
  );
}

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-sky-600 underline-offset-2 hover:underline dark:text-sky-400"
    >
      {children}
    </a>
  );
}
