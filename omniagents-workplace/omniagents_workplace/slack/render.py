"""Text in the shapes Slack produces.

``to_mrkdwn`` turns the standard markdown Slack's MCP tools accept into the
mrkdwn a message stores (``*pagination*`` is read back as
``_pagination_``). The ``render_*`` functions lay out tool results the way
the hosted Slack MCP server does; ``reference/slack_mcp_responses.json``
holds the real responses they follow.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

from .workspace import Channel, Message, SlackWorkspace, User

__all__ = [
    "message_link",
    "plain_text",
    "slack_time",
    "to_mrkdwn",
]

_CODE = re.compile(r"(```.*?```|`[^`\n]+`)", re.S)


def to_mrkdwn(markdown: str) -> str:
    """Standard markdown as Slack mrkdwn; code is left as it is."""
    parts = _CODE.split(markdown)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part)
            continue
        text = part
        text = re.sub(r"^#{1,6}\s+(.+?)\s*#*$", r"**\1**", text, flags=re.M)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"<\2|\1>", text)
        text = re.sub(r"~~(.+?)~~", r"~\1~", text)
        # Italic first (single * or _), then bold, so **x** does not become _x_.
        text = re.sub(r"(?<![*\w])\*(?!\*)([^*\n]+?)\*(?![*\w])", r"_\1_", text)
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
        text = re.sub(r"__(.+?)__", r"*\1*", text)
        text = re.sub(r"^(\s*)[-+]\s+", r"\1• ", text, flags=re.M)
        out.append(text)
    return "".join(out)


def plain_text(mrkdwn: str) -> str:
    """mrkdwn without its formatting, as Slack's search shows a message."""
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", mrkdwn)
    text = re.sub(r"<([^>]+)>", r"\1", text)
    text = re.sub(r"(?<!\w)[*_~](\S(?:.*?\S)?)[*_~](?!\w)", r"\1", text)
    return text.replace("`", "")


def slack_time(ts: str, tz: str) -> str:
    """``2026-10-23 20:40:56 CDT``: a message time in the reader's time zone."""
    moment = datetime.fromtimestamp(float(ts), ZoneInfo(tz))
    return moment.strftime("%Y-%m-%d %H:%M:%S %Z")


def message_link(base_url: str, message: Message) -> str:
    link = f"{base_url.rstrip('/')}/archives/{message.channel}/p{message.ts.replace('.', '')}"
    if message.is_reply:
        link += f"?thread_ts={message.thread_ts}&cid={message.channel}"
    return link


class Renderer:
    """Tool results for one reader (the token's user), in their time zone."""

    def __init__(self, workspace: SlackWorkspace, reader: User, base_url: str):
        self.ws = workspace
        self.reader = reader
        self.base_url = base_url

    def time(self, ts: str) -> str:
        return slack_time(ts, self.ws.tz_of(self.reader))

    def who(self, user_id: str) -> str:
        user = self.ws.users.get(user_id)
        if user is None:
            return f" (ID: {user_id})"
        return f"{user.real_name} <{user.email}> ({user.id})"

    def body(self, message: Message) -> str:
        text = message.text
        if message.app_id and message.app_id in self.ws.apps:
            text += f"\n*Sent using* {self.ws.apps[message.app_id].name}"
        for file_id in message.files:
            file = self.ws.files.get(file_id)
            if file is not None:
                text += f"\nFile: {file.title} ({file.id}, {file.mimetype})"
        return text

    def reactions_line(self, message: Message) -> Optional[str]:
        if not message.reactions:
            return None
        parts = [
            f"{emoji} ({len(users)})" for emoji, users in message.reactions.items()
        ]
        return "Reactions: " + ", ".join(parts)

    # -- channels and threads -----------------------------------------------

    def channel_label(self, channel: Channel) -> str:
        if channel.is_im:
            other = next(
                (m for m in channel.members if m != self.reader.id), self.reader.id
            )
            return f"DM with {self.ws.users[other].real_name} ({channel.id})"
        if channel.is_mpim:
            return f"Group DM ({channel.id})"
        return f"#{channel.name} ({channel.id})"

    def read_channel(
        self, channel: Channel, messages: List[Message], concise: bool
    ) -> str:
        head = f"Channel: {self.channel_label(channel)}\n\n"
        if concise:
            entries = [
                f"{self._concise_name(m.user)}: {self.body(m)} [{self.time(m.ts)}]"
                for m in messages
            ]
            return head + "\n\n".join(entries)
        entries = []
        for m in messages:
            lines = [
                f"=== Message from {self.who(m.user)} at {self.time(m.ts)} === ",
                f"Message TS: {m.ts}",
                self.body(m),
            ]
            replies = self.ws.replies(m.channel, m.ts)
            if replies:
                lines.append(
                    f"Thread: {len(replies)} replies (latest: {self.time(replies[-1].ts)})"
                )
            reactions = self.reactions_line(m)
            if reactions:
                lines.append(reactions)
            entries.append("\n".join(lines))
        return head + "\n\n".join(entries)

    def _concise_name(self, user_id: str) -> str:
        user = self.ws.users.get(user_id)
        return f"{user.real_name} <{user.email}>" if user else user_id

    def read_thread(
        self, parent: Message, replies: List[Message], concise: bool
    ) -> str:
        if concise:
            out = f"THREAD: {self.body(parent)} [{self._concise_name(parent.user)}]\n\n"
            for r in replies:
                out += f"> {self._concise_name(r.user)}: {self.body(r)}\n"
            return out
        lines = [
            "=== THREAD PARENT MESSAGE ===",
            f"From: {self.who(parent.user)}",
            f"Time: {self.time(parent.ts)}",
            f"Message TS: {parent.ts}",
            self.body(parent),
        ]
        reactions = self.reactions_line(parent)
        if reactions:
            lines.append(reactions)
        out = (
            "\n".join(lines) + f"\n\n=== THREAD REPLIES ({len(replies)} total) ===\n\n"
        )
        for i, r in enumerate(replies, 1):
            reply = [
                f"--- Reply {i} of {len(replies)} ---",
                f"From: {self.who(r.user)}",
                f"Time: {self.time(r.ts)}",
                f"Message TS: {r.ts}",
                self.body(r),
            ]
            reactions = self.reactions_line(r)
            if reactions:
                reply.append(reactions)
            out += "\n".join(reply) + "\n"
        return out

    def reactions(self, message: Message) -> str:
        if not message.reactions:
            return "No reactions on this message."
        lines = []
        for emoji, users in message.reactions.items():
            names = ", ".join(
                f"{self.ws.users[u].real_name} ({u})" if u in self.ws.users else u
                for u in users[:50]
            )
            lines.append(f":{emoji}: × {len(users)} — {names}")
        return "Reactions on message:\n\n" + "\n".join(lines)

    # -- people and conversations -------------------------------------------

    def members(self, users: List[User]) -> str:
        out = f"# Channel Members ({len(users)})\n\n"
        for i, u in enumerate(users, 1):
            out += (
                f"## Member {i}\nUser ID: {u.id}\nUsername: @{u.name}\n"
                f"Display Name: {u.display_name}\nReal Name: {u.real_name}\n"
                f"Email: {u.email}\n\n"
            )
        return out

    def user_channels(self, channels: List[Channel]) -> str:
        out = f"## My Channels (showing {len(channels)} of {len(channels)} total)\n\n"
        for c in channels:
            out += (
                f"### {self._channel_title(c)}\n- **ID:** {c.id}\n"
                f"- **Type:** {_type_label(c)}\n"
                f"- **Archived:** {'Yes' if c.is_archived else 'No'}\n\n"
            )
        return out

    def _channel_title(self, channel: Channel) -> str:
        if channel.name:
            return f"#{channel.name}"
        others = [
            self.ws.users[m].real_name for m in channel.members if m != self.reader.id
        ]
        return ", ".join(others) or self.reader.real_name

    def profile(self, user: User, concise: bool) -> str:
        if concise:
            return (
                f"{user.real_name} ({user.title})\n{self.ws.name}\n\n{user.email}\n"
                "Status:  \n"
            )
        yes = lambda flag: "Yes" if flag else "No"  # noqa: E731
        return (
            f"User ID: {user.id}\nUsername: {user.name}\n"
            f"Display Name: {user.display_name}\nReal Name: {user.real_name}\n"
            f"Pronouns: \nTitle: {user.title}\nEmail: {user.email}\n"
            f"Organization Name: {self.ws.name}\nPhone: \nStatus:  \n"
            f"Timezone: {self.ws.tz_of(user)}\nAdmin: {yes(user.is_admin)}\n"
            f"Owner: {yes(user.is_admin)}\nBot: {yes(user.is_bot)}\nRestricted: No\n"
        )

    # -- search --------------------------------------------------------------

    def search_messages(self, results: List[Message], concise: bool) -> str:
        out = "# Search Results for: \n\n"
        if not results:
            return out + "No results found.\n"
        out += f"## Messages ({len(results)} results)\n"
        if concise:
            out += f"({len(results)} results)\n"
            for i, m in enumerate(results, 1):
                channel = self.ws.channels[m.channel]
                user = self.ws.users.get(m.user)
                out += (
                    f"{i}. {self._channel_title(channel)} - {user.real_name if user else m.user}: "
                    f"{plain_text(m.text)} {self.time(m.ts)}\n"
                )
            return out
        for i, m in enumerate(results, 1):
            channel = self.ws.channels[m.channel]
            replies = self.ws.replies(m.channel, m.ts)
            lines = [
                f"### Result {i} of {len(results)}",
                f"Channel: {self._channel_title(channel)} (ID: {channel.id})",
                f"From: {self._search_who(m.user)} ",
                f"Time: {self.time(m.ts)}",
                f"Message_ts: {m.ts}",
            ]
            if replies:
                lines.append(f"Reply count: {len(replies)}")
            link = message_link(self.base_url, m)
            if not m.is_reply and replies:
                link += f"?thread_ts={m.ts}&cid={m.channel}"
            lines += [f"Permalink: [link]({link})", "Text: ", plain_text(m.text)]
            context = replies[:1] if replies else []
            if context:
                lines.append("Context after: ")
                for c in context:
                    lines.append(
                        f"- From: {self._search_who(c.user)} \n  Message_ts: {c.ts}\n"
                        f"  {self.body(c).replace(chr(10), ' ')}"
                    )
            out += "\n".join(lines) + "\n\n---\n\n"
        return out

    def _search_who(self, user_id: str) -> str:
        user = self.ws.users.get(user_id)
        if user is None:
            return f" (ID: {user_id})"
        return f"{user.real_name} <{user.email}> (ID: {user.id})"

    def search_channels(self, channels: List[Channel], concise: bool) -> str:
        out = "# Search Results for: \n\n"
        if not channels:
            return out + "No results found.\n"
        out += f"## Channels ({len(channels)} results)\n"
        for i, c in enumerate(channels, 1):
            if concise:
                out += f"{i}. #{c.name} ({c.id})\n"
                continue
            out += (
                f"### Result {i} of {len(channels)}\nName: #{c.name}\nID: {c.id}\n"
                f"Type: {_type_label(c)}\nMembers: {len(c.members)}\n"
                f"Topic: {c.topic}\nPurpose: {c.purpose}\n"
                f"Archived: {'Yes' if c.is_archived else 'No'}\n\n"
            )
        return out

    def search_users(self, users: Iterable[User], concise: bool) -> str:
        users = list(users)
        out = "# Search Results for: \n\n"
        if not users:
            return out + "No results found.\n"
        out += f"## Users ({len(users)} results)\n"
        for i, u in enumerate(users, 1):
            if concise:
                out += f"{i}. {u.real_name} (@{u.name}, {u.id})\n"
                continue
            out += (
                f"### Result {i} of {len(users)}\nUser ID: {u.id}\nUsername: @{u.name}\n"
                f"Real Name: {u.real_name}\nTitle: {u.title}\nEmail: {u.email}\n\n"
            )
        return out


def _type_label(channel: Channel) -> str:
    return {
        "public_channel": "Public Channel",
        "private_channel": "Private Channel",
        "im": "Direct Message",
        "mpim": "Group Direct Message",
    }[channel.type]
