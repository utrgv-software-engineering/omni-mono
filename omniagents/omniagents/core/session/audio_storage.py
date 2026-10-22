"""Audio storage backends for realtime/voice agent sessions.

:class:`LocalAudioStorage` writes PCM chunks to
``<base>/<session_id>/<item_id>_<index>.<format>`` on the local
filesystem. Other backends implement :class:`AudioStorageBackend` and are
injected into the realtime session directly.

The ``save_audio_chunk`` method returns the *relative path* it wrote — for
the local backend that's a real filesystem-relative path; for another
backend it's whatever key it uses. Either is opaque to ``history_db.save_audio_metadata``
which just stores the string; readers (UI playback, audit export) fetch
the bytes back through ``get_audio_chunk(relative_path)`` on the same
backend, so the abstraction is consistent end-to-end.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

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


def build_audio_storage(
    project_slug: str | None, agent_slug: str | None
) -> AudioStorageBackend:
    """The default backend: ``LocalAudioStorage`` rooted at
    ``get_audio_storage_path(project, agent)``."""
    # Late import to avoid a circular paths import at module load.
    from omniagents.core.paths import get_audio_storage_path

    return LocalAudioStorage(base_path=get_audio_storage_path(project_slug, agent_slug))

