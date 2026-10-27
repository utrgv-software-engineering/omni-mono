import { describe, expect, it } from 'vitest';

import { resolveWsTokenSecret, signWsToken, verifyWsToken } from '@/server/ws-token';

const SECRET = 'test-secret-at-least-16-chars-long';

describe('ws token', () => {
  it('round-trips claims', () => {
    const token = signWsToken(SECRET, { sessionId: 'sess-1' });
    expect(verifyWsToken(SECRET, token)).toEqual({ sessionId: 'sess-1' });
  });

  it('rejects a token signed with a different secret (unforgeable across secrets)', () => {
    const token = signWsToken(SECRET, { sessionId: 'sess-1' });
    expect(verifyWsToken('another-secret-16chars!!', token)).toBeNull();
  });

  it('rejects a tampered payload', () => {
    const token = signWsToken(SECRET, { sessionId: 'sess-1' });
    const [, sig] = token.split('.');
    // Re-encode the payload with a far-future expiry but keep the old MAC.
    const forgedPayload = Buffer.from(JSON.stringify({ sid: 'sess-1', iat: 1, exp: 9_999_999_999 })).toString(
      'base64url'
    );
    expect(verifyWsToken(SECRET, `${forgedPayload}.${sig}`)).toBeNull();
  });

  it('rejects an expired token', () => {
    const now = Date.now();
    const token = signWsToken(SECRET, { sessionId: 'sess-1' }, 60, now);
    // 61s later → past the 60s TTL.
    expect(verifyWsToken(SECRET, token, now + 61_000)).toBeNull();
    expect(verifyWsToken(SECRET, token, now + 59_000)).not.toBeNull();
  });

  it('rejects malformed tokens', () => {
    expect(verifyWsToken(SECRET, '')).toBeNull();
    expect(verifyWsToken(SECRET, 'no-dot')).toBeNull();
    expect(verifyWsToken(SECRET, '.sig')).toBeNull();
    expect(verifyWsToken(SECRET, 'garbage.payload')).toBeNull();
  });

  it('prefers an explicit secret of sufficient length', () => {
    expect(resolveWsTokenSecret({ OMNI_WS_TOKEN_SECRET: 'x'.repeat(32) } as NodeJS.ProcessEnv)).toBe('x'.repeat(32));
  });

  it('falls back to a random secret when none is configured', () => {
    const a = resolveWsTokenSecret({} as NodeJS.ProcessEnv);
    const b = resolveWsTokenSecret({} as NodeJS.ProcessEnv);
    expect(a).not.toBe(b); // random per call
    expect(a.length).toBeGreaterThanOrEqual(16);
  });
});
