import { describe, it, expect } from 'vitest';
import { parsePairingToken, panelUrl } from '../src/auth/pairing';

function jwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64(claims)}.sig`;
}

describe('parsePairingToken', () => {
  const now = 1_700_000_000_000;

  it('reads the session and expiry from a pairing token', () => {
    const token = jwt({ type: 'pairing', sid: 'sess-1', exp: now / 1000 + 60 });
    expect(parsePairingToken(`  ${token}\n`, now)).toEqual({ token, sessionId: 'sess-1', expiresAt: now + 60_000 });
  });

  it.each([
    ['expired', jwt({ type: 'pairing', sid: 's', exp: now / 1000 - 1 })],
    ['access token', jwt({ type: 'access', sub: 'u', exp: now / 1000 + 60 })],
    ['no session', jwt({ type: 'pairing', exp: now / 1000 + 60 })],
    ['a bare session id', '3f2b1c9e-0000-4000-8000-000000000000'],
    ['garbage', 'a.b.c'],
  ])('rejects %s', (_label, raw) => {
    expect(parsePairingToken(raw, now)).toBeNull();
  });
});

describe('panelUrl', () => {
  it('puts the token in the fragment, not the query', () => {
    const pairing = { token: 'x.y.z', sessionId: 's1', expiresAt: 0 };
    const url = new URL(panelUrl('https://app.test/', pairing));
    expect(url.pathname).toBe('/session/s1');
    expect(url.search).toBe('?panel=1');
    expect(url.hash).toBe('#pairing=x.y.z');
  });
});
