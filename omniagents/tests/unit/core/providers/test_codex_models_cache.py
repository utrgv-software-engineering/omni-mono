"""Tests for Codex model-discovery degradation
(``core/providers/codex.list_codex_models``).

A failed live fetch must serve the last-known-good model set (memory,
then disk) instead of ``None`` — an unresolvable default model strips
worker sessions of provider metadata (codex prompt-cache gate, context
window), which surfaced as workers 400ing on ``prompt_cache_retention``.
"""

from __future__ import annotations

import time

import pytest

import omniagents.core.providers.codex as codex


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    codex.set_token_store_dir(tmp_path)
    monkeypatch.setattr(codex, "_models_cache", {})
    monkeypatch.setattr(
        codex, "load_tokens", lambda: {"refresh": "r", "account_id": "acct"}
    )
    yield
    codex._token_store_dir = None


MODELS = {"gpt-5.6-sol": {"model": "gpt-5.6-sol", "max_input_tokens": 272000}}


def _fail_refresh(monkeypatch):
    monkeypatch.setattr(codex, "_ensure_fresh_sync", lambda: None)


def test_failed_fetch_serves_expired_memory_cache(monkeypatch):
    codex._models_cache["acct"] = {"models": MODELS, "expires_at": time.time() - 1}
    _fail_refresh(monkeypatch)
    assert codex.list_codex_models() == MODELS


def test_failed_fetch_serves_disk_cache_on_cold_start(monkeypatch):
    codex._save_disk_models("acct", MODELS)
    _fail_refresh(monkeypatch)
    assert codex.list_codex_models() == MODELS


def test_failed_fetch_with_no_prior_state_returns_none(monkeypatch):
    _fail_refresh(monkeypatch)
    assert codex.list_codex_models() is None


def test_successful_fetch_persists_to_disk(monkeypatch):
    import httpx

    monkeypatch.setattr(
        codex, "_ensure_fresh_sync", lambda: {"access": "a", "account_id": "acct"}
    )

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "models": [
                    {"slug": "gpt-5.6-sol", "visibility": "list"},
                    {"slug": "hidden-model", "visibility": "hidden"},
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    live = codex.list_codex_models(force=True)
    assert live is not None and "gpt-5.6-sol" in live and "hidden-model" not in live
    # The disk copy now backs future cold-start failures.
    assert codex._load_disk_models("acct") == live
