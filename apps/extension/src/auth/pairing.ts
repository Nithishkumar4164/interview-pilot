/**
 * Pairing tokens: session-scoped credentials minted by the dashboard.
 *
 * The extension only reads the claims it needs (which session, when it
 * expires); the backend verifies the signature on every socket upgrade.
 */

export interface Pairing {
  token: string;
  sessionId: string;
  expiresAt: number; // epoch ms
}

function decodePayload(token: string): Record<string, unknown> | null {
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
    return JSON.parse(atob(b64));
  } catch {
    return null;
  }
}

/** Parse a pasted or received pairing code; null if it isn't a live pairing token. */
export function parsePairingToken(raw: string, now: number = Date.now()): Pairing | null {
  const token = raw.trim();
  const claims = decodePayload(token);
  if (!claims || claims.type !== 'pairing') return null;
  if (typeof claims.sid !== 'string' || typeof claims.exp !== 'number') return null;
  const expiresAt = claims.exp * 1000;
  if (expiresAt <= now) return null;
  return { token, sessionId: claims.sid, expiresAt };
}

/** The dashboard URL for the side panel; the token rides in the fragment so it never reaches server logs. */
export function panelUrl(frontendUrl: string, pairing: Pairing): string {
  return `${frontendUrl.replace(/\/$/, '')}/session/${pairing.sessionId}?panel=1#pairing=${encodeURIComponent(pairing.token)}`;
}
