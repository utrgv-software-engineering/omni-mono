"""Server-side realtime session management for voice agents.

This module provides the RealtimeSession class for managing realtime agent
sessions on the server side, including event streaming, audio storage, and
lifecycle management.
"""

import asyncio
import uuid
import base64
import logging
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Optional, Dict, Any, AsyncIterator, Tuple
from contextlib import asynccontextmanager
from contextlib import AsyncExitStack

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session.audio_storage import (
    AudioStorageBackend,
    build_audio_storage,
)
from omniagents.core.session import history_db
from omniagents.rpc.realtime_events import (
    RealtimeEventBase,
    RealtimeTranscriptEvent,
    map_realtime_session_event_to_rpc,
)

logger = logging.getLogger(__name__)


def _mask_secret(value: Optional[str]) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _realtime_targets_openai(settings: Dict[str, Any]) -> bool:
    """True when the realtime socket will land on OpenAI's own API.

    A ChatGPT OAuth bearer is only meaningful against ``api.openai.com``;
    Azure and self-hosted proxies must supply their own key. The first
    configured override wins, mirroring ``_resolve_realtime_url``'s
    precedence; with no override the SDK defaults to OpenAI.
    """
    for setting_key, env_key in (
        ("openai_realtime_url", "OPENAI_REALTIME_URL"),
        ("openai_realtime_base_url", "OPENAI_REALTIME_BASE_URL"),
        ("openai_base_url", "OPENAI_BASE_URL"),
    ):
        value = settings.get(setting_key)
        if not (isinstance(value, str) and value.strip()):
            value = os.getenv(env_key)
        if isinstance(value, str) and value.strip():
            candidate = value if "://" in value else f"https://{value}"
            host = urlparse(candidate).netloc.lower().split("@")[-1].split(":")[0]
            return host == "api.openai.com"
    return True


def realtime_credential_status(settings: Dict[str, Any]) -> Tuple[bool, str]:
    """Is a realtime credential configured? Offline and side-effect free.

    The gate for availability probes. Deliberately does NOT mint or refresh
    anything: ``capabilities()`` runs on every client boot, and refreshing
    there would spend a refresh token — and, on rejection, stamp the store as
    broken — merely because someone asked whether voice exists.

    Returns ``(available, detail)``; ``detail`` names the credential source
    when available and the reason when not, and never contains a secret.
    """
    if settings.get("openai_api_key") or settings.get("api_key"):
        return True, "settings api key"

    if not _realtime_targets_openai(settings):
        return False, (
            "a non-OpenAI realtime endpoint is configured, so ChatGPT OAuth "
            "cannot be used for it; set an OpenAI API key in settings"
        )

    from omniagents.core.providers.codex import realtime_signin_state

    state = realtime_signin_state()
    if state == "ok":
        return True, "ChatGPT OAuth (codex.json)"
    if state == "broken":
        return False, (
            "ChatGPT sign-in was rejected — sign in again "
            "(`omniagents auth codex login`)"
        )
    return False, (
        "no OpenAI API key in settings and not signed in with ChatGPT "
        "(`omniagents auth codex login`)"
    )


async def resolve_realtime_credential(
    settings: Dict[str, Any],
) -> Tuple[Optional[str], str]:
    """Produce the realtime bearer, or explain why there isn't one.

    Gated by :func:`realtime_credential_status` so the mic button's
    availability and the session's actual auth resolve through one rule and
    cannot drift. Unlike that check, this one may refresh — which is correct
    here, because a session is actually being opened.

    Returns ``(token, source)`` or ``(None, reason)``.
    """
    available, detail = realtime_credential_status(settings)
    if not available:
        return None, detail

    explicit = settings.get("openai_api_key") or settings.get("api_key")
    if explicit:
        return explicit, detail

    from omniagents.core.providers.codex import realtime_access_token

    try:
        token = await realtime_access_token()
    except Exception as exc:  # refresh rejected, network down, unreadable store
        return None, f"ChatGPT sign-in could not be refreshed: {exc}"

    if token:
        return token, detail
    return None, "ChatGPT sign-in became unavailable while opening the session"


def _collect_headers(settings: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key in ("headers", "extra_headers", "openai_headers", "openai_extra_headers"):
        value = settings.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _scrub_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    scrubbed: Dict[str, Any] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if isinstance(value, str) and any(
            token in lowered for token in ("authorization", "api", "token", "secret")
        ):
            scrubbed[key] = _mask_secret(value)
        else:
            scrubbed[key] = value
    return scrubbed


def _is_azure_endpoint(url: Optional[str]) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "openai.azure.com" in lowered or "services.ai.azure.com" in lowered


def _is_openai_compatible_azure_endpoint(url: Optional[str]) -> bool:
    if not _is_azure_endpoint(url):
        return False
    lowered = (url or "").lower()
    return "/openai/v1" in lowered


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _resolve_realtime_deployment_name(
    spec: AgentSpec, settings: Dict[str, Any]
) -> Optional[str]:
    candidates = [
        settings.get("azure_openai_deployment_name"),
        settings.get("openai_realtime_deployment"),
        settings.get("openai_realtime_model"),
        os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        os.getenv("OPENAI_REALTIME_DEPLOYMENT"),
        os.getenv("OPENAI_REALTIME_MODEL"),
    ]
    if spec.realtime_settings and spec.realtime_settings.model_name:
        candidates.append(spec.realtime_settings.model_name)
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Default to gpt-realtime if nothing specified
    return "gpt-realtime"


def _resolve_realtime_model_name(
    spec: AgentSpec, settings: Dict[str, Any]
) -> Optional[str]:
    candidates = [
        settings.get("openai_realtime_model"),
        os.getenv("OPENAI_REALTIME_MODEL"),
    ]
    if spec.realtime_settings and spec.realtime_settings.model_name:
        candidates.append(spec.realtime_settings.model_name)
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_ws_scheme(raw_scheme: Optional[str]) -> str:
    if not raw_scheme:
        return "wss"
    lowered = raw_scheme.lower()
    if lowered == "https":
        return "wss"
    if lowered == "http":
        return "ws"
    if lowered in {"ws", "wss"}:
        return lowered
    return "wss"


def _build_azure_realtime_url(
    candidate_url: Optional[str],
    base_url: Optional[str],
    spec: AgentSpec,
    settings: Dict[str, Any],
) -> Optional[str]:
    deployment = _resolve_realtime_deployment_name(spec, settings)
    if not deployment:
        return candidate_url or base_url
    endpoint = (
        settings.get("azure_openai_endpoint")
        or settings.get("openai_base_url")
        or candidate_url
        or base_url
        or os.getenv("AZURE_OPENAI_ENDPOINT")
        or os.getenv("OPENAI_BASE_URL")
    )
    if not endpoint:
        return candidate_url or base_url
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    netloc = parsed.netloc or parsed.path
    if not netloc:
        return candidate_url or base_url
    scheme = _normalize_ws_scheme(parsed.scheme)
    api_version = (
        settings.get("azure_openai_api_version")
        or os.getenv("AZURE_OPENAI_API_VERSION")
        or "2024-10-21-preview"
    )
    deployment_path = deployment.strip()
    return f"{scheme}://{netloc}/openai/deployments/{deployment_path}/realtime?api-version={api_version}"


def _build_realtime_url_from_base(
    base_url: str,
    model_name: Optional[str],
) -> str:
    """Build a realtime WebSocket URL from a base HTTP URL.

    Converts https://host/openai/v1 to wss://host/openai/v1/realtime?model=X
    """
    # Convert scheme
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")

    # Ensure it ends with /realtime
    ws_url = ws_url.rstrip("/")
    if not ws_url.endswith("/realtime"):
        ws_url = ws_url + "/realtime"

    # Add model as query param if provided
    if model_name:
        parsed = urlparse(ws_url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "model" not in params:
            params["model"] = model_name
            ws_url = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(params, doseq=True),
                    parsed.fragment,
                )
            )

    return ws_url


def _resolve_realtime_url(
    explicit_url: Optional[str],
    base_url: Optional[str],
    spec: AgentSpec,
    settings: Dict[str, Any],
) -> Optional[str]:
    candidate = explicit_url or base_url
    logger.debug(
        "_resolve_realtime_url: explicit_url=%s, base_url=%s, candidate=%s",
        explicit_url,
        base_url,
        candidate,
    )
    if not candidate:
        logger.debug("_resolve_realtime_url: no candidate URL, returning None")
        return None

    # If an explicit realtime URL was provided (openai_realtime_url), use it as-is
    if explicit_url:
        logger.debug(
            "_resolve_realtime_url: explicit URL provided, returning as-is: %s",
            explicit_url,
        )
        return explicit_url

    # We're falling back to base_url - need to construct the realtime URL
    if (
        (_is_azure_endpoint(base_url) or _is_azure_endpoint(candidate))
        and not _is_openai_compatible_azure_endpoint(base_url)
        and not _is_openai_compatible_azure_endpoint(candidate)
    ):
        model_name = _resolve_realtime_deployment_name(spec, settings)
        azure_url = _build_azure_realtime_url(
            candidate_url=candidate,
            base_url=base_url,
            spec=spec,
            settings=settings,
        )
        if azure_url:
            logger.debug("_resolve_realtime_url: built Azure URL: %s", azure_url)
            return azure_url

    model_name = _resolve_realtime_model_name(spec, settings)
    logger.debug(
        "_resolve_realtime_url: building from base_url=%s with model=%s",
        base_url,
        model_name,
    )

    result = _build_realtime_url_from_base(base_url, model_name)
    logger.debug("_resolve_realtime_url: built URL: %s", result)
    return result


class RealtimeSession:
    """Manages a realtime agent session on the server side.

    This class:
    - Creates and manages a realtime agent from the OpenAI SDK
    - Streams events from the agent and converts them to RPC events
    - Stores audio chunks to disk and metadata to database
    - Manages session lifecycle (start, stop, cleanup)
    """

    def __init__(
        self,
        spec: AgentSpec,
        settings: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        audio_storage: Optional[AudioStorageBackend] = None,
        owner_user_id: Optional[str] = None,
        extra_tools: Optional[list] = None,
        request_context: Optional[Any] = None,
        agent_service: Optional[Any] = None,
        paired_session: Optional[Any] = None,
    ):
        """Initialize a realtime session.

        Args:
            spec: Agent specification with realtime settings
            settings: API keys and other settings
            session_id: Optional session ID (generated if not provided)
            audio_storage: Optional AudioStorageBackend instance (created if not provided)
            owner_user_id: Authenticated principal that owns this session
            extra_tools: Additional SDK tools appended to the spec's tools —
                the client-tool proxies bridged from a GUI session sharing
                this session id (see AgentService.build_realtime_client_tools)
            request_context: Routed RequestContext for the paired GUI
                session (see
                ``AgentService.prepare_environment_context_for_session``).
                Its lease binds the workspace contextvar and stamps
                workspace/environment identity on the run context so
                server-side tools execute where text runs do, and the
                context itself is bound for the voice task tree so
                lease-routed helpers resolve. Without it, workspace-touching
                tools raise WorkspaceRootRequiredError.
            agent_service: The deployment's AgentService, when available.
                Routes voice tool approvals through the text path's
                ``request_tool_approval`` (prompts render in the chat UI on
                the shared /ws session channels).
            paired_session: The GUI ``Session`` sharing this session id.
                Voice turns persist into its history + canonical thread, and
                its always-allow state gates tool approvals.
        """
        if not spec.realtime_mode:
            raise ValueError("AgentSpec must have realtime_mode=True")

        self.spec = spec
        self.settings = settings or {}
        self.session_id = session_id or str(uuid.uuid4())
        self.owner_user_id = owner_user_id
        self.extra_tools = list(extra_tools or [])
        self.request_context = request_context
        # One source of truth: execution derives from the routed context.
        self.environment_lease = getattr(request_context, "environment_lease", None)
        self.agent_service = agent_service
        self.paired_session = paired_session

        # Extract project and agent slugs for database storage
        self._project_slug = getattr(spec, "project_slug", None)
        self._agent_slug = getattr(spec, "agent_slug", None)

        # Initialize audio storage (local filesystem unless injected).
        if audio_storage:
            self.audio_storage = audio_storage
        else:
            self.audio_storage = build_audio_storage(
                self._project_slug, self._agent_slug
            )

        # Session state
        self._agent = None
        self._runner = None
        self._realtime_session = None
        self._run_id: Optional[str] = None
        self._api_key: Optional[str] = None
        self._active = False
        self._event_task: Optional[asyncio.Task] = None

        # Voice transcript state: assistant deltas accumulate per item_id
        # (insertion-ordered) until turn end flushes them as final
        # transcripts; ``_voice_turn_id`` is the open canonical turn.
        self._assistant_transcripts: Dict[str, str] = {}
        self._voice_turn_id: Optional[str] = None
        self._approval_tasks: set[asyncio.Task] = set()

        self._mcp_shutdown: Optional[asyncio.Event] = None
        self._mcp_lifecycle_task: Optional[asyncio.Task] = None
        self._mcp_servers_active: list[Any] = []

    async def _ensure_mcp_servers(self) -> list[Any]:
        if self._mcp_servers_active:
            return self._mcp_servers_active

        if not getattr(self.spec, "mcp_servers", None):
            return []

        from omniagents.core.agents.mcp import build_mcp_servers

        servers_ready = asyncio.Event()
        active_servers_holder: dict[str, list[Any]] = {"servers": []}

        async def mcp_lifecycle_manager():
            settings = dict(self.settings)
            async with AsyncExitStack() as stack:
                active_servers: list[Any] = []
                try:
                    servers = build_mcp_servers(self.spec, settings)
                    for server in servers:
                        server_name = getattr(server, "name", "unknown")
                        try:
                            if hasattr(server, "__aenter__") and hasattr(
                                server, "__aexit__"
                            ):
                                active = await stack.enter_async_context(server)
                            else:
                                await server.connect()
                                stack.push_async_callback(server.cleanup)
                                active = server
                            active_servers.append(active)
                        except Exception as server_err:
                            logger.debug(
                                "MCP server '%s' failed to connect: %s",
                                server_name,
                                server_err,
                            )

                    self._mcp_servers_active = active_servers
                    active_servers_holder["servers"] = active_servers
                    servers_ready.set()

                    shutdown_event = self._mcp_shutdown
                    if shutdown_event:
                        await shutdown_event.wait()
                finally:
                    self._mcp_servers_active = []

        self._mcp_shutdown = asyncio.Event()
        self._mcp_lifecycle_task = asyncio.create_task(
            mcp_lifecycle_manager(),
            name=f"realtime_mcp_lifecycle_{self.session_id[:8]}",
        )
        await servers_ready.wait()
        return active_servers_holder["servers"]

    async def start(self) -> str:
        """Start the realtime session.

        Returns:
            The run ID for this session

        Raises:
            RuntimeError: If session is already active
        """
        if self._active:
            raise RuntimeError("Session is already active")

        # Import OpenAI SDK components
        try:
            from agents.realtime import RealtimeAgent, RealtimeRunner
        except ImportError:
            raise ImportError(
                "OpenAI agents SDK not installed. "
                "Install with: pip install openai-agents"
            )

        # Get the bearer: an explicit key, else the ChatGPT OAuth token.
        api_key, credential_source = await resolve_realtime_credential(self.settings)
        if not api_key:
            raise ValueError(f"No realtime credential available: {credential_source}")

        masked_key = _mask_secret(api_key)
        logger.info(
            "RealtimeSession %s authenticating via %s (%s)",
            self.session_id,
            credential_source,
            masked_key,
        )

        # Build tools if available (spec tools + capability-pack tools + any
        # bridged client tools). Capability packs are normally expanded by
        # build_agent(); realtime builds its agent here, so expand them the
        # same way. Approval predicates are attached below, once the full
        # static list is assembled.
        from omniagents.core.capabilities.profiles import spec_for_capability_profile

        profile_spec = spec_for_capability_profile(self.spec, self.paired_session)
        tools = list(self.spec.available_tools or [])
        resolved_caps = list(getattr(profile_spec, "resolved_capabilities", []) or [])
        for cap in resolved_caps:
            cap_label = getattr(cap, "capability_name", None) or getattr(
                cap, "type", "?"
            )
            try:
                tools.extend(cap.tools())
            except Exception as e:
                logger.error(
                    "Capability %r tools() raised: %s. Skipping its tools.",
                    cap_label,
                    e,
                )
        # Agent-as-tool FunctionTools (e.g. the exploration pack's `explore`):
        # the loader flattens capability agent_tools into
        # spec.agent_tool_configs; build them here like build_agent() does.
        if getattr(self.spec, "agent_tool_configs", None):
            from omniagents.core.agents.builder import _build_agent_tools_from_configs

            try:
                agent_tools_built = await _build_agent_tools_from_configs(
                    agent_tool_configs=self.spec.agent_tool_configs,
                    settings=self.settings,
                    approval_callback=None,
                    parent_spec=self.spec,
                )
                tools.extend(agent_tools_built)
            except Exception as e:
                logger.error(
                    "Failed to build agent_tools for '%s': %s", self.spec.name, e
                )
        if self.extra_tools:
            tools = tools + self.extra_tools

        # Gate tools with the same needs_approval contract text runs use —
        # the SDK's realtime session honors it and emits
        # ``tool_approval_required``, which stream_events() routes through
        # AgentService.request_tool_approval on the paired GUI session (the
        # approval card renders in the chat UI over /ws). Bridged client
        # tools are exempt: only ``safe: true`` registrations are bridged.
        # Without a paired session there is no approval surface, so tools
        # stay ungated as before (embedded single-user use).
        safe_opts = getattr(self.spec, "safe_agent_options", None)
        gate_tools = (
            getattr(self.spec, "use_safe_agent", False)
            and not getattr(safe_opts, "skip_approvals", False)
            and self.agent_service is not None
            and self.paired_session is not None
        )
        if gate_tools:
            from omniagents.core.agents.approval import (
                attach_tool_approval_predicates,
            )

            tools = attach_tool_approval_predicates(
                tools,
                session=self.paired_session,
                safe_tool_names=getattr(safe_opts, "safe_tool_names", None),
                safe_tool_patterns=getattr(safe_opts, "safe_tool_patterns", None),
                exempt=self.extra_tools,
            )

        mcp_servers = await self._ensure_mcp_servers()

        # Build handoffs if configured
        handoffs = []
        if self.spec.handoff_configs:
            from omniagents.core.agents.builder import _build_handoffs_from_configs

            handoffs = await _build_handoffs_from_configs(
                handoff_configs=self.spec.handoff_configs,
                settings=self.settings,
                approval_callback=None,  # No approval callback for realtime
                parent_spec=self.spec,
            )

        # Get guardrails from spec
        output_guardrails = (
            self.spec.output_guardrails if self.spec.output_guardrails else []
        )

        # Build instructions with context (Jinja2 template processing)
        raw_instructions = (
            self.spec.get_agent_instructions()
            if self.spec.get_agent_instructions
            else ""
        )

        # Append capability instruction fragments before rendering, matching
        # build_agent(): fragments are appended to the raw string so they
        # flow through Jinja2 alongside the agent's own template.
        cap_instructions_parts = []
        for cap in resolved_caps:
            text = getattr(cap, "instructions_text", None)
            if isinstance(text, str) and text.strip():
                cap_instructions_parts.append(text.strip())
        if cap_instructions_parts and isinstance(raw_instructions, str):
            raw_instructions = (
                raw_instructions.rstrip() + "\n\n" + "\n\n".join(cap_instructions_parts)
            )

        # Build context from factory if available
        context_dict = {}
        if self.spec.build_context and self.spec.variables is not None:
            try:
                context_dict = self.spec.build_context(self.spec.variables)
            except Exception as e:
                logger.warning(f"Failed to build context: {e}")
        elif self.spec.context:
            # Use pre-built context if available
            context_dict = (
                self.spec.context if isinstance(self.spec.context, dict) else {}
            )

        # Include search paths for {% include %} fragments (set by the YAML
        # loader from the instructions file's directory + _shared conventions).
        search_paths = getattr(self.spec, "instructions_search_paths", None)
        # Render Jinja2 when context is available, or when the template uses
        # {% include %} and search paths exist (voice agents share fragments
        # with their text siblings). Templates without includes and without
        # context keep the historical pass-through behavior.
        import re as _re

        has_include = bool(
            raw_instructions
            and _re.search(r"\{%-?\s*(include|import|from)\b", raw_instructions)
        )
        if raw_instructions and (context_dict or (search_paths and has_include)):
            from omniagents.core.utils.jinja_instructions import render_template

            try:
                instructions = render_template(
                    raw_instructions,
                    silent_undefined=True,
                    search_paths=search_paths,
                    **context_dict,
                )
            except Exception as e:
                logger.warning(f"Failed to render instructions template: {e}")
                instructions = raw_instructions
        else:
            instructions = raw_instructions

        # Voice joins the ongoing conversation: append a rendered tail of
        # the shared session's history so the agent knows what was already
        # discussed (text runs get this for free from the SDK session; the
        # realtime model starts from instructions alone).
        tail = self._render_history_tail()
        if tail:
            instructions = (
                (instructions or "").rstrip()
                + "\n\n# Conversation so far\n"
                + "You are joining this user's ongoing conversation; earlier "
                + "turns may have happened in text. Recent history:\n\n"
                + tail
            )

        # Create agent with realtime configuration
        # RealtimeAgent only takes: name, instructions, tools, handoffs, output_guardrails, hooks
        self._agent = RealtimeAgent(
            name=self.spec.name or "RealtimeAgent",
            instructions=instructions,
            tools=tools,
            handoffs=handoffs,
            output_guardrails=output_guardrails,
            mcp_servers=list(mcp_servers),
            mcp_config=self.spec.mcp_config or {},
        )

        from omniagents.core.tools.output_budget import install_output_budget

        install_output_budget(
            self._agent, (self.spec.project_slug, self.spec.agent_slug)
        )

        # MCP tools are not in the static list above: the SDK resolves them
        # inside RealtimeAgent.get_all_tools on every run, after that pass.
        # Gating them needs the agent, so it happens here — otherwise every
        # tool from a user-configured MCP server would execute in a voice
        # session with no approval, while the same server IS gated in text
        # (configure_tool_approval's agent walk covers it there).
        if gate_tools:
            from omniagents.core.agents.approval import attach_lazy_tool_approval

            attach_lazy_tool_approval(
                self._agent,
                session=self.paired_session,
                safe_tool_names=getattr(safe_opts, "safe_tool_names", None),
                safe_tool_patterns=getattr(safe_opts, "safe_tool_patterns", None),
            )

        # Create runner with agent
        self._runner = RealtimeRunner(self._agent)

        # Store API key for model_config
        self._api_key = api_key

        # Generate run ID
        self._run_id = str(uuid.uuid4())

        # Register session in database
        history_db.register_session(
            self.session_id,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
            user_id=self.owner_user_id,
        )

        self._active = True
        return self._run_id

    async def connect(self) -> Any:
        """Connect to the realtime session.

        Returns:
            The OpenAI RealtimeSession object

        Raises:
            RuntimeError: If session not started
        """
        if not self._active:
            raise RuntimeError("Session not started. Call start() first.")

        # Build model configuration
        realtime_settings = self.spec.realtime_settings

        # Build initial model settings from spec
        initial_model_settings = {}

        if realtime_settings.model_name:
            initial_model_settings["model_name"] = realtime_settings.model_name

        if realtime_settings.modalities:
            initial_model_settings["modalities"] = realtime_settings.modalities

        if realtime_settings.voice:
            initial_model_settings["voice"] = realtime_settings.voice

        if realtime_settings.temperature is not None:
            # Note: temperature is not in RealtimeSessionModelSettings
            # It may be part of a different config or not supported
            pass

        if realtime_settings.input_audio_format:
            initial_model_settings["input_audio_format"] = (
                realtime_settings.input_audio_format
            )

        if realtime_settings.output_audio_format:
            initial_model_settings["output_audio_format"] = (
                realtime_settings.output_audio_format
            )

        if realtime_settings.turn_detection:
            initial_model_settings["turn_detection"] = realtime_settings.turn_detection

        if realtime_settings.input_audio_transcription:
            initial_model_settings["input_audio_transcription"] = (
                realtime_settings.input_audio_transcription
            )

        # Build model_config
        model_config: Dict[str, Any] = {
            "initial_model_settings": initial_model_settings,
        }

        # Debug: log all URL sources
        logger.debug(
            "RealtimeSession %s URL resolution inputs: "
            "settings[openai_realtime_url]=%s, "
            "env[OPENAI_REALTIME_URL]=%s, "
            "settings[openai_realtime_base_url]=%s, "
            "env[OPENAI_REALTIME_BASE_URL]=%s, "
            "settings[openai_base_url]=%s, "
            "env[OPENAI_BASE_URL]=%s",
            self.session_id,
            self.settings.get("openai_realtime_url"),
            os.getenv("OPENAI_REALTIME_URL"),
            self.settings.get("openai_realtime_base_url"),
            os.getenv("OPENAI_REALTIME_BASE_URL"),
            self.settings.get("openai_base_url"),
            os.getenv("OPENAI_BASE_URL"),
        )

        def _get_setting_or_env(setting_key: str, env_key: str) -> Optional[str]:
            if setting_key in self.settings:
                value = self.settings.get(setting_key)
                return value if isinstance(value, str) and value.strip() else None
            value = os.getenv(env_key)
            return value if isinstance(value, str) and value.strip() else None

        realtime_url = _get_setting_or_env(
            "openai_realtime_url", "OPENAI_REALTIME_URL"
        ) or _get_setting_or_env("openai_realtime_base_url", "OPENAI_REALTIME_BASE_URL")
        base_url = _get_setting_or_env("openai_base_url", "OPENAI_BASE_URL")

        logger.debug(
            "RealtimeSession %s URL resolution: realtime_url=%s, base_url=%s",
            self.session_id,
            realtime_url,
            base_url,
        )

        resolved_url = _resolve_realtime_url(
            explicit_url=realtime_url,
            base_url=base_url,
            spec=self.spec,
            settings=self.settings,
        )

        logger.debug(
            "RealtimeSession %s _resolve_realtime_url returned: %s",
            self.session_id,
            resolved_url,
        )

        if resolved_url:
            model_config["url"] = resolved_url

        headers = _collect_headers(self.settings)
        current_url = model_config.get("url")
        azure_target = _is_azure_endpoint(current_url) or _is_azure_endpoint(base_url)
        force_header_flag = self.settings.get("openai_realtime_force_api_key_header")
        if force_header_flag is None:
            force_header_flag = os.getenv("OPENAI_REALTIME_FORCE_API_KEY_HEADER")
        use_api_key_header = _coerce_bool(force_header_flag) or azure_target
        header_has_api_key = any(
            isinstance(key, str) and key.lower() == "api-key" for key in headers.keys()
        )
        api_key_consumed = False

        if use_api_key_header and self._api_key and not header_has_api_key:
            headers["api-key"] = self._api_key
            api_key_consumed = True
        elif header_has_api_key:
            api_key_consumed = True

        if headers:
            model_config["headers"] = headers

        query_flag = self.settings.get("openai_realtime_api_key_in_query")
        if query_flag is None:
            query_flag = os.getenv("OPENAI_REALTIME_API_KEY_IN_QUERY")
        query_flag = _coerce_bool(query_flag)

        if query_flag and model_config.get("url") and self._api_key:
            parsed = urlparse(model_config["url"])
            params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if "api-key" not in params:
                params["api-key"] = self._api_key
                model_config["url"] = urlunparse(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        parsed.params,
                        urlencode(params, doseq=True),
                        parsed.fragment,
                    )
                )
            if "api-key" in params:
                api_key_consumed = True

        if api_key_consumed:
            model_config.pop("api_key", None)
        elif self._api_key:
            model_config["api_key"] = self._api_key

        scrubbed_headers = _scrub_headers(headers)
        logger.info(
            "RealtimeSession %s connecting run_id=%s base_url=%s headers=%s",
            self.session_id,
            self._run_id,
            model_config.get("url") or base_url or "<default>",
            scrubbed_headers or {},
        )
        logger.debug(
            "RealtimeSession %s model settings: %s",
            self.session_id,
            initial_model_settings,
        )

        # Log the final model_config being passed to runner.run()
        # Scrub sensitive data for logging
        loggable_config = {
            k: (_mask_secret(v) if k == "api_key" else v)
            for k, v in model_config.items()
            if k != "headers"
        }
        loggable_config["headers"] = scrubbed_headers
        logger.debug(
            "RealtimeSession %s final model_config for runner.run(): %s",
            self.session_id,
            loggable_config,
        )

        # Execution wiring, mirroring the text-run path (runtime/bridge.py +
        # AgentService context stamping + start_run's own task bindings):
        #
        #   1. the lease's workspace on the contextvar, so tool bodies route
        #      through the right backend;
        #   2. workspace/environment identity on the run context, so tools
        #      resolve cwd and scope per-session state;
        #   3. the routed request context, so lease-routed helpers
        #      (``require_environment_lease``, the worker isolation source
        #      provider) resolve instead of raising "requires a routed
        #      request context";
        #   4. the owning AgentService, so ``current_service()`` consumers
        #      work — without it a spawned worker gets no AgentHost
        #      resources, builds a private registry, and its run fails with
        #      "execution environment ... was not found".
        #
        # All four are set here, before __aenter__, so the model's listener
        # task — where the SDK executes tool calls — inherits them. They are
        # deliberately NOT reset: the event-pump task is created after this
        # returns and must inherit them too (an approval-gated tool resumes
        # from THAT tree, via approve_tool_call), and RPC dispatch resets its
        # own request-context token when this call unwinds.
        run_context: Dict[str, Any] = {"session_id": self.session_id}
        if self.environment_lease is not None:
            from omniagents.workspace import set_requested_cwd, set_workspace

            lease = self.environment_lease
            set_requested_cwd(lease.workspace.root)
            set_workspace(lease.workspace)
            run_context.update(
                {
                    "workspace_root": lease.workspace.root,
                    "workspace_id": lease.workspace_id,
                    "environment_id": lease.environment_id,
                    "environment_generation": lease.generation,
                }
            )
        if self.request_context is not None:
            from omniagents.execution import bind_request_context

            bind_request_context(self.request_context)
        if self.agent_service is not None:
            from omniagents.core.agents.service import bind_current_service

            bind_current_service(self.agent_service)

        # Get the session from runner.run()
        # This returns a RealtimeSession that needs to be used as async context manager
        self._realtime_session = await self._runner.run(
            context=run_context, model_config=model_config
        )

        # Enter the async context manager
        await self._realtime_session.__aenter__()

        return self._realtime_session

    async def stream_events(self) -> AsyncIterator[RealtimeEventBase]:
        """Stream realtime events from the agent.

        Yields:
            RPC realtime event objects

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        # Cache tool call arguments from raw model events
        # Key: (tool_name, call_id) -> arguments string
        # Raw model events arrive before tool_start events and contain the arguments
        tool_call_cache = {}

        # Stream events from the realtime session (it's an async iterator)
        async for event in self._realtime_session:
            # Tool approval pauses live inside the SDK session; route the
            # decision through the text path's approval flow WITHOUT
            # blocking this event pump (audio keeps streaming while the
            # prompt is up in the chat UI).
            if getattr(event, "type", None) == "tool_approval_required":
                task = asyncio.create_task(
                    self._route_tool_approval(event),
                    name=f"realtime_tool_approval_{str(getattr(event, 'call_id', '') or '')[:12]}",
                )
                self._approval_tasks.add(task)
                task.add_done_callback(self._approval_tasks.discard)
                continue

            # Check for raw model events first to cache tool call arguments
            # This mimics how non-realtime mode extracts arguments from tool_call_item
            if event.type == "raw_model_event":
                data = getattr(event, "data", None)
                # RealtimeModelToolCallEvent has name, call_id, arguments
                if data and getattr(data, "type", None) == "function_call":
                    tool_name = getattr(data, "name", None)
                    call_id = getattr(data, "call_id", None)
                    arguments = getattr(data, "arguments", "")
                    if tool_name and call_id:
                        tool_call_cache[(tool_name, call_id)] = arguments

            # Convert SDK event to RPC event (pass cache for tool_start events)
            rpc_event = map_realtime_session_event_to_rpc(
                event, self._run_id, tool_call_cache=tool_call_cache
            )

            if rpc_event:
                # Handle audio storage for audio events
                if rpc_event.type == "realtime_audio":
                    await self._store_audio_chunk(rpc_event)

                # Clean up cache after tool completes to prevent memory leak
                if rpc_event.type == "realtime_tool_end":
                    cache_key = (rpc_event.tool_name, rpc_event.tool_call_id)
                    tool_call_cache.pop(cache_key, None)

                if rpc_event.type == "realtime_transcript_delta":
                    key = rpc_event.item_id or "__no_item__"
                    self._assistant_transcripts[key] = (
                        self._assistant_transcripts.get(key, "") + rpc_event.delta
                    )

                if rpc_event.type == "realtime_transcript" and rpc_event.role == "user":
                    self._persist_voice_message("user", rpc_event.transcript)

                # Turn end: flush accumulated assistant items as final
                # transcripts (client replaces delta-built text; history
                # gains the canonical turn) before the boundary event.
                if rpc_event.type == "realtime_turn_ended":
                    for final in self._flush_assistant_transcripts():
                        yield final

                yield rpc_event

    async def _store_audio_chunk(self, audio_event) -> None:
        """Store an audio chunk to disk and save metadata.

        Args:
            audio_event: RealtimeAudioEvent with audio data
        """
        # Decode base64 audio
        audio_bytes = base64.b64decode(audio_event.audio_base64)

        # Determine audio format (from realtime settings)
        audio_format = self.spec.realtime_settings.output_audio_format

        # Save audio file
        relative_path = await self.audio_storage.save_audio_chunk(
            session_id=self.session_id,
            item_id=audio_event.item_id,
            content_index=audio_event.content_index,
            audio_data=audio_bytes,
            audio_format=audio_format,
        )

        # Save metadata to database
        history_db.save_audio_metadata(
            session_id=self.session_id,
            item_id=audio_event.item_id,
            content_index=audio_event.content_index,
            file_path=relative_path,
            audio_format=audio_format,
            duration_ms=audio_event.delta_ms,
            project_slug=self._project_slug,
            agent_slug=self._agent_slug,
        )

    # ------------------------------------------------------------------
    #  Transcript persistence + canonical recording
    # ------------------------------------------------------------------

    def _flush_assistant_transcripts(self) -> list[RealtimeTranscriptEvent]:
        """Drain accumulated assistant deltas into final transcript events.

        Called at turn end (yielded to the client) and from stop() for a
        mid-turn teardown (persist only). Each flushed item is persisted
        into the shared session history + canonical thread.
        """
        finals: list[RealtimeTranscriptEvent] = []
        for item_id, text in self._assistant_transcripts.items():
            self._persist_voice_message("assistant", text)
            finals.append(
                RealtimeTranscriptEvent(
                    run_id=self._run_id or "",
                    role="assistant",
                    transcript=text,
                    item_id="" if item_id == "__no_item__" else item_id,
                )
            )
        self._assistant_transcripts = {}
        return finals

    def _persist_voice_message(self, role: str, text: str) -> None:
        """Land one voice turn in the session thread text runs share.

        ``Session.append_message`` feeds the SDK history (the next text
        run's model context and the legacy transcript); the canonical
        recording mirrors what the notification chokepoint does for text
        runs, because nothing here broadcasts. Best-effort: a persistence
        fault must not kill the audio stream.
        """
        text = (text or "").strip()
        if not text:
            return
        if role == "user":
            msg: Dict[str, Any] = {"role": "user", "content": text}
        else:
            # Explicit output parts: bare assistant strings get coerced to
            # input_text by the SDK's input conversion, which
            # responses.compact rejects.
            msg = {
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
                "type": "message",
            }
        sess = self.paired_session
        if sess is not None:
            try:
                sess.append_message(msg)
            except Exception:
                logger.exception(
                    "voice message not persisted", extra={"session": self.session_id}
                )
            self._record_canonical(sess, role, text)
        else:
            try:
                history_db.append_message(
                    self.session_id,
                    msg,
                    project_slug=self._project_slug,
                    agent_slug=self._agent_slug,
                )
            except Exception:
                logger.exception(
                    "voice message not persisted", extra={"session": self.session_id}
                )

    def _record_canonical(self, sess: Any, role: str, text: str) -> None:
        """Project a voice turn into the canonical conversation store.

        The chat UI loads transcripts canonical-first, so without this the
        voice conversation would vanish from the thread view even though
        the SDK history has it. Turn model: a user final opens a voice
        turn (its prompt), the assistant flush closes it; an assistant
        reply with no transcribed prompt (transcription lag or text input)
        opens a promptless turn. A second user utterance into an open turn
        records as a mid-run user message.
        """
        from omniagents.core.conversation.recorder import ensure_conversation_thread

        try:
            recorder = ensure_conversation_thread(sess)
        except Exception:
            recorder = None
        if recorder is None:
            return
        try:
            if role == "user":
                if self._voice_turn_id is None:
                    self._voice_turn_id = f"voice_{uuid.uuid4().hex[:12]}"
                    recorder.record(
                        sess.id,
                        "run_started",
                        {
                            "run_id": self._voice_turn_id,
                            "prompt": text,
                            "prompt_role": "user",
                        },
                    )
                else:
                    recorder.record_user_message(
                        sess.id, run_id=self._voice_turn_id, content=text
                    )
            else:
                turn_id = self._voice_turn_id
                if turn_id is None:
                    turn_id = f"voice_{uuid.uuid4().hex[:12]}"
                    recorder.record(sess.id, "run_started", {"run_id": turn_id})
                recorder.record(
                    sess.id, "message_output", {"run_id": turn_id, "content": text}
                )
                recorder.record(
                    sess.id,
                    "run_end",
                    {"run_id": turn_id, "end_reason": "completed"},
                )
                self._voice_turn_id = None
        except Exception:
            logger.exception(
                "voice canonical recording failed", extra={"session": self.session_id}
            )

    # ------------------------------------------------------------------
    #  Text → voice context
    # ------------------------------------------------------------------

    _HISTORY_TAIL_ITEMS = 30
    _HISTORY_TAIL_CHARS = 8000

    def _render_history_tail(self) -> str:
        """Plain-text tail of the shared session's conversation.

        Last ``_HISTORY_TAIL_ITEMS`` message items, capped at
        ``_HISTORY_TAIL_CHARS`` (oldest dropped first). Non-message items
        (tool calls, reasoning) are skipped. Empty string when there is no
        usable history.
        """
        history: list = []
        sess = self.paired_session
        if sess is not None:
            history = list(getattr(sess, "history", None) or [])
        else:
            try:
                history = history_db.load_history(
                    self.session_id,
                    project_slug=self._project_slug,
                    agent_slug=self._agent_slug,
                )
            except Exception:
                history = []

        lines: list[str] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in ("user", "assistant"):
                continue
            content = item.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict)
                    and p.get("type") in ("output_text", "input_text", "text")
                ]
                text = "".join(parts)
            else:
                continue
            text = (text or "").strip()
            if not text:
                continue
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {text}")

        lines = lines[-self._HISTORY_TAIL_ITEMS :]
        while lines and sum(len(line) + 1 for line in lines) > self._HISTORY_TAIL_CHARS:
            lines.pop(0)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    #  Tool approvals
    # ------------------------------------------------------------------

    async def _route_tool_approval(self, event: Any) -> None:
        """Resolve one SDK ``tool_approval_required`` pause.

        The prompt goes through ``AgentService.request_tool_approval`` on
        the paired GUI session — the same broadcast-and-await the text
        path uses, so the card renders in the chat UI and "Always" updates
        the session allow-list the needs_approval predicates consult. The
        decision resumes the SDK session via approve/reject_tool_call.
        """
        call_id = str(getattr(event, "call_id", "") or "")
        tool = getattr(event, "tool", None)
        tool_name = str(getattr(tool, "name", "") or "unknown")
        arguments = str(getattr(event, "arguments", "") or "")
        rt = self._realtime_session
        if rt is None or not call_id:
            return

        svc = self.agent_service
        sess = self.paired_session
        if svc is None or sess is None:
            # Predicates are only attached when both exist (see start()),
            # so this is a defensive dead end, not a policy decision.
            await rt.reject_tool_call(
                call_id,
                rejection_message="Tool approval is unavailable in this voice session",
            )
            return

        try:
            response = await svc.request_tool_approval(
                session=sess,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                run_id=self._run_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "voice tool approval failed", extra={"session": self.session_id}
            )
            try:
                await rt.reject_tool_call(
                    call_id, rejection_message=f"Approval failed: {exc}"
                )
            except Exception:
                pass
            return

        decision = response.get("decision") if isinstance(response, dict) else None
        try:
            if decision == "approve":
                await rt.approve_tool_call(
                    call_id, always=bool(response.get("always_approve"))
                )
            else:
                await rt.reject_tool_call(
                    call_id,
                    rejection_message=(
                        response.get("rejection_message")
                        if isinstance(response, dict)
                        else None
                    ),
                )
        except Exception:
            logger.exception(
                "voice tool approval decision not applied",
                extra={"session": self.session_id},
            )

    async def send_audio(self, audio_base64: str, commit: bool = False) -> None:
        """Send audio input to the realtime agent.

        Args:
            audio_base64: Base64-encoded audio data
            commit: If True, commit the audio and trigger response generation

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        # Decode base64 audio
        audio_bytes = base64.b64decode(audio_base64)

        # Send to realtime session
        # commit=False means streaming (buffer audio)
        # commit=True means commit and trigger response
        await self._realtime_session.send_audio(audio_bytes, commit=commit)

    async def send_text(self, text: str) -> None:
        """Send text input to the realtime agent.

        Args:
            text: Text message to send

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        # Send message to session (accepts string or structured message)
        await self._realtime_session.send_message(text)

    async def interrupt(self) -> None:
        """Interrupt the current agent response.

        Raises:
            RuntimeError: If session not connected
        """
        if not self._realtime_session:
            raise RuntimeError("Session not connected. Call connect() first.")

        await self._realtime_session.interrupt()

    async def stop(self) -> None:
        """Stop the realtime session and clean up resources."""
        self._active = False

        # A teardown mid-turn still keeps what was said: persist any
        # accumulated assistant transcript (nobody is left to receive the
        # final events, so this is persistence-only).
        try:
            self._flush_assistant_transcripts()
        except Exception:
            pass

        for task in list(self._approval_tasks):
            task.cancel()
        self._approval_tasks.clear()

        shutdown_event = self._mcp_shutdown
        if shutdown_event:
            shutdown_event.set()
        lifecycle_task = self._mcp_lifecycle_task
        if lifecycle_task:
            try:
                await lifecycle_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._mcp_shutdown = None
        self._mcp_lifecycle_task = None
        self._mcp_servers_active = []

        # Cancel event streaming task if running
        if self._event_task and not self._event_task.done():
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass

        # Exit the realtime session's async context manager
        if self._realtime_session:
            try:
                await self._realtime_session.__aexit__(None, None, None)
            except Exception:
                pass
            self._realtime_session = None

        self._runner = None
        self._agent = None
        self._run_id = None

    @asynccontextmanager
    async def managed(self):
        """Context manager for automatic session lifecycle management.

        Example:
            async with session.managed():
                await session.connect()
                async for event in session.stream_events():
                    # Process events
                    pass
        """
        try:
            await self.start()
            yield self
        finally:
            await self.stop()
