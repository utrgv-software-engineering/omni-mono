"""A credential-free origin for MCP App frames, with header-enforced CSP."""

import json
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qs
from starlette.responses import HTMLResponse, PlainTextResponse, FileResponse
from pathlib import Path
from importlib import resources


def app_origin(base_url, configured=""):
    parsed = urlsplit(configured or base_url)
    if configured:
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("WORKPLACE_APP_ORIGIN must be an HTTP(S) origin")
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    else:
        host = parsed.hostname
        host = (
            "127.0.0.1"
            if host == "localhost"
            else "localhost" if host in {"127.0.0.1", "::1"} else "apps." + str(host)
        )
        origin = f"{parsed.scheme}://{host}" + (
            f":{parsed.port}" if parsed.port else ""
        )
    base = urlsplit(base_url)
    if urlsplit(origin).netloc == base.netloc:
        raise ValueError("MCP Apps require a separate origin from Workplace")
    return origin


def isolated_origin(base_url, identity, configured=""):
    base = urlsplit(app_origin(base_url, configured))
    host = (
        "localhost"
        if base.hostname in {"127.0.0.1", "localhost", "::1"}
        else base.hostname
    )
    key = hashlib.sha256((base_url.rstrip("/") + ":" + identity).encode()).hexdigest()[
        :32
    ]
    return f"{base.scheme}://{key}.{host}" + (f":{base.port}" if base.port else "")


def domains(values):
    result = []
    for value in values or []:
        if not isinstance(value, str):
            raise ValueError("Invalid CSP domain")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http", "wss", "ws"}
            or not parsed.hostname
            or parsed.username
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(c in value for c in "'\"; \n\r")
        ):
            raise ValueError("CSP domains must be explicit HTTP(S) or WS(S) origins")
        result.append(value.rstrip("/"))
    return " ".join(result)


def csp_header(csp, parent):
    resources = domains(csp.get("resourceDomains", csp.get("resource_domains", [])))
    connections = domains(csp.get("connectDomains", csp.get("connect_domains", [])))
    frames = domains(csp.get("frameDomains", csp.get("frame_domains", [])))
    return "; ".join(
        [
            "default-src 'none'",
            f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {resources}",
            f"style-src 'self' 'unsafe-inline' {resources}",
            f"img-src 'self' data: blob: {resources}",
            f"font-src 'self' data: {resources}",
            f"media-src 'self' blob: data: {resources}",
            f"connect-src 'self' {connections}",
            f"worker-src 'self' blob: {resources}",
            f"frame-src 'self' blob: {frames}",
            "object-src 'none'",
            "base-uri 'none'",
            f"frame-ancestors {parent}",
            "form-action 'none'",
        ]
    )


# Follows the official basic-host double-frame proxy. The parent is never on this
# origin; this origin serves no account APIs, credentials, or uploaded documents.
PROXY = r"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,iframe{margin:0;border:0;width:100%;height:100%;overflow:hidden}</style></head><body><script>
const expected=__PARENT__;
if(window===window.parent) throw Error('App sandbox requires a host');
try { void window.parent.document; throw Error('Unsafe app origin'); } catch(e) { if(e.name!=='SecurityError') throw e; }
const inner=document.createElement('iframe');
inner.setAttribute('sandbox','allow-scripts allow-same-origin allow-forms');
document.body.append(inner);
window.addEventListener('message',event=>{
 if(event.source===window.parent && event.origin===expected){
  if(event.data?.method==='ui/notifications/sandbox-resource-ready'){
   const p=event.data.params||{};
   const allowed=Object.keys(p.permissions||{}).filter(k=>['microphone','camera','clipboardWrite','geolocation'].includes(k));
   const names={clipboardWrite:'clipboard-write'};
   inner.allow=allowed.map(k=>(names[k]||k)+" 'self'").join('; ');
   const doc=inner.contentDocument; doc.open(); doc.write(p.html||''); doc.close();
  } else inner.contentWindow.postMessage(event.data,location.origin);
 } else if(event.source===inner.contentWindow && event.origin===location.origin){
  window.parent.postMessage(event.data,expected);
 }
});
window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/sandbox-proxy-ready',params:{}},expected);
</script></body></html>"""


class AppSandboxOrigin:
    def __init__(self, app, *, base_url, configured="", static_dir=None, frame_ancestors=""):
        self.app = app
        self.frame_ancestors = frame_ancestors
        self.assets = (
            Path(static_dir)
            if static_dir
            else Path(str(resources.files("omniagents_workplace").joinpath("static")))
        ).resolve() / "assets"
        base = urlsplit(base_url)
        self.parent = urlunsplit((base.scheme, base.netloc, "", "", ""))
        self.origin = app_origin(base_url, configured)
        self.host = urlsplit(self.origin).netloc.lower()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode().lower()
        suffix = self.host
        if urlsplit(self.origin).hostname in {"127.0.0.1", "localhost", "::1"}:
            port = urlsplit(self.origin).port
            suffix = "localhost" + (f":{port}" if port else "")
        if host != self.host and not re.fullmatch(
            r"[a-f0-9]{32}\." + re.escape(suffix), host
        ):
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return await send({"type": "websocket.close", "code": 1008})
        response_headers = {
            "X-Robots-Tag": "noindex, nofollow, noimageindex",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }
        if scope["path"].startswith("/assets/") and scope["method"] in {"GET", "HEAD"}:
            relative = (
                scope["path"].removeprefix("/assets/").removeprefix("mcp-apps-v1/")
            )
            candidate = (self.assets / relative).resolve()
            if self.assets in candidate.parents and candidate.is_file():
                response_headers.update(
                    {
                        "Cache-Control": "public, max-age=31536000, immutable",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
                response = FileResponse(candidate, headers=response_headers)
            else:
                response = PlainTextResponse(
                    "Not found", status_code=404, headers=response_headers
                )
        elif scope["path"] == "/robots.txt":
            response = PlainTextResponse(
                "User-agent: *\nDisallow: /\n", headers=response_headers
            )
        elif scope["path"] == "/mcp-app-sandbox" and scope["method"] == "GET":
            try:
                query = parse_qs(scope.get("query_string", b"").decode())
                csp = json.loads(query.get("csp", ["{}"])[0])
                response_headers["Content-Security-Policy"] = csp_header(
                    csp, self.parent + (" " + self.frame_ancestors if self.frame_ancestors else "")
                )
                response = HTMLResponse(
                    PROXY.replace(
                        "__PARENT__", json.dumps(self.parent).replace("<", "\\u003c")
                    ),
                    headers=response_headers,
                )
            except (ValueError, TypeError, AttributeError):
                response = PlainTextResponse(
                    "Invalid app policy", status_code=400, headers=response_headers
                )
        else:
            response = PlainTextResponse(
                "Not found", status_code=404, headers=response_headers
            )
        return await response(scope, receive, send)
