"""Audio-storage backend selection tests.

The Azure path is covered structurally — fully exercising it needs a live
storage account and is left to integration / smoke testing."""

from __future__ import annotations

import pytest

from omniagents.core.session.audio_storage import (
    AudioStorage,
    AzureBlobAudioStorage,
    LocalAudioStorage,
    build_audio_storage,
)


def test_audio_storage_alias_matches_local():
    """``AudioStorage`` stays as a name alias so ``AudioStorage(base_path=...)``
    constructors in pre-refactor consumer code keep working."""
    assert AudioStorage is LocalAudioStorage


def test_build_audio_storage_defaults_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    monkeypatch.delenv("OMNIAGENTS_AUDIO_BACKEND", raising=False)
    storage = build_audio_storage("p", "a")
    assert isinstance(storage, LocalAudioStorage)


def test_build_audio_storage_unknown_backend_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIAGENTS_AUDIO_BACKEND", "made-up")
    storage = build_audio_storage("p", "a")
    assert isinstance(storage, LocalAudioStorage)


def test_build_audio_storage_blob_requires_env(monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_AUDIO_BACKEND", "blob")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("OMNI_AZURE_AUDIO_CONTAINER", raising=False)
    with pytest.raises(RuntimeError, match="OMNIAGENTS_AUDIO_BACKEND=blob requires"):
        build_audio_storage("p", "a")


def test_build_audio_storage_blob_constructs_when_env_set(monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_AUDIO_BACKEND", "blob")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "k")
    monkeypatch.setenv("OMNI_AZURE_AUDIO_CONTAINER", "audio")
    storage = build_audio_storage("p", "a")
    assert isinstance(storage, AzureBlobAudioStorage)


@pytest.mark.asyncio
async def test_local_audio_storage_round_trip(tmp_path):
    storage = LocalAudioStorage(base_path=tmp_path)
    rel = await storage.save_audio_chunk("sess", "item", 0, b"hello", audio_format="pcm16")
    assert rel == "sess/item_0.pcm16"
    assert await storage.get_audio_chunk(rel) == b"hello"
    assert await storage.get_session_audio_size("sess") == 5
    assert await storage.verify_audio_chunk(rel) is True
    assert await storage.delete_session_audio("sess") == 1
    assert await storage.get_session_audio_size("sess") == 0
