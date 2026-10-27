import pytest
import asyncio
from pathlib import Path

from omniagents.core.session.audio_storage import LocalAudioStorage


@pytest.mark.asyncio
async def test_save_audio_chunk(tmp_path):
    """Test saving an audio chunk to disk."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "test_session"
    item_id = "item_123"
    content_index = 0
    audio_data = b"fake_audio_data_pcm16"

    file_path = await storage.save_audio_chunk(
        session_id, item_id, content_index, audio_data
    )

    assert file_path == f"{session_id}/item_123_0.pcm16"
    saved_file = tmp_path / file_path
    assert saved_file.exists()
    assert saved_file.read_bytes() == audio_data


@pytest.mark.asyncio
async def test_save_audio_chunk_different_format(tmp_path):
    """Test saving audio chunks with different formats."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "test_session"

    # Save pcm16 format
    file_path_pcm = await storage.save_audio_chunk(
        session_id, "item_1", 0, b"pcm_data", "pcm16"
    )
    assert file_path_pcm.endswith(".pcm16")

    # Save g711_ulaw format
    file_path_ulaw = await storage.save_audio_chunk(
        session_id, "item_2", 0, b"ulaw_data", "g711_ulaw"
    )
    assert file_path_ulaw.endswith(".g711_ulaw")

    # Save g711_alaw format
    file_path_alaw = await storage.save_audio_chunk(
        session_id, "item_3", 0, b"alaw_data", "g711_alaw"
    )
    assert file_path_alaw.endswith(".g711_alaw")


@pytest.mark.asyncio
async def test_get_audio_chunk(tmp_path):
    """Test retrieving an audio chunk from disk."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "test_session"
    item_id = "item_456"
    audio_data = b"test_audio_content"

    # Save first
    file_path = await storage.save_audio_chunk(session_id, item_id, 0, audio_data)

    # Retrieve
    retrieved_data = await storage.get_audio_chunk(file_path)
    assert retrieved_data == audio_data


@pytest.mark.asyncio
async def test_get_audio_chunk_not_found(tmp_path):
    """Test that get_audio_chunk raises FileNotFoundError for missing files."""
    storage = LocalAudioStorage(tmp_path)

    with pytest.raises(FileNotFoundError):
        await storage.get_audio_chunk("nonexistent/file.pcm16")


@pytest.mark.asyncio
async def test_delete_session_audio(tmp_path):
    """Test deleting all audio files for a session."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "delete_test"

    # Save multiple audio chunks
    await storage.save_audio_chunk(session_id, "item_1", 0, b"data1")
    await storage.save_audio_chunk(session_id, "item_2", 0, b"data2")
    await storage.save_audio_chunk(session_id, "item_3", 0, b"data3")

    # Verify files exist
    session_dir = tmp_path / session_id
    assert session_dir.exists()
    assert len(list(session_dir.glob("*"))) == 3

    # Delete all
    deleted_count = await storage.delete_session_audio(session_id)
    assert deleted_count == 3
    assert not session_dir.exists()


@pytest.mark.asyncio
async def test_delete_session_audio_nonexistent(tmp_path):
    """Test deleting audio for a nonexistent session returns 0."""
    storage = LocalAudioStorage(tmp_path)
    deleted_count = await storage.delete_session_audio("nonexistent_session")
    assert deleted_count == 0


@pytest.mark.asyncio
async def test_get_session_audio_size(tmp_path):
    """Test calculating total size of session audio."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "size_test"

    # Save multiple chunks of different sizes
    data1 = b"x" * 100
    data2 = b"y" * 200
    data3 = b"z" * 300

    await storage.save_audio_chunk(session_id, "item_1", 0, data1)
    await storage.save_audio_chunk(session_id, "item_2", 0, data2)
    await storage.save_audio_chunk(session_id, "item_3", 0, data3)

    total_size = await storage.get_session_audio_size(session_id)
    assert total_size == 600


@pytest.mark.asyncio
async def test_get_session_audio_size_nonexistent(tmp_path):
    """Test getting size of nonexistent session returns 0."""
    storage = LocalAudioStorage(tmp_path)
    size = await storage.get_session_audio_size("nonexistent_session")
    assert size == 0


@pytest.mark.asyncio
async def test_verify_audio_chunk_exists(tmp_path):
    """Test verifying an audio chunk exists."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "verify_test"
    audio_data = b"verification_data"

    # Save chunk
    file_path = await storage.save_audio_chunk(session_id, "item_1", 0, audio_data)

    # Verify exists
    exists = await storage.verify_audio_chunk(file_path)
    assert exists is True


@pytest.mark.asyncio
async def test_verify_audio_chunk_not_exists(tmp_path):
    """Test verifying a nonexistent audio chunk."""
    storage = LocalAudioStorage(tmp_path)
    exists = await storage.verify_audio_chunk("nonexistent/file.pcm16")
    assert exists is False


@pytest.mark.asyncio
async def test_verify_audio_chunk_with_hash(tmp_path):
    """Test verifying an audio chunk with hash validation."""
    import hashlib

    storage = LocalAudioStorage(tmp_path)
    session_id = "hash_test"
    audio_data = b"hash_verification_data"
    expected_hash = hashlib.sha256(audio_data).hexdigest()

    # Save chunk
    file_path = await storage.save_audio_chunk(session_id, "item_1", 0, audio_data)

    # Verify with correct hash
    valid = await storage.verify_audio_chunk(file_path, expected_hash)
    assert valid is True

    # Verify with incorrect hash
    invalid = await storage.verify_audio_chunk(file_path, "wrong_hash")
    assert invalid is False


@pytest.mark.asyncio
async def test_multiple_content_indices(tmp_path):
    """Test saving multiple content indices for the same item."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "multi_index"
    item_id = "item_789"

    # Save multiple indices
    path_0 = await storage.save_audio_chunk(session_id, item_id, 0, b"data0")
    path_1 = await storage.save_audio_chunk(session_id, item_id, 1, b"data1")
    path_2 = await storage.save_audio_chunk(session_id, item_id, 2, b"data2")

    # Verify all are different
    assert path_0 != path_1 != path_2

    # Verify all exist
    assert (tmp_path / path_0).exists()
    assert (tmp_path / path_1).exists()
    assert (tmp_path / path_2).exists()


@pytest.mark.asyncio
async def test_concurrent_saves(tmp_path):
    """Test concurrent audio chunk saves."""
    storage = LocalAudioStorage(tmp_path)
    session_id = "concurrent_test"

    # Create multiple save tasks
    tasks = [
        storage.save_audio_chunk(session_id, f"item_{i}", 0, f"data_{i}".encode())
        for i in range(10)
    ]

    # Run concurrently
    file_paths = await asyncio.gather(*tasks)

    # Verify all were saved
    assert len(file_paths) == 10
    assert len(set(file_paths)) == 10  # All unique

    # Verify all files exist
    for file_path in file_paths:
        assert (tmp_path / file_path).exists()


@pytest.mark.asyncio
async def test_base_path_creation(tmp_path):
    """Test that base path is created if it doesn't exist."""
    base_path = tmp_path / "audio_storage" / "nested" / "path"
    assert not base_path.exists()

    storage = LocalAudioStorage(base_path)
    assert base_path.exists()
    assert base_path.is_dir()
