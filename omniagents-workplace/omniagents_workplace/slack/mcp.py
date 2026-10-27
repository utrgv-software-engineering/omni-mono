"""The Slack MCP server, served by the workplace.

Same server, as an agent sees it: the 27 tools of the hosted Slack MCP
server (``https://mcp.slack.com/mcp``), with their definitions verbatim
(``reference/slack_mcp_tools.json``), over Streamable HTTP, authorized with
a user token (``Authorization: Bearer xoxp-...``) and acting as that user.
Results are the text the real server returns (see
``reference/slack_mcp_responses.json``), so an agent moved from the
workplace to a real workspace sees the same thing.

Messaging, reading, reactions, conversations, people, search, drafts,
scheduled messages and file uploads work. Canvases and lists answer
``execution_failed: not_available_in_workplace``.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import time
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..wire import Wire
from .render import Renderer, message_link, plain_text, to_mrkdwn
from .workspace import App, Channel, Message, SlackError, SlackWorkspace, User

__all__ = ["SlackMCP", "load_tool_definitions"]

_UNAVAILABLE = {
    "slack_create_list",
    "slack_update_list",
    "slack_add_list_record",
    "slack_update_list_record",
    "slack_read_list",
    "slack_create_canvas",
    "slack_update_canvas",
    "slack_read_canvas",
}


def load_tool_definitions() -> Dict[str, Any]:
    """The real server's ``initialize`` and ``tools/list`` results."""
    text = (
        resources.files("omniagents_workplace")
        .joinpath("reference/slack_mcp_tools.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


class ToolError(Exception):
    """A tool failure, reported as the real server does: ``execution_failed: <code>``."""


def _ok(payload: Dict[str, Any]) -> str:
    # The real server escapes slashes, as PHP's json_encode does.
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
        "/", "\\/"
    )


class SlackMCP:
    """The MCP endpoint: an ASGI app for ``/slack/mcp``."""

    def __init__(
        self,
        workspace: SlackWorkspace,
        wire: Wire,
        base_url: Callable[[], str],
        upload_url: Optional[Callable[[], str]] = None,
    ):
        """``upload_url`` is where uploads are POSTed from (a factory's sandbox)."""
        self.ws = workspace
        self.wire = wire
        self.base_url = base_url
        self.upload_url = upload_url or base_url
        reference = load_tool_definitions()
        self._definitions: List[Dict[str, Any]] = reference["tools"]
        self.server: Server = Server(
            reference["server"]["name"],
            version=reference["server"]["version"],
            instructions=reference.get("instructions"),
        )
        self.server.list_tools()(self._list_tools)
        self.server.call_tool(validate_input=False)(self._call_tool)
        self.manager = StreamableHTTPSessionManager(
            self.server, json_response=False, stateless=True
        )

    @contextlib.asynccontextmanager
    async def run(self):
        async with self.manager.run():
            yield

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if self._caller(headers) is None:
            body = json.dumps({"error": "invalid_token"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer error="invalid_token"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.manager.handle_request(scope, receive, send)

    def _caller(self, headers: Dict[str, str]) -> Optional[Tuple[App, User]]:
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        # Slack's MCP server takes user tokens (not app-level ones), of active members.
        app = self.ws.app_for_token(token)
        if app is None or token != app.user_token or app.user not in self.ws.users:
            return None
        return app, self.ws.users[app.user]

    def _request_caller(self) -> Tuple[App, User]:
        request = self.server.request_context.request
        caller = (
            self._caller({k.lower(): v for k, v in request.headers.items()})
            if request
            else None
        )
        if caller is None:
            raise ToolError("execution_failed: invalid_auth")
        return caller

    # -- tools/list ----------------------------------------------------------

    async def _list_tools(self) -> List[types.Tool]:
        _, user = self._request_caller()
        tools = []
        for definition in self._definitions:
            raw = json.dumps(definition)
            raw = raw.replace("{{current_user_id}}", user.id).replace(
                "{{team_id}}", self.ws.team_id
            )
            tools.append(types.Tool.model_validate(json.loads(raw)))
        return tools

    # -- tools/call ----------------------------------------------------------

    async def _call_tool(self, name: str, arguments: Dict[str, Any]):
        app, user = self._request_caller()
        started = time.monotonic()
        entry = self.wire.record(
            service="slack",
            direction="in",
            kind="mcp",
            title=name,
            actor=f"{user.real_name} via {app.name}",
            status="pending",
            request=arguments,
        )
        try:
            handler = getattr(self, f"_{name}", None)
            if name in _UNAVAILABLE:
                raise ToolError("execution_failed: not_available_in_workplace")
            if handler is None:
                raise ToolError(f"Unknown tool: {name}")
            try:
                text = handler(
                    app, user, Renderer(self.ws, user, self.base_url()), arguments or {}
                )
            except SlackError as exc:
                raise ToolError(f"execution_failed: {exc.code}") from exc
        except ToolError as exc:
            self.wire.update(
                entry,
                status="error",
                response=str(exc),
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], isError=True
            )
        self.wire.update(
            entry,
            status="ok",
            response=_display(text),
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return [types.TextContent(type="text", text=text)]

    # -- files -----------------------------------------------------------------

    def _slack_get_file_upload_url(self, app, user, r, args):
        file = self.ws.start_upload(
            user.id,
            str(_required(args, "filename")),
            int(_required(args, "content_length")),
            alt_txt=str(args.get("alt_txt") or ""),
            snippet_type=str(args.get("snippet_type") or ""),
        )
        return _ok(
            {
                "upload_url": f"{self.upload_url().rstrip('/')}/slack/upload/{file.upload_token}",
                "file_id": file.id,
            }
        )

    def _slack_complete_file_upload(self, app, user, r, args):
        file = self.ws.complete_upload(
            user.id,
            str(_required(args, "file_id")),
            channel_id=args.get("channel_id") or None,
            thread_ts=args.get("thread_ts") or None,
            initial_comment=to_mrkdwn(str(args.get("initial_comment") or "")),
            title=args.get("title") or None,
            app_id=app.id,
        )
        return _ok(
            {
                "file_id": file.id,
                "title": file.title,
                "permalink": f"{self.base_url().rstrip('/')}/slack/files/{file.id}/{file.name}",
            }
        )

    def _slack_read_file(self, app, user, r, args):
        file = self.ws.files.get(str(_required(args, "file_id")))
        if file is None or not file.completed or file.content is None:
            raise SlackError("file_not_found")
        if (
            file.channel
            and user.id not in self.ws.channels[file.channel].members
            and file.user != user.id
        ):
            raise SlackError("file_not_found")
        if file.mimetype.startswith("text/") or file.mimetype == "application/json":
            import secrets

            suffix = secrets.token_hex(8)
            return (
                f"File: {file.title} ({file.id})\nType: {file.mimetype}\n\n"
                f"<file_content_{suffix}>\n{file.content.decode(errors='replace')}\n</file_content_{suffix}>"
            )
        return _ok(
            {
                "file_id": file.id,
                "name": file.name,
                "mimeType": file.mimetype,
                "content": base64.b64encode(file.content).decode(),
            }
        )

    # -- messaging -----------------------------------------------------------

    def _slack_send_message(self, app, user, r, args):
        message = self.ws.post_message(
            user.id,
            _required(args, "channel_id"),
            to_mrkdwn(_required(args, "message")),
            thread_ts=args.get("thread_ts") or None,
            reply_broadcast=bool(args.get("reply_broadcast")),
            app_id=app.id,
        )
        if args.get("draft_id"):
            self.ws.drafts.pop(str(args["draft_id"]), None)
        return _ok(
            {
                "message_link": message_link(self.base_url(), message),
                "message_context": {
                    "message_ts": message.ts,
                    "channel_id": message.channel,
                },
            }
        )

    def _slack_schedule_message(self, app, user, r, args):
        scheduled = self.ws.schedule(
            user.id,
            _required(args, "channel_id"),
            to_mrkdwn(_required(args, "message")),
            int(_required(args, "post_at")),
            thread_ts=args.get("thread_ts") or None,
            reply_broadcast=bool(args.get("reply_broadcast")),
            app_id=app.id,
        )
        when = r.time(f"{scheduled.post_at}.000000")
        return _ok(
            {
                "result": f"Message scheduled successfully for {when}.",
                "scheduled_message_id": scheduled.id,
                "channel_id": scheduled.channel,
                "post_at": scheduled.post_at,
            }
        )

    def _slack_send_message_draft(self, app, user, r, args):
        draft = self.ws.save_draft(
            user.id,
            _required(args, "channel_id"),
            to_mrkdwn(_required(args, "message")),
            args.get("thread_ts") or None,
        )
        return _ok(
            {
                "channel_link": f"{self.base_url().rstrip('/')}/client/{self.ws.team_id}/{draft.channel}"
            }
        )

    def _slack_add_reaction(self, app, user, r, args):
        emoji = _required(args, "emoji").strip(":")
        self.ws.add_reaction(
            user.id, _required(args, "channel_id"), _required(args, "message_ts"), emoji
        )
        return _ok({"result": f"Successfully added :{emoji}: reaction to the message."})

    def _slack_get_reactions(self, app, user, r, args):
        channel = self.ws.resolve_channel(user.id, _required(args, "channel_id"))
        message = self.ws.message(channel.id, _required(args, "message_ts"))
        return _ok({"result": r.reactions(message)})

    def _slack_create_conversation(self, app, user, r, args):
        name = args.get("channel_name")
        user_ids = [str(u) for u in args.get("user_ids") or []]
        if name:
            channel = self.ws.create_channel(
                user.id,
                str(name),
                is_private=bool(args.get("is_private")),
                invite=user_ids,
            )
            kind = "Private Channel" if channel.is_private else "Public Channel"
            return _ok(
                {
                    "result": (
                        "Channel created successfully.\n\n"
                        f"Channel ID: {channel.id}\nType: {kind}\nName: {channel.name}\n"
                        f"Members invited: {len(channel.members)}\n"
                        "You can now use `slack_send_message` with this channel_id."
                    ),
                    "channel_id": channel.id,
                }
            )
        if not user_ids:
            raise SlackError("invalid_arguments")
        channel = self.ws.open_dm(
            user.id, [u for u in user_ids if u != user.id] or [user.id]
        )
        kind = "Direct Message" if channel.is_im else "Group Direct Message"
        return _ok(
            {
                "result": (
                    "Conversation opened successfully.\n\n"
                    f"Channel ID: {channel.id}\nType: {kind}\n"
                    f"Members: {len(channel.members)}\n"
                    "You can now use `slack_send_message` with this channel_id."
                ),
                "channel_id": channel.id,
            }
        )

    # -- reading -------------------------------------------------------------

    def _slack_read_channel(self, app, user, r, args):
        channel = self.ws.resolve_channel(user.id, _required(args, "channel_id"))
        messages = _in_range(self.ws.history(channel.id), args)
        page, more = _page(messages, args, default=100, cap=100)
        return _ok(
            {
                "messages": r.read_channel(channel, page, _concise(args)),
                "pagination_info": (
                    f"More messages available. Use cursor: {more}\n"
                    if more
                    else "There are no more messages available.\n"
                ),
            }
        )

    def _slack_read_thread(self, app, user, r, args):
        channel = self.ws.resolve_channel(user.id, _required(args, "channel_id"))
        ts = _required(args, "message_ts")
        parent = next((m for m in self.ws.messages(channel.id) if m.ts == ts), None)
        if parent is None:
            raise SlackError("thread_not_found")
        if parent.is_reply:
            parent = self.ws.message(channel.id, parent.thread_ts or ts)
        replies = _in_range(self.ws.replies(channel.id, parent.ts), args)
        page, more = _page(replies, args, default=100, cap=1000)
        return _ok(
            {
                "messages": r.read_thread(parent, page, _concise(args)),
                "pagination_info": (
                    f"More replies available. Use cursor: {more}\n"
                    if more
                    else "There are no more messages in this thread.\n"
                ),
            }
        )

    def _slack_read_user_profile(self, app, user, r, args):
        target = self.ws.user(str(args.get("user_id") or user.id))
        return _ok({"result": r.profile(target, _concise(args))})

    def _slack_list_channel_members(self, app, user, r, args):
        channel = self.ws.resolve_channel(user.id, _required(args, "channel_id"))
        members = [
            self.ws.users[m]
            for m in channel.members
            if m in self.ws.users
            and (args.get("include_deleted") or not self.ws.users[m].deleted)
            and (args.get("include_bots", True) or not self.ws.users[m].is_bot)
        ]
        page, more = _page(members, args, default=100, cap=1000)
        return _ok(
            {
                "members": r.members(page),
                "pagination_info": (
                    f"More members available. Use cursor: {more}\n"
                    if more
                    else "No more members available.\n"
                ),
            }
        )

    def _slack_list_user_channels(self, app, user, r, args):
        types_ = {
            t.strip()
            for t in str(args.get("types") or "public_channel,private_channel").split(
                ","
            )
        }
        prefix = str(args.get("name_prefix") or "")
        channels = [
            c
            for c in self.ws.channels.values()
            if user.id in c.members
            and c.type in types_
            and (not prefix or c.name.startswith(prefix))
            and not (args.get("exclude_archived") and c.is_archived)
        ]
        page, _ = _page(channels, args, default=100, cap=1000)
        return _ok({"result": r.user_channels(page)})

    # -- search --------------------------------------------------------------

    def _slack_search_public(self, app, user, r, args):
        return self._search_messages(user, r, args, {"public_channel"})

    def _slack_search_public_and_private(self, app, user, r, args):
        kinds = {
            t.strip()
            for t in str(
                args.get("channel_types") or "public_channel,private_channel,mpim,im"
            ).split(",")
        }
        return self._search_messages(user, r, args, kinds)

    def _search_messages(self, user: User, r: Renderer, args, kinds) -> str:
        keywords = [str(k) for k in args.get("keywords") or []]
        filters = str(args.get("filters") or "")
        if not keywords and not filters and not args.get("natural_language_query"):
            raise SlackError("invalid_arguments")
        candidates = [
            m
            for c in self.ws.visible_channels(user.id)
            if c.type in kinds
            for m in self.ws.messages(c.id)
        ]
        results = [
            m
            for m in candidates
            if _matches_keywords(plain_text(m.text), keywords)
            and _matches_filters(self.ws, user, m, filters)
            and _matches_dates(m, args.get("after"), args.get("before"))
            and (
                args.get("include_bots", False)
                or not self.ws.users.get(m.user, _NOBODY).is_bot
            )
        ]
        results.sort(key=lambda m: float(m.ts), reverse=args.get("sort_dir") != "asc")
        page, more = _page(results, args, default=20, cap=20)
        return _ok(
            {
                "results": r.search_messages(page, _concise(args)),
                "pagination_info": (
                    f"More results available. Use cursor: {more}\\n"
                    if more
                    else "End of results - No more pages available.\\n"
                ),
            }
        )

    def _slack_search_channels(self, app, user, r, args):
        kinds = {
            t.strip()
            for t in str(args.get("channel_types") or "public_channel").split(",")
        }
        keywords = [str(k).lower().strip('"') for k in args.get("keywords") or []]
        channels = [
            c
            for c in self.ws.visible_channels(user.id)
            if c.name
            and c.type in kinds
            and (args.get("include_archived") or not c.is_archived)
            and all(
                k in f"{c.name} {c.topic} {c.purpose}".lower()
                for k in keywords
                if not k.startswith("-")
            )
        ]
        page, more = _page(channels, args, default=20, cap=20)
        return _ok(
            {
                "results": r.search_channels(page, _concise(args)),
                "pagination_info": (
                    f"More results available. Use cursor: {more}\\n"
                    if more
                    else "End of results - No more pages available.\\n"
                ),
            }
        )

    def _slack_search_users(self, app, user, r, args):
        keywords = [str(k).lower().strip('"') for k in args.get("keywords") or []]

        def matches(u: User) -> bool:
            hay = f"{u.name} {u.real_name} {u.display_name} {u.email} {u.title}".lower()
            return all(
                (k[1:] not in hay) if k.startswith("-") else (k in hay)
                for k in keywords
            )

        users = [u for u in self.ws.users.values() if not u.deleted and matches(u)]
        page, more = _page(users, args, default=20, cap=20)
        return _ok(
            {
                "results": r.search_users(page, _concise(args)),
                "pagination_info": (
                    f"More results available. Use cursor: {more}\\n"
                    if more
                    else "End of results - No more pages available.\\n"
                ),
            }
        )

    def _slack_search_emojis(self, app, user, r, args):
        query = _required(args, "query")
        return _ok({"result": f"No custom emojis found matching: {query}"})


_NOBODY = User(id="", name="", real_name="", email="")


def _required(args: Dict[str, Any], key: str) -> Any:
    value = args.get(key)
    if value in (None, ""):
        raise SlackError("invalid_arguments")
    return value if not isinstance(value, str) else value.strip()


def _concise(args: Dict[str, Any]) -> bool:
    return str(args.get("response_format") or "detailed") == "concise"


def _page(items: List[Any], args: Dict[str, Any], *, default: int, cap: int):
    """One page and the cursor for the next (``None`` on the last page)."""
    try:
        limit = max(1, min(cap, int(args.get("limit") or default)))
    except (TypeError, ValueError):
        limit = default
    start = 0
    cursor = args.get("cursor")
    if cursor:
        try:
            start = int(base64.b64decode(str(cursor)).decode().split(":", 1)[1])
        except Exception as exc:
            raise SlackError("invalid_cursor") from exc
    end = start + limit
    more = (
        base64.b64encode(f"next:{end}".encode()).decode() if end < len(items) else None
    )
    return items[start:end], more


def _in_range(messages: List[Message], args: Dict[str, Any]) -> List[Message]:
    oldest = float(args["oldest"]) if args.get("oldest") else None
    latest = float(args["latest"]) if args.get("latest") else None
    return [
        m
        for m in messages
        if (oldest is None or float(m.ts) >= oldest)
        and (latest is None or float(m.ts) <= latest)
    ]


_PHRASE = re.compile(r'^"(.*)"$')


def _matches_keywords(text: str, keywords: List[str]) -> bool:
    hay = text.lower()
    for keyword in keywords:
        keyword = keyword.strip()
        if not keyword:
            continue
        negate = keyword.startswith("-")
        term = _PHRASE.sub(r"\1", keyword.lstrip("-")).lower()
        found = term in hay
        if found == negate:
            return False
    return True


def _matches_filters(
    ws: SlackWorkspace, user: User, message: Message, filters: str
) -> bool:
    groups: Dict[str, List[bool]] = {}
    channel = ws.channels[message.channel]
    for token in filters.split():
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        key = key.lower()
        if key == "in":
            hit = _channel_matches(ws, user, channel, value)
        elif key == "from":
            hit = _user_matches(ws, message.user, value)
        elif key == "with":
            hit = any(_user_matches(ws, m, value) for m in channel.members)
        elif key == "is":
            hit = {
                "thread": message.is_reply
                or bool(ws.replies(message.channel, message.ts)),
                "dm": channel.is_im,
            }.get(value, False)
        elif key == "has":
            hit = {
                "reaction": bool(message.reactions),
                "link": "http" in message.text,
            }.get(value, value.strip(":") in message.reactions)
        elif key in ("before", "after", "on"):
            hit = _matches_dates(
                message,
                value if key == "after" else None,
                value if key == "before" else None,
                on=value if key == "on" else None,
            )
        else:
            continue
        groups.setdefault(key, []).append(hit)
    # The same modifier repeated is OR, except with and has (AND).
    return all(
        all(hits) if key in ("with", "has") else any(hits)
        for key, hits in groups.items()
    )


def _channel_matches(
    ws: SlackWorkspace, user: User, channel: Channel, value: str
) -> bool:
    match = re.match(r"<#([A-Z0-9]+)(?:\|[^>]*)?>", value)
    if match:
        return channel.id == match.group(1)
    if value.startswith("@"):
        other = _find_user(ws, value[1:])
        return bool(other and channel.is_im and other.id in channel.members)
    return channel.name == value.lstrip("#")


def _user_matches(ws: SlackWorkspace, user_id: str, value: str) -> bool:
    match = re.match(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", value)
    if match:
        return user_id == match.group(1)
    other = _find_user(ws, value.lstrip("@"))
    return bool(other and other.id == user_id)


def _find_user(ws: SlackWorkspace, name: str) -> Optional[User]:
    return next((u for u in ws.users.values() if name in (u.name, u.id)), None)


def _matches_dates(message: Message, after, before, *, on=None) -> bool:
    day = datetime.fromtimestamp(float(message.ts), timezone.utc).date().isoformat()
    try:
        if after and day <= _date(after):
            return False
        if before and day >= _date(before):
            return False
        if on and day != _date(on):
            return False
    except ValueError:
        return True
    return True


def _date(value: Any) -> str:
    text = str(value)
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return datetime.fromtimestamp(float(text), timezone.utc).date().isoformat()
    return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()


def _display(text: str) -> Any:
    """A result as the wire panel shows it: the JSON parsed, when it is JSON."""
    try:
        return json.loads(text)
    except ValueError:
        return text
