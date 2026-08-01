"""Accept several credential kinds on one surface.

An upstream behind a serve proxy authenticates with
:class:`~omniagents.core.providers.auth.proxy_assertion.ProxyAssertionAuthProvider`,
which by design accepts nothing else. That leaves a real deployment shape
unserved: a server reached *both* directly (browsers, native clients) and
through a proxy. Without this provider the only self-service ways out are
to lock the direct clients out, or to delete the proxy's own
authentication — and the second one boots cleanly while silently
collapsing every user onto one identity. That is a perverse incentive on
a security control, so the supported path exists instead::

    security:
      providers:
        auth:
          type: composite
          accept:
            - type: proxy_assertion
              signing_secret_env: OMNI_PROXY_IDENTITY_SECRET
            - type: oidc
              issuer: "https://login.microsoftonline.com/{tenant}/v2.0"
              ...

Each entry is an ordinary provider config. A request is authenticated by
the **first** entry that accepts it; if none does, the first entry's
error is reported. Each sub-provider still validates its own credential
in full — no entry can weaken another's *verification*.

It can, however, overrule another's *decision*: acceptance is a
disjunction, so a member's refusal is not a veto. That is intended,
because members normally verify disjoint credential kinds and a refusal
means "not mine" — but where two members can see the same credential,
configured order decides authorization. A later acceptance following an
earlier refusal is logged at WARNING for exactly that reason. Put
members that verify disjoint credential kinds in one composite; do not
use it to layer two verifiers of the *same* credential.

Capabilities compose in the direction that is safe for each. A request
may arrive on *any* member, so:

* ``identifies_principals`` and ``supports_connect_tickets`` are
  **conjunctions** — they are capability claims, and the weakest member
  sets what the composite may honestly claim;
* ``enforces_authorization`` is a **disjunction** — it is a restriction,
  and the strictest member sets it, so adding a credentialed member
  never relaxes the rules for the surface as a whole.

See the properties below.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from omniagents.core.providers.auth.base import AuthProvider, AuthResult, UserIdentity

logger = logging.getLogger(__name__)

__all__ = ["CompositeAuthProvider"]


class CompositeAuthProvider(AuthProvider):
    """Authenticates against the first of several providers that accepts."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})
        from omniagents.core.providers import resolve_provider

        cfg = config or {}
        entries = cfg.get("accept")
        if not isinstance(entries, list) or not entries:
            raise ValueError(
                "Composite authentication requires a non-empty `accept` list "
                "of provider configurations."
            )
        members: List[AuthProvider] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not entry.get("type"):
                raise ValueError(
                    f"`accept[{index}]` must be a provider config with a " "`type` key."
                )
            if str(entry["type"]).lower() in ("none", "composite"):
                # ``none`` authenticates everything, so it would make every
                # sibling unreachable and turn the surface open; ``composite``
                # nested in itself buys nothing and hides the real list.
                raise ValueError(
                    f"`accept[{index}].type` may not be "
                    f"`{entry['type']}` — it would make the composite "
                    "accept every request regardless of the other entries."
                )
            members.append(resolve_provider("auth", entry))
        self._members = members

    @property
    def identifies_principals(self) -> bool:  # type: ignore[override]
        """A composite is only as discriminating as its weakest member.

        Conjunction, not disjunction: a request may arrive on *any*
        member, so one member that resolves every caller to the same
        identity means the deployment cannot separate those callers —
        regardless of how precise its siblings are. Reporting True there
        would run the server in enforced mode while everyone on the blind
        member shared one owner.

        This is also the control that actually contains a ``none``-alike
        member. The ``accept`` type blacklist in ``__init__`` is a
        string check and an alias registered through ``register_provider``
        or the ``omniagents.providers`` entry point slips past it; the
        capability conjunction does not care what it is called.
        """
        return all(
            getattr(member, "identifies_principals", True) for member in self._members
        )

    @property
    def enforces_authorization(self) -> bool:  # type: ignore[override]
        """Enforce if *any* member does — the opposite direction to
        ``identifies_principals``, and correct for the same reason.

        These two aggregate oppositely because they answer opposite
        questions. ``identifies_principals`` is a *capability claim*, so
        the weakest member sets it. Enforcement is a *restriction*, so
        the strictest member sets it: mixing a credentialed member into a
        composite must not relax the rules for the whole surface.
        """
        return any(
            getattr(member, "enforces_authorization", True) for member in self._members
        )

    @property
    def supports_connect_tickets(self) -> bool:  # type: ignore[override]
        """Also a conjunction, for the same reason.

        A ticket minted here is redeemable on the whole surface, and the
        ticket route authenticates through *this* provider — so if any
        member declines tickets, offering them would let that member's
        credential be exchanged for one. Concretely: a 30 s single-use
        identity assertion buying a 60 s single-use query-string ticket,
        which is exactly what declining tickets exists to prevent.
        """
        return all(
            getattr(member, "supports_connect_tickets", True)
            for member in self._members
        )

    async def authenticate(self, request: Any) -> AuthResult:
        first_error: Optional[str] = None
        refused_by: Optional[str] = None
        retryable = False
        retryable_error_seen = False
        for member in self._members:
            name = type(member).__name__
            try:
                result = await member.authenticate(request)
            except Exception:  # noqa: BLE001 - one member must not veto the rest
                logger.exception(
                    "Composite auth member %s raised; treating as a refusal", name
                )
                refused_by = refused_by or name
                continue
            if result.authenticated and result.identity is not None:
                if refused_by is not None:
                    # Not a veto, and deliberately so — members verify
                    # independent credentials, so an earlier "no" usually
                    # means "not mine". But if the earlier member DID
                    # recognise this credential and refused it (a
                    # revocation, say), accepting on a later member is a
                    # silent override an operator needs to see.
                    logger.warning(
                        "Composite auth: %s refused a credential that %s then "
                        "accepted as user_id=%s. Members are tried in "
                        "configured order and the first acceptance wins; if "
                        "these members can see the same credential, order is "
                        "deciding your authorization.",
                        refused_by,
                        name,
                        result.identity.user_id,
                    )
                return result
            refused_by = refused_by or name
            # A member that refused for *capacity* rather than for the
            # credential makes the whole composite's refusal retryable:
            # it might well have accepted this credential with room to
            # spare, so reporting the aggregate as terminal would
            # permanently disconnect a client that did nothing wrong.
            if getattr(result, "retryable", False):
                retryable = True
                if first_error is None or not retryable_error_seen:
                    first_error = result.error
                    retryable_error_seen = True
            elif first_error is None:
                first_error = result.error
        return AuthResult(
            authenticated=False,
            error=first_error or "Authentication required",
            retryable=retryable,
        )

    async def get_user_identity(self, request: Any) -> Optional[UserIdentity]:
        result = await self.authenticate(request)
        return result.identity if result.authenticated else None
