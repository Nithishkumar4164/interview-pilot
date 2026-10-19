/**
 * Authentication state.
 *
 * Two ways to be authenticated:
 *  - signed_in: a Google login. The access token lives only in memory; the
 *    refresh token is an HttpOnly cookie the page can't read, so on load we
 *    call /api/auth/refresh to recover the session.
 *  - paired: the extension's side panel embeds the dashboard with a
 *    session-scoped pairing token in the URL fragment (#pairing=...). It works
 *    only for that one session.
 *
 * This module talks to the auth endpoints with plain fetch (not the API
 * client) so the client can depend on it without a cycle.
 */
import { create } from 'zustand';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
  avatar_url: string | null;
}

export interface Pairing {
  sessionId: string;
  token: string;
  expiresAt: number; // epoch ms
}

type Status = 'unknown' | 'signed_in' | 'signed_out' | 'paired';

interface AuthSession {
  access_token: string;
  expires_in: number;
  user: AuthUser;
  next: string;
}

interface AuthState {
  status: Status;
  user: AuthUser | null;
  accessToken: string | null;
  accessExpiresAt: number;
  pairing: Pairing | null;

  bootstrap: () => Promise<void>;
  startSignIn: (next: string) => Promise<void>;
  completeSignIn: (code: string, state: string) => Promise<string>;
  signOut: () => Promise<void>;
  /** A token valid for at least the next minute, refreshing if needed. */
  getValidToken: () => Promise<string | null>;
  /** Called after a 401: refresh once; returns the new token or null (signed out). */
  recoverFromUnauthorized: () => Promise<string | null>;
}

const PAIRING_STORAGE_KEY = 'ip_pairing';
const REFRESH_MARGIN_MS = 60_000;

export class AuthError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

/** Decode a JWT payload without verifying it (the server verifies). */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
    return JSON.parse(atob(b64));
  } catch {
    return null;
  }
}

export function pairingFromToken(token: string): Pairing | null {
  const claims = decodeJwtPayload(token);
  if (!claims || claims.type !== 'pairing' || typeof claims.sid !== 'string' || typeof claims.exp !== 'number') {
    return null;
  }
  return { sessionId: claims.sid, token, expiresAt: claims.exp * 1000 };
}

/** Take a pairing token from the URL fragment (then remove it from the URL) or from sessionStorage. */
function loadPairing(): Pairing | null {
  const hash = new URLSearchParams(window.location.hash.slice(1));
  const fromHash = hash.get('pairing');
  if (fromHash) {
    hash.delete('pairing');
    const rest = hash.toString();
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search + (rest ? `#${rest}` : ''),
    );
    const pairing = pairingFromToken(fromHash);
    if (pairing) {
      try {
        sessionStorage.setItem(PAIRING_STORAGE_KEY, fromHash);
      } catch {
        // storage unavailable: the pairing lasts for this page load only
      }
    }
    return pairing;
  }
  try {
    const stored = sessionStorage.getItem(PAIRING_STORAGE_KEY);
    const pairing = stored ? pairingFromToken(stored) : null;
    return pairing && pairing.expiresAt > Date.now() ? pairing : null;
  } catch {
    return null;
  }
}

async function postAuth(path: string, body?: unknown): Promise<Response> {
  return fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

let refreshInFlight: Promise<string | null> | null = null;
// Bumped whenever a session is established. A refresh that started under an
// older epoch must not sign out a session established while it was in flight
// (e.g. the startup refresh racing the OAuth callback).
let sessionEpoch = 0;

const CALLBACK_PATH = '/auth/callback';

export const useAuthStore = create<AuthState>((set, get) => {
  const applySession = (session: AuthSession) => {
    sessionEpoch += 1;
    set({
      status: 'signed_in',
      user: session.user,
      accessToken: session.access_token,
      accessExpiresAt: Date.now() + session.expires_in * 1000,
    });
  };

  const signedOut = () => set({ status: 'signed_out', user: null, accessToken: null, accessExpiresAt: 0 });

  /** Single-flight: concurrent 401s share one refresh request. */
  const refresh = (): Promise<string | null> => {
    if (!refreshInFlight) {
      refreshInFlight = (async () => {
        const startedAt = sessionEpoch;
        try {
          const res = await postAuth('/api/auth/refresh');
          if (!res.ok) {
            if (sessionEpoch !== startedAt) return get().accessToken; // superseded by a newer session
            signedOut();
            return null;
          }
          const session = (await res.json()) as AuthSession;
          applySession(session);
          return session.access_token;
        } catch {
          // Network failure: keep current state, let the caller surface the error.
          return null;
        } finally {
          refreshInFlight = null;
        }
      })();
    }
    return refreshInFlight;
  };

  return {
    status: 'unknown',
    user: null,
    accessToken: null,
    accessExpiresAt: 0,
    pairing: null,

    bootstrap: async () => {
      const pairing = loadPairing();
      if (pairing) {
        set({ status: 'paired', pairing });
        return;
      }
      // The callback page establishes the session itself; a refresh here would
      // race it (and there is no cookie yet on a first sign-in).
      if (window.location.pathname === CALLBACK_PATH) return;
      await refresh();
      if (get().status === 'unknown') signedOut(); // network failure during bootstrap
    },

    startSignIn: async (next: string) => {
      const res = await fetch(`/api/auth/google/start?next=${encodeURIComponent(next)}`);
      if (!res.ok) throw new AuthError(res.status, (await res.text()) || 'Sign-in is unavailable');
      const { authorization_url } = (await res.json()) as { authorization_url: string };
      window.location.assign(authorization_url);
    },

    completeSignIn: async (code: string, state: string) => {
      const res = await postAuth('/api/auth/google/callback', { code, state });
      if (!res.ok) {
        let detail = 'Sign-in failed';
        try {
          detail = ((await res.json()) as { detail?: string }).detail ?? detail;
        } catch {
          // keep default
        }
        throw new AuthError(res.status, detail);
      }
      const session = (await res.json()) as AuthSession;
      applySession(session);
      return session.next || '/';
    },

    signOut: async () => {
      try {
        await postAuth('/api/auth/logout');
      } finally {
        signedOut();
      }
    },

    getValidToken: async () => {
      const { status, pairing, accessToken, accessExpiresAt } = get();
      if (status === 'paired') return pairing?.token ?? null;
      if (accessToken && accessExpiresAt - Date.now() > REFRESH_MARGIN_MS) return accessToken;
      if (status === 'signed_out') return null;
      return refresh();
    },

    recoverFromUnauthorized: async () => {
      if (get().status === 'paired') return null; // a pairing token can't be refreshed
      return refresh();
    },
  };
});
