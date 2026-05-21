"""Translate OmniAgents YAML ``sandbox:`` blocks into SDK sandbox objects.

The SDK already exposes Pydantic models for manifests, manifest entries,
capabilities and snapshot specs, with a ``type`` discriminator. This factory
is therefore intentionally thin: it dispatches on the YAML ``client.type`` to
build the concrete sandbox client, then hands raw mappings to the SDK
validators wherever possible (``BaseEntry.parse``, ``Capability`` subclasses,
``SnapshotSpec`` subclasses).

YAML schema (all fields optional unless marked required)::

    sandbox:
      use_sandbox_agent: false        # opt-in: build SandboxAgent instead of Agent
      run_as: "1000:1000"            # default user identity for sandbox tools
      client:
        type: unix_local | docker | e2b | modal | runloop |
              daytona | blaxel | vercel | cloudflare        # required
        # remaining keys are forwarded as kwargs to the matching
        # ``*SandboxClient`` constructor (provider-specific)
        cap_add: ["NET_ADMIN"]        # docker: extra capabilities
        security_opt: ["seccomp=unconfined"]  # docker: relax seccomp (required for iptables-nft)
        network_mode: "none"          # docker: container network mode
      options:                        # optional client options dict
        # forwarded to the matching ``*SandboxClientOptions``
        image: my/image:tag           # docker
        exposed_ports: [8000, 8080]
      manifest:
        root: /workspace
        environment:
          value: { FOO: bar }
        entries:
          repo:
            type: git_repo
            host: github.com
            repo: openai/openai-python
            ref: main
          data:
            type: local_dir
            src: ./data
      snapshot:
        type: local | noop | remote
        # type-specific kwargs (base_path, client_dependency_key, ...)
      concurrency_limits:
        manifest_entries: 4
        local_dir_files: 8
      capabilities:
        - filesystem
        - shell
        - skills:
            skills_path: .agents
            lazy_from:
              source:
                type: local_dir
                src: ./skills
        - memory
        - compaction:
            policy:
              type: static
              threshold: 240000

Each provider client and each capability is opt-in. Provider clients require
their own pip extras (``openai-agents[e2b]`` etc.); attempting to use one
without the extra raises a clear ``ImportError`` describing how to install it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -------------------------- Sandbox clients --------------------------- #


def _missing_extra_message(client_type: str, extra: str) -> str:
    return (
        f"Sandbox client '{client_type}' requires the '{extra}' extra. "
        f"Install it with: pip install omniagents[{extra}]"
    )


def _build_unix_local_client(client_kwargs: Dict[str, Any]) -> Any:
    # ExtendedUnixLocalSandboxClient adds pty_stream_output / pty_resize on
    # the session it creates. Drop-in replacement for the SDK class — the
    # extra capability is invisible to non-streaming callers.
    from omniagents.core.sandbox.unix_local_extended import (
        ExtendedUnixLocalSandboxClient,
    )

    return ExtendedUnixLocalSandboxClient(**client_kwargs)


def _build_unix_local_options(options: Dict[str, Any]) -> Any:
    from agents.sandbox.sandboxes import UnixLocalSandboxClientOptions  # type: ignore

    exposed = tuple(options.get("exposed_ports") or ())
    return UnixLocalSandboxClientOptions(exposed_ports=exposed)


def _build_docker_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        import docker as docker_sdk  # type: ignore
    except Exception as e:  # pragma: no cover - exercised when extra missing
        raise ImportError(_missing_extra_message("docker", "sandbox-docker")) from e

    from omniagents.core.sandbox.docker_extended import BindSpec, ExtendedDockerSandboxClient

    # YAML-driven docker clients always come from the local engine. Callers
    # who need a custom docker SDK client can construct the sandbox client
    # in Python directly. ``cap_add`` and friends are routed here
    # (factory's ``options:`` block stripped them out of the SDK's options
    # since they're not first-class SDK fields).
    cap_add = tuple(client_kwargs.pop("cap_add", ()))
    network_mode = client_kwargs.pop("network_mode", None)
    security_opt = tuple(client_kwargs.pop("security_opt", ()))
    user = client_kwargs.pop("user", None)
    binds_raw = client_kwargs.pop("binds", None) or []
    binds = _parse_binds(binds_raw)
    docker_client = client_kwargs.pop("docker_client", None) or docker_sdk.from_env()

    return ExtendedDockerSandboxClient(
        docker_client,
        cap_add=cap_add,
        network_mode=network_mode,
        security_opt=security_opt,
        user=user,
        binds=binds,
        **client_kwargs,
    )


def _parse_binds(raw: Any) -> list[Any]:
    """Parse the YAML ``client.binds:`` list into ``BindSpec`` instances.

    Accepts a list of mappings: ``[{source: ..., target: ..., read_only: ...}, ...]``.
    Required keys: ``source``, ``target``. ``read_only`` defaults False.
    """
    from omniagents.core.sandbox.docker_extended import BindSpec

    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("sandbox.client.binds must be a list")
    out: list[Any] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"sandbox.client.binds[{idx}] must be a mapping")
        source = item.get("source")
        target = item.get("target")
        if not isinstance(source, str) or not source:
            raise ValueError(f"sandbox.client.binds[{idx}].source is required (string)")
        if not isinstance(target, str) or not target:
            raise ValueError(f"sandbox.client.binds[{idx}].target is required (string)")
        out.append(BindSpec(source=source, target=target, read_only=bool(item.get("read_only", False))))
    return out


def _build_docker_options(options: Dict[str, Any]) -> Any:
    from agents.sandbox.sandboxes import DockerSandboxClientOptions  # type: ignore

    image = options.get("image")
    if not image:
        raise ValueError("sandbox.options.image is required for docker clients")
    exposed = tuple(options.get("exposed_ports") or ())
    return DockerSandboxClientOptions(image=image, exposed_ports=exposed)


def _build_e2b_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import E2BSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(_missing_extra_message("e2b", "sandbox-e2b")) from e
    return E2BSandboxClient(**client_kwargs)


def _build_e2b_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import E2BSandboxClientOptions  # type: ignore

    return E2BSandboxClientOptions(**options)


def _build_modal_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import ModalSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(_missing_extra_message("modal", "sandbox-modal")) from e
    return ModalSandboxClient(**client_kwargs)


def _build_modal_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import ModalSandboxClientOptions  # type: ignore

    return ModalSandboxClientOptions(**options)


def _build_runloop_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import RunloopSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(_missing_extra_message("runloop", "sandbox-runloop")) from e
    return RunloopSandboxClient(**client_kwargs)


def _build_runloop_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import RunloopSandboxClientOptions  # type: ignore

    return RunloopSandboxClientOptions(**options)


def _build_daytona_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import DaytonaSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(_missing_extra_message("daytona", "sandbox-daytona")) from e
    return DaytonaSandboxClient(**client_kwargs)


def _build_daytona_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import DaytonaSandboxClientOptions  # type: ignore

    return DaytonaSandboxClientOptions(**options)


def _build_blaxel_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import BlaxelSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(_missing_extra_message("blaxel", "sandbox-blaxel")) from e
    return BlaxelSandboxClient(**client_kwargs)


def _build_blaxel_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import BlaxelSandboxClientOptions  # type: ignore

    return BlaxelSandboxClientOptions(**options)


def _build_vercel_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import VercelSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(_missing_extra_message("vercel", "sandbox-vercel")) from e
    return VercelSandboxClient(**client_kwargs)


def _build_vercel_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import VercelSandboxClientOptions  # type: ignore

    return VercelSandboxClientOptions(**options)


def _build_cloudflare_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from agents.extensions.sandbox import CloudflareSandboxClient  # type: ignore
    except Exception as e:
        raise ImportError(
            _missing_extra_message("cloudflare", "sandbox-cloudflare")
        ) from e
    return CloudflareSandboxClient(**client_kwargs)


def _build_cloudflare_options(options: Dict[str, Any]) -> Any:
    from agents.extensions.sandbox import CloudflareSandboxClientOptions  # type: ignore

    return CloudflareSandboxClientOptions(**options)


def _build_aci_client(client_kwargs: Dict[str, Any]) -> Any:
    try:
        from omniagents.core.sandbox.aci import AciSandboxClient
    except Exception as e:  # pragma: no cover - exercised when extra missing
        raise ImportError(_missing_extra_message("aci", "sandbox-aci")) from e
    return AciSandboxClient(**client_kwargs)


def _build_aci_options(options: Dict[str, Any]) -> Any:
    from omniagents.core.sandbox.aci import AciSandboxClientOptions

    return AciSandboxClientOptions(**options)


# Each entry: (client_factory, options_factory_or_None)
_CLIENT_BUILDERS: Dict[
    str,
    Tuple[Callable[[Dict[str, Any]], Any], Optional[Callable[[Dict[str, Any]], Any]]],
] = {
    "unix_local": (_build_unix_local_client, _build_unix_local_options),
    "docker": (_build_docker_client, _build_docker_options),
    "e2b": (_build_e2b_client, _build_e2b_options),
    "modal": (_build_modal_client, _build_modal_options),
    "runloop": (_build_runloop_client, _build_runloop_options),
    "daytona": (_build_daytona_client, _build_daytona_options),
    "blaxel": (_build_blaxel_client, _build_blaxel_options),
    "vercel": (_build_vercel_client, _build_vercel_options),
    "cloudflare": (_build_cloudflare_client, _build_cloudflare_options),
    "aci": (_build_aci_client, _build_aci_options),
}


def build_sandbox_client(
    config: Optional[Dict[str, Any]],
) -> Tuple[Optional[Any], Optional[Any]]:
    """Build a ``(client, options)`` pair from the YAML ``sandbox.client`` block.

    *config* is the raw ``sandbox:`` mapping (top-level), not just the
    ``client:`` sub-block — passing the parent gives us access to the
    sibling ``options:`` block.

    Returns ``(None, None)`` when no sandbox client is configured.
    """
    if not config:
        return None, None
    client_block = config.get("client")
    if not isinstance(client_block, dict):
        return None, None
    client_type = client_block.get("type")
    if not isinstance(client_type, str):
        raise ValueError("sandbox.client.type is required and must be a string")

    builder = _CLIENT_BUILDERS.get(client_type)
    if builder is None:
        raise ValueError(
            f"Unsupported sandbox.client.type: {client_type!r}. "
            f"Supported: {sorted(_CLIENT_BUILDERS)}"
        )

    client_kwargs = {k: v for k, v in client_block.items() if k != "type"}
    client_factory, options_factory = builder
    client = client_factory(client_kwargs)

    options_block = config.get("options")
    options: Optional[Any] = None
    if isinstance(options_block, dict) and options_factory is not None:
        options = options_factory(options_block)

    return client, options


# ----------------------------- Manifest ------------------------------ #


def build_manifest(config: Optional[Dict[str, Any]]) -> Optional[Any]:
    """Build a :class:`agents.sandbox.Manifest` from the YAML ``manifest:`` block.

    *config* is the raw ``sandbox:`` mapping (top-level). Returns ``None`` when
    no manifest is configured.

    Manifest entries are passed straight through ``Manifest.model_validate``
    which dispatches to the correct ``BaseEntry`` subclass via the ``type``
    field (``git_repo``, ``local_dir``, ``local_file``, ``s3_mount``,
    ``gcs_mount``, etc.). See ``agents.sandbox.entries`` for the list.
    """
    if not config:
        return None
    manifest_block = config.get("manifest")
    if not isinstance(manifest_block, dict):
        return None

    from agents.sandbox import Manifest  # type: ignore

    return Manifest.model_validate(manifest_block)


# --------------------------- Capabilities ---------------------------- #


def _import_capability_classes() -> Dict[str, Any]:
    from agents.sandbox.capabilities import (  # type: ignore
        Compaction,
        Filesystem,
        Memory,
        Shell,
        Skills,
    )

    return {
        "filesystem": Filesystem,
        "shell": Shell,
        "skills": Skills,
        "memory": Memory,
        "compaction": Compaction,
    }


def _coerce_capability(item: Any) -> Any:
    """Coerce a single YAML capability entry into a ``Capability`` instance.

    Each entry may be one of:

    * a plain string (``"filesystem"``)
    * a single-key mapping (``{"skills": {...}}``)
    * an explicit dict with a ``type`` key (``{"type": "skills", ...}``)
    """
    classes = _import_capability_classes()

    if isinstance(item, str):
        cls = classes.get(item)
        if cls is None:
            raise ValueError(
                f"Unknown sandbox capability: {item!r}. "
                f"Supported: {sorted(classes)}"
            )
        return cls()

    if isinstance(item, dict):
        if "type" in item:
            cap_type = item["type"]
            cls = classes.get(cap_type)
            if cls is None:
                raise ValueError(
                    f"Unknown sandbox capability type: {cap_type!r}. "
                    f"Supported: {sorted(classes)}"
                )
            return cls.model_validate(item)
        # Single-key shorthand: {"skills": {...}}
        if len(item) == 1:
            cap_type, payload = next(iter(item.items()))
            cls = classes.get(cap_type)
            if cls is None:
                raise ValueError(
                    f"Unknown sandbox capability: {cap_type!r}. "
                    f"Supported: {sorted(classes)}"
                )
            payload = payload or {}
            if not isinstance(payload, dict):
                raise ValueError(
                    f"sandbox capability {cap_type!r} payload must be a dict, "
                    f"got {type(payload).__name__}"
                )
            return cls.model_validate({"type": cap_type, **payload})

    raise ValueError(
        f"Invalid sandbox capability entry: {item!r}. "
        "Expected string, single-key mapping, or dict with 'type'."
    )


def _build_capabilities(items: Optional[Iterable[Any]]) -> Optional[List[Any]]:
    if items is None:
        return None
    return [_coerce_capability(item) for item in items]


# ------------------------------ Snapshot ----------------------------- #


def _build_snapshot(spec: Optional[Dict[str, Any]]) -> Optional[Any]:
    if not isinstance(spec, dict):
        return None
    snap_type = spec.get("type")
    if not isinstance(snap_type, str):
        raise ValueError("sandbox.snapshot.type must be a string")

    from agents.sandbox import (  # type: ignore
        LocalSnapshotSpec,
        RemoteSnapshotSpec,
    )
    from agents.sandbox.snapshot import NoopSnapshotSpec  # type: ignore

    if snap_type == "local":
        return LocalSnapshotSpec.model_validate(spec)
    if snap_type == "remote":
        return RemoteSnapshotSpec.model_validate(spec)
    if snap_type == "noop":
        return NoopSnapshotSpec.model_validate(spec)
    raise ValueError(
        f"Unsupported sandbox.snapshot.type: {snap_type!r}. "
        "Supported: local | remote | noop"
    )


# ------------------------- Concurrency limits ------------------------ #


def _build_concurrency_limits(spec: Optional[Dict[str, Any]]) -> Optional[Any]:
    if not isinstance(spec, dict):
        return None
    from agents.run_config import SandboxConcurrencyLimits  # type: ignore

    kwargs: Dict[str, Any] = {}
    if "manifest_entries" in spec:
        kwargs["manifest_entries"] = spec["manifest_entries"]
    if "local_dir_files" in spec:
        kwargs["local_dir_files"] = spec["local_dir_files"]
    return SandboxConcurrencyLimits(**kwargs)


# --------------------------- SandboxRunConfig ------------------------ #


def build_sandbox_run_config(
    config: Optional[Dict[str, Any]],
) -> Optional[Any]:
    """Build a :class:`agents.run_config.SandboxRunConfig` from a YAML block.

    Returns ``None`` when *config* is empty or contains no actionable settings.
    The resulting object is meant to be passed to ``RunConfig(sandbox=...)``
    by the OmniAgents runtime bridge.

    Capabilities resolved from the YAML block are *also* meant to be applied
    to a :class:`~agents.sandbox.SandboxAgent` instance (the SDK's preferred
    home for capabilities). The current factory only sets fields that
    ``SandboxRunConfig`` accepts; capability handling is delegated to the
    builder when ``use_sandbox_agent: true``.
    """
    if not config:
        return None

    from agents.run_config import SandboxRunConfig  # type: ignore

    client, options = build_sandbox_client(config)
    manifest = build_manifest(config)
    snapshot = _build_snapshot(config.get("snapshot"))
    concurrency_limits = _build_concurrency_limits(config.get("concurrency_limits"))

    has_anything = any(
        value is not None
        for value in (client, options, manifest, snapshot, concurrency_limits)
    )
    if not has_anything:
        return None

    kwargs: Dict[str, Any] = {}
    if client is not None:
        kwargs["client"] = client
    if options is not None:
        kwargs["options"] = options
    if manifest is not None:
        kwargs["manifest"] = manifest
    if snapshot is not None:
        kwargs["snapshot"] = snapshot
    if concurrency_limits is not None:
        kwargs["concurrency_limits"] = concurrency_limits
    return SandboxRunConfig(**kwargs)


# ---------------------- Capability bundle for builder ---------------- #


def build_sandbox_capabilities(config: Optional[Dict[str, Any]]) -> Optional[List[Any]]:
    """Resolve the YAML ``capabilities:`` list into ``Capability`` instances.

    Returns ``None`` when no capabilities block is present, indicating the
    SDK default (``Filesystem`` + ``Shell`` + ``Compaction``) should apply.
    Returns ``[]`` when an explicit empty list was given.
    """
    if not config:
        return None
    raw = config.get("capabilities")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("sandbox.capabilities must be a list")
    return _build_capabilities(raw)
