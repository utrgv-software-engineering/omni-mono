"""Inject proxy environment variables into os.environ."""

import os


def build_proxy_env_vars(http_port: int, socks5_port: int = 0) -> dict[str, str]:
    """Build the dict of proxy env vars to set.

    Does NOT include vars whose keys already exist in os.environ
    (respects user-set proxies).
    """
    http_url = f"http://127.0.0.1:{http_port}"
    env: dict[str, str] = {}

    # HTTP/HTTPS proxy — both casings for compatibility
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        if key not in os.environ:
            env[key] = http_url

    # SOCKS5 proxy
    if socks5_port:
        socks_url = f"socks5://127.0.0.1:{socks5_port}"
        for key in ("ALL_PROXY", "all_proxy"):
            if key not in os.environ:
                env[key] = socks_url

    # NO_PROXY — prevent proxying local services
    for key in ("NO_PROXY", "no_proxy"):
        if key not in os.environ:
            env[key] = "127.0.0.1,localhost,::1"

    # Package manager proxies
    pm_vars = {
        "npm_config_proxy": http_url,
        "npm_config_https_proxy": http_url,
        "YARN_HTTP_PROXY": http_url,
        "YARN_HTTPS_PROXY": http_url,
    }
    for key, val in pm_vars.items():
        if key not in os.environ:
            env[key] = val

    return env


def inject_into_os_environ(env_vars: dict[str, str]) -> list[str]:
    """Write env vars into os.environ. Returns list of keys that were set."""
    injected = []
    for key, val in env_vars.items():
        os.environ[key] = val
        injected.append(key)
    return injected


def remove_from_os_environ(keys: list[str]) -> None:
    """Remove previously injected env vars."""
    for key in keys:
        os.environ.pop(key, None)
