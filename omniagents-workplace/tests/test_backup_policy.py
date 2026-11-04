"""Backups under the strict policy: keys apart, Confidential working copies out, Blob Storage."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from omniagents_workplace.backup import (
    backup,
    confidential_paths,
    export_keys,
    restore,
    upload,
)
from omniagents_workplace.store import Store


def factory(root: Path, factory_id: str, classification: str) -> Path:
    directory = root / "installation" / "factories" / factory_id
    store = Store(directory / "workplace" / "workplace.db")
    store.set_meta("factory.profile", {"classification": classification})
    store.close()
    (directory / "workplace" / "shared").mkdir(parents=True)
    (directory / "workplace" / "shared" / "extract.csv").write_text("mrn,name\n")
    (directory / "workplace" / "factory-secrets.key").write_text("secret")
    (directory / "vms").mkdir()
    (directory / "vms" / "disk.qcow2").write_text("disk")
    (directory / "runtime").mkdir()
    (directory / "runtime" / "events.db").write_text("x")
    return directory


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "data"
    store = Store(root / "workplace.db")
    store.set_meta("installation.name", "test")
    store.close()
    (root / "factory-secrets.key").write_text("root-secret")
    Store(root / "installation" / "installation.db").close()
    factory(root, "conf", "confidential")
    factory(root, "open", "published")
    return root


def test_confidential_factories_working_copies_and_all_keys_are_left_out(
    data, tmp_path
):
    assert confidential_paths(data) == [
        "installation/factories/conf/workplace/shared",
        "installation/factories/conf/workplace/shared-staging",
        "installation/factories/conf/vms",
    ]
    made = backup(data, tmp_path / "backups", keys=False, confidential=True)
    copied = {str(p.relative_to(made)) for p in made.rglob("*") if p.is_file()}
    assert "installation/factories/open/workplace/shared/extract.csv" in copied
    assert "installation/factories/open/vms/disk.qcow2" in copied
    assert not any(
        c.startswith("installation/factories/conf/workplace/shared") for c in copied
    )
    assert not any(c.startswith("installation/factories/conf/vms") for c in copied)
    assert not any(c.endswith(".key") for c in copied)
    assert "installation/factories/conf/workplace/workplace.db" in copied
    metadata = json.loads((made / "backup-metadata.json").read_text())
    assert metadata["keys"].startswith("excluded")
    assert metadata["confidential_excluded"] == confidential_paths(data)


def test_keys_are_escrowed_apart_and_restored_with_the_data(data, tmp_path):
    escrow = tmp_path / "escrow"
    assert export_keys(data, escrow) == [
        "factory-secrets.key",
        "installation/factories/conf/workplace/factory-secrets.key",
        "installation/factories/open/workplace/factory-secrets.key",
    ]
    made = backup(data, tmp_path / "backups", keys=False)
    restored = tmp_path / "restored"
    restore(made, restored, mode="production", keys=escrow)
    assert (restored / "factory-secrets.key").read_text() == "root-secret"


def test_a_backup_uploads_to_blob_storage_in_blocks(data, tmp_path):
    made = backup(data, tmp_path / "backups", keys=False, confidential=True)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201)

    with pytest.raises(ValueError):
        upload(made, "https://account.blob.core.windows.net/backups")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sent = upload(
            made, "https://account.blob.core.windows.net/backups?sv=1&sig=x", client
        )
    files = [p for p in made.rglob("*") if p.is_file()]
    assert sent == sum(p.stat().st_size for p in files)
    lists = [r for r in requests if "comp=blocklist" in str(r.url)]
    assert len(lists) == len(files)
    first = lists[0]
    assert first.url.path.startswith(f"/backups/{made.name}/")
    assert "sig=x" in str(first.url)
    block = next(r for r in requests if "comp=block&" in str(r.url))
    assert base64.b64decode(block.url.params["blockid"]) == b"00000000"
