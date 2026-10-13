"""Layered configuration engine: provenance, validation, atomic writes,
and secret redaction (omniagents/core/config/layered.py)."""

import json
import os
from pathlib import Path

import pytest
import yaml

from omniagents.core.config.layered import (
    CONFIG_FIELDS,
    CONFIG_FIELDS_BY_KEY,
    GUI_OVERLAY_FILENAME,
    LayeredConfigStore,
    RELOAD_RESTART,
    RELOAD_SESSION,
)

PRODUCT = "cfgtest"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated config-dir + overlay environment for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "xdg"))  # Windows config root
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    return tmp_path


def make_manifest(tmp_path: Path, extra: dict | None = None) -> Path:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    doc: dict = {"product": {"name": PRODUCT}}
    if extra:
        doc.update(extra)
    manifest = root / "project.yml"
    manifest.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return manifest


def write_user_overlay(tmp_path: Path, name: str, doc: dict) -> Path:
    overlay_dir = tmp_path / "xdg" / PRODUCT / "project.d"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    path = overlay_dir / name
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def write_managed_overlay(tmp_path: Path, monkeypatch, doc: dict) -> Path:
    path = tmp_path / "managed-policy.yml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setenv("OMNIAGENTS_PROJECT_OVERLAYS", str(path))
    return path


def field_by_key(described: dict, key: str) -> dict:
    return next(f for f in described["fields"] if f["key"] == key)


# ---------------------------------------------------------------------------
# Provenance per layer
# ---------------------------------------------------------------------------


def test_defaults_layer_provenance(env):
    store = LayeredConfigStore(make_manifest(env))
    field = field_by_key(store.describe(), "security.safety_mode")
    assert field["is_set"] is True
    assert field["value"] == "recommended"
    assert field["effective_layer"] == "defaults"
    assert field["read_only"] is False
    assert field["read_only_reason"] is None
    assert field["layers"] == [
        {"layer": "defaults", "source": None, "is_set": True, "value": "recommended"}
    ]


def test_project_layer_provenance(env):
    manifest = make_manifest(env, {"security": {"safety_mode": "enforced"}})
    store = LayeredConfigStore(manifest)
    field = field_by_key(store.describe(), "security.safety_mode")
    assert field["value"] == "enforced"
    assert field["effective_layer"] == "project"
    assert [entry["layer"] for entry in field["layers"]] == ["defaults", "project"]
    assert field["layers"][-1]["source"] == str(manifest)


def test_user_overlays_win_over_project_in_lexical_order(env):
    manifest = make_manifest(env, {"security": {"safety_mode": "off"}})
    write_user_overlay(env, "10-a.yml", {"security": {"safety_mode": "enforced"}})
    later = write_user_overlay(
        env, "20-b.yml", {"security": {"safety_mode": "recommended"}}
    )
    store = LayeredConfigStore(manifest)
    field = field_by_key(store.describe(), "security.safety_mode")
    assert field["value"] == "recommended"
    assert field["effective_layer"] == "user"
    assert field["layers"][-1]["source"] == str(later)
    assert [entry["layer"] for entry in field["layers"]] == [
        "defaults",
        "project",
        "user",
        "user",
    ]
    # A user-layer value overriding a project one is not read-only.
    assert field["read_only"] is False


def test_managed_overlay_makes_field_read_only(env, monkeypatch):
    manifest = make_manifest(env)
    write_user_overlay(env, "10-a.yml", {"security": {"mcp_require_allowlist": False}})
    managed = write_managed_overlay(
        env, monkeypatch, {"security": {"mcp_require_allowlist": True}}
    )
    store = LayeredConfigStore(manifest)
    field = field_by_key(store.describe(), "security.mcp_require_allowlist")
    assert field["value"] is True
    assert field["effective_layer"] == "managed"
    assert field["read_only"] is True
    assert field["read_only_reason"] == "managed_policy"
    assert field["layers"][-1]["source"] == str(managed)

    # Writing an overridden field is refused with a structured error.
    result = store.write({"security.mcp_require_allowlist": False})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "read_only"
    assert result["errors"][0]["reason"] == "managed_policy"
    assert result["written"] == []


def test_environment_override_wins_and_is_read_only(env, monkeypatch):
    manifest = make_manifest(env, {"security": {"legacy_query_auth": True}})
    monkeypatch.setenv("OMNIAGENTS_LEGACY_QUERY_AUTH", "0")
    store = LayeredConfigStore(manifest)
    field = field_by_key(store.describe(), "security.legacy_query_auth")
    # The env var overrides the config key even when it disables the flag.
    assert field["value"] is False
    assert field["effective_layer"] == "environment"
    assert field["read_only"] is True
    assert field["read_only_reason"] == "environment"
    assert field["layers"][-1]["source"] == "OMNIAGENTS_LEGACY_QUERY_AUTH"

    errors, _ = store.validate_updates({"security.legacy_query_auth": True})
    assert errors[0]["code"] == "read_only"
    assert errors[0]["reason"] == "environment"


def test_structural_read_only_field(env):
    store = LayeredConfigStore(make_manifest(env))
    field = field_by_key(store.describe(), "product.name")
    assert field["read_only"] is True
    assert field["read_only_reason"] == "structural"
    result = store.write({"product.name": "other"})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "read_only"


def test_layer_descriptors_and_write_target(env):
    manifest = make_manifest(env)
    overlay = write_user_overlay(env, "10-a.yml", {"tracing": {"provider": "x"}})
    store = LayeredConfigStore(manifest)
    layers = {layer["name"]: layer for layer in store.describe()["layers"]}
    assert layers["project"]["sources"] == [str(manifest)]
    assert layers["project"]["writable"] is False
    assert layers["user"]["writable"] is True
    assert layers["user"]["sources"] == [str(overlay)]
    assert layers["user"]["write_target"].endswith(GUI_OVERLAY_FILENAME)
    assert layers["managed"]["sources"] == []


def test_store_without_manifest_still_describes(env):
    store = LayeredConfigStore(None)
    described = store.describe()
    layers = {layer["name"]: layer for layer in described["layers"]}
    assert layers["project"]["sources"] == []
    field = field_by_key(described, "security.safety_mode")
    assert field["effective_layer"] == "defaults"


# ---------------------------------------------------------------------------
# Dry-run validation
# ---------------------------------------------------------------------------


def test_validate_structured_errors(env):
    store = LayeredConfigStore(make_manifest(env))
    result = store.validate(
        {
            "nope.nothing": 1,
            "security.safety_mode": "sideways",
            "security.audit.retention_days": -3,
            "allow_user_mcp_servers": "yes",
            "security.blocked_tools": ["ok", 5],
        }
    )
    assert result["valid"] is False
    by_key = {error["key"]: error for error in result["errors"]}
    assert by_key["nope.nothing"]["code"] == "unknown_key"
    assert by_key["security.safety_mode"]["code"] == "invalid_value"
    assert by_key["security.safety_mode"]["allowed_values"] == [
        "recommended",
        "enforced",
        "off",
    ]
    assert by_key["security.audit.retention_days"]["code"] == "out_of_range"
    assert by_key["security.audit.retention_days"]["minimum"] == 0
    assert by_key["allow_user_mcp_servers"]["code"] == "invalid_type"
    assert by_key["allow_user_mcp_servers"]["expected_type"] == "boolean"
    assert by_key["security.blocked_tools"]["code"] == "invalid_type"


def test_validate_valid_batch_reports_reload(env):
    store = LayeredConfigStore(make_manifest(env))
    result = store.validate(
        {"security.safety_mode": "enforced", "tracing.provider": "custom"}
    )
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["reload"][RELOAD_SESSION] == ["security.safety_mode"]
    assert result["reload"][RELOAD_RESTART] == ["tracing.provider"]


def test_validate_never_echoes_submitted_values(env):
    store = LayeredConfigStore(make_manifest(env))
    leaked = "sk-super-secret-value"
    result = store.validate({"security.audit.retention_days": leaked})
    assert result["valid"] is False
    assert leaked not in json.dumps(result)


# ---------------------------------------------------------------------------
# Writes: atomicity, rollback, clears
# ---------------------------------------------------------------------------


def test_write_single_value_lands_in_user_layer(env):
    store = LayeredConfigStore(make_manifest(env))
    result = store.write({"security.safety_mode": "enforced"})
    assert result["ok"] is True
    assert result["written"] == ["security.safety_mode"]
    assert result["restart_required"] is False
    assert result["reload"][RELOAD_SESSION] == ["security.safety_mode"]

    target = store.user_write_target()
    assert target.is_file()
    assert yaml.safe_load(target.read_text()) == {
        "security": {"safety_mode": "enforced"}
    }
    field = field_by_key(store.describe(), "security.safety_mode")
    assert field["value"] == "enforced"
    assert field["effective_layer"] == "user"
    assert field["layers"][-1]["source"] == str(target)


def test_write_restart_required_aggregation(env):
    store = LayeredConfigStore(make_manifest(env))
    result = store.write(
        {"tracing.provider": "custom", "security.safety_mode": "enforced"}
    )
    assert result["ok"] is True
    assert result["restart_required"] is True
    assert result["reload"][RELOAD_RESTART] == ["tracing.provider"]


def test_batch_write_is_atomic_and_rolls_back_on_validation_failure(env):
    store = LayeredConfigStore(make_manifest(env))
    assert store.write({"tracing.provider": "before"})["ok"] is True
    target = store.user_write_target()
    before = target.read_bytes()

    result = store.write(
        {
            "tracing.provider": "after",  # valid on its own
            "security.safety_mode": "bogus",  # invalid: aborts the batch
        }
    )
    assert result["ok"] is False
    assert result["written"] == []
    assert [error["code"] for error in result["errors"]] == ["invalid_value"]
    # Nothing was written: file bytes and effective value are unchanged.
    assert target.read_bytes() == before
    assert field_by_key(store.describe(), "tracing.provider")["value"] == "before"


def test_null_clears_user_layer_value(env):
    manifest = make_manifest(env, {"security": {"safety_mode": "off"}})
    store = LayeredConfigStore(manifest)
    assert store.write({"security.safety_mode": "enforced"})["ok"] is True
    result = store.write({"security.safety_mode": None})
    assert result["ok"] is True
    assert result["cleared"] == ["security.safety_mode"]
    assert result["written"] == []
    # Reverts to the project-layer value; emptied mappings are pruned.
    field = field_by_key(store.describe(), "security.safety_mode")
    assert field["value"] == "off"
    assert field["effective_layer"] == "project"
    assert yaml.safe_load(store.user_write_target().read_text()) == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_overlay_file_written_with_restrictive_permissions(env):
    store = LayeredConfigStore(make_manifest(env))
    assert store.write({"security.providers.auth.token": "hunter2-token"})["ok"]
    mode = store.user_write_target().stat().st_mode & 0o777
    assert mode == 0o600


def test_write_preserves_unmanaged_keys_in_gui_overlay(env):
    store = LayeredConfigStore(make_manifest(env))
    assert store.write({"tracing.provider": "one"})["ok"] is True
    assert store.write({"security.safety_mode": "enforced"})["ok"] is True
    assert yaml.safe_load(store.user_write_target().read_text()) == {
        "tracing": {"provider": "one"},
        "security": {"safety_mode": "enforced"},
    }


# ---------------------------------------------------------------------------
# Secret fields: write-only, never read back through ANY response
# ---------------------------------------------------------------------------

SECRET_KEY = "security.providers.auth.token"


def test_secret_values_never_appear_in_describe(env):
    manifest = make_manifest(
        env, {"security": {"providers": {"auth": {"token": "hunter2-project"}}}}
    )
    write_user_overlay(
        env,
        "10-a.yml",
        {"security": {"providers": {"auth": {"token": "hunter2-user"}}}},
    )
    store = LayeredConfigStore(manifest)
    described = store.describe()
    dumped = json.dumps(described)
    assert "hunter2-project" not in dumped
    assert "hunter2-user" not in dumped

    field = field_by_key(described, SECRET_KEY)
    assert field["secret"] is True
    assert field["is_set"] is True
    assert "value" not in field
    assert all("value" not in entry for entry in field["layers"])
    # Provenance still explains WHERE it is set, just not what it is.
    assert [entry["layer"] for entry in field["layers"]] == ["project", "user"]


def test_secret_value_never_echoed_by_write_or_later_reads(env):
    store = LayeredConfigStore(make_manifest(env))
    secret = "hunter2-written"
    result = store.write({SECRET_KEY: secret})
    assert result["ok"] is True
    assert secret not in json.dumps(result)
    field = next(f for f in result["fields"] if f["key"] == SECRET_KEY)
    assert field["is_set"] is True
    assert "value" not in field

    # The value IS persisted (write-only storage)...
    assert secret in store.user_write_target().read_text()
    # ...but no read path returns it: describe and validate stay clean.
    assert secret not in json.dumps(store.describe())
    assert secret not in json.dumps(store.validate({SECRET_KEY: "replacement"}))


def test_secret_clear_via_null(env):
    store = LayeredConfigStore(make_manifest(env))
    assert store.write({SECRET_KEY: "hunter2-x"})["ok"] is True
    result = store.write({SECRET_KEY: None})
    assert result["ok"] is True
    assert field_by_key(store.describe(), SECRET_KEY)["is_set"] is False
    assert "hunter2-x" not in store.user_write_target().read_text()


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


def test_registry_keys_are_unique_and_typed():
    assert len(CONFIG_FIELDS_BY_KEY) == len(CONFIG_FIELDS)
    for field in CONFIG_FIELDS:
        assert field.type in {"string", "boolean", "integer", "string_list"}
        assert field.reload in {"hot", "session", "restart"}


def test_every_field_documents_reload_behavior(env):
    store = LayeredConfigStore(make_manifest(env))
    for field in store.describe()["fields"]:
        assert field["reload"] in {"hot", "session", "restart"}
