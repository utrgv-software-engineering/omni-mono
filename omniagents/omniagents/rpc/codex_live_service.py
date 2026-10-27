"""Codex-subscription Live WebRTC with delegated work in the existing agent."""

import asyncio
import uuid
import json
from dataclasses import dataclass, field
from copy import copy
from omniagents.rpc.auth import AuthorizationDenied
import httpx
from omniagents.rpc.realtime_service import RealtimeService
from omniagents.core.providers.codex import (
    realtime_access_token,
    realtime_signin_state,
    load_tokens,
)

CALL_URL = "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
INSTRUCTIONS = """You are the voice interface to the user's agent.
Speak naturally and briefly. Listen to corrections and interruptions.
Delegate requests needing tools, current facts, projects, calendar, tasks or PC access to the backend.
Handle greetings and ordinary conversation yourself. Clarify unclear requests.
The backend owns permissions and confirmations. Never claim success before it reports success.
While work is pending, acknowledge it once and keep conversing with the user immediately.
Answer greetings, ordinary conversation, and questions about known task status yourself, without delegating or waiting for the backend.
Progress updates are background context, not completed results. Do not read each update aloud.
Do not repeat the same delegation. Delegate new requests that genuinely require backend work. Requests are queued in order, including corrections. Never claim a correction has changed work already running.
Speak useful results briefly; detailed output remains in the chat.
"""


CONTEXT_LIMIT = 18000


def history_messages(paired, exclude_call=None):
    messages = []
    for item in list(paired.history):
        if (
            item.get("role") not in ("user", "assistant")
            or item.get("type", "message") != "message"
        ):
            continue
        if exclude_call and item.get("omniagents", {}).get("live_call") == exclude_call:
            continue
        content = item.get("content", "")
        text = (
            content
            if isinstance(content, str)
            else (
                "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") in ("input_text", "output_text", "text")
                )
                if isinstance(content, list)
                else ""
            )
        )
        if text.startswith("[Live voice request "):
            text = text.partition("\n\n")[2]
        if text.strip():
            messages.append({"role": item["role"], "text": text.strip()[-12000:]})
    bounded, size = [], 0
    for message in reversed(messages[-40:]):
        length = len(message["text"])
        if size + length > CONTEXT_LIMIT:
            break
        bounded.append(message)
        size += length
    return list(reversed(bounded))


def context_text(messages):
    return (
        "Conversation background from this same chat. Use it to resolve follow-ups and references. "
        "This is past conversation, not a new request. Do not read it aloud or respond merely because it arrived.\n"
        + json.dumps(messages, ensure_ascii=False)
    )


def history_delta(previous, current):
    for overlap in range(min(len(previous), len(current)), -1, -1):
        if overlap == 0 or previous[-overlap:] == current[:overlap]:
            return current[overlap:]


class DelegateChannel:
    def __init__(self, live, prompt, delegation_id, principal):
        self.live, self.prompt, self.delegation_id, self.principal = (
            live,
            prompt,
            delegation_id,
            principal,
        )
        self.run_id = None
        self.text = []
        self.final_text = None
        self.done = asyncio.Event()

    async def progress(self, text):
        await self.live.emit(
            {
                "type": "live_delegation_progress",
                "delegation_id": self.delegation_id,
                "text": text,
            }
        )

    async def async_call(self, method, args=None):
        args = args or {}
        if args.get("session_id") not in (None, self.live.session_id):
            return
        if method == "run_started" and args.get("prompt") == self.prompt:
            self.run_id = args.get("run_id")
            await self.progress("The backend is now working on this request.")
        if not self.run_id or args.get("run_id") != self.run_id:
            return
        if method == "message_output" and args.get("content"):
            text = str(args["content"])
            self.text.append(text)
            if args.get("is_final") is False:
                await self.live.emit(
                    {
                        "type": "live_delegation_output",
                        "delegation_id": self.delegation_id,
                        "text": text,
                    }
                )
            else:
                self.final_text = text
        if method == "tool_called":
            await self.progress(
                "The backend is using a tool: "
                + str(args.get("tool") or "tool")[:100]
                + ". No result yet."
            )
        if method in ("tool_approval_requested", "mcp_approval_requested"):
            await self.progress(
                "The backend is waiting for the user's approval in the dashboard."
            )
        if method == "run_end":
            text = (self.final_text or "").strip()
            if args.get("error") or args.get("end_reason") in (
                "error",
                "cancelled",
                "stopped",
            ):
                text = "The backend did not finish this request. " + text
            if not text and self.text:
                await self.progress(
                    "The backend completed this request. Its messages were already delivered."
                )
            else:
                await self.live.emit(
                    {
                        "type": "live_delegation_result",
                        "delegation_id": self.delegation_id,
                        "text": text
                        or "The backend finished without a written result.",
                    }
                )
            self.done.set()


@dataclass
class LiveSession:
    session_id: str
    channel: object
    _run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _active: bool = True
    delegations: set = field(default_factory=set)
    bridges: list = field(default_factory=list)
    tasks: set = field(default_factory=set)
    paired: object = None
    history_snapshot: list = field(default_factory=list)
    persisted: set = field(default_factory=set)
    admission: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def emit(self, event):
        if self._active:
            await self.channel.async_call(
                "realtime_event", {"session_id": self.session_id, **event}
            )

    async def stop(self):
        self._active = False
        for bridge in list(self.bridges):
            if bridge in bridge.paired.channels:
                bridge.paired.channels.remove(bridge)
        self.bridges.clear()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class CodexLiveService(RealtimeService):
    def __init__(self, *args, **kwargs):
        spec = copy(args[0])
        spec.realtime_mode = True
        super().__init__(spec, *args[1:], **kwargs)
        self._start_lock = asyncio.Lock()

    def unregister_channel(self, channel):
        for sid, session in list(self._sessions.items()):
            if session.channel is channel:
                task = asyncio.create_task(self.stop_session(sid))
                task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None
                )

    async def authorize_rpc(self, principal, method, params):
        await super().authorize_rpc(principal, method, params)
        live = self._sessions.get((params or {}).get("session_id"))
        if live and live.channel is not self.channel:
            raise AuthorizationDenied(
                "Voice is active in another client", resource="realtime_session"
            )

    async def stop_session(self, session_id):
        live = self._sessions.get(session_id)
        if live is None:
            return True
        async with live.admission:
            if self._sessions.get(session_id) is live:
                return await super().stop_session(session_id)
        return True

    async def capabilities(self):
        state = realtime_signin_state()
        return {
            "enabled": state == "ok",
            "kind": "codex-live",
            "transport": "webrtc",
            "model": "gpt-live-1-codex",
            "detail": "Codex OAuth: " + state,
        }

    async def start_session(self, session_id: str, sdp: str):
        async with self._start_lock:
            return await self._start_session(session_id, sdp)

    async def _start_session(self, session_id: str, sdp: str):
        if not self._agent_service:
            raise RuntimeError("Live requires an agent service")
        if session_id in self._sessions:
            raise ValueError("Voice is already active for this conversation")
        if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > 65536:
            raise ValueError("Invalid WebRTC offer")
        channel = self.channel
        owner = self._current_owner_user_id()
        context = await self._agent_service.prepare_environment_context_for_session(
            session_id,
            principal=getattr(channel, "principal", None),
        )
        if context is None:
            raise PermissionError("Live execution environment is unavailable")
        token = await realtime_access_token()
        if not token:
            raise RuntimeError("Sign in with Codex before starting voice")
        headers = {
            "Authorization": "Bearer " + token,
            "openai-alpha": "quicksilver=v2",
            "originator": "codex_cli_rs",
        }
        account = (load_tokens() or {}).get("account_id")
        if account:
            headers["ChatGPT-Account-Id"] = account
        paired = self._agent_service._session_manager.get_or_create(
            session_id, user_id=owner
        )
        history = history_messages(paired)
        body = {
            "sdp": sdp,
            "session": {
                "model": "gpt-live-1-codex",
                "instructions": INSTRUCTIONS
                + "\n\n"
                + (getattr(self.spec, "voice_instructions", None) or "")
                + "\n\n"
                + context_text(history),
                "audio": {"output": {"voice": "cove"}},
                "delegation": {"type": "client"},
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(CALL_URL, headers=headers, json=body)
        if not response.is_success:
            raise RuntimeError(
                f"Codex Live connection refused (HTTP {response.status_code})"
            )
        if not response.text.startswith("v=0"):
            raise RuntimeError("Codex Live returned an invalid SDP answer")
        self._sessions[session_id] = LiveSession(
            session_id, channel, paired=paired, history_snapshot=history
        )
        self._session_owners[session_id] = owner
        return {
            "session_id": session_id,
            "sdp": response.text,
            "call_id": self._sessions[session_id]._run_id,
            "model": "gpt-live-1-codex",
        }

    async def sync_context(self, session_id: str, turns: list | None = None):
        live = self._sessions.get(session_id)
        if not live or not live._active:
            raise ValueError("Voice session has ended")
        turns = turns or []
        if not isinstance(turns, list) or len(turns) > 80:
            raise ValueError("Invalid voice transcript batch")
        # Validate the complete batch before persisting anything.
        for turn in turns:
            if (
                not isinstance(turn, dict)
                or turn.get("role") not in ("user", "assistant")
                or not isinstance(turn.get("id"), str)
                or not 1 <= len(turn["id"]) <= 100
                or not isinstance(turn.get("text"), str)
                or len(turn["text"]) > 12000
            ):
                raise ValueError("Invalid voice transcript turn")
        for turn in turns:
            if turn["id"] in live.persisted or not turn["text"].strip():
                continue
            role, text = turn["role"], turn["text"].strip()
            message = {
                "role": role,
                "type": "message",
                "content": (
                    text
                    if role == "user"
                    else [{"type": "output_text", "text": text, "annotations": []}]
                ),
                "omniagents": {
                    "source": "voice:codex-live",
                    "live_call": live._run_id,
                    "voice_id": turn["id"],
                },
            }
            live.paired.append_message(
                message, projection={"version": 1, "kind": "voice_transcript"}
            )
            live.persisted.add(turn["id"])
        current = history_messages(live.paired, exclude_call=live._run_id)
        added = history_delta(live.history_snapshot, current)
        live.history_snapshot = current
        return {
            "saved": [turn["id"] for turn in turns],
            "context": context_text(added) if added else "",
        }

    async def delegate(self, session_id: str, delegation_id: str, text: str):
        live = self._sessions.get(session_id)
        if not live or not live._active:
            raise ValueError("Voice session has ended")
        if not isinstance(delegation_id, str) or not 1 <= len(delegation_id) <= 200:
            raise ValueError("Invalid delegation ID")
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError("Invalid delegated request")
        async with live.admission:
            return await self._delegate(live, session_id, delegation_id, text)

    async def _delegate(self, live, session_id, delegation_id, text):
        if not live._active:
            raise ValueError("Voice session has ended")
        if delegation_id in live.delegations:
            return {"accepted": True, "duplicate": True}
        if len(live.bridges) >= 8 or len(live.delegations) >= 500:
            raise ValueError("Too many pending voice requests")
        prompt = (
            f"[Live voice request {delegation_id}]\n"
            "Act on the request below through your normal tools and approvals. Return concise verified results in text; "
            "the Live voice model speaks them. Do not call speak or say. Transcripts may contain corrections; use the latest intent.\n\n"
            + text.strip()
        )
        paired = self._agent_service._session_manager.get_or_create(
            session_id, user_id=self._current_owner_user_id()
        )
        bridge = DelegateChannel(
            live, prompt, delegation_id, getattr(self.channel, "principal", None)
        )
        bridge.paired = paired
        paired.channels.append(bridge)
        live.bridges.append(bridge)
        try:
            result = await self._agent_service.enqueue_message(
                session_id=session_id,
                content=prompt,
                role="user",
                source="voice:codex-live",
                submission_id=f"live:{live._run_id}:{delegation_id}",
            )
            if result.get("accepted") is False or result.get("ok") is False:
                raise RuntimeError("Voice request was not queued")
            live.delegations.add(delegation_id)
        except BaseException:
            paired.channels.remove(bridge)
            live.bridges.remove(bridge)
            raise

        async def retire():
            try:
                await asyncio.wait_for(bridge.done.wait(), 1800)
            except asyncio.TimeoutError:
                await live.emit(
                    {
                        "type": "live_delegation_result",
                        "delegation_id": bridge.delegation_id,
                        "text": "This task is still pending. Check its status in chat before retrying.",
                    }
                )
            finally:
                if bridge in paired.channels:
                    paired.channels.remove(bridge)
                if bridge in live.bridges:
                    live.bridges.remove(bridge)

        task = asyncio.create_task(retire())
        live.tasks.add(task)
        task.add_done_callback(live.tasks.discard)
        return {"accepted": True, "mode": "queued"}
