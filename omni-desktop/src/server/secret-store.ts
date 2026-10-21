/**
 * Server-mode `SecretStore`. The server shares the desktop's on-disk secret
 * store (same `git-secrets.json`, AES-256-GCM) so a credential linked in either
 * shell is usable in the other — hence this is just a re-export of
 * {@link LocalSecretStore}.
 */
export { LocalSecretStore as ServerSecretStore } from '@/main/secret-store';
