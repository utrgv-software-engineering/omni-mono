# Workplace in Microsoft Teams

Teams support is opt-in for standalone Workplace behind Azure App Service EasyAuth. Ordinary browser sign-in is unchanged. No Teams bot, Microsoft Graph data permissions, Entra user/group assignments, new Azure infrastructure, or public ingress is required.

## Authentication

A personal Teams tab opens `/?host=teams`. TeamsJS initializes before MSAL's nestable client. Tokens use the organization's tenant and the delegated `api://<client-id>/access_as_user` scope. The initial attempt is silent; consent/MFA requires the user to select **Continue with Microsoft**. Unsupported hosts offer the normal browser sign-in instead. Teams context supplies a login hint, never authorization.

EasyAuth validates the access token. Workplace trusts only its existing authenticated proxy boundary, verifies tenant/account mapping, delegated scope, requesting client and expiry, and starts a session lasting at most five minutes or the token's remaining lifetime. Refresh reuses the session so repository device-login state stays bound to it. Normal API requests carry both a fresh Microsoft token and an opaque Workplace session header; neither is persisted by Workplace's frontend. MSAL uses memory caching.

Live updates use authenticated fetch streaming. WebSockets exchange authenticated requests for single-use, exact-path tickets expiring within 60 seconds. Download tickets also expire within 60 seconds, are bound to the exact path/query, and permit only GET/HEAD to the approved file routes. Tickets are opaque, backed by the current Workplace session and checked against account disablement. Microsoft access tokens and Workplace session credentials never go into URLs. Ticket-bearing URLs should not be retained in request-query logs. Tickets are held in the existing single-process server and expire on restart.

Images/media/PDF previews use authenticated fetch and temporary blob URLs, revoked when unmounted. Large media therefore downloads before preview; streaming-range optimization is not part of this change. Library and canvas pages retain their existing scoped view URLs. MCP frames retain their separate origins, restricted APIs and exact-parent postMessage validation; they do not receive the Teams credentials.

Only the framing directives change: Teams and `*.cloud.microsoft` can be ancestors. Mutation origin/CSRF checks still require Workplace's origin. GitHub device sign-in works in Teams; the optional GitHub App redirect flow explicitly directs users to the browser rather than weakening callback/session binding.

## Configure the existing Entra registration

First preview the narrow registration update:

```sh
python omniagents-workplace/scripts/configure_teams.py \
  --client-id YOUR_EXISTING_WORKPLACE_CLIENT_ID \
  --origin https://YOUR_WORKPLACE_HOST
```

Add `--apply` to apply it. The script preserves the existing web callback, declares the NAA SPA redirect `brk-multihub://YOUR_WORKPLACE_HOST`, exposes `access_as_user` as a user-consentable delegated scope, requests v2 access tokens, and declares the client permission. It does not grant consent, alter tenant policy, create secrets, add Graph permissions, or change assignment-required access. User consent remains subject to tenant policy: an admin approval prompt is a policy outcome, not an implementation guarantee or bypass.

EasyAuth must remain configured for the tenant's v2 issuer and the Workplace client-ID/API audiences. Keep the existing proxy secret/header protections and identity-to-account mapping. Do not add Graph as an accepted token audience. Set `WORKPLACE_TEAMS_CLIENT_ID` to the same client ID in the persistent runtime environment; an empty value disables Teams endpoints and preserves the original framing policy. The Azure shared Compose overlay passes this optional variable into Workplace.

Rebuild and deploy the runtime through the existing pinned-image process, carrying the updated shared Compose file. No deployment is performed by the configuration or package scripts.

## Build the Teams app package

Supply real organization-approved privacy and terms URLs; the generator does not invent legal policies:

```sh
python omniagents-workplace/scripts/package_teams.py \
  --client-id YOUR_EXISTING_WORKPLACE_CLIENT_ID \
  --origin https://YOUR_WORKPLACE_HOST \
  --privacy-url https://YOUR_APPROVED_PRIVACY_POLICY \
  --terms-url https://YOUR_APPROVED_TERMS \
  --output workplace-teams.zip
```

The package contains a version 1.22 manifest, a personal tab, `webApplicationInfo`, the exact Workplace domain and correctly sized PNG icons. Upload it as an internal custom Teams app. Teams app publishing/installation policy may require an administrator separately from Entra consent. No public Teams Store publication is assumed. Optional NAA token prefetching is deliberately omitted.

Users still need private-network access to Workplace and outbound access to Microsoft authentication services. Agents' factory egress allowlists are not the browser's network policy.

## Verification before enabling for users

- Test Teams web and desktop, including third-party cookies blocked, reload, silent renewal, account switching, consent denial and MFA.
- Confirm invalid/wrong-tenant/wrong-audience tokens fail at EasyAuth; unlinked/disabled users and insufficient scopes fail in Workplace.
- Confirm factory/admin permissions, MCP app calls, attachments, shared downloads, live events and WebSocket reconnects.
- Confirm browser login/logout, existing GitHub device login and restricted MCP origins remain unchanged.
- Automated tests use simulated trusted EasyAuth assertions and mocked MSAL/Teams APIs; they do not prove the tenant allows consent or that real Teams SSO has completed.

To roll back, clear `WORKPLACE_TEAMS_CLIENT_ID` and deploy the prior runtime/configuration. Preserve account data and the original Entra web callback. Do not delete the existing app registration or modify tenant consent policy.
