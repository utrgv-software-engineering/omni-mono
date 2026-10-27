"""The factory's model, configured from the workplace, as omni-desktop configures its own.

Two parts, as the desktop has them (``omni-desktop/src/lib/provider-config.ts``,
``shared/model-catalog.ts``, ``main/local-codex-account.ts``):

- The factory's ``models.json`` (version 3: providers, their models, the
  default), shared with the factory as the desktop shares it with the CLI:
  the workplace writes it, the factory reads it on every run. An API key is
  kept there, in the provider, so it lasts across restarts.
- Signing in with ChatGPT (Codex) goes through the factory's runtime, which
  owns the token store: its ``account_login_start`` (device code) and
  ``account_login_complete`` calls, over its JSON-RPC WebSocket (``/ws``,
  with its auth token).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

__all__ = [
    "FactoryModels",
    "FactoryRpc",
    "FactoryRpcError",
    "ModelsError",
    "model_ref",
    "resolve_model_choices",
    "validate_provider",
    "CHATGPT_PROVIDER_ID",
]

CHATGPT_PROVIDER_ID = "openai-chatgpt"
MODELS_VERSION = 3
PROBE_TIMEOUT = 10.0
DEFAULT_MAX_INPUT_TOKENS = 272000
DEFAULT_MAX_OUTPUT_TOKENS = 128000

#: The desktop's curated catalog (shared/model-catalog.ts): labels, blurbs and real limits.
CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "openai": [
        {
            "id": "gpt-6-luna",
            "label": "GPT-6 Luna",
            "blurb": "Best for everyday use",
            "recommended": True,
            "maxInput": 272000,
            "maxOutput": 128000,
        },
        {
            "id": "gpt-5.5",
            "label": "GPT-5.5",
            "blurb": "Previous generation",
            "maxInput": 272000,
            "maxOutput": 128000,
        },
        {
            "id": "gpt-5.5-codex",
            "label": "GPT-5.5 Codex",
            "blurb": "Tuned for coding and agents",
            "maxInput": 272000,
            "maxOutput": 128000,
        },
        {
            "id": "gpt-5.1",
            "label": "GPT-5.1",
            "blurb": "Previous generation, lower cost",
            "maxInput": 272000,
            "maxOutput": 128000,
        },
        {
            "id": "gpt-5.1-mini",
            "label": "GPT-5.1 mini",
            "blurb": "Fast and inexpensive",
            "maxInput": 272000,
            "maxOutput": 128000,
        },
    ],
    "anthropic": [
        {
            "id": "claude-fable-5",
            "label": "Claude Fable 5",
            "blurb": "Best for everyday use",
            "recommended": True,
            "maxInput": 200000,
            "maxOutput": 64000,
        },
        {
            "id": "claude-opus-4-8",
            "label": "Claude Opus 4.8",
            "blurb": "Deep reasoning and long tasks",
            "maxInput": 200000,
            "maxOutput": 64000,
        },
        {
            "id": "claude-sonnet-4-6",
            "label": "Claude Sonnet 4.6",
            "blurb": "Balanced speed and capability",
            "maxInput": 200000,
            "maxOutput": 64000,
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "label": "Claude Haiku 4.5",
            "blurb": "Fast and inexpensive",
            "maxInput": 200000,
            "maxOutput": 64000,
        },
    ],
}

#: Realtime voice models a ChatGPT sign-in can use (provider-config.ts's CODEX_REALTIME_MODELS).
CODEX_REALTIME_MODELS: Dict[str, Dict[str, Any]] = {
    key: {"model": key, "label": label, "realtime": True}
    for key, label in (
        ("gpt-realtime-2.1", "Realtime 2.1"),
        ("gpt-realtime-2.1-mini", "Realtime 2.1 Mini"),
        ("gpt-realtime-2", "Realtime 2"),
        ("gpt-realtime", "Realtime"),
        ("gpt-realtime-1.5", "Realtime 1.5"),
        ("gpt-realtime-mini", "Realtime Mini"),
    )
}
CODEX_DEFAULT_VOICE_MODEL = "codex/gpt-realtime-2.1"

#: Provider-entry names the flows write: stable, so doing it again replaces rather than duplicates.
PROVIDER_NAMES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "ollama": "local",
    "openai-compatible": "local",
}
KINDS = tuple(PROVIDER_NAMES)


class ModelsError(Exception):
    pass


class FactoryRpcError(Exception):
    pass


def normalize_compatible_base_url(url: str) -> str:
    trimmed = url.strip().rstrip("/")
    return trimmed if trimmed.endswith("/v1") else f"{trimmed}/v1"


def validate_provider(
    kind: str, api_key: str = "", base_url: str = ""
) -> Dict[str, Any]:
    """A key (or a server) works: the provider's model listing, as the desktop checks it."""
    headers: Dict[str, str] = {}
    if kind == "openai":
        url = "https://api.openai.com/v1/models"
        headers["Authorization"] = f"Bearer {api_key}"
    elif kind == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif kind == "ollama":
        if not base_url.strip():
            return {
                "ok": False,
                "code": "unknown",
                "detail": "Ollama's address is required",
            }
        url = f"{base_url.strip().rstrip('/')}/api/tags"
    elif kind == "openai-compatible":
        if not base_url.strip():
            return {"ok": False, "code": "unknown", "detail": "Base URL is required"}
        url = f"{normalize_compatible_base_url(base_url)}/models"
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
    else:
        return {"ok": False, "code": "unknown", "detail": f"No provider kind {kind!r}"}
    try:
        response = httpx.get(url, headers=headers, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        return {"ok": False, "code": "network", "detail": str(exc)}
    if response.status_code in (401, 403):
        return {
            "ok": False,
            "code": "unauthorized",
            "detail": f"HTTP {response.status_code}",
        }
    if response.status_code == 404:
        return {"ok": False, "code": "not-found", "detail": "HTTP 404"}
    if response.status_code >= 400:
        return {
            "ok": False,
            "code": "unknown",
            "detail": f"HTTP {response.status_code}",
        }
    try:
        body = response.json()
    except ValueError:
        return {
            "ok": False,
            "code": "unknown",
            "detail": "Unexpected response from provider",
        }
    if kind == "ollama":
        models = [
            m.get("name")
            for m in body.get("models") or []
            if isinstance(m.get("name"), str)
        ]
    else:
        models = [
            m.get("id") for m in body.get("data") or [] if isinstance(m.get("id"), str)
        ]
    return {"ok": True, "models": models}


def resolve_model_choices(kind: str, live: List[str]) -> List[Dict[str, Any]]:
    """The models to offer (model-catalog.ts's resolveModelChoices): the curated ones the
    provider has, in the catalog's order; for a compatible server, every model it lists.
    """
    if kind in CATALOG:
        curated = CATALOG[kind]
        if not live:
            return [{**m, "verified": False} for m in curated]
        have = set(live)
        chosen = [{**m, "verified": True} for m in curated if m["id"] in have]
        if chosen and not any(m.get("recommended") for m in chosen):
            chosen[0]["recommended"] = True
        return chosen
    return [
        {
            "id": m,
            "label": m,
            "blurb": "",
            "recommended": i == 0,
            "maxInput": DEFAULT_MAX_INPUT_TOKENS,
            "maxOutput": DEFAULT_MAX_OUTPUT_TOKENS,
            "verified": True,
        }
        for i, m in enumerate(live)
    ]


def model_ref(entry: Dict[str, Any]) -> str:
    """A runtime catalog entry's reference, ``<provider>/<model>``: its id, which leaves out the
    provider for the project's default one."""
    ident = str(entry.get("id") or "")
    provider = (entry.get("provider") or {}).get("name")
    return (
        ident
        if not provider or ident.startswith(f"{provider}/")
        else f"{provider}/{ident}"
    )


def mask_api_key(key: str) -> str:
    trimmed = key.strip()
    return "••••" if len(trimmed) <= 8 else f"{trimmed[:3]}…{trimmed[-4:]}"


class FactoryModels:
    """The factory's ``models.json``, in its config directory."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir).expanduser()
        self.path = self.config_dir / "models.json"

    def read(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {
                "version": MODELS_VERSION,
                "default": None,
                "voice_default": None,
                "providers": {},
            }
        config = json.loads(self.path.read_text(encoding="utf-8"))
        if config.get("version") != MODELS_VERSION or not isinstance(
            config.get("providers"), dict
        ):
            raise ModelsError(
                f"{self.path} is not a version-3 models.json: repair it first"
            )
        return config

    def write(self, config: Dict[str, Any]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.config_dir / f".models-{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(config, indent=2) + "\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def summary(self) -> Dict[str, Any]:
        """What the page shows: providers and their models, keys masked."""
        config = self.read()
        providers = []
        for name, provider in config["providers"].items():
            key = str(provider.get("api_key") or "")
            providers.append(
                {
                    "name": name,
                    "type": provider.get("type"),
                    "base_url": provider.get("base_url"),
                    "key": (
                        mask_api_key(key)
                        if key and not key.startswith("${")
                        else (key or None)
                    ),
                    "models": [
                        {
                            "ref": f"{name}/{k}",
                            "label": m.get("label") or k,
                            "realtime": bool(m.get("realtime")),
                        }
                        for k, m in (provider.get("models") or {}).items()
                    ],
                }
            )
        return {
            "path": str(self.path),
            "default": config.get("default"),
            "voice_default": config.get("voice_default"),
            "providers": providers,
        }

    def add_provider(
        self,
        kind: str,
        *,
        api_key: str = "",
        base_url: str = "",
        model: Dict[str, Any],
        make_default: bool = True,
    ) -> str:
        """A provider with its key and the chosen model (provider-config.ts's buildProviderConfig); its ref."""
        if kind not in KINDS:
            raise ModelsError(f"A provider is one of {', '.join(KINDS)}")
        model_id = str(model.get("id") or "").strip()
        if not model_id:
            raise ModelsError("Choose a model")
        config = self.read()
        name = PROVIDER_NAMES[kind]
        existing = config["providers"].get(name)
        provider = (
            {**existing, "models": dict(existing.get("models") or {})}
            if existing
            else {"models": {}}
        )
        if kind == "openai":
            provider["type"] = "openai"
        elif kind == "anthropic":
            provider["type"] = "litellm"
            provider["routing_prefix"] = "anthropic"
        else:
            if not base_url.strip():
                raise ModelsError("The server's address is required")
            provider["type"] = "openai-compatible"
            provider["base_url"] = normalize_compatible_base_url(base_url)
        if api_key.strip():
            provider["api_key"] = api_key.strip()
        entry: Dict[str, Any] = {
            # litellm routes by its own provider/model ref; native providers take the bare id.
            "model": f"anthropic/{model_id}" if kind == "anthropic" else model_id,
            "label": str(model.get("label") or model_id),
            "max_input_tokens": int(model.get("maxInput") or DEFAULT_MAX_INPUT_TOKENS),
            "max_output_tokens": int(
                model.get("maxOutput") or DEFAULT_MAX_OUTPUT_TOKENS
            ),
        }
        if kind == "openai":
            # reasoning.encrypted_content is the Responses API's only.
            entry["model_settings"] = {
                "store": False,
                "extra_body": {"include": ["reasoning.encrypted_content"]},
            }
        provider["models"][model_id] = entry
        config["providers"][name] = provider
        ref = f"{name}/{model_id}"
        if make_default or not config.get("default"):
            config["default"] = ref
        self.write(config)
        return ref

    def add_chatgpt(self, runtime_models: List[Dict[str, Any]]) -> Optional[str]:
        """After a ChatGPT sign-in (buildCodexConfig): the codex provider (its models come
        from the runtime's discovery), its voice models, and the default when nothing else is set up.
        """
        config = self.read()
        names = [
            model_ref(m)
            for m in runtime_models
            if (m.get("provider") or {}).get("type") == "openai-oauth"
            and not m.get("realtime")
        ]
        preferred = (
            next((n for n in names if n.endswith("/gpt-6-luna")), None)
            or next((n for n in names if n.endswith("/gpt-5.5")), None)
            or (names[0] if names else None)
        )
        others = any(name != "codex" for name in config["providers"])
        existing = config["providers"].get("codex") or {}
        config["providers"]["codex"] = {
            **existing,
            "type": "openai-oauth",
            "models": {**CODEX_REALTIME_MODELS, **(existing.get("models") or {})},
        }
        made = None
        if not others and preferred and not config.get("default"):
            config["default"] = made = preferred
        if not others and not config.get("voice_default"):
            config["voice_default"] = CODEX_DEFAULT_VOICE_MODEL
        self.write(config)
        return made

    def set_default(
        self, ref: str, runtime_models: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        config = self.read()
        provider, _, key = ref.partition("/")
        discovered = any(
            model_ref(m) == ref
            and not m.get("realtime")
            and (m.get("availability") or {}).get("available")
            for m in runtime_models or []
        )
        if not key or (
            not discovered
            and key
            not in ((config["providers"].get(provider) or {}).get("models") or {})
        ):
            raise ModelsError(f"No model {ref} in the factory's models.json")
        config["default"] = ref
        self.write(config)

    def remove_provider(self, name: str) -> None:
        config = self.read()
        if name not in config["providers"]:
            raise ModelsError(f"No provider {name}")
        del config["providers"][name]
        for key in ("default", "voice_default"):
            if str(config.get(key) or "").startswith(f"{name}/"):
                config[key] = None
        self.write(config)


class FactoryRpc:
    """The factory's runtime, as the desktop reaches its own: JSON-RPC over ``/ws``."""

    def __init__(self, url: str, token: Optional[str]):
        base = url.rstrip("/")
        self.base = base
        self.url = re.sub(r"^http", "ws", base) + "/ws"
        self.token = token

    async def http(self, method: str, path: str, timeout: float = 30.0) -> Any:
        """One of the factory server's HTTP routes (its schedules), with its token."""
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method, self.base + path, headers=headers
                )
        except httpx.HTTPError as exc:
            raise FactoryRpcError(
                f"The factory at {self.base} is not answering ({exc})"
            )
        if response.status_code == 401:
            raise FactoryRpcError(
                "The factory refused the workplace's token (factory.auth_token)"
            )
        if response.status_code == 404 and path.endswith("/run"):
            raise FactoryRpcError(response.json().get("error") or "No such schedule")
        if response.status_code == 404 and path.startswith("/integrations/schedules"):
            raise FactoryRpcError(
                "The factory has no schedules (none of its automations has one)"
            )
        if response.status_code == 404:
            raise FactoryRpcError(
                f"The factory's server has no {path.split('?')[0]}: "
                "it needs its integrations and a newer omniagents"
            )
        if response.status_code >= 400:
            raise FactoryRpcError(
                f"The factory said {response.status_code}: {response.text}"
            )
        return response.json()

    async def call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Any:
        import websockets

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        try:
            async with websockets.connect(
                self.url,
                additional_headers=headers,
                open_timeout=10,
                max_size=16 * 1024 * 1024,
            ) as ws:
                next_id = iter(range(1, 1_000_000))

                async def request(name: str, args: Dict[str, Any]) -> Any:
                    rid = next(next_id)
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": rid,
                                "method": name,
                                "params": args,
                            }
                        )
                    )
                    while True:
                        message = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                        if message.get("id") != rid:
                            continue  # notifications
                        if "error" in message:
                            error = message["error"] or {}
                            raise FactoryRpcError(str(error.get("message") or error))
                        return message.get("result")

                await request(
                    "initialize",
                    {
                        "protocol_version": "2.0.0",
                        "identity": {"name": "omniagents-workplace", "version": "0"},
                        "platform": {
                            "os": platform.system().lower(),
                            "arch": platform.machine().lower(),
                        },
                        "capabilities": {
                            "realtime": False,
                            "mcp_apps": False,
                            "client_functions": False,
                            "approvals": False,
                            "artifacts": False,
                            "replay": False,
                            "terminal": False,
                            "experimental_operations": [],
                            "disabled_notifications": [],
                        },
                    },
                )
                await ws.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "method": "initialized", "params": {}}
                    )
                )
                return await request(method, params or {})
        except FactoryRpcError:
            raise
        except (
            OSError,
            asyncio.TimeoutError,
            Exception,
        ) as exc:  # noqa: BLE001 - the factory is unreachable
            raise FactoryRpcError(f"The factory did not answer at {self.url}: {exc}")
