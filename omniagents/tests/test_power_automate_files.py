import base64
import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omniagents.integrations.power_automate_files import assemble_attachment
from omniagents.integrations.power_automate_files import download_attachment


def test_large_file_is_assembled_without_twenty_mb_limit():
    part = b"a" * 786432
    count = 40
    checksum = hashlib.sha256(part * count).hexdigest()
    manifest = {
        "byte_size": len(part) * count,
        "chunk_count": count,
        "expected_sha256": checksum,
    }
    output = io.BytesIO()
    actual = assemble_attachment(
        manifest,
        ((index, base64.b64encode(part).decode()) for index in range(count)),
        output,
    )
    assert actual == checksum
    assert len(output.getvalue()) == 31457280


@pytest.mark.parametrize(
    "chunks,manifest",
    [
        ([], {"byte_size": 3, "chunk_count": 1}),
        ([(1, "YWJj")], {"byte_size": 3, "chunk_count": 1}),
        ([(0, "!!!!")], {"byte_size": 3, "chunk_count": 1}),
        ([(0, "YWJj")], {"byte_size": 2, "chunk_count": 1}),
        (
            [(0, "YWJj")],
            {"byte_size": 3, "chunk_count": 1, "expected_sha256": "0" * 64},
        ),
        ([(0, "YWJj"), (0, "YWJj")], {"byte_size": 6, "chunk_count": 2}),
        ([], {"byte_size": 1073741825, "chunk_count": 1}),
    ],
)
def test_rejects_partial_corrupt_or_oversize_transfers(chunks, manifest):
    with pytest.raises(ValueError):
        assemble_attachment(manifest, chunks, io.BytesIO())


def test_empty_file():
    assert (
        assemble_attachment({"byte_size": 0, "chunk_count": 1}, [(0, "")], io.BytesIO())
        == hashlib.sha256(b"").hexdigest()
    )


@pytest.mark.parametrize(
    "content,valid", [(b"abc", True), (b"abd", False), (b"abcd", False)]
)
def test_download_validates_before_exposing_file(tmp_path, content, valid):
    identity = uuid4()
    database = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(
            fetchone=lambda: ("blob", hashlib.sha256(b"abc").hexdigest(), 3)
        )
    )
    container = SimpleNamespace(
        get_blob_client=lambda name: SimpleNamespace(
            download_blob=lambda: SimpleNamespace(chunks=lambda: iter([content]))
        )
    )
    if valid:
        path = download_attachment(database, container, identity, "intake", tmp_path)
        assert path.read_bytes() == b"abc"
        assert path.name == str(identity)
    else:
        with pytest.raises(ValueError):
            download_attachment(database, container, identity, "intake", tmp_path)
        assert list(tmp_path.iterdir()) == []
