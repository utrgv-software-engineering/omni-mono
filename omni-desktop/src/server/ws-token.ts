/**
 * WS tokens — the credential a launcher client presents on `/ws` and
 * `/ws/chat` (`?token=`; the browser WebSocket API can't send Bearer headers).
 *
 * Minted by `/api/ws-token` for trusted networks, or directly by the Windows
 * shell for its WSL daemon (which shares the secret via `OMNI_WS_TOKEN_SECRET`).
 *
 * Format (compact, dependency-free): `<base64url(payload)>.<base64url(hmac)>`
 * where payload is `{ sid, iat, exp }` and the MAC is HMAC-SHA256 over the
 * payload segment. Symmetric by design: the same server that mints also
 * verifies.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';

import { uuidv4 } from '@/lib/uuid';

const DEFAULT_TTL_SEC = 12 * 60 * 60; // 12h — comfortably longer than a session.

export interface WsTokenClaims {
  sessionId: string;
}

interface TokenPayload {
  sid: string;
  iat: number;
  exp: number;
}

function b64url(buf: Buffer): string {
  return buf.toString('base64url');
}

function hmac(secret: string, data: string): Buffer {
  return createHmac('sha256', secret).update(data).digest();
}

/**
 * Resolve the signing secret: the explicit env var when set (the WSL daemon
 * receives its shell's secret this way), else a random per-process secret.
 */
export function resolveWsTokenSecret(env: NodeJS.ProcessEnv = process.env): string {
  const explicit = env['OMNI_WS_TOKEN_SECRET'];
  if (explicit && explicit.length >= 16) {
    return explicit;
  }
  if (explicit) {
    console.warn('[ws-token] OMNI_WS_TOKEN_SECRET is too short (<16 chars); ignoring.');
  }
  return uuidv4() + uuidv4();
}

/** Mint a signed WS token. */
export function signWsToken(secret: string, claims: WsTokenClaims, ttlSec = DEFAULT_TTL_SEC, now = Date.now()): string {
  const iat = Math.floor(now / 1000);
  const payload: TokenPayload = { sid: claims.sessionId, iat, exp: iat + ttlSec };
  const segment = b64url(Buffer.from(JSON.stringify(payload)));
  const sig = b64url(hmac(secret, segment));
  return `${segment}.${sig}`;
}

/**
 * Verify a WS token and return its claims, or null if the signature is
 * invalid, the format is malformed, or it has expired.
 */
export function verifyWsToken(secret: string, token: string, now = Date.now()): WsTokenClaims | null {
  const dot = token.indexOf('.');
  if (dot <= 0) {
    return null;
  }
  const segment = token.slice(0, dot);
  const provided = token.slice(dot + 1);

  const expected = b64url(hmac(secret, segment));
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    return null;
  }

  let payload: TokenPayload;
  try {
    payload = JSON.parse(Buffer.from(segment, 'base64url').toString('utf-8')) as TokenPayload;
  } catch {
    return null;
  }
  if (
    !payload ||
    typeof payload.sid !== 'string' ||
    !payload.sid ||
    typeof payload.exp !== 'number' ||
    !Number.isFinite(payload.exp)
  ) {
    return null;
  }
  if (payload.exp * 1000 <= now) {
    return null;
  }
  return { sessionId: payload.sid };
}
