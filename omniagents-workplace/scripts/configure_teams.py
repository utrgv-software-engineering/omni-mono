import argparse
import json
import subprocess
from urllib.parse import urlsplit
from uuid import UUID, uuid5


def az(*arguments):
    return json.loads(subprocess.check_output(['az', *arguments, '-o', 'json'], text=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    client = str(UUID(args.client_id))
    origin = urlsplit(args.origin)
    if origin.scheme != 'https' or not origin.netloc or origin.path not in ('', '/') or origin.query or origin.fragment or origin.username:
        parser.error('origin must be an HTTPS origin')
    app = az('ad', 'app', 'show', '--id', client)
    principal = az('ad', 'sp', 'show', '--id', client)
    if app['signInAudience'] != 'AzureADMyOrg' or principal.get('appRoleAssignmentRequired'):
        raise SystemExit('Expected an existing single-tenant app without assignment-required access; no changes made.')
    api = app.get('api') or {}
    scopes = list(api.get('oauth2PermissionScopes') or [])
    existing = next((entry for entry in scopes if entry['value'] == 'access_as_user'), None)
    if existing and (existing.get('type') != 'User' or not existing.get('isEnabled')):
        raise SystemExit('Existing access_as_user scope has incompatible consent settings; no changes made.')
    scope_id = existing['id'] if existing else str(uuid5(UUID(client), 'workplace.access_as_user'))
    if not existing:
        scopes.append({'id': scope_id, 'value': 'access_as_user', 'type': 'User', 'isEnabled': True,
            'adminConsentDisplayName': 'Access Workplace as the signed-in user',
            'adminConsentDescription': 'Access Workplace using the signed-in user’s existing Workplace permissions.',
            'userConsentDisplayName': 'Access your Workplace',
            'userConsentDescription': 'Use your existing Workplace account and permissions inside Teams.'})
    redirects = list((app.get('spa') or {}).get('redirectUris') or [])
    redirect = 'brk-multihub://' + origin.netloc
    if redirect not in redirects:
        redirects.append(redirect)
    identifiers = list(app.get('identifierUris') or [])
    resource = 'api://' + client
    if resource not in identifiers:
        identifiers.append(resource)
    required = list(app.get('requiredResourceAccess') or [])
    permission = next((entry for entry in required if entry['resourceAppId'] == client), None)
    if permission is None:
        permission = {'resourceAppId': client, 'resourceAccess': []}
        required.append(permission)
    if not any(entry['id'] == scope_id for entry in permission['resourceAccess']):
        permission['resourceAccess'].append({'id': scope_id, 'type': 'Scope'})
    patch = {'spa': {'redirectUris': redirects}, 'identifierUris': identifiers,
        'api': {**api, 'requestedAccessTokenVersion': 2, 'oauth2PermissionScopes': scopes},
        'requiredResourceAccess': required}
    print(json.dumps(patch, indent=2))
    if args.apply:
        subprocess.run(['az', 'rest', '--method', 'PATCH', '--url', 'https://graph.microsoft.com/v1.0/applications/' + app['id'], '--headers', 'Content-Type=application/json', '--body', json.dumps(patch), '-o', 'none'], check=True)
        print('Teams registration configured. No consent grants, assignments, secrets or tenant policies changed.')
    else:
        print('Preview only. Use --apply to update this app registration.')


if __name__ == '__main__':
    main()
