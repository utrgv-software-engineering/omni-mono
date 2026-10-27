"""Slack's HTTP surface: the Web API, Socket Mode, and the MCP endpoint.

Mounted under ``/slack``:

- ``/slack/api/<method>``: the Web API methods a factory and its tools use
  (``auth.test``, ``apps.connections.open``, ``chat.postMessage``,
  ``conversations.*``, ``reactions.*``, ``users.*``), answering
  ``{"ok": ...}`` as ``https://slack.com/api`` does. Point a Slack SDK's
  base URL here.
- ``/slack/link/?ticket=...``: the Socket Mode WebSocket that
  ``apps.connections.open`` hands out.
- ``/slack/mcp``: the Slack MCP server (:mod:`.mcp`).
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from ..wire import Wire
from .events import EventDelivery, SocketConnection
from .render import message_link
from .workspace import Channel, Message, SlackError, SlackWorkspace, User

__all__ = ["channel_json", "message_json", "slack_router", "user_json"]


def message_json(ws: SlackWorkspace, m: Message) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "type": "message",
        "user": m.user,
        "text": m.text,
        "ts": m.ts,
        "team": ws.team_id,
    }
    if m.subtype:
        out["subtype"] = m.subtype
    if m.thread_ts:
        out["thread_ts"] = m.thread_ts
    if m.app_id:
        out["app_id"] = m.app_id
    if m.edited_ts:
        out["edited"] = {"user": m.user, "ts": m.edited_ts}
    if m.pinned_by:
        out["pinned_to"] = [m.channel]
    if not m.is_reply:
        replies = ws.replies(m.channel, m.ts)
        if replies:
            out["thread_ts"] = m.ts
            out["reply_count"] = len(replies)
            out["reply_users"] = list(dict.fromkeys(r.user for r in replies))
            out["latest_reply"] = replies[-1].ts
    if m.files:
        out["files"] = [
            {
                "id": f.id,
                "name": f.name,
                "title": f.title,
                "mimetype": f.mimetype,
                "size": f.length,
                "url_private": f"/slack/files/{f.id}/{f.name}",
                "alt_txt": f.alt_txt,
            }
            for f in (ws.files.get(i) for i in m.files)
            if f is not None
        ]
    if m.reactions:
        out["reactions"] = [
            {"name": name, "users": list(users), "count": len(users)}
            for name, users in m.reactions.items()
        ]
    return out


def channel_json(c: Channel) -> Dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "is_channel": not (c.is_im or c.is_mpim or c.is_private),
        "is_group": c.is_private and not c.is_mpim,
        "is_im": c.is_im,
        "is_mpim": c.is_mpim,
        "is_private": c.is_private or c.is_im or c.is_mpim,
        "is_archived": c.is_archived,
        "is_general": c.is_general,
        "created": int(c.created),
        "creator": c.creator,
        "members": list(c.members),
        "num_members": len(c.members),
        "topic": {"value": c.topic},
        "purpose": {"value": c.purpose},
    }


def user_json(ws: SlackWorkspace, u: User) -> Dict[str, Any]:
    return {
        "id": u.id,
        "team_id": ws.team_id,
        "name": u.name,
        "real_name": u.real_name,
        "deleted": u.deleted,
        "tz": ws.tz_of(u),
        "is_admin": u.is_admin,
        "is_bot": u.is_bot,
        "profile": {
            "real_name": u.real_name,
            "display_name": u.display_name,
            "email": u.email,
            "title": u.title,
            "status_text": u.status_text,
            "status_emoji": f":{u.status_emoji}:" if u.status_emoji else "",
        },
    }


class _ApiError(Exception):
    def __init__(self, code: str):
        self.code = code


def slack_router(
    ws: SlackWorkspace,
    wire: Wire,
    delivery: EventDelivery,
    base_url,
    reader: Optional[Callable[[Request], Optional[str]]] = None,
) -> APIRouter:
    """``reader`` says who is fetching a file (a Slack user id), as Slack's
    ``url_private`` takes a token: a file is for the people who can see it."""
    router = APIRouter(prefix="/slack")

    async def _params(request: Request) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(request.query_params)
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if isinstance(body, dict):
                params.update(body)
        elif request.method == "POST":
            form = await request.form()
            params.update({k: v for k, v in form.items()})
        return params

    def _token(request: Request, params: Dict[str, Any]) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return str(params.get("token") or "")

    @router.api_route("/api/{method}", methods=["GET", "POST"])
    async def web_api(method: str, request: Request):
        params = await _params(request)
        token = _token(request, params)
        app = ws.app_for_token(token) if token else None
        started = time.monotonic()
        entry = wire.record(
            service="slack",
            direction="in",
            kind="api",
            title=method,
            actor=app.name if app else None,
            status="pending",
            request={k: v for k, v in params.items() if k != "token"},
        )
        try:
            if app is None:
                raise _ApiError("not_authed" if not token else "invalid_auth")
            handler = _METHODS.get(method)
            if handler is None:
                raise _ApiError("unknown_method")
            if method == "apps.connections.open":
                if token != app.app_token:
                    raise _ApiError("not_allowed_token_type")
            elif token != app.user_token:
                raise _ApiError("not_allowed_token_type")
            result = {
                "ok": True,
                **handler(ws, app, params, request, delivery, base_url),
            }
        except _ApiError as exc:
            result = {"ok": False, "error": exc.code}
        except SlackError as exc:
            result = {"ok": False, "error": exc.code}
        wire.update(
            entry,
            status="ok" if result["ok"] else "error",
            response=result,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return result

    @router.post("/upload/{token}")
    async def upload(token: str, request: Request):
        """Step two of an upload: the file's bytes, as files.slack.com takes them."""
        content = await request.body()
        try:
            file = ws.receive_upload(token, content)
        except SlackError as exc:
            status = 401 if exc.code == "unauthorized" else 400
            return JSONResponse({"ok": False, "error": exc.code}, status_code=status)
        wire.record(
            service="slack",
            direction="in",
            kind="api",
            title="file upload",
            actor=file.name,
            request={"file_id": file.id, "bytes": len(content)},
            response={"ok": True},
        )
        return Response("OK - " + str(len(content)), media_type="text/plain")

    @router.get("/files/{file_id}/{name}")
    def file_content(file_id: str, name: str, request: Request):
        file = ws.files.get(file_id)
        if file is None or file.content is None:
            return Response(status_code=404)
        who = reader(request) if reader is not None else None
        if who is None:
            return Response(status_code=404)
        if file.user != who:
            try:
                ws.resolve_channel(who, file.channel or "")
            except SlackError:
                return Response(status_code=404)
        return Response(
            file.content,
            media_type=file.mimetype,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.websocket("/link/")
    async def socket_mode(socket: WebSocket):
        app = delivery.redeem_ticket(socket.query_params.get("ticket", ""))
        if app is None:
            await socket.close(code=4001)
            return
        await socket.accept()
        connection = SocketConnection(
            app_id=app.id, send=socket.send_text, close=socket.close
        )
        delivery.connect(connection)
        wire.record(
            service="slack",
            direction="in",
            kind="socket",
            title="connected",
            actor=app.name,
            detail={"connection": connection.id},
        )
        try:
            await socket.send_text(
                json.dumps(
                    {
                        "type": "hello",
                        "num_connections": len(delivery.connections.get(app.id, [])),
                        "debug_info": {
                            "host": "workplace",
                            "approximate_connection_time": 18060,
                        },
                        "connection_info": {"app_id": app.id},
                    }
                )
            )
            while True:
                raw = await socket.receive_text()
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                envelope_id = (
                    message.get("envelope_id") if isinstance(message, dict) else None
                )
                if envelope_id:
                    connection.acknowledge(str(envelope_id))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            delivery.disconnect(connection)
            wire.record(
                service="slack",
                direction="in",
                kind="socket",
                title="disconnected",
                actor=app.name,
                detail={"connection": connection.id},
            )

    return router


# -- Web API methods ----------------------------------------------------------


def _auth_test(ws, app, params, request, delivery, base_url):
    user = ws.user(app.user)
    return {
        "url": base_url().rstrip("/") + "/",
        "team": ws.name,
        "user": user.name,
        "team_id": ws.team_id,
        "user_id": user.id,
        "app_id": app.id,
    }


def _connections_open(ws, app, params, request, delivery, base_url):
    if not app.socket_mode:
        raise _ApiError("not_allowed_token_type")
    ticket = delivery.issue_ticket(app)
    scheme = "wss" if request.url.scheme == "https" else "ws"
    return {
        "url": f"{scheme}://{request.url.netloc}/slack/link/?ticket={ticket}&app_id={app.id}"
    }


def _post_message(ws, app, params, request, delivery, base_url):
    channel = str(params.get("channel") or "")
    text = str(params.get("text") or "")
    if not channel:
        raise _ApiError("channel_not_found")
    message = ws.post_message(
        app.user,
        channel,
        text,
        thread_ts=str(params["thread_ts"]) if params.get("thread_ts") else None,
        reply_broadcast=str(params.get("reply_broadcast")).lower() in ("1", "true"),
        app_id=app.id,
    )
    return {
        "channel": message.channel,
        "ts": message.ts,
        "message": message_json(ws, message),
    }


def _limit(params, default=100, cap=1000) -> int:
    try:
        return max(1, min(cap, int(params.get("limit") or default)))
    except (TypeError, ValueError):
        return default


def _cursor(params) -> int:
    try:
        return int(str(params.get("cursor") or "0").removeprefix("next_"))
    except ValueError as exc:
        raise _ApiError("invalid_cursor") from exc


def _paged(items, params, key, default=100):
    start = _cursor(params)
    end = start + _limit(params, default)
    out = {key: items[start:end], "has_more": end < len(items)}
    out["response_metadata"] = {
        "next_cursor": f"next_{end}" if end < len(items) else ""
    }
    return out


def _history(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, str(params.get("channel") or ""))
    oldest = float(params["oldest"]) if params.get("oldest") else None
    latest = float(params["latest"]) if params.get("latest") else None
    messages = [
        message_json(ws, m)
        for m in ws.history(channel.id)
        if (oldest is None or float(m.ts) > oldest)
        and (latest is None or float(m.ts) < latest)
    ]
    return _paged(messages, params, "messages")


def _replies(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, str(params.get("channel") or ""))
    ts = str(params.get("ts") or "")
    parent = ws.message(channel.id, ts)
    if parent.is_reply:
        parent = ws.message(channel.id, parent.thread_ts or ts)
    thread = [parent, *ws.replies(channel.id, parent.ts)]
    return _paged([message_json(ws, m) for m in thread], params, "messages")


def _conversations_list(ws, app, params, request, delivery, base_url):
    kinds = {t.strip() for t in str(params.get("types") or "public_channel").split(",")}
    exclude_archived = str(params.get("exclude_archived")).lower() in ("1", "true")
    channels = [
        channel_json(c)
        for c in ws.visible_channels(app.user)
        if c.type in kinds and not (exclude_archived and c.is_archived)
    ]
    return _paged(channels, params, "channels")


def _conversations_info(ws, app, params, request, delivery, base_url):
    return {
        "channel": channel_json(
            ws.resolve_channel(app.user, str(params.get("channel") or ""))
        )
    }


def _conversations_members(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, str(params.get("channel") or ""))
    return _paged(list(channel.members), params, "members")


def _conversations_open(ws, app, params, request, delivery, base_url):
    users = [u.strip() for u in str(params.get("users") or "").split(",") if u.strip()]
    return {"channel": channel_json(ws.open_dm(app.user, users))}


def _reactions_add(ws, app, params, request, delivery, base_url):
    ws.add_reaction(
        app.user,
        str(params.get("channel") or ""),
        str(params.get("timestamp") or ""),
        str(params.get("name") or ""),
    )
    return {}


def _reactions_remove(ws, app, params, request, delivery, base_url):
    ws.remove_reaction(
        app.user,
        str(params.get("channel") or ""),
        str(params.get("timestamp") or ""),
        str(params.get("name") or ""),
    )
    return {}


def _reactions_get(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, str(params.get("channel") or ""))
    message = ws.message(channel.id, str(params.get("timestamp") or ""))
    return {
        "type": "message",
        "channel": channel.id,
        "message": message_json(ws, message),
    }


def _users_info(ws, app, params, request, delivery, base_url):
    return {"user": user_json(ws, ws.user(str(params.get("user") or "")))}


def _users_list(ws, app, params, request, delivery, base_url):
    return _paged([user_json(ws, u) for u in ws.users.values()], params, "members")


def _permalink(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, str(params.get("channel") or ""))
    message = ws.message(channel.id, str(params.get("message_ts") or ""))
    return {"channel": channel.id, "permalink": message_link(base_url(), message)}


def _channel_arg(params) -> str:
    channel = str(params.get("channel") or "")
    if not channel:
        raise _ApiError("channel_not_found")
    return channel


def _chat_update(ws, app, params, request, delivery, base_url):
    message = ws.update_message(
        app.user,
        _channel_arg(params),
        str(params.get("ts") or ""),
        str(params.get("text") or ""),
    )
    return {
        "channel": message.channel,
        "ts": message.ts,
        "text": message.text,
        "message": message_json(ws, message),
    }


def _chat_delete(ws, app, params, request, delivery, base_url):
    message = ws.delete_message(
        app.user, _channel_arg(params), str(params.get("ts") or "")
    )
    return {"channel": message.channel, "ts": message.ts}


def _scheduled_list(ws, app, params, request, delivery, base_url):
    channel = str(params.get("channel") or "")
    items = [
        {
            "id": s.id,
            "channel_id": s.channel,
            "post_at": s.post_at,
            "date_created": int(time.time()),
            "text": s.text,
        }
        for s in sorted(ws.scheduled.values(), key=lambda s: s.post_at)
        if s.user == app.user and (not channel or s.channel == channel)
    ]
    return _paged(items, params, "scheduled_messages")


def _scheduled_delete(ws, app, params, request, delivery, base_url):
    ws.delete_scheduled(app.user, str(params.get("scheduled_message_id") or ""))
    return {}


def _conversation_change(change):
    def handler(ws, app, params, request, delivery, base_url):
        return {
            "channel": channel_json(change(ws, app.user, _channel_arg(params), params))
        }

    return handler


def _users_arg(params) -> List[str]:
    return [u.strip() for u in str(params.get("users") or "").split(",") if u.strip()]


def _conversations_mark(ws, app, params, request, delivery, base_url):
    ws.mark(app.user, _channel_arg(params), str(params.get("ts") or ""))
    return {}


def _pins_add(ws, app, params, request, delivery, base_url):
    ws.pin(app.user, _channel_arg(params), str(params.get("timestamp") or ""))
    return {}


def _pins_remove(ws, app, params, request, delivery, base_url):
    ws.unpin(app.user, _channel_arg(params), str(params.get("timestamp") or ""))
    return {}


def _pins_list(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, _channel_arg(params))
    return {
        "items": [
            {
                "type": "message",
                "channel": channel.id,
                "created_by": m.pinned_by,
                "message": message_json(ws, m),
            }
            for m in ws.pins(channel.id)
        ]
    }


def _bookmark_json(channel_id: str, b) -> Dict[str, Any]:
    return {
        "id": b.id,
        "channel_id": channel_id,
        "title": b.title,
        "link": b.link,
        "type": "link",
        "date_created": int(b.created),
        "created_by": b.created_by,
    }


def _bookmarks_add(ws, app, params, request, delivery, base_url):
    channel_id = _channel_arg(params)
    bookmark = ws.add_bookmark(
        app.user,
        channel_id,
        str(params.get("title") or ""),
        str(params.get("link") or ""),
    )
    return {
        "bookmark": _bookmark_json(
            ws.resolve_channel(app.user, channel_id).id, bookmark
        )
    }


def _bookmarks_remove(ws, app, params, request, delivery, base_url):
    ws.remove_bookmark(
        app.user, _channel_arg(params), str(params.get("bookmark_id") or "")
    )
    return {}


def _bookmarks_list(ws, app, params, request, delivery, base_url):
    channel = ws.resolve_channel(app.user, _channel_arg(params))
    return {"bookmarks": [_bookmark_json(channel.id, b) for b in channel.bookmarks]}


def _profile_get(ws, app, params, request, delivery, base_url):
    user = ws.user(str(params.get("user") or app.user))
    return {"profile": user_json(ws, user)["profile"]}


def _profile_set(ws, app, params, request, delivery, base_url):
    profile = params.get("profile") or {}
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except ValueError as exc:
            raise _ApiError("invalid_profile") from exc
    if params.get("name"):
        profile = {**profile, str(params["name"]): params.get("value", "")}
    fields = {}
    for key in ("display_name", "title", "status_text"):
        if key in profile:
            fields[key] = str(profile[key] or "")
    if "status_emoji" in profile:
        fields["status_emoji"] = str(profile["status_emoji"] or "").strip(":")
    if not fields:
        raise _ApiError("invalid_profile")
    user = ws.update_user(app.user, **fields)
    return {"profile": user_json(ws, user)["profile"]}


_METHODS = {
    "auth.test": _auth_test,
    "apps.connections.open": _connections_open,
    "chat.postMessage": _post_message,
    "chat.getPermalink": _permalink,
    "conversations.history": _history,
    "conversations.replies": _replies,
    "conversations.list": _conversations_list,
    "conversations.info": _conversations_info,
    "conversations.members": _conversations_members,
    "conversations.open": _conversations_open,
    "reactions.add": _reactions_add,
    "reactions.remove": _reactions_remove,
    "reactions.get": _reactions_get,
    "users.info": _users_info,
    "users.list": _users_list,
    "chat.update": _chat_update,
    "chat.delete": _chat_delete,
    "chat.scheduledMessages.list": _scheduled_list,
    "chat.deleteScheduledMessage": _scheduled_delete,
    "conversations.join": _conversation_change(
        lambda ws, user, channel, p: ws.join_channel(user, channel)
    ),
    "conversations.leave": _conversation_change(
        lambda ws, user, channel, p: ws.leave(user, channel)
    ),
    "conversations.invite": _conversation_change(
        lambda ws, user, channel, p: ws.invite(user, channel, _users_arg(p))
    ),
    "conversations.kick": _conversation_change(
        lambda ws, user, channel, p: ws.kick(user, channel, str(p.get("user") or ""))
    ),
    "conversations.setTopic": _conversation_change(
        lambda ws, user, channel, p: ws.set_topic(
            user, channel, str(p.get("topic") or "")
        )
    ),
    "conversations.setPurpose": _conversation_change(
        lambda ws, user, channel, p: ws.set_purpose(
            user, channel, str(p.get("purpose") or "")
        )
    ),
    "conversations.rename": _conversation_change(
        lambda ws, user, channel, p: ws.rename(user, channel, str(p.get("name") or ""))
    ),
    "conversations.archive": _conversation_change(
        lambda ws, user, channel, p: ws.archive(user, channel)
    ),
    "conversations.unarchive": _conversation_change(
        lambda ws, user, channel, p: ws.unarchive(user, channel)
    ),
    "conversations.mark": _conversations_mark,
    "pins.add": _pins_add,
    "pins.remove": _pins_remove,
    "pins.list": _pins_list,
    "bookmarks.add": _bookmarks_add,
    "bookmarks.remove": _bookmarks_remove,
    "bookmarks.list": _bookmarks_list,
    "users.profile.get": _profile_get,
    "users.profile.set": _profile_set,
}
