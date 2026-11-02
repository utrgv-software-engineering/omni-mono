"""Host-bound HTTPS login cookies beside untrusted preview application origins."""

from http.cookies import CookieError, SimpleCookie

NAMES = {"workplace_session", "omni_deployment_access", "omni_deployment_state"}


class HostCookies:
    def __init__(self, app, base_url):
        self.app = app
        self.enabled = base_url.startswith("https://")

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = []
        for key, value in scope.get("headers", []):
            if key.lower() == b"cookie":
                cookies = SimpleCookie()
                try:
                    cookies.load(value.decode("latin1"))
                except CookieError:
                    cookies.clear()
                # Do not accept legacy unprefixed login cookies: a preview's
                # JavaScript could set a parent-domain cookie with that name.
                for name in NAMES:
                    cookies.pop(name, None)
                    prefixed = "__Host-" + name
                    if prefixed in cookies:
                        cookies[name] = cookies.pop(prefixed).value
                value = "; ".join(
                    f"{name}={cookie.coded_value}" for name, cookie in cookies.items()
                ).encode("latin1")
            headers.append((key, value))

        async def host_only(message):
            if message["type"] == "http.response.start":
                headers = []
                for key, value in message.get("headers", []):
                    if key.lower() == b"set-cookie":
                        name, _, _rest = value.partition(b"=")
                        if name.decode("latin1") in NAMES:
                            cookie = SimpleCookie()
                            cookie.load(value.decode("latin1"))
                            morsel = cookie[name.decode("latin1")]
                            morsel["secure"] = True
                            morsel["path"] = "/"
                            morsel["domain"] = ""
                            value = ("__Host-" + morsel.OutputString()).encode("latin1")
                    headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app({**scope, "headers": headers}, receive, host_only)
