"""Tests for the network isolation proxy module."""

import asyncio
import os
import socket
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from omni_code.proxy.config import NetworkConfig, load_network_config, save_user_network_config, get_network_config_path
from omni_code.proxy.policy import DomainPolicy
from omni_code.proxy.env_injector import build_proxy_env_vars, inject_into_os_environ, remove_from_os_environ
from omni_code.proxy.manager import start_proxy, stop_proxy


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path):
    """Prevent tests from reading the real ~/.config/omni_code/network.json."""
    fake_path = tmp_path / "network.json"  # does not exist
    with patch("omni_code.proxy.config.get_network_config_path", return_value=fake_path):
        yield


# ======================================================================
# DomainPolicy tests
# ======================================================================


class TestDomainPolicy:
    """Tests for domain allowlist/denylist policy."""

    def test_empty_allowlist_blocks_everything(self):
        policy = DomainPolicy(allowlist=[])
        assert not policy.is_allowed("example.com")
        assert not policy.is_allowed("api.openai.com")

    def test_wildcard_allows_everything(self):
        policy = DomainPolicy(allowlist=["*"])
        assert policy.is_allowed("example.com")
        assert policy.is_allowed("anything.test")

    def test_exact_match(self):
        policy = DomainPolicy(allowlist=["api.openai.com"])
        assert policy.is_allowed("api.openai.com")
        assert not policy.is_allowed("evil.com")
        assert not policy.is_allowed("openai.com")

    def test_single_star_subdomains_only(self):
        """*.example.com matches sub.example.com but NOT example.com."""
        policy = DomainPolicy(allowlist=["*.example.com"])
        assert policy.is_allowed("sub.example.com")
        assert policy.is_allowed("deep.sub.example.com")
        assert not policy.is_allowed("example.com")

    def test_double_star_apex_and_subdomains(self):
        """**.example.com matches example.com AND sub.example.com."""
        policy = DomainPolicy(allowlist=["**.example.com"])
        assert policy.is_allowed("example.com")
        assert policy.is_allowed("sub.example.com")
        assert policy.is_allowed("deep.sub.example.com")
        assert not policy.is_allowed("notexample.com")

    def test_denylist_overrides_allowlist(self):
        policy = DomainPolicy(
            allowlist=["**.example.com"],
            denylist=["evil.example.com"],
        )
        assert policy.is_allowed("good.example.com")
        assert not policy.is_allowed("evil.example.com")

    def test_localhost_always_allowed(self):
        policy = DomainPolicy(allowlist=[])
        assert policy.is_allowed("localhost")
        assert policy.is_allowed("127.0.0.1")
        assert policy.is_allowed("::1")

    def test_case_insensitive(self):
        policy = DomainPolicy(allowlist=["**.OpenAI.COM"])
        assert policy.is_allowed("API.OPENAI.COM")
        assert policy.is_allowed("api.openai.com")

    def test_trailing_dot_stripped(self):
        policy = DomainPolicy(allowlist=["api.openai.com"])
        assert policy.is_allowed("api.openai.com.")

    def test_multiple_allowlist_entries(self):
        policy = DomainPolicy(allowlist=[
            "**.openai.com",
            "**.anthropic.com",
            "**.pypi.org",
        ])
        assert policy.is_allowed("api.openai.com")
        assert policy.is_allowed("anthropic.com")
        assert policy.is_allowed("pypi.org")
        assert not policy.is_allowed("evil.com")

    def test_private_ip_blocked_by_default(self):
        policy = DomainPolicy(allowlist=["*"], allow_private_ips=False)
        assert not policy.is_allowed("10.0.0.1")
        assert not policy.is_allowed("192.168.1.1")
        assert not policy.is_allowed("172.16.0.1")

    def test_private_ip_allowed_when_configured(self):
        policy = DomainPolicy(allowlist=["*"], allow_private_ips=True)
        assert policy.is_allowed("10.0.0.1")
        assert policy.is_allowed("192.168.1.1")


# ======================================================================
# Config tests
# ======================================================================


class TestNetworkConfig:
    """Tests for configuration loading."""

    def test_defaults_disabled(self):
        config = load_network_config()
        assert not config.enabled
        assert config.allowlist == []

    def test_env_var_overrides(self):
        with patch.dict(os.environ, {"OMNI_NETWORK_ALLOWLIST": "**.openai.com, **.pypi.org"}):
            config = load_network_config()
        assert config.enabled
        assert config.allowlist == ["**.openai.com", "**.pypi.org"]

    def test_empty_env_var_ignored(self):
        with patch.dict(os.environ, {"OMNI_NETWORK_ALLOWLIST": ""}):
            config = load_network_config()
        assert not config.enabled

    def test_load_from_yaml(self):
        yaml_content = """\
name: test
network:
  enabled: true
  allowlist:
    - "**.openai.com"
    - "**.anthropic.com"
  denylist:
    - "evil.com"
  allow_private_ips: true
  enable_socks5: false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                config = load_network_config(f.name)
            finally:
                os.unlink(f.name)

        assert config.enabled
        assert config.allowlist == ["**.openai.com", "**.anthropic.com"]
        assert config.denylist == ["evil.com"]
        assert config.allow_private_ips is True
        assert config.enable_socks5 is False

    def test_missing_network_section(self):
        yaml_content = "name: test\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                config = load_network_config(f.name)
            finally:
                os.unlink(f.name)
        assert not config.enabled

    def test_nonexistent_file(self):
        config = load_network_config("/nonexistent/path.yml")
        assert not config.enabled

    def test_user_config_overrides_project(self):
        """User ~/.config/omni_code/network.json overrides project.yml."""
        yaml_content = """\
name: test
network:
  enabled: true
  allowlist:
    - "**.openai.com"
  allow_private_ips: false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            yml_path = f.name

        # User config adds more domains and flips allow_private_ips
        user_config = {
            "allowlist": ["**.anthropic.com", "**.custom.dev"],
            "allow_private_ips": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_config_dir = Path(tmpdir)
            network_json = fake_config_dir / "network.json"
            import json
            network_json.write_text(json.dumps(user_config))

            with patch("omni_code.proxy.config.get_network_config_path", return_value=network_json):
                try:
                    config = load_network_config(yml_path)
                finally:
                    os.unlink(yml_path)

        # User's allowlist replaces project's allowlist
        assert config.enabled  # from project
        assert config.allowlist == ["**.anthropic.com", "**.custom.dev"]
        assert config.allow_private_ips is True  # user override

    def test_user_config_alone(self):
        """User config works even without project.yml network section."""
        user_config = {
            "enabled": True,
            "allowlist": ["**.mycompany.com"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            network_json = Path(tmpdir) / "network.json"
            import json
            network_json.write_text(json.dumps(user_config))

            with patch("omni_code.proxy.config.get_network_config_path", return_value=network_json):
                config = load_network_config()

        assert config.enabled
        assert config.allowlist == ["**.mycompany.com"]

    def test_save_user_network_config(self):
        """save_user_network_config writes to the correct path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = Path(tmpdir) / "network.json"
            with patch("omni_code.proxy.config.get_network_config_path", return_value=fake_path):
                save_user_network_config({
                    "enabled": True,
                    "allowlist": ["**.example.com"],
                })
            import json
            saved = json.loads(fake_path.read_text())
            assert saved["enabled"] is True
            assert saved["allowlist"] == ["**.example.com"]


# ======================================================================
# Env injector tests
# ======================================================================


class TestEnvInjector:
    """Tests for environment variable injection."""

    def test_build_proxy_env_vars(self):
        env = build_proxy_env_vars(8080, 1080)
        assert env["HTTP_PROXY"] == "http://127.0.0.1:8080"
        assert env["HTTPS_PROXY"] == "http://127.0.0.1:8080"
        assert env["http_proxy"] == "http://127.0.0.1:8080"
        assert env["ALL_PROXY"] == "socks5://127.0.0.1:1080"
        assert "127.0.0.1" in env["NO_PROXY"]

    def test_no_socks5_when_port_zero(self):
        env = build_proxy_env_vars(8080, 0)
        assert "ALL_PROXY" not in env

    def test_respects_existing_env(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://user-proxy:3128"}):
            env = build_proxy_env_vars(8080, 1080)
        assert "HTTP_PROXY" not in env
        assert "HTTPS_PROXY" in env  # not set by user

    def test_inject_and_remove(self):
        env = {"TEST_PROXY_VAR_1": "val1", "TEST_PROXY_VAR_2": "val2"}
        keys = inject_into_os_environ(env)
        assert os.environ["TEST_PROXY_VAR_1"] == "val1"
        assert os.environ["TEST_PROXY_VAR_2"] == "val2"

        remove_from_os_environ(keys)
        assert "TEST_PROXY_VAR_1" not in os.environ
        assert "TEST_PROXY_VAR_2" not in os.environ


# ======================================================================
# Integration: proxy start/stop
# ======================================================================


class TestProxyLifecycle:
    """Integration tests for proxy startup and shutdown."""

    def test_start_and_stop(self):
        config = NetworkConfig(
            enabled=True,
            allowlist=["**.example.com"],
            enable_socks5=True,
        )
        handle = start_proxy(config)
        try:
            assert handle.http_port > 0
            assert handle.socks5_port > 0
            assert handle.thread.is_alive()

            # Verify env vars were injected
            assert os.environ.get("HTTP_PROXY") == f"http://127.0.0.1:{handle.http_port}"
            assert os.environ.get("ALL_PROXY") == f"socks5://127.0.0.1:{handle.socks5_port}"

            # Verify proxy is listening
            with socket.create_connection(("127.0.0.1", handle.http_port), timeout=2):
                pass  # connection succeeded
            with socket.create_connection(("127.0.0.1", handle.socks5_port), timeout=2):
                pass  # connection succeeded
        finally:
            handle.stop()
            # Env vars should be cleaned up
            assert "HTTP_PROXY" not in os.environ

    def test_http_connect_blocked(self):
        """Verify that CONNECT to a non-allowlisted host gets 403."""
        config = NetworkConfig(
            enabled=True,
            allowlist=["**.allowed.com"],
            enable_socks5=False,
        )
        handle = start_proxy(config)
        try:
            # Send a CONNECT request to a blocked host
            with socket.create_connection(("127.0.0.1", handle.http_port), timeout=5) as s:
                s.sendall(b"CONNECT blocked.com:443 HTTP/1.1\r\nHost: blocked.com\r\n\r\n")
                response = s.recv(4096)
                assert b"403" in response
        finally:
            handle.stop()

    def test_socks5_blocked(self):
        """Verify that SOCKS5 CONNECT to a blocked host gets NOT_ALLOWED."""
        config = NetworkConfig(
            enabled=True,
            allowlist=["**.allowed.com"],
            enable_socks5=True,
        )
        handle = start_proxy(config)
        try:
            with socket.create_connection(("127.0.0.1", handle.socks5_port), timeout=5) as s:
                # Greeting: version 5, 1 method, no-auth
                s.sendall(bytes([0x05, 0x01, 0x00]))
                greeting_resp = s.recv(2)
                assert greeting_resp == bytes([0x05, 0x00])  # no-auth accepted

                # CONNECT request to blocked.com:443
                domain = b"blocked.com"
                request = bytes([
                    0x05, 0x01, 0x00,  # VER, CMD=CONNECT, RSV
                    0x03,              # ATYP=DOMAIN
                    len(domain),
                ]) + domain + struct.pack("!H", 443)
                s.sendall(request)

                reply = s.recv(10)
                assert len(reply) >= 2
                assert reply[1] == 0x02  # NOT_ALLOWED
        finally:
            handle.stop()
