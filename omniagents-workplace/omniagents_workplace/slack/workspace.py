"""A Slack workspace: its people, conversations and messages, in memory.

Everything the simulator serves (the MCP server, the Web API, the events an
app receives, the UI) reads and changes this one object, so what an agent
does through MCP is what a person sees in the UI and what the factory
receives as events.

Errors are :class:`SlackError` with Slack's own error codes
(``channel_not_found``, ``not_in_channel``, ``thread_not_found``...), so each
surface reports them the way Slack does.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "App",
    "Bookmark",
    "Channel",
    "Message",
    "SlackError",
    "SlackWorkspace",
    "User",
]


class SlackError(Exception):
    """A Slack error code (``channel_not_found``...)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class User:
    id: str
    name: str  # the username: lowercase, no spaces
    real_name: str
    email: str
    #: The person's own time zone (an IANA name); empty uses the workspace's.
    tz: str = ""
    title: str = ""
    display_name: str = ""
    is_admin: bool = False
    is_bot: bool = False
    deleted: bool = False
    status_text: str = ""
    status_emoji: str = ""
    #: The last message they have read in each conversation (``conversations.mark``).
    last_read: Dict[str, str] = field(default_factory=dict)


@dataclass
class Channel:
    id: str
    name: str  # empty for a DM or group DM
    members: List[str]
    is_private: bool = False
    is_im: bool = False
    is_mpim: bool = False
    is_archived: bool = False
    #: The workspace's one channel everyone is in (Slack's ``is_general``).
    is_general: bool = False
    #: One of the workspace's default channels, which new members join (as the general one).
    is_default: bool = False
    created: float = field(default_factory=time.time)
    creator: str = ""
    topic: str = ""
    purpose: str = ""
    bookmarks: List["Bookmark"] = field(default_factory=list)

    @property
    def type(self) -> str:
        if self.is_im:
            return "im"
        if self.is_mpim:
            return "mpim"
        return "private_channel" if self.is_private else "public_channel"

    @property
    def event_channel_type(self) -> str:
        """``channel_type`` on message events: channel, group, im or mpim."""
        if self.is_im:
            return "im"
        if self.is_mpim:
            return "mpim"
        return "group" if self.is_private else "channel"


@dataclass
class Message:
    channel: str
    ts: str
    user: str
    text: str
    thread_ts: Optional[str] = None
    subtype: Optional[str] = None
    #: The app a user token sent it with: Slack shows "Sent using <app>".
    app_id: Optional[str] = None
    reply_broadcast: bool = False
    reactions: Dict[str, List[str]] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)
    #: When its author last changed it (``chat.update``).
    edited_ts: Optional[str] = None
    #: Who pinned it to its channel, if it is pinned.
    pinned_by: Optional[str] = None

    @property
    def is_reply(self) -> bool:
        return self.thread_ts is not None and self.thread_ts != self.ts


@dataclass
class Bookmark:
    """A link saved at the top of a channel."""

    id: str
    title: str
    link: str
    created_by: str
    created: float = field(default_factory=time.time)


@dataclass
class File:
    """An uploaded file: pending until its upload is completed, then shared."""

    id: str
    user: str
    name: str
    length: int
    created: float
    upload_token: str
    alt_txt: str = ""
    snippet_type: str = ""
    title: str = ""
    mimetype: str = "application/octet-stream"
    content: Optional[bytes] = None
    completed: bool = False
    channel: Optional[str] = None
    message_ts: Optional[str] = None


@dataclass
class App:
    """An installed app: tokens, and how its events are delivered.

    Slack's MCP server acts with user tokens, so the app is authorized by an
    account (``user``) and acts as it; ``events`` are that account's event
    subscriptions (``message.channels``, ``message.groups``, ``message.im``,
    ``message.mpim``, ``reaction_added``).
    """

    id: str
    name: str
    user: str
    #: ``None`` once revoked.
    user_token: Optional[str]
    app_token: Optional[str]
    signing_secret: Optional[str]
    socket_mode: bool = True
    request_url: Optional[str] = None
    #: A person's own token, for their MCP client: no events, not listed as an app.
    personal: bool = False
    events: List[str] = field(
        default_factory=lambda: [
            "message.channels",
            "message.groups",
            "message.im",
            "reaction_added",
        ]
    )


@dataclass
class Draft:
    id: str
    user: str
    channel: str
    text: str
    thread_ts: Optional[str] = None


@dataclass
class ScheduledMessage:
    id: str
    user: str
    channel: str
    text: str
    post_at: int
    thread_ts: Optional[str] = None
    reply_broadcast: bool = False
    app_id: Optional[str] = None


#: ``(kind, data)``: ``message``, ``message_changed`` (edited, pinned or
#: unpinned), ``message_deleted`` (``tombstone`` when its thread stays),
#: ``reaction_added``, ``reaction_removed``, ``channel`` (created or changed),
#: ``user``, ``read`` (a person's read marker), ``app``, ``team``, ``draft``,
#: ``draft_removed``, ``file``, ``scheduled``, ``scheduled_removed``, ``reset``.
Listener = Callable[[str, Dict[str, Any]], None]

_CHANNEL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def _seed_time(raw: Dict[str, Any]) -> Optional[float]:
    """A seed message's time: ``at`` (epoch seconds) or ``minutes_ago``."""
    if raw.get("at") is not None:
        return float(raw["at"])
    if raw.get("minutes_ago") is not None:
        return time.time() - float(raw["minutes_ago"]) * 60
    return None


def _id(prefix: str, n: int) -> str:
    return f"{prefix}0W{n:08X}"


class SlackWorkspace:
    def __init__(self, team_id: str, name: str, domain: str, tz: str = ""):
        self.team_id = team_id
        self.name = name
        self.domain = domain
        #: The workspace's time zone: its members' unless they have their own.
        self.tz = tz
        self.users: Dict[str, User] = {}
        self.channels: Dict[str, Channel] = {}
        self.apps: Dict[str, App] = {}
        self.drafts: Dict[str, Draft] = {}
        self.scheduled: Dict[str, ScheduledMessage] = {}
        self.files: Dict[str, File] = {}
        self._messages: Dict[str, List[Message]] = {}
        self._listeners: List[Listener] = []
        self._lock = threading.RLock()
        #: The next id's number, and the last message's time: ids and
        #: timestamps are unique for the workspace's life (persisted with it).
        self.next_id = 1
        self._last_ts = 0.0

    # -- setup ---------------------------------------------------------------

    @classmethod
    def from_seed(cls, seed: Dict[str, Any]) -> "SlackWorkspace":
        """A workspace from a seed's ``slack:`` mapping (see ``seeds/``)."""
        ws = cls(team_id="", name="", domain="")
        ws.load_seed(seed)
        return ws

    def load_seed(self, seed: Dict[str, Any]) -> None:
        """Replace everything with the seed's workspace (listeners stay)."""
        with self._lock:
            team = seed.get("team") or {}
            self.team_id = str(team.get("id") or "")
            self.name = str(team.get("name") or "")
            self.domain = str(team.get("domain") or "")
            self.tz = str(team.get("tz") or "")
            self.users = {}
            self.channels = {}
            self.apps = {}
            self.drafts = {}
            self.scheduled = {}
            self.files = {}
            self._messages = {}
            for raw in seed.get("users") or []:
                user = User(**raw)
                self.users[user.id] = user
            everyone = [u.id for u in self.users.values() if not u.deleted]
            for raw in seed.get("channels") or []:
                raw = dict(raw)
                members = raw.pop("members", "all")
                channel = Channel(
                    members=list(everyone if members == "all" else members), **raw
                )
                self.channels[channel.id] = channel
                self._messages[channel.id] = []
            if not any(c.is_general for c in self.channels.values()):
                general = self.channel_by_name("general")
                if general is not None:
                    general.is_general = True
            for raw in seed.get("apps") or []:
                app = App(**raw)
                self.apps[app.id] = app
            for raw in seed.get("messages") or []:
                self._seed_message(raw)
        self._emit("reset", {})

    def _seed_message(self, raw: Dict[str, Any]) -> None:
        channel = (
            self.channel_by_name(str(raw["channel"])) or self.channels[raw["channel"]]
        )
        message = Message(
            channel=channel.id,
            ts=self._next_ts(_seed_time(raw)),
            user=str(raw["user"]),
            text=str(raw["text"]),
        )
        self._messages[channel.id].append(message)
        for reply in raw.get("replies") or []:
            self._messages[channel.id].append(
                Message(
                    channel=channel.id,
                    ts=self._next_ts(_seed_time(reply)),
                    user=str(reply["user"]),
                    text=str(reply["text"]),
                    thread_ts=message.ts,
                )
            )
        for emoji, users in (raw.get("reactions") or {}).items():
            message.reactions[str(emoji)] = list(users)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _emit(self, kind: str, data: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, data)
            except Exception:  # a listener must not break the workspace
                pass

    def _next_ts(self, at: Optional[float] = None) -> str:
        with self._lock:
            now = float(at) if at is not None else time.time()
            if now <= self._last_ts:
                now = self._last_ts + 0.000001
            self._last_ts = now
            return f"{now:.6f}"

    def _new_id(self, prefix: str) -> str:
        with self._lock:
            n, self.next_id = self.next_id, self.next_id + 1
        return _id(prefix, n)

    # -- lookups -------------------------------------------------------------

    def user(self, user_id: str) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise SlackError("user_not_found")
        return user

    def channel_by_name(self, name: str) -> Optional[Channel]:
        name = name.lstrip("#")
        return next(
            (c for c in self.channels.values() if c.name and c.name == name), None
        )

    def app_for_token(self, token: str) -> Optional[App]:
        if not token:
            return None
        app = next(
            (a for a in self.apps.values() if token in (a.user_token, a.app_token)),
            None,
        )
        # A deactivated member's tokens act for no one.
        if (
            app is not None
            and self.users.get(app.user) is not None
            and self.users[app.user].deleted
        ):
            return None
        return app

    def resolve_channel(self, user_id: str, channel_id: str) -> Channel:
        """``channel_id`` as Slack's tools take it: a conversation id, or a
        user id meaning the DM with that user (opened if need be)."""
        if channel_id in self.users:
            return self.open_dm(user_id, [channel_id])
        channel = self.channels.get(channel_id)
        if channel is None:
            raise SlackError("channel_not_found")
        if (channel.is_private or channel.is_im or channel.is_mpim) and (
            user_id not in channel.members
        ):
            # Slack does not reveal private conversations to non-members.
            raise SlackError("channel_not_found")
        return channel

    def visible_channels(self, user_id: str) -> List[Channel]:
        return [
            c
            for c in self.channels.values()
            if not (c.is_private or c.is_im or c.is_mpim) or user_id in c.members
        ]

    def messages(self, channel_id: str) -> List[Message]:
        """Every message in a conversation, oldest first."""
        return list(self._messages.get(channel_id, []))

    def message(self, channel_id: str, ts: str) -> Message:
        for message in self._messages.get(channel_id, []):
            if message.ts == ts:
                return message
        raise SlackError("message_not_found")

    def history(self, channel_id: str) -> List[Message]:
        """The conversation's top-level messages (and broadcast replies), newest first."""
        return [
            m
            for m in reversed(self._messages.get(channel_id, []))
            if not m.is_reply or m.reply_broadcast
        ]

    def replies(self, channel_id: str, ts: str) -> List[Message]:
        """A thread's replies, oldest first."""
        return [
            m
            for m in self._messages.get(channel_id, [])
            if m.is_reply and m.thread_ts == ts
        ]

    def all_messages(self) -> Iterable[Message]:
        for messages in self._messages.values():
            yield from messages

    # -- changes -------------------------------------------------------------

    def set_team(self, name: str, domain: str, tz: str) -> None:
        """Name the workspace and set its time zone (a new one gets its team id)."""
        valid_tz(tz)
        with self._lock:
            if not self.team_id:
                self.team_id = self._new_id("T")
            self.name, self.domain, self.tz = name, domain, tz
        self._emit("team", {})

    def tz_of(self, user: User) -> str:
        """The time zone ``user`` sees times in: their own, else the workspace's."""
        return user.tz or self.tz

    def general(self) -> Optional[Channel]:
        return next((c for c in self.channels.values() if c.is_general), None)

    def add_user(
        self,
        name: str,
        real_name: str,
        email: str,
        *,
        is_admin: bool = False,
        is_bot: bool = False,
        title: str = "",
    ) -> User:
        """A new member (or an app's bot user); people join the general and default channels."""
        with self._lock:
            if any(u.name == name for u in self.users.values()):
                raise SlackError("name_taken")
            user = User(
                id=self._new_id("U"),
                name=name,
                real_name=real_name,
                email=email,
                is_admin=is_admin,
                is_bot=is_bot,
                title=title,
            )
            self.users[user.id] = user
        self._emit("user", {"user": user})
        if not is_bot:
            for channel in list(self.channels.values()):
                if (
                    channel.is_general or channel.is_default
                ) and not channel.is_archived:
                    self._join(channel, user.id)
        return user

    def update_user(self, user_id: str, **fields: Any) -> User:
        with self._lock:
            user = self.user(user_id)
            for key, value in fields.items():
                if key not in (
                    "real_name",
                    "email",
                    "is_admin",
                    "deleted",
                    "title",
                    "display_name",
                    "tz",
                    "status_text",
                    "status_emoji",
                ):
                    raise SlackError("invalid_arguments")
                if key == "tz" and value:
                    valid_tz(value)
                setattr(user, key, value)
        self._emit("user", {"user": user})
        return user

    def update_app(self, app_id: str, **fields: Any) -> App:
        """Change an app's settings (``request_url``, ``socket_mode``, ``events``)."""
        with self._lock:
            app = self.apps.get(app_id)
            if app is None:
                raise SlackError("app_not_found")
            for key, value in fields.items():
                if key not in (
                    "request_url",
                    "socket_mode",
                    "events",
                    "name",
                    "user_token",
                    "app_token",
                    "signing_secret",
                ):
                    raise SlackError("invalid_arguments")
                setattr(app, key, value)
        self._emit("app", {"app": app})
        return app

    def add_app(self, app: App) -> App:
        with self._lock:
            self.user(app.user)
            self.apps[app.id] = app
        self._emit("app", {"app": app})
        return app

    def post_message(
        self,
        user_id: str,
        channel_id: str,
        text: str,
        *,
        thread_ts: Optional[str] = None,
        reply_broadcast: bool = False,
        app_id: Optional[str] = None,
        subtype: Optional[str] = None,
        files: Optional[List[str]] = None,
    ) -> Message:
        with self._lock:
            self.user(user_id)
            channel = self.resolve_channel(user_id, channel_id)
            if channel.is_archived:
                raise SlackError("is_archived")
            if user_id not in channel.members:
                raise SlackError("not_in_channel")
            if not text.strip() and not files:
                raise SlackError("no_text")
            if thread_ts:
                parent = next(
                    (m for m in self._messages[channel.id] if m.ts == thread_ts), None
                )
                if parent is None:
                    raise SlackError("thread_not_found")
                # A reply to a reply joins the thread it is in.
                thread_ts = parent.thread_ts or parent.ts
            message = Message(
                channel=channel.id,
                ts=self._next_ts(),
                user=user_id,
                text=text,
                thread_ts=thread_ts,
                reply_broadcast=bool(thread_ts and reply_broadcast),
                app_id=app_id,
                subtype=subtype,
                files=list(files or []),
            )
            self._messages[channel.id].append(message)
        self._emit("message", {"message": message, "channel": channel})
        return message

    def start_upload(
        self,
        user_id: str,
        name: str,
        length: int,
        *,
        alt_txt: str = "",
        snippet_type: str = "",
    ) -> File:
        """Step one of an upload: a file id and the token its bytes are POSTed with."""
        import secrets

        if length < 1:
            raise SlackError("invalid_arguments")
        if length > 1024 * 1024 * 1024:
            raise SlackError("file_too_large")
        with self._lock:
            self.user(user_id)
            file = File(
                id=self._new_id("F"),
                user=user_id,
                name=name,
                length=int(length),
                created=time.time(),
                upload_token=secrets.token_urlsafe(24),
                alt_txt=alt_txt,
                snippet_type=snippet_type,
                title=name,
                mimetype=guess_mimetype(name),
            )
            self.files[file.id] = file
        return file

    def receive_upload(self, token: str, content: bytes) -> File:
        """The bytes POSTed to an upload URL; their length must be the one announced."""
        file = next(
            (
                f
                for f in self.files.values()
                if f.upload_token == token and not f.completed
            ),
            None,
        )
        if file is None:
            raise SlackError("unauthorized")
        if len(content) != file.length:
            raise SlackError("invalid_content_length")
        file.content = content
        self._emit("file", {"file": file})
        return file

    def complete_upload(
        self,
        user_id: str,
        file_id: str,
        *,
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
        initial_comment: str = "",
        title: Optional[str] = None,
        app_id: Optional[str] = None,
    ) -> File:
        file = self.files.get(file_id)
        if file is None or file.user != user_id or file.completed:
            raise SlackError("file_not_found")
        if file.content is None:
            raise SlackError("file_upload_incomplete")
        file.title = title or file.title
        if channel_id:
            message = self.post_message(
                user_id,
                channel_id,
                initial_comment,
                thread_ts=thread_ts,
                app_id=app_id,
                files=[file.id],
            )
            file.channel, file.message_ts = message.channel, message.ts
        file.completed = True
        self._emit("file", {"file": file})
        return file

    def add_reaction(
        self, user_id: str, channel_id: str, ts: str, emoji: str
    ) -> Message:
        emoji = emoji.strip(":")
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            if not re.fullmatch(r"[a-z0-9_+'-]+", emoji):
                raise SlackError("invalid_name")
            message = self.message(channel.id, ts)
            users = message.reactions.setdefault(emoji, [])
            if user_id in users:
                raise SlackError("already_reacted")
            users.append(user_id)
        self._emit(
            "reaction_added",
            {
                "message": message,
                "channel": channel,
                "user": user_id,
                "reaction": emoji,
            },
        )
        return message

    def remove_reaction(
        self, user_id: str, channel_id: str, ts: str, emoji: str
    ) -> Message:
        emoji = emoji.strip(":")
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            message = self.message(channel.id, ts)
            users = message.reactions.get(emoji) or []
            if user_id not in users:
                raise SlackError("no_reaction")
            users.remove(user_id)
            if not users:
                message.reactions.pop(emoji, None)
        self._emit(
            "reaction_removed",
            {
                "message": message,
                "channel": channel,
                "user": user_id,
                "reaction": emoji,
            },
        )
        return message

    def create_channel(
        self,
        user_id: str,
        name: str,
        *,
        is_private: bool = False,
        invite: Iterable[str] = (),
        is_general: bool = False,
        is_default: bool = False,
        purpose: str = "",
    ) -> Channel:
        name = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")[:80]
        with self._lock:
            self.user(user_id)
            if not _CHANNEL_NAME.match(name):
                raise SlackError("invalid_name")
            if self.channel_by_name(name):
                raise SlackError("name_taken")
            for other in invite:
                self.user(other)
            channel = Channel(
                id=self._new_id("G" if is_private else "C"),
                name=name,
                members=[user_id],
                is_private=is_private,
                creator=user_id,
                is_general=is_general,
                is_default=is_default,
                purpose=purpose,
            )
            self.channels[channel.id] = channel
            self._messages[channel.id] = []
        self._emit("channel", {"channel": channel})
        self._join(channel, user_id)
        for other in invite:
            if other not in channel.members:
                channel.members.append(other)
                self._join(channel, other)
        return channel

    def join(self, channel_id: str, user_id: str) -> Channel:
        """``user_id`` in the channel (a member already stays one)."""
        channel = self.channels.get(channel_id)
        if channel is None:
            raise SlackError("channel_not_found")
        self.user(user_id)
        if user_id not in channel.members:
            self._join(channel, user_id)
        return channel

    def _join(self, channel: Channel, user_id: str) -> None:
        if user_id not in channel.members:
            channel.members.append(user_id)
        user = self.users[user_id]
        self.post_message(
            user_id,
            channel.id,
            f"<@{user_id}|{user.name}> has joined the channel",
            subtype="channel_join",
        )

    def open_dm(self, user_id: str, others: List[str]) -> Channel:
        """The DM (one other person) or group DM (2-8) with ``others``."""
        with self._lock:
            self.user(user_id)
            people = sorted({user_id, *others})
            for other in others:
                if self.user(other).is_bot:
                    raise SlackError("cannot_dm_bot")
            if not others or len(others) > 8:
                raise SlackError("too_many_users" if others else "invalid_arguments")
            is_im = len(people) <= 2
            for channel in self.channels.values():
                if (channel.is_im or channel.is_mpim) and sorted(
                    channel.members
                ) == people:
                    return channel
            channel = Channel(
                id=self._new_id("D" if is_im else "G"),
                name="",
                members=people,
                is_im=is_im,
                is_mpim=not is_im,
                creator=user_id,
            )
            self.channels[channel.id] = channel
            self._messages[channel.id] = []
        self._emit("channel", {"channel": channel})
        return channel

    # -- messages: editing, deleting, pinning ----------------------------------

    def update_message(
        self, user_id: str, channel_id: str, ts: str, text: str
    ) -> Message:
        """``chat.update``: its author changes a message's text."""
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            message = self.message(channel.id, ts)
            if message.user != user_id:
                raise SlackError("cant_update_message")
            if message.subtype:
                raise SlackError("cant_update_message")
            if not text.strip() and not message.files:
                raise SlackError("no_text")
            previous = message.text
            message.text = text
            message.edited_ts = self._next_ts()
        self._emit(
            "message_changed",
            {"message": message, "channel": channel, "previous_text": previous},
        )
        return message

    def delete_message(self, user_id: str, channel_id: str, ts: str) -> Message:
        """``chat.delete``: its author (or an admin) deletes a message. One a
        thread hangs off stays as Slack's tombstone ("This message was deleted.")."""
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            message = self.message(channel.id, ts)
            if message.user != user_id and not self.user(user_id).is_admin:
                raise SlackError("cant_delete_message")
            if message.subtype == "tombstone":
                raise SlackError("message_not_found")
            tombstone = not message.is_reply and bool(self.replies(channel.id, ts))
            if tombstone:
                message.text, message.subtype, message.files = (
                    "This message was deleted.",
                    "tombstone",
                    [],
                )
                message.reactions, message.pinned_by, message.edited_ts = {}, None, None
            else:
                self._messages[channel.id].remove(message)
            # A deleted message's tombstone goes with its thread's last reply.
            parent = None
            if message.is_reply:
                parent = next(
                    (
                        m
                        for m in self._messages[channel.id]
                        if m.ts == message.thread_ts
                    ),
                    None,
                )
                if parent is not None and (
                    parent.subtype != "tombstone" or self.replies(channel.id, parent.ts)
                ):
                    parent = None
                if parent is not None:
                    self._messages[channel.id].remove(parent)
        self._emit(
            "message_deleted",
            {"message": message, "channel": channel, "tombstone": tombstone},
        )
        if parent is not None:
            self._emit(
                "message_deleted",
                {"message": parent, "channel": channel, "tombstone": False},
            )
        return message

    def pin(self, user_id: str, channel_id: str, ts: str) -> Message:
        with self._lock:
            channel = self._member_of(user_id, channel_id)
            message = self.message(channel.id, ts)
            if message.pinned_by:
                raise SlackError("already_pinned")
            if message.subtype:
                raise SlackError("not_pinnable")
            message.pinned_by = user_id
        self._emit(
            "message_changed",
            {"message": message, "channel": channel, "previous_text": message.text},
        )
        return message

    def unpin(self, user_id: str, channel_id: str, ts: str) -> Message:
        with self._lock:
            channel = self._member_of(user_id, channel_id)
            message = self.message(channel.id, ts)
            if not message.pinned_by:
                raise SlackError("no_pin")
            message.pinned_by = None
        self._emit(
            "message_changed",
            {"message": message, "channel": channel, "previous_text": message.text},
        )
        return message

    def pins(self, channel_id: str) -> List[Message]:
        return [m for m in self._messages.get(channel_id, []) if m.pinned_by]

    def mark(self, user_id: str, channel_id: str, ts: str) -> None:
        """``conversations.mark``: ``user_id`` has read the conversation up to ``ts``."""
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            user = self.user(user_id)
            if float(user.last_read.get(channel.id, "0")) >= float(ts):
                return
            user.last_read[channel.id] = ts
        self._emit("read", {"user": user})

    def unread(self, user_id: str, channel_id: str) -> Tuple[int, int]:
        """Messages in the conversation since ``user_id`` last read it (not their
        own, nor thread replies), and how many of them mention them."""
        user = self.user(user_id)
        since = float(user.last_read.get(channel_id, "0"))
        count = mentions = 0
        for m in self._messages.get(channel_id, []):
            if (
                float(m.ts) <= since
                or m.user == user_id
                or m.subtype
                or (m.is_reply and not m.reply_broadcast)
            ):
                continue
            count += 1
            if f"<@{user_id}" in m.text:
                mentions += 1
        return count, mentions

    # -- conversations: membership and settings ----------------------------------

    def _member_of(self, user_id: str, channel_id: str) -> Channel:
        channel = self.resolve_channel(user_id, channel_id)
        if user_id not in channel.members:
            raise SlackError("not_in_channel")
        return channel

    def _channel_to_manage(self, user_id: str, channel_id: str) -> Channel:
        channel = self._member_of(user_id, channel_id)
        if channel.is_im or channel.is_mpim:
            raise SlackError("method_not_supported_for_channel_type")
        return channel

    def join_channel(self, user_id: str, channel_id: str) -> Channel:
        """``conversations.join``: a public channel, by whoever wants to be in it."""
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            if channel.is_private or channel.is_im or channel.is_mpim:
                raise SlackError("method_not_supported_for_channel_type")
            if channel.is_archived:
                raise SlackError("is_archived")
        return self.join(channel.id, user_id)

    def leave(self, user_id: str, channel_id: str) -> Channel:
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            if channel.is_general:
                raise SlackError("cant_leave_general")
            user = self.users[user_id]
        if not channel.is_archived:
            self.post_message(
                user_id,
                channel.id,
                f"<@{user_id}|{user.name}> has left the channel",
                subtype="channel_leave",
            )
        with self._lock:
            channel.members.remove(user_id)
        self._emit("channel", {"channel": channel})
        return channel

    def invite(self, user_id: str, channel_id: str, others: Iterable[str]) -> Channel:
        """``conversations.invite``: a member brings people (or apps) in."""
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            if channel.is_archived:
                raise SlackError("is_archived")
            others = [o for o in dict.fromkeys(others) if o]
            if not others:
                raise SlackError("no_user")
            for other in others:
                if self.user(other).deleted:
                    raise SlackError("user_is_restricted")
        for other in others:
            self.join(channel.id, other)
        return channel

    def kick(self, user_id: str, channel_id: str, other: str) -> Channel:
        """``conversations.kick``: a member removes someone else."""
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            if channel.is_general:
                raise SlackError("cant_kick_from_general")
            if other == user_id:
                raise SlackError("cant_kick_self")
            if other not in channel.members:
                raise SlackError("not_in_channel")
            channel.members.remove(other)
        self._emit("channel", {"channel": channel})
        return channel

    def set_topic(self, user_id: str, channel_id: str, topic: str) -> Channel:
        return self._set_text(user_id, channel_id, "topic", topic[:250])

    def set_purpose(self, user_id: str, channel_id: str, purpose: str) -> Channel:
        return self._set_text(user_id, channel_id, "purpose", purpose[:250])

    def _set_text(self, user_id: str, channel_id: str, key: str, value: str) -> Channel:
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            if channel.is_archived:
                raise SlackError("is_archived")
            setattr(channel, key, value.strip())
            user = self.users[user_id]
        said = (
            f"set the channel {key}: {value.strip()}"
            if value.strip()
            else f"cleared the channel {key}"
        )
        self.post_message(
            user_id,
            channel.id,
            f"<@{user_id}|{user.name}> {said}",
            subtype=f"channel_{key}",
        )
        self._emit("channel", {"channel": channel})
        return channel

    def rename(self, user_id: str, channel_id: str, name: str) -> Channel:
        """``conversations.rename``: its creator or an admin renames a channel."""
        name = name.strip().lstrip("#").lower()
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            if channel.creator != user_id and not self.user(user_id).is_admin:
                raise SlackError("not_authorized")
            if not _CHANNEL_NAME.match(name):
                raise SlackError("invalid_name_specials")
            if name == channel.name:
                return channel
            if self.channel_by_name(name):
                raise SlackError("name_taken")
            channel.name = name
            user = self.users[user_id]
        self.post_message(
            user_id,
            channel.id,
            f"<@{user_id}|{user.name}> renamed the channel to #{name}",
            subtype="channel_name",
        )
        self._emit("channel", {"channel": channel})
        return channel

    def archive(self, user_id: str, channel_id: str) -> Channel:
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            if channel.is_general:
                raise SlackError("cant_archive_general")
            if channel.is_archived:
                raise SlackError("already_archived")
            user = self.users[user_id]
        self.post_message(
            user_id,
            channel.id,
            f"<@{user_id}|{user.name}> archived the channel",
            subtype="channel_archive",
        )
        with self._lock:
            channel.is_archived = True
            channel.is_default = False
        self._emit("channel", {"channel": channel})
        return channel

    def unarchive(self, user_id: str, channel_id: str) -> Channel:
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            if channel.is_im or channel.is_mpim:
                raise SlackError("method_not_supported_for_channel_type")
            if not channel.is_archived:
                raise SlackError("not_archived")
            if user_id not in channel.members and not self.user(user_id).is_admin:
                raise SlackError("not_in_channel")
            channel.is_archived = False
            if user_id not in channel.members:
                channel.members.append(user_id)
            user = self.users[user_id]
        self.post_message(
            user_id,
            channel.id,
            f"<@{user_id}|{user.name}> un-archived the channel",
            subtype="channel_unarchive",
        )
        self._emit("channel", {"channel": channel})
        return channel

    def set_private(self, user_id: str, channel_id: str, private: bool) -> Channel:
        """An admin makes a channel private (only its members see it) or public."""
        with self._lock:
            if not self.user(user_id).is_admin:
                raise SlackError("not_authorized")
            channel = self.resolve_channel(user_id, channel_id)
            if channel.is_im or channel.is_mpim:
                raise SlackError("method_not_supported_for_channel_type")
            if channel.is_general and private:
                raise SlackError("cant_convert_general")
            channel.is_private = private
            if private:
                channel.is_default = False
        self._emit("channel", {"channel": channel})
        return channel

    def set_default(self, user_id: str, channel_id: str, default: bool) -> Channel:
        """An admin makes a public channel one new members join, or not."""
        with self._lock:
            if not self.user(user_id).is_admin:
                raise SlackError("not_authorized")
            channel = self.resolve_channel(user_id, channel_id)
            if default and (
                channel.is_private
                or channel.is_im
                or channel.is_mpim
                or channel.is_archived
            ):
                raise SlackError("method_not_supported_for_channel_type")
            channel.is_default = default
        self._emit("channel", {"channel": channel})
        return channel

    def add_bookmark(
        self, user_id: str, channel_id: str, title: str, link: str
    ) -> Bookmark:
        title, link = title.strip(), link.strip()
        if not title:
            raise SlackError("invalid_title")
        if not re.match(r"^https?://", link):
            raise SlackError("invalid_link")
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            bookmark = Bookmark(
                id=self._new_id("Bk"), title=title, link=link, created_by=user_id
            )
            channel.bookmarks.append(bookmark)
        self._emit("channel", {"channel": channel})
        return bookmark

    def remove_bookmark(
        self, user_id: str, channel_id: str, bookmark_id: str
    ) -> Channel:
        with self._lock:
            channel = self._channel_to_manage(user_id, channel_id)
            bookmark = next((b for b in channel.bookmarks if b.id == bookmark_id), None)
            if bookmark is None:
                raise SlackError("not_found")
            channel.bookmarks.remove(bookmark)
        self._emit("channel", {"channel": channel})
        return channel

    # -- drafts and scheduled messages ----------------------------------------------

    def update_draft(self, user_id: str, draft_id: str, text: str) -> Draft:
        with self._lock:
            draft = self.drafts.get(draft_id)
            if draft is None or draft.user != user_id:
                raise SlackError("draft_not_found")
            draft.text = text
        self._emit("draft", {"draft": draft})
        return draft

    def delete_draft(self, user_id: str, draft_id: str) -> Draft:
        with self._lock:
            draft = self.drafts.get(draft_id)
            if draft is None or draft.user != user_id:
                raise SlackError("draft_not_found")
            del self.drafts[draft_id]
        self._emit("draft_removed", {"draft": draft})
        return draft

    def send_draft(self, user_id: str, draft_id: str) -> Message:
        draft = self.drafts.get(draft_id)
        if draft is None or draft.user != user_id:
            raise SlackError("draft_not_found")
        message = self.post_message(
            user_id, draft.channel, draft.text, thread_ts=draft.thread_ts
        )
        self.delete_draft(user_id, draft_id)
        return message

    def delete_scheduled(self, user_id: str, scheduled_id: str) -> ScheduledMessage:
        """``chat.deleteScheduledMessage``."""
        with self._lock:
            scheduled = self.scheduled.get(scheduled_id)
            if scheduled is None or scheduled.user != user_id:
                raise SlackError("invalid_scheduled_message_id")
            del self.scheduled[scheduled_id]
        self._emit("scheduled_removed", {"scheduled": scheduled})
        return scheduled

    def save_draft(
        self, user_id: str, channel_id: str, text: str, thread_ts: Optional[str] = None
    ) -> Draft:
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            if user_id not in channel.members:
                raise SlackError("not_in_channel")
            for draft in self.drafts.values():
                if (draft.user, draft.channel, draft.thread_ts) == (
                    user_id,
                    channel.id,
                    thread_ts,
                ):
                    raise SlackError("attached_draft_exists")
            draft = Draft(
                id=self._new_id("Dr"),
                user=user_id,
                channel=channel.id,
                text=text,
                thread_ts=thread_ts,
            )
            self.drafts[draft.id] = draft
        self._emit("draft", {"draft": draft})
        return draft

    def schedule(
        self,
        user_id: str,
        channel_id: str,
        text: str,
        post_at: int,
        *,
        thread_ts: Optional[str] = None,
        reply_broadcast: bool = False,
        app_id: Optional[str] = None,
    ) -> ScheduledMessage:
        with self._lock:
            channel = self.resolve_channel(user_id, channel_id)
            if user_id not in channel.members:
                raise SlackError("not_in_channel")
            now = time.time()
            if post_at < now + 60 * 2 - 5:
                raise SlackError("time_too_soon")
            if post_at > now + 120 * 24 * 3600:
                raise SlackError("time_too_far")
            scheduled = ScheduledMessage(
                id=self._new_id("Q"),
                user=user_id,
                channel=channel.id,
                text=text,
                post_at=int(post_at),
                thread_ts=thread_ts,
                reply_broadcast=reply_broadcast,
                app_id=app_id,
            )
            self.scheduled[scheduled.id] = scheduled
        self._emit("scheduled", {"scheduled": scheduled})
        return scheduled

    def post_due(self, now: Optional[float] = None) -> List[Message]:
        """Send the scheduled messages whose time has come."""
        now = time.time() if now is None else now
        with self._lock:
            due = [s for s in self.scheduled.values() if s.post_at <= now]
            for s in due:
                del self.scheduled[s.id]
        for s in due:
            self._emit("scheduled_removed", {"scheduled": s})
        posted = []
        for s in due:
            try:
                posted.append(
                    self.post_message(
                        s.user,
                        s.channel,
                        s.text,
                        thread_ts=s.thread_ts,
                        reply_broadcast=s.reply_broadcast,
                        app_id=s.app_id,
                    )
                )
            except SlackError:
                pass
        return posted


_MIMETYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".log": "text/plain",
}


def valid_tz(tz: str) -> str:
    """``tz`` if it is an IANA time zone name."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise SlackError("invalid_timezone")
    return tz


def guess_mimetype(name: str) -> str:
    for ext, mime in _MIMETYPES.items():
        if name.lower().endswith(ext):
            return mime
    return "application/octet-stream"
