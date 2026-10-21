"""Omni Platform provider package.

Connects OmniAgents to the omni-platform management plane for:
- JWT authentication (validates runtime tokens via JWKS)
- Remote audit event shipping (POST /api/v1/audit/ingest)
- Policy fetching (GET /api/v1/sessions/{id}/policy at session start)
- Credential vending (GET /api/v1/sessions/{id}/credentials/{rid})

Configuration in project.yml::

    security:
      audit:
        enabled: true
        backend: "remote"
      providers:
        auth:
          type: "platform"
          platform_url: "${OMNI_PLATFORM_URL}"
        notification:
          type: "webhook"
          url: "${OMNI_PLATFORM_URL}/api/v1/audit/ingest"
"""

from omniagents.core.providers import register_provider

from omniagents.core.providers.platform.auth import PlatformAuthProvider
from omniagents.core.providers.platform.audit import (
    RemoteAuditBackend,
    create_remote_audit_hook,
)

# Register providers so resolve_provider("auth", {"type": "platform"}) works
register_provider("auth", "platform", PlatformAuthProvider)
