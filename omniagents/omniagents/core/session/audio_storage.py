"""Audio storage backends for realtime/voice agent sessions.

Two implementations:

  * :class:`LocalAudioStorage` — original behaviour. PCM chunks land in
    ``<base>/<session_id>/<item_id>_<index>.<format>`` on the local
    filesystem. Default everywhere except cloud.
  * :class:`AzureBlobAudioStorage` — opt-in for cloud deployments. The
    launcher container's disk is ephemeral on App Service, so PCM chunks
    instead go to a Blob container (one blob per chunk, named
    ``<session_id>/<item_id>_<index>.<format>``). Selected via
    ``OMNIAGENTS_AUDIO_BACKEND=blob`` + ``AZURE_STORAGE_ACCOUNT_NAME`` +
    ``AZURE_STORAGE_ACCOUNT_KEY`` + ``OMNI_AZURE_AUDIO_CONTAINER``.

``AudioStorage`` is kept as a name alias for ``LocalAudioStorage`` so
existing callers (and the SDK-style constructor ``AudioStorage(base_path=...)``)
keep working unchanged.

The ``save_audio_chunk`` method returns the *relative path* it wrote — for
the local backend that's a real filesystem-relative path, for the blob
backend it's the blob name. Either is opaque to ``history_db.save_audio_metadata``
which just stores the string; readers (UI playback, audit export) fetch
the bytes back through ``get_audio_chunk(relative_path)`` on the same
backend, so the abstraction is consistent end-to-end.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)


class AudioStorageBackend(ABC):
    """Interface for storing realtime-session audio chunks."""

    @abstractmethod
    async def save_audio_chunk(
        self,
        session_id: str,
        item_id: str,
        content_index: int,
        audio_data: bytes,
        audio_format: str = "pcm16",
    ) -> str:
        """Persist *audio_data* and return its backend-relative path/key."""

    @abstractmethod
    async def get_audio_chunk(self, relative_path: str) -> bytes:
        """Read an audio chunk back. Raises FileNotFoundError if missing."""

    @abstractmethod
    async def delete_session_audio(self, session_id: str) -> int:
        """Delete all chunks belonging to *session_id*. Returns count deleted."""

    @abstractmethod
    async def get_session_audio_size(self, session_id: str) -> int:
        """Total bytes occupied by *session_id*'s chunks."""

    async def verify_audio_chunk(
        self,
        relative_path: str,
        expected_hash: Optional[str] = None,
    ) -> bool:
        """Default impl: download bytes + hash. Backends with cheaper
        existence checks should override."""
        try:
            audio_data = await self.get_audio_chunk(relative_path)
        except FileNotFoundError:
            return False
        if expected_hash is None:
            return True
        return hashlib.sha256(audio_data).hexdigest() == expected_hash


class LocalAudioStorage(AudioStorageBackend):
    """Filesystem-backed audio storage. PCM chunks live in
    ``<base_path>/<session_id>/<item_id>_<content_index>.<format>``."""

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save_audio_chunk(
        self,
        session_id: str,
        item_id: str,
        content_index: int,
        audio_data: bytes,
        audio_format: str = "pcm16",
    ) -> str:
        session_dir = self.base_path / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{item_id}_{content_index}.{audio_format}"
        file_path = session_dir / filename
        await asyncio.to_thread(file_path.write_bytes, audio_data)
        # A storage key, not a host path: "/"-separated on every OS so it
        # stays valid in transcripts read elsewhere (blob backends, other OSes).
        return file_path.relative_to(self.base_path).as_posix()

    async def get_audio_chunk(self, relative_path: str) -> bytes:
        file_path = self.base_path / relative_path
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {relative_path}")
        return await asyncio.to_thread(file_path.read_bytes)

    async def delete_session_audio(self, session_id: str) -> int:
        session_dir = self.base_path / session_id
        if not session_dir.exists():
            return 0
        deleted_count = 0

        def _delete_files() -> None:
            nonlocal deleted_count
            for audio_file in session_dir.glob("*"):
                if audio_file.is_file():
                    audio_file.unlink()
                    deleted_count += 1
            try:
                session_dir.rmdir()
            except OSError:
                # Directory not empty (concurrent writer) or already gone.
                pass

        await asyncio.to_thread(_delete_files)
        return deleted_count

    async def get_session_audio_size(self, session_id: str) -> int:
        session_dir = self.base_path / session_id
        if not session_dir.exists():
            return 0

        def _calculate_size() -> int:
            total = 0
            for audio_file in session_dir.glob("*"):
                if audio_file.is_file():
                    total += audio_file.stat().st_size
            return total

        return await asyncio.to_thread(_calculate_size)

    def get_audio_file_path(self, relative_path: str) -> Path:
        """Backwards-compat accessor for code that needs a Path."""
        return self.base_path / relative_path


class AzureBlobAudioStorage(AudioStorageBackend):
    """Azure Blob Storage backend. One blob per chunk, keyed
    ``<session_id>/<item_id>_<content_index>.<format>``. The lazy SDK
    import keeps ``omniagents[session-postgres]``-only installs lean."""

    def __init__(self, *, account: str, key: str, container: str) -> None:
        self._account = account
        self._key = key
        self._container_name = container
        self._client = None  # lazy

    def _get_container(self):
        if self._client is not None:
            return self._client
        try:
            from azure.storage.blob.aio import BlobServiceClient
        except ImportError as e:
            raise ImportError(
                "AzureBlobAudioStorage requires azure-storage-blob. "
                "Install with: pip install 'omniagents[audio-blob]'"
            ) from e
        url = f"https://{self._account}.blob.core.windows.net"
        svc = BlobServiceClient(account_url=url, credential=self._key)
        self._svc = svc
        self._client = svc.get_container_client(self._container_name)
        return self._client

    @staticmethod
    def _blob_name(
        session_id: str, item_id: str, content_index: int, audio_format: str
    ) -> str:
        return f"{session_id}/{item_id}_{content_index}.{audio_format}"

    async def save_audio_chunk(
        self,
        session_id: str,
        item_id: str,
        content_index: int,
        audio_data: bytes,
        audio_format: str = "pcm16",
    ) -> str:
        name = self._blob_name(session_id, item_id, content_index, audio_format)
        container = self._get_container()
        await container.upload_blob(name=name, data=audio_data, overwrite=True)
        return name

    async def get_audio_chunk(self, relative_path: str) -> bytes:
        container = self._get_container()
        try:
            stream = await container.download_blob(relative_path)
            return await stream.readall()
        except Exception as e:  # azure-storage-blob raises ResourceNotFoundError
            if e.__class__.__name__ == "ResourceNotFoundError":
                raise FileNotFoundError(f"Audio blob not found: {relative_path}")
            raise

    async def delete_session_audio(self, session_id: str) -> int:
        container = self._get_container()
        prefix = f"{session_id}/"
        deleted = 0
        async for blob in container.list_blobs(name_starts_with=prefix):
            try:
                await container.delete_blob(blob.name)
                deleted += 1
            except Exception:
                logger.exception("delete_session_audio: blob %s failed", blob.name)
        return deleted

    async def get_session_audio_size(self, session_id: str) -> int:
        container = self._get_container()
        prefix = f"{session_id}/"
        total = 0
        async for blob in container.list_blobs(name_starts_with=prefix):
            total += blob.size or 0
        return total

    async def verify_audio_chunk(
        self,
        relative_path: str,
        expected_hash: Optional[str] = None,
    ) -> bool:
        # Cheap existence check: HEAD via get_blob_properties instead of
        # downloading the whole blob.
        container = self._get_container()
        try:
            blob_client = container.get_blob_client(relative_path)
            await blob_client.get_blob_properties()
        except Exception as e:
            if e.__class__.__name__ == "ResourceNotFoundError":
                return False
            raise
        if expected_hash is None:
            return True
        # Hash check still requires the bytes.
        return await super().verify_audio_chunk(relative_path, expected_hash)


def build_audio_storage(
    project_slug: str | None,
    agent_slug: str | None,
    *,
    env: Optional[dict] = None,
) -> AudioStorageBackend:
    """Resolve the audio backend from env. Defaults to ``LocalAudioStorage``
    rooted at ``get_audio_storage_path(project, agent)`` for parity with
    the pre-refactor constructor."""
    env_map: Mapping[str, str] = env if env is not None else os.environ
    backend = (env_map.get("OMNIAGENTS_AUDIO_BACKEND") or "fs").strip().lower()
    if backend == "blob":
        account = env_map.get("AZURE_STORAGE_ACCOUNT_NAME")
        key = env_map.get("AZURE_STORAGE_ACCOUNT_KEY")
        container = env_map.get("OMNI_AZURE_AUDIO_CONTAINER")
        if not (account and key and container):
            raise RuntimeError(
                "OMNIAGENTS_AUDIO_BACKEND=blob requires AZURE_STORAGE_ACCOUNT_NAME, "
                "AZURE_STORAGE_ACCOUNT_KEY, and OMNI_AZURE_AUDIO_CONTAINER."
            )
        return AzureBlobAudioStorage(account=account, key=key, container=container)
    if backend != "fs":
        logger.warning(
            "Unknown OMNIAGENTS_AUDIO_BACKEND=%r; falling back to local filesystem.",
            backend,
        )
    # Local default — late import to avoid a circular paths import at module load.
    from omniagents.core.paths import get_audio_storage_path

    return LocalAudioStorage(base_path=get_audio_storage_path(project_slug, agent_slug))


# Backwards-compatible alias for legacy ``AudioStorage(base_path=...)`` callers
# (notably realtime_session.py before this refactor).
AudioStorage = LocalAudioStorage
