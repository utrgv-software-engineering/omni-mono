"""Audio storage for realtime agent sessions.

Handles saving and retrieving audio chunks from disk with associated metadata
stored in the session database.
"""

import asyncio
from pathlib import Path
from typing import Optional
import hashlib
from datetime import datetime


class AudioStorage:
    """Manages audio file storage for realtime sessions.

    Audio files are stored in a directory structure:
        <base_path>/<session_id>/<item_id>_<content_index>.pcm16

    Metadata about the audio files is stored in the database separately.
    """

    def __init__(self, base_path: Path):
        """Initialize audio storage.

        Args:
            base_path: Base directory for storing audio files.
        """
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
        """Save an audio chunk to disk.

        Args:
            session_id: The session ID
            item_id: The realtime item ID
            content_index: The content index within the item
            audio_data: Raw audio bytes
            audio_format: Audio format (e.g., 'pcm16', 'g711_ulaw')

        Returns:
            Relative file path where the audio was saved
        """
        # Create session directory
        session_dir = self.base_path / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        filename = f"{item_id}_{content_index}.{audio_format}"
        file_path = session_dir / filename

        # Write audio data asynchronously
        await asyncio.to_thread(file_path.write_bytes, audio_data)

        # Return relative path for database storage
        return str(file_path.relative_to(self.base_path))

    async def get_audio_chunk(self, relative_path: str) -> bytes:
        """Retrieve an audio chunk from disk.

        Args:
            relative_path: Relative file path from base_path

        Returns:
            Raw audio bytes

        Raises:
            FileNotFoundError: If the audio file doesn't exist
        """
        file_path = self.base_path / relative_path
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {relative_path}")

        # Read audio data asynchronously
        audio_data = await asyncio.to_thread(file_path.read_bytes)
        return audio_data

    async def delete_session_audio(self, session_id: str) -> int:
        """Delete all audio files for a session.

        Args:
            session_id: The session ID

        Returns:
            Number of files deleted
        """
        session_dir = self.base_path / session_id
        if not session_dir.exists():
            return 0

        deleted_count = 0

        def _delete_files():
            nonlocal deleted_count
            for audio_file in session_dir.glob("*"):
                if audio_file.is_file():
                    audio_file.unlink()
                    deleted_count += 1
            # Remove the directory if it's empty
            try:
                session_dir.rmdir()
            except OSError:
                pass  # Directory not empty or other error

        await asyncio.to_thread(_delete_files)
        return deleted_count

    async def get_session_audio_size(self, session_id: str) -> int:
        """Get total size of audio files for a session.

        Args:
            session_id: The session ID

        Returns:
            Total size in bytes
        """
        session_dir = self.base_path / session_id
        if not session_dir.exists():
            return 0

        def _calculate_size():
            total_size = 0
            for audio_file in session_dir.glob("*"):
                if audio_file.is_file():
                    total_size += audio_file.stat().st_size
            return total_size

        return await asyncio.to_thread(_calculate_size)

    def get_audio_file_path(self, relative_path: str) -> Path:
        """Get the absolute path for an audio file.

        Args:
            relative_path: Relative file path from base_path

        Returns:
            Absolute Path object
        """
        return self.base_path / relative_path

    async def verify_audio_chunk(
        self, relative_path: str, expected_hash: Optional[str] = None
    ) -> bool:
        """Verify an audio chunk exists and optionally check its hash.

        Args:
            relative_path: Relative file path from base_path
            expected_hash: Optional SHA256 hash to verify against

        Returns:
            True if file exists (and hash matches if provided), False otherwise
        """
        file_path = self.base_path / relative_path
        if not file_path.exists():
            return False

        if expected_hash is not None:
            audio_data = await self.get_audio_chunk(relative_path)
            actual_hash = hashlib.sha256(audio_data).hexdigest()
            return actual_hash == expected_hash

        return True
