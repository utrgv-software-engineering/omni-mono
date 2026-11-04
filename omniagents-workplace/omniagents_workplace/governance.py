"""A factory's data classification, and the services a deployment allows.

**Classification** (UTRGV's classes): ``published``, ``controlled`` or
``confidential``, with the factory's purpose and owner. Under the strict
policy every factory needs one before it starts. Controlled and Confidential
factories have every definition change reviewed in a pull request, and use
only allowed integrations and MCP servers.

**Allowed services**: the models, integrations and MCP servers the deployment
permits, kept by its administrators (an installation's, for all of its
factories). Under the strict policy every factory's models must be on the
list; the runtime enforces it (``omniagents.core.allowed_services``), from the
environment and managed configuration Workplace gives it.

Every change is audited with what it was before and after.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import admin, me

PROFILE_KEY = "factory.profile"
SERVICES_KEY = "allowed_services"
CLASSES = ("published", "controlled", "confidential")
RESTRICTED = {"controlled", "confidential"}
KINDS = ("model", "integration", "mcp")


class Profile(BaseModel):
    classification: str
    purpose: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=200)


class Service(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=100)
    #: Models: the provider type (``azure``, ``openai``, ``openai-compatible``,
    #: ``openai-oauth``, ``litellm``). Integrations: the integration type.
    provider: str = Field(default="", max_length=100)
    #: Models and integrations: the endpoint's URL prefix (empty: the
    #: provider's own). MCP servers: a URL prefix or ``command:<executable>``.
    endpoint: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=1000)


class Services(BaseModel):
    services: List[Service]


def validate_services(services: List[Service]) -> List[dict]:
    out = []
    for service in services:
        if service.kind not in KINDS:
            raise HTTPException(422, "A service is a model, integration or mcp server")
        if service.kind in ("model", "integration") and not service.provider.strip():
            raise HTTPException(422, f"Give {service.name}'s provider or type")
        endpoint = service.endpoint.strip()
        if endpoint and not (
            endpoint.startswith("https://")
            or endpoint.startswith("http://")
            or (service.kind == "mcp" and endpoint.startswith("command:"))
        ):
            raise HTTPException(
                422, f"{service.name}'s endpoint is a URL (or command:<executable>)"
            )
        out.append({**service.model_dump(), "endpoint": endpoint})
    return out


def runtime_entries(services: List[dict]) -> dict:
    """What the runtime is given: model endpoints, integrations, MCP servers."""
    return {
        "models": [
            {"provider": s["provider"], "base_url": s["endpoint"] or None}
            for s in services
            if s["kind"] == "model"
        ],
        "integrations": [
            {"type": s["provider"], "url": s["endpoint"] or None}
            for s in services
            if s["kind"] == "integration"
        ],
        "mcp": [
            s["name"] + ("=" + s["endpoint"] if s["endpoint"] else "")
            for s in services
            if s["kind"] == "mcp"
        ],
    }


class Governance:
    def __init__(self, workplace: Any) -> None:
        self.w = workplace
        #: An installation's allowed services, for all of its factories.
        self.installation_services: Optional[Callable[[], List[dict]]] = None

    # -- classification ------------------------------------------------------

    def profile(self) -> dict:
        return self.w.store.meta(PROFILE_KEY) or {}

    def classification(self) -> Optional[str]:
        return self.profile().get("classification")

    def restricted(self) -> bool:
        return self.classification() in RESTRICTED

    def review_forced(self) -> bool:
        """Every definition change is a reviewed pull request."""
        return self.w.policy.strict and self.restricted()

    def startable(self) -> Optional[str]:
        """Why the factory may not start, or ``None``."""
        if self.w.policy.strict and not self.classification():
            return "Classify the factory's data before starting it"
        return None

    def set_profile(self, account: Any, body: Profile) -> dict:
        if body.classification not in CLASSES:
            raise HTTPException(
                422, "Classify as published, controlled or confidential"
            )
        before = self.profile()
        after = {
            "classification": body.classification,
            "purpose": body.purpose.strip(),
            "owner": body.owner.strip(),
        }
        self.w.store.set_meta(PROFILE_KEY, after)
        self.w.audit_log.record(
            "factory.profile_changed",
            actor=account.username,
            before=before,
            after=after,
        )
        return self.view()

    # -- allowed services ----------------------------------------------------

    def services(self) -> List[dict]:
        if self.installation_services is not None:
            return self.installation_services()
        return self.w.store.meta(SERVICES_KEY) or []

    def set_services(self, account: Any, services: List[Service]) -> List[dict]:
        if self.installation_services is not None:
            raise HTTPException(
                409, "The installation keeps this factory's allowed services"
            )
        before = self.services()
        after = validate_services(services)
        self.w.store.set_meta(SERVICES_KEY, after)
        self.w.audit_log.record(
            "allowed_services.changed",
            actor=account.username,
            before=before,
            after=after,
        )
        return after

    def environ(self) -> dict:
        """The allowed-services environment for the runtime (strict policy)."""
        if not self.w.policy.strict:
            return {}
        entries = runtime_entries(self.services())
        env = {"OMNIAGENTS_ALLOWED_MODEL_ENDPOINTS": json.dumps(entries["models"])}
        if self.restricted():
            env["OMNIAGENTS_ALLOWED_INTEGRATIONS"] = json.dumps(entries["integrations"])
        return env

    def runtime_environment(self, directory: Path) -> dict:
        """Everything the runtime is given to hold it to the policy."""
        security = None
        if self.w.policy.strict and self.restricted():
            security = {
                "mcp_require_allowlist": True,
                "mcp_allowed_servers": runtime_entries(self.services())["mcp"],
            }
        return {
            **self.w.policy.runtime_environment(directory, security),
            **self.environ(),
        }

    def require_model(self, provider: str, base_url: Optional[str]) -> None:
        """Refuse a model provider that is not allowed (the Factory model page)."""
        if not self.w.policy.strict:
            return
        from omniagents.core.allowed_services import ServiceNotAllowed, require_model

        try:
            require_model({"provider": provider, "base_url": base_url}, self.environ())
        except ServiceNotAllowed as exc:
            raise HTTPException(422, str(exc)) from None

    # -- API -----------------------------------------------------------------

    def view(self) -> dict:
        return {
            "profile": self.profile(),
            "classes": list(CLASSES),
            "strict": self.w.policy.strict,
            "review_forced": self.review_forced(),
            "startable": self.startable() is None,
        }

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/workplace/api/factory/profile")
        def profile(request: Request):
            me(request)
            return self.view()

        @router.put("/workplace/api/factory/profile")
        def save_profile(body: Profile, request: Request):
            return self.set_profile(admin(request), body)

        @router.get("/workplace/api/allowed-services")
        def services(request: Request):
            me(request)
            return {
                "services": self.services(),
                "managed_by_installation": self.installation_services is not None,
            }

        @router.put("/workplace/api/allowed-services")
        def save_services(body: Services, request: Request):
            return {"services": self.set_services(admin(request), body.services)}

        return router


# -- the factory's own repository ---------------------------------------------

#: What the personal-data check looks for in the lines a pull request adds:
#: identifiers and credentials, not ordinary names or emails, which factory
#: definitions legitimately contain.
SCANNED = {"ssn", "mrn", "api_key", "bearer_token"}
PERSONAL_DATA_CHECK = "personal-data"


def personal_data_findings(diff: str) -> List[str]:
    """The kinds of personal data or credentials the diff's added lines carry."""
    from omniagents.core.providers.content_scanner.regex import _DEFAULT_PATTERNS

    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return sorted(
        {
            name
            for name, pattern in _DEFAULT_PATTERNS
            if name in SCANNED and pattern.search(added)
        }
    )


def install_repository_rules(
    workplace: Any, factory_repositories: Callable[[], set]
) -> None:
    """On the factory's own repository: approvals count only from people, and a
    Confidential factory's pull requests must pass the personal-data check, so
    self-improvement cannot copy personal details into the factory."""
    github = workplace.github

    def is_factory_repository(repo: Any) -> bool:
        return repo.full_name in factory_repositories()

    def is_person(login: str) -> bool:
        return any(
            a.github_login == login and not a.disabled
            for a in workplace.accounts.people()
        )

    cache: dict = {}

    def personal_data(repo: Any, issue: Any) -> dict:
        if workplace.governance.classification() != "confidential":
            return {}
        if not is_factory_repository(repo):
            return {}
        head, base = github.head_sha(repo, issue), github.base_sha(repo, issue)
        key = (repo.full_name, base, head)
        if key not in cache:
            findings = personal_data_findings(repo.bare.diff(base, head))
            cache[key] = "failure" if findings else "success"
            if findings:
                workplace.audit_log.record(
                    "factory.personal_data_found",
                    repository=repo.full_name,
                    pull_request=issue.number,
                    kinds=findings,
                )
        return {PERSONAL_DATA_CHECK: cache[key]}

    github.person_review = is_factory_repository
    github.person = is_person
    github.extra_checks.append(personal_data)
