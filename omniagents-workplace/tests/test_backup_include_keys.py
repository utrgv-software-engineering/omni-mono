import json
import sys

import pytest

from omniagents_workplace.backup import backup, export_keys, restore
from omniagents_workplace.store import Store


@pytest.fixture
def recovery_paths(tmp_path):
    data = tmp_path / "data"
    Store(data / "workplace.db").close()
    (data / "factory-secrets.key").write_text("synthetic-root-key")
    included = tmp_path / "runtime"
    (included / "nested").mkdir(parents=True)
    (included / "runtime.key").write_text("synthetic-runtime-key")
    (included / "nested" / "session.key").write_text("synthetic-session-key")
    (included / "config.json").write_text("{}")
    return data, included


@pytest.mark.parametrize("keys", [False, True])
def test_include_obeys_key_policy(recovery_paths, tmp_path, keys):
    data, included = recovery_paths
    made = backup(data, tmp_path / "backups", keys=keys, include={"runtime": included})
    copied = made / "additional" / "runtime"
    assert (copied / "runtime.key").exists() is keys
    assert (copied / "nested" / "session.key").exists() is keys
    assert (copied / "config.json").read_text() == "{}"
    metadata = json.loads((made / "backup-metadata.json").read_text())
    if not keys:
        assert "runtime/*.key" in metadata["excluded_additional_paths"]


def test_include_keys_round_trip_separately(recovery_paths, tmp_path):
    data, included = recovery_paths
    escrow = tmp_path / "escrow"
    paths = export_keys(data, escrow, include={"runtime": included})
    assert paths == [
        "factory-secrets.key",
        "additional/runtime/nested/session.key",
        "additional/runtime/runtime.key",
    ]
    assert all((escrow / path).stat().st_mode & 0o777 == 0o600 for path in paths)
    made = backup(data, tmp_path / "backups", keys=False, include={"runtime": included})
    assert not list(made.rglob("*.key"))
    restored = tmp_path / "restored"
    restored_runtime = tmp_path / "restored-runtime"
    restore(
        made,
        restored,
        mode="production",
        include={"runtime": restored_runtime},
        keys=escrow,
    )
    assert (restored / "factory-secrets.key").read_text() == "synthetic-root-key"
    assert (restored_runtime / "runtime.key").read_text() == "synthetic-runtime-key"
    assert (
        restored_runtime / "nested" / "session.key"
    ).read_text() == "synthetic-session-key"
    assert not (restored / "additional").exists()


def test_staging_does_not_restore_escrow_keys(recovery_paths, tmp_path):
    data, included = recovery_paths
    escrow = tmp_path / "escrow"
    export_keys(data, escrow, include={"runtime": included})
    made = backup(data, tmp_path / "backups", keys=False, include={"runtime": included})
    restored = tmp_path / "restored"
    restored_runtime = tmp_path / "restored-runtime"
    restore(made, restored, include={"runtime": restored_runtime}, keys=escrow)
    assert not list(restored.rglob("*.key"))
    assert not list(restored_runtime.rglob("*.key"))


def test_escrow_rejects_unknown_include_before_restore(recovery_paths, tmp_path):
    data, included = recovery_paths
    made = backup(data, tmp_path / "backups", keys=False, include={"runtime": included})
    escrow = tmp_path / "escrow"
    (escrow / "additional" / "unknown").mkdir(parents=True)
    (escrow / "additional" / "unknown" / "secret.key").write_text("synthetic")
    restored = tmp_path / "restored"
    with pytest.raises(ValueError, match="escrow"):
        restore(
            made,
            restored,
            mode="production",
            include={"runtime": tmp_path / "out"},
            keys=escrow,
        )
    assert not restored.exists()


@pytest.mark.parametrize("name", ["../escape", "nested/path", "", "bad\\path"])
def test_export_rejects_invalid_include_names(recovery_paths, tmp_path, name):
    data, included = recovery_paths
    with pytest.raises(ValueError, match="include names"):
        export_keys(data, tmp_path / "escrow", include={name: included})
    assert not (tmp_path / "escrow").exists()


def test_export_rejects_nonempty_destination(recovery_paths, tmp_path):
    data, included = recovery_paths
    escrow = tmp_path / "escrow"
    escrow.mkdir()
    marker = escrow / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        export_keys(data, escrow, include={"runtime": included})
    assert marker.read_text() == "keep"


def test_export_does_not_follow_key_symlinks(recovery_paths, tmp_path):
    data, included = recovery_paths
    outside = tmp_path / "outside.key"
    outside.write_text("synthetic-outside")
    (included / "linked.key").symlink_to(outside)
    escrow = tmp_path / "escrow"
    copied = export_keys(data, escrow, include={"runtime": included})
    assert "additional/runtime/linked.key" not in copied


def test_escrow_symlink_is_rejected_before_restore(recovery_paths, tmp_path):
    data, included = recovery_paths
    made = backup(data, tmp_path / "backups", keys=False, include={"runtime": included})
    escrow = tmp_path / "escrow"
    escrow.mkdir()
    (escrow / "factory-secrets.key").symlink_to(data / "factory-secrets.key")
    restored = tmp_path / "restored"
    with pytest.raises(ValueError, match="escrow"):
        restore(
            made,
            restored,
            mode="production",
            include={"runtime": tmp_path / "out"},
            keys=escrow,
        )
    assert not restored.exists()


def test_included_repository_keys_are_excluded(recovery_paths, tmp_path):
    data, included = recovery_paths
    Store(included / "workplace.db").close()
    repo = included / "git" / "owner" / "example.git"
    (repo / "objects").mkdir(parents=True)
    (repo / "objects" / "private.key").write_text("synthetic-object-key")
    (repo / "server.key").write_text("synthetic-repo-key")
    (repo / "HEAD").write_text("ref: refs/heads/main\n")
    made = backup(data, tmp_path / "backups", keys=False, include={"runtime": included})
    assert not list(made.rglob("*.key"))
    assert (made / "additional/runtime/git/owner/example.git/HEAD").is_file()


def test_existing_include_exclusions_still_apply(recovery_paths, tmp_path):
    data, included = recovery_paths
    made = backup(
        data,
        tmp_path / "backups",
        keys=False,
        include={"runtime": included},
        exclude=("runtime/config.json",),
    )
    assert not (made / "additional/runtime/config.json").exists()
    assert not list(made.rglob("*.key"))


def test_export_rejects_output_inside_source(recovery_paths):
    data, included = recovery_paths
    with pytest.raises(ValueError, match="outside"):
        export_keys(data, data / "escrow", include={"runtime": included})
    assert not (data / "escrow").exists()


def test_export_rejects_reserved_namespace(recovery_paths, tmp_path):
    data, included = recovery_paths
    (data / "additional").mkdir()
    (data / "additional" / "root.key").write_text("synthetic")
    with pytest.raises(ValueError, match="namespace"):
        export_keys(data, tmp_path / "escrow", include={"runtime": included})
    assert not (tmp_path / "escrow").exists()


def test_export_rejects_missing_include(recovery_paths, tmp_path):
    data, included = recovery_paths
    with pytest.raises(ValueError, match="exist"):
        export_keys(
            data, tmp_path / "escrow", include={"missing": tmp_path / "missing"}
        )
    assert not (tmp_path / "escrow").exists()


def test_restore_rejects_backed_symlink_target(recovery_paths, tmp_path):
    data, included = recovery_paths
    made = backup(data, tmp_path / "backups", keys=False, include={"runtime": included})
    outside = tmp_path / "outside"
    outside.mkdir()
    (made / "additional/runtime/nested").rmdir()
    (made / "additional/runtime/nested").symlink_to(outside, target_is_directory=True)
    escrow = tmp_path / "escrow"
    export_keys(data, escrow, include={"runtime": included})
    restored = tmp_path / "restored"
    with pytest.raises(ValueError, match="symlinks"):
        restore(
            made,
            restored,
            mode="production",
            keys=escrow,
            include={"runtime": tmp_path / "out"},
        )
    assert not restored.exists()
    assert not list(outside.iterdir())


def test_keys_cli_supports_named_includes(recovery_paths, tmp_path, monkeypatch):
    from omniagents_workplace.cli import main

    data, included = recovery_paths
    escrow = tmp_path / "escrow"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents-workplace",
            "keys",
            "export",
            "--data",
            str(data),
            "--to",
            str(escrow),
            "--include",
            f"runtime={included}",
        ],
    )
    main()
    assert (
        escrow / "additional/runtime/runtime.key"
    ).read_text() == "synthetic-runtime-key"


def test_keys_cli_rejects_duplicate_includes(recovery_paths, tmp_path, monkeypatch):
    from omniagents_workplace.cli import main

    data, included = recovery_paths
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents-workplace",
            "keys",
            "export",
            "--data",
            str(data),
            "--to",
            str(tmp_path / "escrow"),
            "--include",
            f"runtime={included}",
            "--include",
            f"runtime={included}",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not (tmp_path / "escrow").exists()
