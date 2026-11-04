# Optional Azure App Service Easy Auth

Workplace can use Microsoft sign-in supplied by Azure App Service Easy Auth.
This is opt-in; local authentication and existing non-Azure deployments do not
change. This is not a portable OIDC implementation or a new authentication proxy.

## Configure Azure first

1. Deploy Workplace behind **Azure App Service Authentication**, with Microsoft
   Entra as its identity provider and a tenant-specific issuer. Configure the
   Entra app registration and its Azure callback (`https://<host>/.auth/login/aad/callback`)
   as directed by Azure. Use the production public HTTPS hostname as Workplace's
   base URL. Persist the Workplace data directory across deployments.
2. Set Azure's unauthenticated-request action to **Allow anonymous requests**.
   Workplace still enforces its existing authentication and permissions. This
   permits token-authenticated agent APIs, webhooks, guest share links, health
   checks, and public sandbox assets to work without Microsoft login redirects.
   Do not disable Workplace authentication or expose its private API publicly.
3. Ensure **all public application traffic passes through Easy Auth**. Block
   direct access to the backend port and other ingress routes that could bypass
   it. Azure strips/replaces client-supplied identity headers. Workplace cannot
   cryptographically verify these headers; enabling this mode behind an ordinary
   reverse proxy would permit impersonation. Do not forward Azure identity
   headers from untrusted callers or agent sandbox listeners.
4. If a custom login hostname is used, configure Azure's allowed post-login and
   logout redirect destinations for that hostname. Sandbox app origins must
   continue serving their public assets without interactive authentication; they
   must never gain access to Workplace accounts or cookies.

Microsoft references:
- [App Service authentication](https://learn.microsoft.com/en-us/azure/app-service/overview-authentication-authorization)
- [Identity headers and claims](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-user-identities)

## Link existing accounts explicitly

Complete the normal initial administrator setup and add people through Workplace.
For a multi-factory installation, add them at the **installation** level and grant
factory memberships separately. They do not need to redeem a password invitation
before Microsoft sign-in can work. Local password login remains available.

Obtain each Workplace account's `id` from the authenticated administration API
`GET /workplace/api/people` for standalone Workplace, or
`GET /workplace/api/installation/people` for multi-factory hosts.
Obtain the corresponding user's **Object ID** and the directory's **Tenant ID**
from Microsoft Entra. Configure the Workplace **server process**, not factory or
agent secrets, with:

```sh
WORKPLACE_AZURE_EASY_AUTH=true
WORKPLACE_AZURE_TENANT_ID=11111111-1111-1111-1111-111111111111
WORKPLACE_AZURE_ACCOUNT_MAP='{"22222222-2222-2222-2222-222222222222":"<workplace-account-id>"}'
```

The map's keys are Entra object IDs, and values are existing Workplace account
IDs. Multiple entries are supported. These identifiers are not login secrets.
Restart Workplace after changing the configuration. An empty map is allowed and
refuses all Microsoft identities until accounts are linked. Invalid configuration
fails startup. `WORKPLACE_AZURE_EASY_AUTH` defaults to `false`; merely running on
Azure (`WEBSITE_HOSTNAME`) does not enable it.

There is no email-based matching, automatic account creation, role import, or
first-Microsoft-user-becomes-admin behavior. An unlinked identity or disabled
account is refused. Deleting and recreating a Workplace account requires updating
its mapping. Existing account roles and factory memberships remain authoritative.

## Login and logout

The login page displays **Sign in with Microsoft** alongside local sign-in. It
starts at `/workplace/auth/azure/login`, redirects to Azure, and returns to
`/workplace/auth/azure/callback`. The callback validates a one-time, ten-minute
browser-bound state and the Azure principal's provider, tenant and object ID.
It rotates any existing Workplace session and returns to the installation root.
The Azure principal name/email alone is never accepted.

Workplace uses its normal session cookie, lifetime, disabled-account checks, and
factory authorization for subsequent HTTP requests, WebSockets and UI bridges.
Signing out revokes that session before navigating through `/.auth/logout`.
Session responses expose only login/logout URLs, not the account map or claims.

Removing an Azure mapping or disabling the provider prevents new Microsoft
logins; it does not revoke already issued Workplace sessions. Disable the
Workplace account to revoke its sessions immediately. Disabling an Entra identity
alone does not immediately revoke an existing Workplace session. Choose an
appropriate Workplace session lifetime for the installation's access policy.

## Verification on an Azure deployment

Before admitting users, verify the actual Azure ingress boundary:

- A mapped Microsoft user signs in; an unlinked user and wrong-tenant identity
  cannot. Disabled Workplace accounts remain blocked.
- A direct unauthenticated request with forged `X-MS-CLIENT-PRINCIPAL` and
  `X-MS-CLIENT-PRINCIPAL-NAME` headers cannot create a session, including through
  alternate hostnames or backend ports. Azure must strip these headers.
- An ordinary member sees only assigned factories; chat WebSockets work with
  the resulting Workplace session. Removing membership stops access.
- Logout invalidates the local session and visits Azure logout. Local sign-in
  still works for administrator recovery.
- Existing authenticated agent APIs and webhooks work; guest share links,
  health checks, and isolated MCP app assets do not redirect to Microsoft.

Automated tests cover the application boundary with simulated Azure headers.
They cannot establish that a future Azure deployment has correctly configured
its ingress; that requires the checks above on that deployment.

## Behind Workplace's own App Service proxy

On the Azure deployment, App Service runs only a reverse proxy, and Workplace
runs on a private VM behind it (omni-factory's `deploy/azure/`). Easy Auth's
headers then reach Workplace through the proxy, so Workplace must know they
came from it:

- Set `WORKPLACE_AZURE_PROXY_TOKEN` on Workplace to a secret the proxy also
  holds (Key Vault). The proxy sets `X-Workplace-Proxy-Token` on every request,
  replacing any copy a client sent; Workplace honors Microsoft identity headers
  only on requests carrying it, and refuses them from anywhere else (sandboxes,
  previews, anything inside the network).
- Under `WORKPLACE_POLICY=strict`, password sign-in is off, and every request's
  Microsoft identity must still be the session's: signing out of Microsoft, or
  being removed there, ends the Workplace session at once.
- The network adds the rest: the VM accepts HTTPS only from App Service's
  integration subnet.
