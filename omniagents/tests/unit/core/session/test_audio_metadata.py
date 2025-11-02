import pytest

from omniagents.core.session import history_db


def test_save_audio_metadata():
    """Test saving audio metadata to the database."""
    session_id = "audio_test_session"
    item_id = "item_123"
    content_index = 0
    file_path = "session_123/item_123_0.pcm16"
    audio_format = "pcm16"
    duration_ms = 1000

    row_id = history_db.save_audio_metadata(
        session_id=session_id,
        item_id=item_id,
        content_index=content_index,
        file_path=file_path,
        audio_format=audio_format,
        duration_ms=duration_ms,
    )

    assert row_id > 0


def test_save_audio_metadata_minimal():
    """Test saving audio metadata with minimal required fields."""
    session_id = "minimal_test"
    item_id = "item_456"
    content_index = 1
    file_path = "session/item_456_1.pcm16"
    audio_format = "pcm16"

    row_id = history_db.save_audio_metadata(
        session_id=session_id,
        item_id=item_id,
        content_index=content_index,
        file_path=file_path,
        audio_format=audio_format,
    )

    assert row_id > 0


def test_get_audio_metadata():
    """Test retrieving all audio metadata for a session."""
    session_id = "retrieve_test"

    # Save multiple audio metadata entries
    history_db.save_audio_metadata(session_id, "item_1", 0, "path1.pcm16", "pcm16", 100)
    history_db.save_audio_metadata(session_id, "item_2", 0, "path2.pcm16", "pcm16", 200)
    history_db.save_audio_metadata(session_id, "item_3", 0, "path3.pcm16", "pcm16", 300)

    # Retrieve all
    metadata_list = history_db.get_audio_metadata(session_id)

    assert len(metadata_list) == 3
    assert metadata_list[0]["item_id"] == "item_1"
    assert metadata_list[1]["item_id"] == "item_2"
    assert metadata_list[2]["item_id"] == "item_3"
    assert metadata_list[0]["duration_ms"] == 100
    assert metadata_list[1]["duration_ms"] == 200
    assert metadata_list[2]["duration_ms"] == 300


def test_get_audio_metadata_empty_session():
    """Test retrieving metadata for a session with no audio."""
    session_id = "empty_audio_session"
    metadata_list = history_db.get_audio_metadata(session_id)
    assert metadata_list == []


def test_get_audio_metadata_by_item():
    """Test retrieving audio metadata by item ID and content index."""
    session_id = "item_lookup_test"
    item_id = "specific_item"
    content_index = 5
    file_path = "session/specific_item_5.pcm16"
    audio_format = "pcm16"
    duration_ms = 500

    # Save metadata
    history_db.save_audio_metadata(
        session_id, item_id, content_index, file_path, audio_format, duration_ms
    )

    # Retrieve by item
    metadata = history_db.get_audio_metadata_by_item(item_id, content_index)

    assert metadata is not None
    assert metadata["session_id"] == session_id
    assert metadata["item_id"] == item_id
    assert metadata["content_index"] == content_index
    assert metadata["file_path"] == file_path
    assert metadata["audio_format"] == audio_format
    assert metadata["duration_ms"] == duration_ms


def test_get_audio_metadata_by_item_not_found():
    """Test retrieving metadata for nonexistent item returns None."""
    metadata = history_db.get_audio_metadata_by_item("nonexistent_item", 0)
    assert metadata is None


def test_delete_audio_metadata():
    """Test deleting all audio metadata for a session."""
    session_id = "delete_metadata_test"

    # Save multiple entries
    history_db.save_audio_metadata(session_id, "item_1", 0, "path1.pcm16", "pcm16")
    history_db.save_audio_metadata(session_id, "item_2", 0, "path2.pcm16", "pcm16")
    history_db.save_audio_metadata(session_id, "item_3", 0, "path3.pcm16", "pcm16")

    # Verify they exist
    metadata_list = history_db.get_audio_metadata(session_id)
    assert len(metadata_list) == 3

    # Delete
    deleted_count = history_db.delete_audio_metadata(session_id)
    assert deleted_count == 3

    # Verify deleted
    metadata_list = history_db.get_audio_metadata(session_id)
    assert len(metadata_list) == 0


def test_delete_audio_metadata_nonexistent_session():
    """Test deleting metadata for nonexistent session returns 0."""
    deleted_count = history_db.delete_audio_metadata("nonexistent_session")
    assert deleted_count == 0


def test_audio_metadata_with_message_id():
    """Test saving audio metadata linked to a history message."""
    session_id = "message_link_test"

    # First save a message to history to get a valid message_id
    history_db.register_session(session_id)
    message = {"role": "user", "content": "test message"}
    history_db.append_message(session_id, message)

    # Get the message_id from the saved message
    history_messages = history_db.load_history(session_id)
    assert len(history_messages) == 1

    # Get the actual message_id from the database
    conn, lock = history_db._get_store(None, None)
    with lock:
        cursor = conn.execute(
            "SELECT id FROM history WHERE session_id=? LIMIT 1", (session_id,)
        )
        row = cursor.fetchone()
        message_id = row[0]

    item_id = "linked_item"
    content_index = 0
    file_path = "session/linked_item_0.pcm16"
    audio_format = "pcm16"

    row_id = history_db.save_audio_metadata(
        session_id,
        item_id,
        content_index,
        file_path,
        audio_format,
        message_id=message_id,
    )

    # Retrieve and verify
    metadata_list = history_db.get_audio_metadata(session_id)
    assert len(metadata_list) == 1
    assert metadata_list[0]["message_id"] == message_id


def test_audio_metadata_multiple_content_indices():
    """Test saving multiple content indices for the same item."""
    session_id = "multi_index_test"
    item_id = "multi_content_item"

    # Save multiple indices
    history_db.save_audio_metadata(session_id, item_id, 0, "path_0.pcm16", "pcm16", 100)
    history_db.save_audio_metadata(session_id, item_id, 1, "path_1.pcm16", "pcm16", 200)
    history_db.save_audio_metadata(session_id, item_id, 2, "path_2.pcm16", "pcm16", 300)

    # Retrieve all for session
    metadata_list = history_db.get_audio_metadata(session_id)
    assert len(metadata_list) == 3

    # Retrieve each by index
    meta_0 = history_db.get_audio_metadata_by_item(item_id, 0)
    meta_1 = history_db.get_audio_metadata_by_item(item_id, 1)
    meta_2 = history_db.get_audio_metadata_by_item(item_id, 2)

    assert meta_0["duration_ms"] == 100
    assert meta_1["duration_ms"] == 200
    assert meta_2["duration_ms"] == 300


def test_audio_metadata_different_formats():
    """Test saving metadata for different audio formats."""
    session_id = "format_test"

    # Save different formats
    history_db.save_audio_metadata(session_id, "item_pcm", 0, "path.pcm16", "pcm16")
    history_db.save_audio_metadata(
        session_id, "item_ulaw", 0, "path.g711_ulaw", "g711_ulaw"
    )
    history_db.save_audio_metadata(
        session_id, "item_alaw", 0, "path.g711_alaw", "g711_alaw"
    )

    # Retrieve all
    metadata_list = history_db.get_audio_metadata(session_id)
    assert len(metadata_list) == 3

    formats = [m["audio_format"] for m in metadata_list]
    assert "pcm16" in formats
    assert "g711_ulaw" in formats
    assert "g711_alaw" in formats


def test_audio_metadata_ordering():
    """Test that metadata is returned in insertion order."""
    session_id = "ordering_test"

    # Save in specific order
    for i in range(5):
        history_db.save_audio_metadata(
            session_id, f"item_{i}", 0, f"path_{i}.pcm16", "pcm16", i * 100
        )

    # Retrieve
    metadata_list = history_db.get_audio_metadata(session_id)

    # Verify order
    for i, metadata in enumerate(metadata_list):
        assert metadata["item_id"] == f"item_{i}"
        assert metadata["duration_ms"] == i * 100


def test_audio_metadata_isolation_between_sessions():
    """Test that audio metadata is isolated between sessions."""
    session_1 = "isolation_session_1"
    session_2 = "isolation_session_2"

    # Save to session 1
    history_db.save_audio_metadata(session_1, "item_1", 0, "path1.pcm16", "pcm16")

    # Save to session 2
    history_db.save_audio_metadata(session_2, "item_2", 0, "path2.pcm16", "pcm16")

    # Verify isolation
    metadata_1 = history_db.get_audio_metadata(session_1)
    metadata_2 = history_db.get_audio_metadata(session_2)

    assert len(metadata_1) == 1
    assert len(metadata_2) == 1
    assert metadata_1[0]["item_id"] == "item_1"
    assert metadata_2[0]["item_id"] == "item_2"
