import asyncio
import base64
import json
import time
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import HTTPException


def segment(value):
    return quote(str(value), safe="")


class AzureDevOpsClient:
    def __init__(self, connections, identifier, transport=None):
        self.connections = connections
        self.identifier = identifier
        egress = getattr(connections.w, "egress", None)
        proxy = (
            egress.runtime_environment([]).get("HTTPS_PROXY")
            if egress is not None and transport is None
            else None
        )
        self.http = httpx.AsyncClient(
            transport=transport,
            proxy=proxy,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.cli_token = None
        self.cli_expiry = 0

    async def close(self):
        await self.http.aclose()

    async def authorization(self):
        connection = self.connections.get(self.identifier)
        token = self.connections.credential(connection)
        if connection["auth_type"] == "pat":
            return "Basic " + base64.b64encode((":" + token).encode()).decode()
        if connection["auth_type"] == "azcli":
            if self.cli_expiry < time.time() + 120:
                process = await asyncio.create_subprocess_exec(
                    "az",
                    "account",
                    "get-access-token",
                    "--resource",
                    "499b84ac-1321-427f-aa17-267ca6975798",
                    "--output",
                    "json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    output, _ = await asyncio.wait_for(process.communicate(), 30)
                except BaseException:
                    process.kill()
                    await process.wait()
                    raise
                if process.returncode:
                    raise HTTPException(409, "Sign in to Azure CLI on the trusted host")
                result = json.loads(output)
                self.cli_token = result["accessToken"]
                self.cli_expiry = float(result.get("expires_on") or time.time() + 300)
            token = self.cli_token
        return "Bearer " + token

    def url(self, path):
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("API paths must be fixed relative Azure DevOps paths")
        connection = self.connections.get(self.identifier)
        return "https://dev.azure.com/" + segment(connection["organization"]) + path

    async def request(
        self,
        method,
        path,
        *,
        params=None,
        json_body=None,
        content=None,
        content_type=None,
        version="7.1"
    ):
        async with self.lock:
            delay = self.next_request - time.time()
            if delay > 0:
                raise HTTPException(
                    429,
                    "Azure DevOps is throttled; retry after the connection cooldown",
                )
            headers = {
                "Authorization": await self.authorization(),
                "Accept": "application/json",
            }
            if content_type:
                headers["Content-Type"] = content_type
            query = {"api-version": version, **(params or {})}
            try:
                response = await self.http.request(
                    method,
                    self.url(path),
                    params=query,
                    headers=headers,
                    json=json_body,
                    content=content,
                )
            except httpx.HTTPError:
                raise HTTPException(
                    502, "Azure DevOps connection failed; check host connectivity"
                ) from None
            retry = response.headers.get("retry-after")
            if retry:
                try:
                    self.next_request = time.time() + min(3600, max(0, float(retry)))
                except ValueError:
                    self.next_request = time.time() + 60
            if response.status_code >= 300:
                if response.status_code in (429, 503):
                    self.next_request = max(self.next_request, time.time() + 60)
                message = {
                    401: "Azure DevOps credential expired or was rejected",
                    403: "Azure DevOps permission denied",
                    404: "Azure DevOps resource unavailable; check scope and permissions",
                    429: "Azure DevOps rate limit reached",
                    409: "Azure DevOps resource changed; refresh before retrying",
                }.get(response.status_code, "Azure DevOps rejected the request")
                raise HTTPException(
                    response.status_code if response.status_code < 500 else 502, message
                )
            return response

    async def json(self, method, path, **kwargs):
        response = await self.request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError:
            raise HTTPException(
                502, "Azure DevOps returned an invalid JSON response"
            ) from None

    async def pages(self, path, *, params=None, offset=False, version="7.1"):
        query = dict(params or {})
        seen = set()
        while True:
            response = await self.request("GET", path, params=query, version=version)
            value = response.json()
            batch = (
                value
                if isinstance(value, list)
                else value.get("value", value.get("comments", []))
            )
            yield batch
            continuation = response.headers.get("x-ms-continuationtoken") or (
                value.get("continuationToken") if isinstance(value, dict) else None
            )
            if continuation:
                if continuation in seen:
                    raise HTTPException(
                        502, "Azure DevOps repeated a pagination cursor"
                    )
                seen.add(continuation)
                query["continuationToken"] = continuation
            elif offset and len(batch) == int(query.get("$top", 100)):
                query["$skip"] = int(query.get("$skip", 0)) + len(batch)
            else:
                break

    async def all(self, path, **kwargs):
        values = []
        async for batch in self.pages(path, **kwargs):
            values.extend(batch)
        return values
