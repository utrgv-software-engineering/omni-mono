"""Crawler policy for Workplace hosts, including mounted factory responses."""

from starlette.responses import PlainTextResponse


class NoIndex:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def headers(message):
            if message["type"] == "http.response.start":
                values = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {b"x-robots-tag", b"referrer-policy"}
                ]
                values.extend(
                    [
                        (b"x-robots-tag", b"noindex, nofollow, noimageindex"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                if "/share/" in scope.get("path", "") or "/api/sharing/" in scope.get(
                    "path", ""
                ):
                    values = [
                        (k, v) for k, v in values if k.lower() != b"cache-control"
                    ]
                    values.append((b"cache-control", b"no-store"))
                message = {**message, "headers": values}
            await send(message)

        if scope.get("path") == "/robots.txt":
            return await PlainTextResponse(
                "User-agent: *\nDisallow: /\n", headers={"Cache-Control": "no-cache"}
            )(scope, receive, headers)
        await self.app(scope, receive, headers)
