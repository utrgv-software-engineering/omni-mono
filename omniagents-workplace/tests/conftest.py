import socket
import threading
import time

import httpx
import pytest
import uvicorn

from omniagents_workplace.server import Workplace, create_app, load_seed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_SERVED: dict = {}


def factory_env(base_url: str) -> dict:
    """The environment the Acme factory connects to the workplace at ``base_url`` with."""
    workplace = _SERVED[base_url]
    return workplace.apps.env(workplace.apps.by_username("acme-factory"))


def signed_in(
    workplace: Workplace, base_url: str, username: str = "admin"
) -> httpx.Client:
    """A client of the workplace's UI, signed in as ``username``."""
    from omniagents_workplace.accounts import SESSION_COOKIE

    account = workplace.accounts.by_username(username)
    assert account is not None, f"no account {username}"
    return httpx.Client(
        base_url=base_url,
        cookies={SESSION_COOKIE: workplace.accounts.start_session(account)},
    )


def assert_persisted(workplace: Workplace) -> None:
    """A workplace started on ``workplace``'s data directory holds what it held."""
    held = workplace.state()
    reopened = Workplace(workplace.data_dir, base_url=workplace.base_url)
    try:
        loaded = reopened.state()
    finally:
        reopened.close()
    assert loaded == held, f"not persisted: {_first_difference(held, loaded)}"


def _first_difference(a, b, path="") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            if key not in a or key not in b:
                return f"{path}/{key}: {'only in memory' if key in a else 'only in the store'}"
            if a[key] != b[key]:
                return _first_difference(a[key], b[key], f"{path}/{key}")
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return _first_difference(x, y, f"{path}[{i}]")
    return f"{path}: {a!r} in memory, {b!r} in the store"


@pytest.fixture
def workplace_server(tmp_path):
    """A running workplace with the Acme seed: ``(workplace, base_url)``.

    When the test is done, the workplace's state is checked against what a
    workplace started on its data directory loads: every change a test makes
    is one the store must keep.
    """
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    workplace = Workplace(tmp_path / "data", base_url=base_url, seed=load_seed())
    workplace.delivery.retry_delays = (0.0, 0.0, 0.0)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(workplace, static_dir=tmp_path), port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        try:
            httpx.get(f"{base_url}/workplace/api/session", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.02)
    _SERVED[base_url] = workplace
    yield workplace, base_url
    _SERVED.pop(base_url, None)
    server.should_exit = True
    thread.join(5)
    workplace.close()
    assert_persisted(workplace)
