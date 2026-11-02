import asyncio
import io
import json
import shutil
from pathlib import Path
import zipfile

import httpx
import pytest
from fastapi import HTTPException

from conftest import signed_in
from omniagents_workplace.shared.files import snapshot
from omniagents_workplace.backup import backup, restore
from omniagents_workplace.store import Store


def upload(client, files, replace=False):
    r = client.post(
        "/workplace/api/shared/uploads",
        json={
            "files": [{"path": p, "size": len(b)} for p, b in files.items()],
            "replace": replace,
        },
    )
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    for i, (path, data) in enumerate(files.items()):
        r = client.put(
            f"/workplace/api/shared/uploads/{ident}/{i}?offset=0",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
    r = client.post(f"/workplace/api/shared/uploads/{ident}/commit", json={})
    assert r.status_code == 200, r.text
    return ident


def test_files_batch_and_management(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as user, httpx.Client(base_url=url) as guest:
        assert guest.get("/workplace/api/shared/files").status_code == 401
        ident = upload(
            user,
            {"Invoices/one.txt": b"one", "Invoices/two.txt": b"two", "empty.txt": b""},
        )
        assert (w.shared.root / "Invoices/one.txt").read_bytes() == b"one"
        events = dict(w.store.docs("shared.event"))
        assert len(events) == 1 and len(events[ident]["entries"]) == 3
        assert (
            w.store._db.execute(
                "SELECT count(*) FROM factory_events WHERE provider='shared'"
            ).fetchone()[0]
            == 0
        )
        assert (
            user.post(
                f"/workplace/api/shared/uploads/{ident}/commit", json={}
            ).status_code
            == 200
        )
        assert len(w.store.docs("shared.event")) == 1
        r = user.get("/workplace/api/shared/download-folder?path=Invoices")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert z.read("one.txt") == b"one"
        r = user.post(
            "/workplace/api/shared/files",
            json={"action": "move", "path": "Invoices", "destination": "Reviewed"},
        )
        assert r.status_code == 200, r.text
        assert (
            user.get("/workplace/api/shared/content?path=Reviewed/one.txt").content
            == b"one"
        )
        assert (
            user.post(
                "/workplace/api/shared/files",
                json={"action": "delete", "path": "Reviewed"},
            ).status_code
            == 200
        )
        assert not (w.shared.root / "Reviewed").exists()
        events = [v for _, v in w.store.docs("shared.event")]
        assert {e["event"] for e in events} == {
            "shared.files_uploaded",
            "shared.entry_moved",
            "shared.entry_deleted",
        }


def test_incomplete_upload_is_invisible_and_paths_are_safe(workplace_server, tmp_path):
    w, url = workplace_server
    with signed_in(w, url) as user, signed_in(w, url, "priya") as other:
        r = user.post(
            "/workplace/api/shared/uploads",
            json={"files": [{"path": "pending.txt", "size": 6}]},
        )
        ident = r.json()["id"]
        r = user.put(
            f"/workplace/api/shared/uploads/{ident}/0",
            content=b"abc",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
        assert not (w.shared.root / "pending.txt").exists()
        assert (
            user.post(
                f"/workplace/api/shared/uploads/{ident}/commit", json={}
            ).status_code
            == 409
        )
        assert (
            other.put(
                f"/workplace/api/shared/uploads/{ident}/0?offset=3",
                content=b"def",
                headers={"Content-Type": "application/octet-stream"},
            ).status_code
            == 404
        )
        assert (
            user.put(
                f"/workplace/api/shared/uploads/{ident}/0?offset=3",
                content=b"def",
                headers={"Content-Type": "application/octet-stream"},
            ).status_code
            == 200
        )
        assert (
            user.post(
                f"/workplace/api/shared/uploads/{ident}/commit", json={}
            ).status_code
            == 200
        )
        secret = tmp_path / "secret"
        secret.write_text("not shared")
        (w.shared.root / "link").symlink_to(secret)
        (w.shared.root / "escape").symlink_to(tmp_path, target_is_directory=True)
        for path in ("../secret", "/etc/passwd", "link", "escape/secret"):
            r = user.get("/workplace/api/shared/content", params={"path": path})
            assert r.status_code in (404, 409, 422), (path, r.status_code, r.text)
        assert "link" not in snapshot(w.shared.root)
        assert (
            user.post(
                "/workplace/api/shared/files", json={"action": "delete", "path": ""}
            ).status_code
            == 422
        )
        # Cross-site cookie mutations remain blocked even for raw chunks.
        assert (
            user.put(
                f"/workplace/api/shared/uploads/{ident}/0",
                content=b"abc",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Origin": "https://evil.test",
                },
            ).status_code
            == 403
        )


def test_folder_workflows_and_agent_feedback_loop(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as admin, signed_in(w, url, "priya") as member:
        assert (
            admin.post(
                "/workplace/api/shared/files",
                json={"action": "mkdir", "path": "Invoices"},
            ).status_code
            == 200
        )
        rule = {
            "name": "Extract invoices",
            "folder": "Invoices",
            "instruction": "Read the PDFs and summarize them.",
            "patterns": ["*.pdf"],
            "enabled": True,
        }
        assert (
            member.post("/workplace/api/shared/workflows", json=rule).status_code == 403
        )
        r = admin.post("/workplace/api/shared/workflows", json=rule)
        assert r.status_code == 200, r.text
        workflow = r.json()
        upload(
            admin,
            {
                "Invoices/one.pdf": b"PDF one",
                "Invoices/two.pdf": b"PDF two",
                "Invoices/notes.txt": b"notes",
            },
        )
        rows = w.store._db.execute(
            "SELECT body FROM factory_events WHERE provider='shared'"
        ).fetchall()
        assert len(rows) == 1
        event = json.loads(rows[0][0])
        assert len(event["entries"]) == 2 and event["workflow"]["id"] == workflow["id"]
        (w.shared.root / "Invoices/agent.pdf").write_bytes(b"agent output")
        w.shared.reconcile(stable=True)
        w.shared.reconcile(stable=True)
        assert (
            w.store._db.execute(
                "SELECT count(*) FROM factory_events WHERE provider='shared'"
            ).fetchone()[0]
            == 1
        )
        assert any(e["origin"] == "agent" for _, e in w.store.docs("shared.event"))
        # Direct sandbox edits and moves are visible without uploads.
        (w.shared.root / "Invoices/agent.pdf").write_bytes(b"new content")
        w.shared.reconcile()
        (w.shared.root / "Invoices/agent.pdf").rename(
            w.shared.root / "Invoices/renamed.pdf"
        )
        w.shared.reconcile()
        assert any(
            e["event"] == "shared.file_updated" for _, e in w.store.docs("shared.event")
        )
        assert any(
            e["event"] == "shared.entry_moved" and e["origin"] == "agent"
            for _, e in w.store.docs("shared.event")
        )
        assert admin.get("/workplace/api/shared/files?path=Invoices").json()["entries"]


def test_commit_recovery_and_backup(workplace_server, tmp_path, monkeypatch):
    w, url = workplace_server
    with signed_in(w, url) as user:
        upload(user, {"saved.txt": b"saved"})
        record = w.shared.doc("upload", next(iter(dict(w.store.docs("shared.upload")))))
        # Replay after filesystem installation but before event commit.
        record["status"] = "committing"
        w.store.put("shared.upload", record["id"], record)
        w.shared.maintain_once()
        assert len(w.store.docs("shared.event")) == 1
        saved = backup(w.data_dir, tmp_path / "backups")
        restore(saved, tmp_path / "restored")
        assert (tmp_path / "restored/shared/saved.txt").read_bytes() == b"saved"
        store = Store(tmp_path / "restored/workplace.db")
        assert len(store.docs("shared.event")) == 1
        store.close()


@pytest.mark.asyncio
async def test_gateway_retries_and_cancels_changed_rules(workplace_server, monkeypatch):
    w, url = workplace_server
    await asyncio.wrap_future(
        asyncio.run_coroutine_threadsafe(w.event_gateway.close(), w.event_gateway.loop)
    )
    with signed_in(w, url) as user:
        rule = user.post(
            "/workplace/api/shared/workflows",
            json={
                "name": "Review",
                "instruction": "Read uploaded files",
                "enabled": True,
            },
        ).json()
        upload(user, {"test.txt": b"test"})
        monkeypatch.setattr(w.event_gateway, "enabled", lambda: True)
        monkeypatch.setattr(
            w.event_gateway,
            "config",
            lambda: {"factory": {"url": "http://runtime", "auth_token": "test-token"}},
        )
        deliveries = []

        async def fail(request):
            deliveries.append(request)
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            await w.event_gateway.deliver_pending(client)
        row = w.store._db.execute(
            "SELECT delivered,attempts FROM factory_events WHERE provider='shared'"
        ).fetchone()
        assert row == (None, 1)
        assert deliveries[0].headers["authorization"] == "Bearer test-token"
        with w.store.transaction():
            w.store._db.execute(
                "UPDATE factory_events SET next_attempt=0 WHERE provider='shared'"
            )
        body = {k: v for k, v in rule.items() if k not in ("id", "revision")}
        body["enabled"] = False
        assert (
            user.put(
                "/workplace/api/shared/workflows/" + rule["id"], json=body
            ).status_code
            == 200
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            await w.event_gateway.deliver_pending(client)
        assert len(deliveries) == 1
        row = w.store._db.execute(
            "SELECT delivered,error FROM factory_events WHERE provider='shared'"
        ).fetchone()
        assert row[0] and row[1].startswith("Cancelled")


def test_empty_folders_replacement_workflows_and_move_patterns(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as user:
        r = user.post(
            "/workplace/api/shared/uploads",
            json={"files": [], "folders": ["Empty/nested"]},
        )
        assert r.status_code == 200, r.text
        assert (
            user.post(
                "/workplace/api/shared/uploads/" + r.json()["id"] + "/commit", json={}
            ).status_code
            == 200
        )
        assert (w.shared.root / "Empty/nested").is_dir()
        upload(user, {"Empty/one.pdf": b"old"})
        rule = user.post(
            "/workplace/api/shared/workflows",
            json={
                "name": "Changed PDFs",
                "instruction": "Review",
                "enabled": True,
                "events": ["shared.file_updated", "shared.entry_moved"],
                "patterns": ["*.pdf"],
            },
        ).json()
        upload(user, {"Empty/one.pdf": b"new"}, replace=True)
        rows = w.store._db.execute(
            "SELECT body FROM factory_events WHERE provider='shared'"
        ).fetchall()
        assert (
            len(rows) == 1
            and json.loads(rows[0][0])["entries"][0]["change"] == "shared.file_updated"
        )
        body = {k: v for k, v in rule.items() if k not in ("id", "revision")}
        body["include_agent"] = True
        assert (
            user.put(
                "/workplace/api/shared/workflows/" + rule["id"], json=body
            ).status_code
            == 200
        )
        (w.shared.root / "Empty").rename(w.shared.root / "Renamed")
        w.shared.reconcile()
        rows = w.store._db.execute(
            "SELECT body FROM factory_events WHERE provider='shared' ORDER BY id"
        ).fetchall()
        assert (
            len(rows) == 2
            and json.loads(rows[-1][0])["entries"][0]["old_path"] == "Empty/one.pdf"
        )


def test_download_ranges_and_no_execution(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as user:
        upload(
            user, {"clip.mp4": b"0123456789", "page.html": b"<script>alert(1)</script>"}
        )
        r = user.get(
            "/workplace/api/shared/content?path=clip.mp4&preview=true",
            headers={"Range": "bytes=2-5"},
        )
        assert (
            r.status_code == 206
            and r.content == b"2345"
            and r.headers["content-range"] == "bytes 2-5/10"
        )
        assert (
            user.get(
                "/workplace/api/shared/content?path=clip.mp4",
                headers={"Range": "bytes=50-60"},
            ).status_code
            == 416
        )
        r = user.get("/workplace/api/shared/content?path=page.html&preview=true")
        assert r.headers["content-type"].startswith("text/plain")
        assert (
            "sandbox;" in r.headers["content-security-policy"]
            and "noindex" in r.headers["x-robots-tag"]
        )
        upload(
            user,
            {"spoof.pdf": b"<script>alert(1)</script>", "document.pdf": b"%PDF-1.4\n"},
        )
        r = user.get("/workplace/api/shared/content?path=spoof.pdf&preview=true")
        assert r.headers["content-type"].startswith("text/plain")
        assert "sandbox;" in r.headers["content-security-policy"]
        r = user.get("/workplace/api/shared/content?path=document.pdf&preview=true")
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in r.headers["content-security-policy"]


def test_invalid_operation_does_not_poison_change_tracking(workplace_server):
    w, url = workplace_server
    with signed_in(w, url) as user:
        r = user.post(
            "/workplace/api/shared/files",
            json={"action": "mkdir", "path": "missing/child"},
        )
        assert r.status_code == 409
        assert not w.store.docs("shared.operation")
        w.shared.reconcile()
        # Even a previously journaled operation blocked by an external rename
        # must not prevent unrelated agent writes from being recorded.
        w.store.put(
            "shared.operation",
            "blocked",
            {
                "id": "blocked",
                "action": "mkdir",
                "path": "missing/child",
                "status": "pending",
            },
        )
        (w.shared.root / "agent.txt").write_text("completed output")
        w.shared.maintain_once()
        w.shared.maintain_once()
        assert "agent.txt" in w.shared.entries()
        assert w.shared.error


def test_backup_failure_removes_only_its_partial_copy(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    mod = importlib.import_module("omniagents_workplace.backup")
    data = tmp_path / "data"
    data.mkdir()
    store = Store(data / "workplace.db")
    store.put("example", "saved", {"value": "keep"})
    (data / "shared").mkdir()
    (data / "shared/input.txt").write_text("input")
    complete = backup(data, tmp_path / "backups")

    def fail_copy(*args, **kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(mod, "_copy", fail_copy)
    with pytest.raises(shutil.Error):
        backup(data, tmp_path / "backups")
    assert complete.exists()
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert (data / "shared/input.txt").read_text() == "input"

    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    with pytest.raises(OSError, match="Backup needs approximately"):
        backup(data, tmp_path / "backups")
    assert complete.exists()
    assert not list((tmp_path / "backups").glob("*.partial"))
