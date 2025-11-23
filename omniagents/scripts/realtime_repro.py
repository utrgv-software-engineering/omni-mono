from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from openai import AsyncOpenAI

from omniagents.rpc.client import JsonRpcClient


def _load_env() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    explicit_path = repo_root / ".env"
    examples_path = repo_root / "examples" / ".env"

    if explicit_path.exists():
        load_dotenv(explicit_path)
    if examples_path.exists():
        load_dotenv(examples_path, override=True)


def _rpc_ws_url() -> str | None:
    url = (
        os.getenv("REALTIME_WS_URL")
        or os.getenv("REALTIME_WS_ENDPOINT")
        or os.getenv("OMNI_REALTIME_WS_URL")
        or os.getenv("OMNI_REMOTE_WS_URL")
    )
    if not url:
        return None

    token = os.getenv("REALTIME_AUTH_TOKEN") or os.getenv("OMNI_REALTIME_TOKEN")
    if token and "token=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={token}"

    return url


def _infer_azure_endpoint() -> tuple[str, str]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        base = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        if base and "openai.azure.com" in base:
            marker = "/openai/"
            idx = base.lower().find(marker)
            endpoint = base[:idx] if idx != -1 else base
    if not endpoint:
        raise RuntimeError(
            "Set AZURE_OPENAI_ENDPOINT (or OPENAI_BASE_URL pointing at Azure) in the .env file"
        )
    scheme = "wss://"
    host = endpoint.rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    elif host.startswith("http://"):
        scheme = "ws://"
        host = host[len("http://") :]
    return scheme, host


def _realtime_path_candidates(deployment: str, api_version: str) -> Iterable[str]:
    explicit_path = os.getenv("AZURE_OPENAI_REALTIME_PATH")
    if explicit_path:
        yield explicit_path
        return

    env_url = os.getenv("OPENAI_REALTIME_BASE_URL")
    base_candidates = [
        c for c in (env_url, "/ws/v1", "/openai/v1", "/openai/realtime") if c
    ]
    base_candidates.extend(
        [
            f"/openai/v1?api-version={api_version}",
            f"/openai/realtime?api-version={api_version}",
            f"/openai/deployments/{deployment}/realtime",
            f"/openai/deployments/{deployment}/realtime?api-version={api_version}",
        ]
    )
    for path in base_candidates:
        yield path


def _build_async_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        websocket_base_url=base_url,
    )


def _resolve_deployment() -> str:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    if not deployment:
        deployment = (
            os.getenv("OPENAI_REALTIME_DEPLOYMENT")
            or os.getenv("OPENAI_REALTIME_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("MODEL_NAME")
        )
    if not deployment:
        deployment = "gpt-realtime"
    return deployment


def _resolve_api_key() -> str:
    api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set AZURE_OPENAI_API_KEY or OPENAI_API_KEY in the .env file"
        )
    return api_key


async def _rpc_repro(url: str) -> None:
    print(f"Connecting to RPC server at {url}")
    client = JsonRpcClient.from_url(url)
    async with client:
        result = await client.call("start_session")
        print("start_session result:", result)


async def _try_azure_candidate(base_url: str, deployment: str, api_key: str) -> bool:
    print(f"→ Trying Azure realtime base: {base_url}")
    client = _build_async_openai_client(base_url, api_key)
    try:
        async with client.realtime.connect(model=deployment) as connection:
            print("  Connected. Sending ping message...")
            await connection.session.update(
                session={
                    "instructions": "Connectivity probe",
                    "modalities": ["text"],
                }
            )
            await connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Hello from omniagents realtime repro script.",
                        }
                    ],
                }
            )
            await connection.response.create()
            async for event in connection:
                print(f"  Event: {event.type}")
                if event.type in {
                    "response.output_text.done",
                    "response.done",
                    "response.completed",
                }:
                    print("  Response completed.")
                    break
        print("  SUCCESS ✅")
        return True
    except Exception as exc:
        print(f"  FAILED ❌ ({exc})")
        return False


def _format_base_url(scheme: str, host: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"{scheme}{host}{path}"


async def _azure_repro() -> None:
    deployment = _resolve_deployment()
    api_key = _resolve_api_key()
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21-preview")
    scheme, host = _infer_azure_endpoint()

    print(f"Testing Azure realtime connectivity for deployment '{deployment}'")
    print(f"Using API version: {api_version}")

    any_success = False
    for path in _realtime_path_candidates(deployment, api_version):
        if "://" in path:
            base_url = path
        else:
            base_url = _format_base_url(scheme, host, path)
        ok = await _try_azure_candidate(base_url, deployment, api_key)
        if ok:
            any_success = True
            break

    if not any_success:
        raise RuntimeError("All Azure realtime connection attempts failed.")


async def _main() -> None:
    _load_env()
    ws_url = _rpc_ws_url()
    if ws_url:
        print("Detected RPC websocket URL from environment.")
        await _rpc_repro(ws_url)
    else:
        print("No RPC URL configured; attempting direct Azure realtime connection...")
        await _azure_repro()


if __name__ == "__main__":
    asyncio.run(_main())
