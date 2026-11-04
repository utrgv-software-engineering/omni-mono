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
#: A standalone Workplace's governance owners (an installation names its own).
OWNERS_KEY = "governance.owners"
SERVICES_KEY = "allowed_services"
CLASSES = ("published", "controlled", "confidential")
RESTRICTED = {"controlled", "confidential"}
KINDS = ("model", "integration", "mcp")


#: Classifications from least to most controlled.
RANK = {name: rank for rank, name in enumerate(CLASSES)}
LABELS = ("classification", "heightened_risk")
DESCRIPTIONS = ("purpose", "owner", "sponsor", "data", "affected", "decides")


class Profile(BaseModel):
    classification: str
    #: Whether the factory may materially affect people's rights,
    #: opportunities, safety or access to services (UTRGV's heightened risk).
    heightened_risk: Optional[bool] = None
    purpose: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=200)
    #: The clinical or business leader the factory is built for.
    sponsor: str = Field(default="", max_length=200)
    #: Intended use: the data it touches, who is affected, and what the AI
    #: does and does not decide.
    data: str = Field(default="", max_length=2000)
    affected: str = Field(default="", max_length=2000)
    decides: str = Field(default="", max_length=2000)
    #: Why, for a change the governance owner decides (lowering).
    reason: str = Field(default="", max_length=2000)


def lowers(before: dict, after: dict) -> bool:
    """Whether a label change removes controls: a lower classification, risk
    taken away, or anything below the most controlled chosen at first."""
    old, new = before.get("classification"), after.get("classification")
    if new != old and (old is None and new != "confidential"):
        return True
    if old is not None and RANK[new] < RANK[old]:
        return True
    old_risk, new_risk = before.get("heightened_risk"), after.get("heightened_risk")
    if new_risk is False and old_risk is not False:
        return True
    return False


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
    reason: str = Field(default="", max_length=2000)


class Decision(BaseModel):
    approve: bool
    reasoning: str = Field(default="", max_length=2000)


class Owners(BaseModel):
    account_ids: List[str]


class ReviewDone(BaseModel):
    kind: str
    result: str = Field(min_length=1, max_length=2000)
    factory: Optional[str] = None


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
        from .decisions import Decisions

        self.w = workplace
        #: An installation's allowed services, for all of its factories.
        self.installation_services: Optional[Callable[[], List[dict]]] = None
        #: Whether an account is the governance owner (an installation sets it).
        self.is_owner: Callable[[Any], bool] = lambda account: (
            account.id in (self.w.store.meta(OWNERS_KEY) or [])
        )
        self.decisions = Decisions(workplace.store, workplace.audit_log)
        #: The factory's project directory, when an installation knows it.
        self.project: Optional[Path] = None
        self._accessed: dict = {}
        self.decisions.appliers["labels"] = self._apply_labels
        self.decisions.appliers["allowed_services"] = self._apply_services

    def project_dir(self) -> Optional[Path]:
        """The factory's project (an installation's record, else beside workplace.yml)."""
        if self.project is not None:
            return self.project
        return self.w.factory_config.root if self.w.factory_config else None

    #: How often one person's reads of one thing are recorded again.
    ACCESS_INTERVAL = 3600

    def record_access(self, account: Any, kind: str, target: str) -> None:
        """In a Confidential factory, who read what (a conversation, a run's
        transcript, a Shared file), once an hour per person and item: what an
        audit of access to sensitive data asks (HIPAA §164.312(b))."""
        import time

        if self.classification() != "confidential" or account is None:
            return
        key = (account.username, kind, target)
        now = time.time()
        last = self._accessed.get(key)
        if last is not None and now - last < self.ACCESS_INTERVAL:
            return
        self._accessed[key] = now
        self.w.audit_log.record("access." + kind, actor=account.username, target=target)

    def gated(self) -> bool:
        """Whether decisions wait for the governance owner (the strict policy)."""
        return self.w.policy.strict

    def factory_id(self) -> Optional[str]:
        return getattr(self.w, "installation_factory_id", None)

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

    def heightened_risk(self) -> Optional[bool]:
        return self.profile().get("heightened_risk")

    def startable(self) -> Optional[str]:
        """Why the factory may not start, or ``None``."""
        if not self.w.policy.strict:
            return None
        if not self.classification() or self.heightened_risk() is None:
            return "Classify the factory's data and risk before starting it"
        if not any(s["kind"] == "model" for s in self.services()):
            return (
                "No model is an allowed service, so under the strict policy the "
                "factory can use none: add the deployment's models to its allowed services"
            )
        return None

    def _apply_labels(self, labels: dict) -> None:
        profile = {**self.profile(), **{k: labels[k] for k in LABELS if k in labels}}
        self.w.store.set_meta(PROFILE_KEY, profile)

    def set_profile(self, account: Any, body: Profile) -> dict:
        """Descriptions change at once; so does raising a label. Lowering one is
        the governance owner's decision: anyone else's waits for them."""
        from .decisions import decide_or_propose

        if body.classification not in CLASSES:
            raise HTTPException(
                422, "Classify as published, controlled or confidential"
            )
        before = self.profile()
        descriptions = {k: getattr(body, k).strip() for k in DESCRIPTIONS}
        if any(before.get(k, "") != v for k, v in descriptions.items()):
            self.w.store.set_meta(PROFILE_KEY, {**before, **descriptions})
            self.w.audit_log.record(
                "factory.profile_changed",
                actor=account.username,
                before={k: before.get(k, "") for k in DESCRIPTIONS},
                after=descriptions,
            )
        labels = {
            "classification": body.classification,
            "heightened_risk": (
                body.heightened_risk
                if body.heightened_risk is not None
                else before.get("heightened_risk")
            ),
        }
        old = {k: before.get(k) for k in LABELS}
        pending = None
        if labels != old:
            change = {"before": old, **labels}
            summary = (
                f"Classification {old['classification'] or 'unset'} → "
                f"{labels['classification']}; heightened risk "
                f"{old['heightened_risk']} → {labels['heightened_risk']}"
            )
            if lowers(old, labels):
                record = decide_or_propose(
                    self.decisions,
                    gated=self.gated(),
                    owner=self.is_owner(account),
                    account=account,
                    kind="labels",
                    summary=summary,
                    change=change,
                    reason=body.reason,
                    apply=lambda: self._apply_labels(labels),
                    factory=self.factory_id(),
                )
                pending = record if record["status"] == "pending" else None
            else:
                # Raising only adds controls: never gated, always recorded.
                self._apply_labels(labels)
                self.decisions.record(
                    kind="labels",
                    summary=summary,
                    change=change,
                    proposer=account.username,
                    reason=body.reason,
                    decider=account.username,
                    factory=self.factory_id(),
                )
        return {**self.view(), "pending": pending}

    # -- allowed services ----------------------------------------------------

    def services(self) -> List[dict]:
        if self.installation_services is not None:
            return self.installation_services()
        return self.w.store.meta(SERVICES_KEY) or []

    def _apply_services(self, change: dict) -> None:
        self.w.store.set_meta(SERVICES_KEY, change["after"])
        self.w.audit_log.record(
            "allowed_services.changed", before=change["before"], after=change["after"]
        )

    def set_services(
        self, account: Any, services: List[Service], reason: str = ""
    ) -> dict:
        from .decisions import decide_or_propose

        if self.installation_services is not None:
            raise HTTPException(
                409, "The installation keeps this factory's allowed services"
            )
        change = {"before": self.services(), "after": validate_services(services)}
        record = decide_or_propose(
            self.decisions,
            gated=self.gated(),
            owner=self.is_owner(account),
            account=account,
            kind="allowed_services",
            summary=f"Allowed services: {len(change['after'])} service(s)",
            change=change,
            reason=reason,
            apply=lambda: self._apply_services(change),
        )
        return {
            "services": self.services(),
            "pending": record if record["status"] == "pending" else None,
        }

    def require_model_owner(self, account: Any, summary: str) -> None:
        """The factory's models are the governance owner's to change (strict)."""
        if self.gated() and not self.is_owner(account):
            raise HTTPException(
                403, "Only the governance owner changes the factory's models"
            )
        self.decisions.record(
            kind="model",
            summary=summary,
            change={},
            proposer=account.username,
            decider=account.username,
            status="decided" if self.gated() else "applied",
            factory=self.factory_id(),
        )

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
            "blocked": self.startable(),
            "pending": [d for d in self.decisions.pending() if d["kind"] == "labels"],
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
            return self.set_services(admin(request), body.services, body.reason)

        @router.get("/workplace/api/factory/system-card")
        def system_card(request: Request):
            from .governance_reports import card_markdown, system_card as card_of

            me(request)
            card = card_of(
                self.w, self.w.slack.name or "The factory", self.project_dir()
            )
            return {"card": card, "markdown": card_markdown(card)}

        @router.get("/workplace/api/governance")
        def governance(request: Request):
            account = me(request)
            return {
                "decisions": self.decisions.all(),
                "owner": self.is_owner(account),
                "gated": self.gated(),
            }

        @router.post("/workplace/api/governance/decisions/{decision_id}")
        def decide(decision_id: str, body: Decision, request: Request):
            account = me(request)
            if not self.is_owner(account):
                raise HTTPException(403, "Only the governance owner decides")
            return self.decisions.decide(
                decision_id, account.username, body.approve, body.reasoning
            )

        @router.put("/workplace/api/governance/owners")
        def owners(body: Owners, request: Request):
            account = admin(request)
            if getattr(self.w, "installation_factory_id", None):
                raise HTTPException(409, "The installation names the governance owner")
            before = self.w.store.meta(OWNERS_KEY) or []
            self.w.store.set_meta(OWNERS_KEY, body.account_ids)
            self.w.audit_log.record(
                "governance.owners_changed",
                actor=account.username,
                before=before,
                after=body.account_ids,
            )
            return {"owners": body.account_ids}

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


GOVERNANCE_CHECK = "governance"
#: What makes a change to the factory's repository material: its model catalog;
#: these sections of project.yml; these settings of an agent.
PROJECT_SECTIONS = ("security", "integrations", "approvals", "mcp_servers", "paths")
AGENT_SETTINGS = (
    "model",
    "model_name",
    "mcp_servers",
    "security",
    "skip_approvals",
    "use_safe_agent",
    "human_approval_tools",
)


def material_changes(bare: Any, base: str, head: str) -> List[str]:
    """What in a change is material: file and section names, or ``[]``."""
    import re

    import yaml

    def load(rev: str, path: str) -> dict:
        found = bare.blob(rev, path)
        if not found:
            return {}
        try:
            value = yaml.safe_load(found[1]) or {}
        except yaml.YAMLError:
            return {"<unparsable>": True}
        return value if isinstance(value, dict) else {}

    diff = bare.diff(base, head)
    paths = set(re.findall(r"^\+\+\+ b/(.+)$", diff, re.M))
    paths |= set(re.findall(r"^--- a/(.+)$", diff, re.M))
    out = []
    for path in sorted(paths):
        name = path.rsplit("/", 1)[-1]
        if name == "models.yml":
            out.append(path)
        elif path == "project.yml":
            before, after = load(base, path), load(head, path)
            out += [
                f"project.yml: {k}"
                for k in PROJECT_SECTIONS
                if before.get(k) != after.get(k)
            ]
        elif name == "agent.yml":
            before, after = load(base, path), load(head, path)
            out += [
                f"{path}: {k}" for k in AGENT_SETTINGS if before.get(k) != after.get(k)
            ]
    return out


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

    def governance(repo: Any, issue: Any) -> dict:
        """A material change needs the governance owner's approval (strict)."""
        if not workplace.governance.gated() or not is_factory_repository(repo):
            return {}
        head, base = github.head_sha(repo, issue), github.base_sha(repo, issue)
        changed = material_changes(repo.bare, base, head)
        if not changed:
            return {}
        decisions = workplace.governance.decisions
        key = f"pull_request:{repo.full_name}#{issue.number}@{head}"
        record = decisions.by_key(key)
        if record is None:
            record = decisions.record(
                kind="pull_request",
                summary=f"{repo.full_name}#{issue.number} {issue.title}: "
                + ", ".join(changed),
                change={
                    "repository": repo.full_name,
                    "number": issue.number,
                    "head": head,
                    "material": changed,
                },
                proposer=issue.user,
                status="pending",
                factory=workplace.governance.factory_id(),
                key=key,
            )
        return {
            GOVERNANCE_CHECK: {"pending": "pending", "decided": "success"}.get(
                record["status"], "failure"
            )
        }

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
    github.extra_checks.append(governance)
