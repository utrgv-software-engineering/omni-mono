"""Environments and encrypted credentials, as Rails (and Jumpstart) have them."""

import os
import stat
import subprocess
import sys

import pytest
import yaml

from omniagents_workplace.config import ConfigError, apply, load_config
from omniagents_workplace.credentials import (
    Credentials,
    CredentialsError,
    decrypt,
    encrypt,
)
from omniagents_workplace.server import Workplace


def _name_admin(creds: Credentials) -> None:
    """Fill in the template's empty admin, as ``credentials edit`` would."""
    creds.write(
        creds.read()
        .replace('name: ""', 'name: "Bill Lumbergh"', 1)
        .replace('username: ""', 'username: "bill"', 1)
        .replace('email: ""', 'email: "bill@initech.test"', 1)
    )


def test_credentials_encrypt_with_their_environments_key(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKPLACE_MASTER_KEY", raising=False)
    production = Credentials(tmp_path, "production")
    key = production.create()
    _name_admin(production)
    assert len(key) == 64 and stat.S_IMODE(production.key_path.stat().st_mode) == 0o600
    assert "config/credentials/*.key" in (tmp_path / ".gitignore").read_text()
    values = production.values()
    assert (
        values["admin"]["name"] == "Bill Lumbergh"
        and len(values["admin"]["password"]) >= 20
    )
    assert values["factory"]["auth_token"]
    assert "Bill" not in production.path.read_text()  # encrypted on disk
    # Another environment's key does not open it; WORKPLACE_MASTER_KEY wins over the key file.
    staging = Credentials(tmp_path, "staging")
    staging.create()
    with pytest.raises(CredentialsError, match="do not decrypt"):
        decrypt(production.path.read_text(), staging.key())
    monkeypatch.setenv("WORKPLACE_MASTER_KEY", key)
    assert Credentials(tmp_path, "production").get("admin.username") == "bill"
    with pytest.raises(CredentialsError, match="no admin.nope"):
        Credentials(tmp_path, "production").get("admin.nope")
    assert decrypt(encrypt("a: 1\n", key), key) == "a: 1\n"


def test_credentials_edit_opens_the_editor_and_encrypts_what_is_saved(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("WORKPLACE_MASTER_KEY", raising=False)
    editor = tmp_path / "editor.py"
    editor.write_text(
        "import sys\np = sys.argv[1]\ns = open(p).read()\nopen(p, 'w').write(s + 'smtp: {password: hunter2}\\n')\n"
    )
    env = {**os.environ, "EDITOR": f"{sys.executable} {editor}"}
    run = lambda *a: subprocess.run(
        [
            sys.executable,
            "-m",
            "omniagents_workplace.cli",
            *a,
            "--root",
            str(tmp_path),
        ],  # noqa: E731
        capture_output=True,
        text=True,
        env=env,
    )
    made = run("credentials", "edit", "--environment", "development")
    assert made.returncode == 0, made.stderr
    shown = run("credentials", "show", "--environment", "development")
    assert "smtp: {password: hunter2}" in shown.stdout and "auth_token" in shown.stdout
    assert run("credentials", "show", "--environment", "production").returncode != 0


def test_the_config_reads_credentials_and_seeds_the_admins_password(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("WORKPLACE_MASTER_KEY", raising=False)
    creds = Credentials(tmp_path, "production")
    creds.create()
    _name_admin(creds)
    (tmp_path / "workplace.yml").write_text(
        yaml.safe_dump(
            {
                "organization": {
                    "name": "Initech",
                    "slug": "initech",
                    "tz": "America/New_York",
                },
                "admin": "${credentials.admin}",
                "factory": {
                    "url": "${FACTORY_URL:-http://127.0.0.1:8765}",
                    "auth_token": "${credentials.factory.auth_token}",
                },
                "app": {"name": "Initech Factory", "username": "initech-factory"},
            }
        )
    )
    config = load_config(
        tmp_path / "workplace.yml", Credentials(tmp_path, "production")
    )
    assert config["admin"]["username"] == "bill" and config["factory"]["auth_token"]
    assert config["factory"]["url"] == "http://127.0.0.1:8765"
    with pytest.raises(ConfigError, match="no credentials"):
        load_config(tmp_path / "workplace.yml")

    workplace = Workplace(
        tmp_path / "data", base_url="https://w.example", environment="production"
    )
    try:
        applied = apply(workplace, config, config_dir=tmp_path)
        assert "Bill Lumbergh's password, from the config" in applied.made
        account, _ = workplace.accounts.sign_in("bill", creds.get("admin.password"))
        assert account.admin
    finally:
        workplace.close()


def test_a_server_environment_refuses_what_a_server_must_not_do(tmp_path):
    def serve(*args):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "omniagents_workplace.cli",
                "serve",
                "--data",
                str(tmp_path / "d"),
                "--port",
                "0",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    refused = serve("--environment", "production")
    assert refused.returncode != 0
    assert (
        "--workflows host" in refused.stderr
        and "--trust-proxy" in refused.stderr
        and "https://" in refused.stderr
    )
