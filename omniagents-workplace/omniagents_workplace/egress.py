"""Egress allowlists: where what factories run may connect.

When the deployment runs the egress proxy (``WORKPLACE_EGRESS_PROXY``, the URL
runtimes reach it at, and ``WORKPLACE_EGRESS_CONFIG``, the file it reads),
Workplace keeps its allowlists and gives every factory's runtime and sandboxes
the proxy:

* the **deployment allowlist** (an installation's, for all of its factories):
  model endpoints, package registries, the integrations factories need;
* a **factory allowlist** (Confidential factories): narrower, within the
  deployment's.

Narrowing takes effect at once. Widening either is a material change: under
the strict policy, the governance owner's decision. Every change is audited,
and so is every connection the proxy refuses (``WORKPLACE_EGRESS_DENIALS``);
refusals from Confidential factories are alerts.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import admin, me

FACTORY_KEY = "factory.egress"
TOKEN_KEY = "egress.token"
DEPLOYMENT_KEY = "egress.deployment"
#: The guest address QuickSand gives a forwarded host service.
GUEST_PROXY = "10.0.2.101"
GUEST_PORT = 3128


class Allowlist(BaseModel):
    allow: Optional[List[str]] = None
    reason: str = Field(default="", max_length=2000)


def clean(entries: Optional[List[str]]) -> Optional[List[str]]:
    if entries is None:
        return None
    out = []
    for entry in entries:
        entry = entry.strip().lower()
        if not entry:
            continue
        host, _, port = entry.rpartition(":")
        name = host if port.isdigit() else entry
        if (
            "/" in entry
            or " " in entry
            or not name
            or (name.startswith("*.") and "." not in name[2:])
        ):
            raise HTTPException(
                422, f"{entry} is not a host, *.domain, or either with :port"
            )
        out.append(entry)
    return sorted(set(out))


def covered(entry: str, entries: List[str]) -> bool:
    """Whether ``entry`` is within ``entries`` (for a narrower factory list)."""
    from .egress_proxy import allowed

    host, _, port = entry.rpartition(":")
    if not port.isdigit():
        host, port = entry, ""
    probe = host[2:] if host.startswith("*.") else host
    ports = [int(port)] if port else [80, 443]
    if host.startswith("*."):
        probe = "x." + probe
    return all(allowed(probe, p, entries) for p in ports)


def write_proxy_config(
    path: Path, deployment: List[str], factories: Dict[str, dict]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"deployment": deployment, "factories": factories}))
    temporary.chmod(0o600)
    os.replace(temporary, path)


class Egress:
    def __init__(self, workplace: Any) -> None:
        self.w = workplace
        env = workplace.environ
        self.proxy = env.get("WORKPLACE_EGRESS_PROXY", "").strip() or None
        self.config_path = (
            Path(env["WORKPLACE_EGRESS_CONFIG"])
            if env.get("WORKPLACE_EGRESS_CONFIG")
            else None
        )
        self.denials_path = (
            Path(env["WORKPLACE_EGRESS_DENIALS"])
            if env.get("WORKPLACE_EGRESS_DENIALS")
            else None
        )
        #: An installation's deployment allowlist (and its config writer).
        self.installation_deployment: Optional[Callable[[], List[str]]] = None
        self.on_change: Optional[Callable[[], None]] = None

    # -- state ---------------------------------------------------------------

    def factory_id(self) -> str:
        return getattr(self.w, "installation_factory_id", None) or "factory"

    def deployment(self) -> List[str]:
        if self.installation_deployment is not None:
            return self.installation_deployment()
        return self.w.store.meta(DEPLOYMENT_KEY) or []

    def own(self) -> Optional[List[str]]:
        return self.w.store.meta(FACTORY_KEY)

    def effective(self) -> Optional[List[str]]:
        """The factory's own list (Confidential), or ``None``: the deployment's."""
        own = self.own()
        if own is not None and self.w.governance.classification() == "confidential":
            return own
        return None

    def token(self) -> str:
        token = self.w.store.meta(TOKEN_KEY)
        if not token:
            token = secrets.token_urlsafe(32)
            self.w.store.set_meta(TOKEN_KEY, token)
        return token

    def proxy_entry(self) -> dict:
        return {
            "token": self.token(),
            "name": self.w.slack.name or self.factory_id(),
            "allow": self.effective(),
        }

    def changed(self) -> None:
        if self.on_change is not None:
            self.on_change()
        elif self.config_path is not None:
            write_proxy_config(
                self.config_path,
                self.deployment(),
                {self.factory_id(): self.proxy_entry()},
            )

    # -- what runtimes and sandboxes are given --------------------------------

    def credentials_url(self, host: str, port: int) -> str:
        return f"http://{quote(self.factory_id())}:{quote(self.token(), safe='')}@{host}:{port}"

    def runtime_environment(self, internal_hosts: List[str]) -> dict:
        if not self.proxy:
            return {}
        url = urlsplit(self.proxy)
        proxy = self.credentials_url(url.hostname or "egress", url.port or GUEST_PORT)
        no_proxy = ",".join(sorted({"localhost", "127.0.0.1", *internal_hosts}))
        return {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
            "NO_PROXY": no_proxy,
            "no_proxy": no_proxy,
        }

    def sandbox_profile(self, profile: dict) -> dict:
        """A QuickSand sandbox reaches only the proxy (and Workplace's own
        forwarded services): ``mounts_only``, with the proxy forwarded in."""
        kind = (profile.get("client") or {}).get("type")
        if not self.proxy or kind not in ("quicksand", "docker"):
            return profile
        url = urlsplit(self.proxy)
        values = (
            profile.setdefault("manifest", {})
            .setdefault("environment", {})
            .setdefault("value", {})
        )
        if kind == "docker":
            # A container on the deployment's internal network reaches the
            # proxy by its name; the network has no other way out.
            proxy = self.credentials_url(
                url.hostname or "egress", url.port or GUEST_PORT
            )
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                values[key] = proxy
            values["NO_PROXY"] = values["no_proxy"] = "localhost,127.0.0.1"
            return profile
        options = profile.setdefault("options", {})
        options["network_mode"] = "mounts_only"
        options.setdefault("host_services", {})[
            GUEST_PORT
        ] = f"{url.hostname or 'egress'}:{url.port or GUEST_PORT}"
        values = (
            profile.setdefault("manifest", {})
            .setdefault("environment", {})
            .setdefault("value", {})
        )
        proxy = self.credentials_url(GUEST_PROXY, GUEST_PORT)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            values[key] = proxy
        values["NO_PROXY"] = values["no_proxy"] = f"{GUEST_PROXY},localhost,127.0.0.1"
        return profile

    # -- changes -------------------------------------------------------------

    def set_own(self, account: Any, body: Allowlist) -> dict:
        from .decisions import decide_or_propose

        after = clean(body.allow)
        deployment = self.deployment()
        if after is not None:
            outside = [e for e in after if not covered(e, deployment)]
            if outside:
                raise HTTPException(
                    422,
                    "A factory's allowlist stays within the deployment's: "
                    + ", ".join(outside),
                )
        before = self.own()
        current = before if before is not None else deployment
        widened = [
            e
            for e in (after if after is not None else deployment)
            if not covered(e, current)
        ]

        def apply():
            self.w.store.set_meta(FACTORY_KEY, after)
            self.w.audit_log.record(
                "egress.factory_changed", before=before, after=after
            )
            self.changed()

        if not widened:
            self.w.audit_log.record(
                "egress.factory_changed",
                actor=account.username,
                before=before,
                after=after,
            )
            self.w.store.set_meta(FACTORY_KEY, after)
            self.changed()
            return self.view()
        g = self.w.governance
        record = decide_or_propose(
            g.decisions,
            gated=g.gated(),
            owner=g.is_owner(account),
            account=account,
            kind="egress",
            summary="Factory egress widened: " + ", ".join(widened),
            change={"before": before, "after": after},
            reason=body.reason,
            apply=apply,
            factory=g.factory_id(),
        )
        return {
            **self.view(),
            "pending": record if record["status"] == "pending" else None,
        }

    def view(self) -> dict:
        return {
            "enabled": bool(self.proxy),
            "deployment": self.deployment(),
            "own": self.own(),
            "effective": self.effective(),
            "managed_by_installation": self.installation_deployment is not None,
        }

    def record_denial(self, denial: dict) -> None:
        self.w.audit_log.record(
            "egress.denied", host=denial.get("host"), port=denial.get("port")
        )

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/workplace/api/factory/egress")
        def egress(request: Request):
            me(request)
            return self.view()

        @router.put("/workplace/api/factory/egress")
        def save_egress(body: Allowlist, request: Request):
            return self.set_own(admin(request), body)

        return router


def set_deployment(
    *,
    account: Any,
    entries: List[str],
    reason: str,
    current: List[str],
    save: Callable[[List[str]], None],
    decisions: Any,
    gated: bool,
    owner: bool,
    audit_log: Any,
) -> Optional[dict]:
    """Change a deployment allowlist: narrowing at once, widening decided."""
    from .decisions import decide_or_propose

    after = clean(entries) or []
    widened = [e for e in after if not covered(e, current)]

    def apply():
        save(after)
        audit_log.record("egress.deployment_changed", before=current, after=after)

    if not widened:
        audit_log.record(
            "egress.deployment_changed",
            actor=account.username,
            before=current,
            after=after,
        )
        save(after)
        return None
    record = decide_or_propose(
        decisions,
        gated=gated,
        owner=owner,
        account=account,
        kind="egress",
        summary="Deployment egress widened: " + ", ".join(widened),
        change={"before": current, "after": after},
        reason=reason,
        apply=apply,
    )
    return record if record["status"] == "pending" else None
