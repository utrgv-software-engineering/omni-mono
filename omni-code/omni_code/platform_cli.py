"""CLI for platform authentication.

Usage:
    omni auth login [url]     Authenticate with the management plane
    omni auth logout          Clear stored credentials
    omni auth status          Show current authentication status
"""

import sys


def main(args: list[str]) -> None:
    if not args or args[0] in {"-h", "--help", "help"}:
        print(
            "usage: omni auth <command>\n\n"
            "Commands:\n"
            "  login [url]    Authenticate with the management plane\n"
            "  logout         Clear stored credentials\n"
            "  status         Show current authentication status\n"
        )
        return

    command = args[0]

    if command == "login":
        _login(args[1:])
    elif command == "logout":
        _logout()
    elif command == "status":
        _status()
    else:
        print(f"Unknown auth command: {command}", file=sys.stderr)
        sys.exit(1)


def _login(args: list[str]) -> None:
    from omni_code.platform import (
        device_code_login,
        get_platform_url,
        save_credentials,
    )

    # URL from args, env, or stored credentials
    url = args[0] if args else get_platform_url()

    if not url:
        url = input("Platform URL: ").strip()

    if not url:
        print("No platform URL provided.", file=sys.stderr)
        sys.exit(1)

    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        creds = device_code_login(url)
        save_credentials(creds)

        user = creds.get("user", {})
        print(f"Authenticated as {user.get('name', 'unknown')} ({user.get('email', '')})")

        domains = user.get("domains", [])
        if domains:
            print(f"Domains: {', '.join(d['name'] for d in domains)}")

        print(f"\nCredentials saved to ~/.config/omni_code/platform.json")
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)


def _logout() -> None:
    from omni_code.platform import clear_credentials, load_credentials

    creds = load_credentials()
    if creds:
        user = creds.get("user", {})
        clear_credentials()
        print(f"Logged out ({user.get('email', 'unknown')})")
    else:
        print("Not logged in.")


def _status() -> None:
    from omni_code.platform import get_access_token, get_platform_url, load_credentials

    url = get_platform_url()
    token = get_access_token()

    if not url or not token:
        print("Not authenticated.")
        print("\nRun: omni auth login <platform-url>")
        return

    creds = load_credentials()
    user = creds.get("user", {}) if creds else {}

    print(f"Platform:  {url}")
    print(f"User:      {user.get('name', 'unknown')} ({user.get('email', '')})")
    print(f"Token:     {token[:20]}...")

    domains = user.get("domains", [])
    if domains:
        print(f"Domains:   {', '.join(d['name'] for d in domains)}")

    # Test connectivity
    try:
        import requests

        resp = requests.get(
            f"{url}/api/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.ok:
            print(f"Status:    connected")
        elif resp.status_code == 401:
            print(f"Status:    token expired (run: omni auth login)")
        else:
            print(f"Status:    error ({resp.status_code})")
    except Exception as e:
        print(f"Status:    unreachable ({e})")
