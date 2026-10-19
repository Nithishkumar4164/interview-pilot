import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { useAuthStore, pairingFromToken } from '@/stores/auth-store';
import { authFetch, streamSSE } from '@/api/client';
import { RequireAuth } from '@/components/auth/RequireAuth';

function jwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64(claims)}.sig`;
}

const session = (token: string, expiresIn = 1800) => ({
  access_token: token,
  expires_in: expiresIn,
  user: { id: 'u1', email: 'a@x.com', name: 'A', role: 'interviewer', avatar_url: null },
  next: '/',
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function resetAuth(partial: Partial<ReturnType<typeof useAuthStore.getState>> = {}) {
  useAuthStore.setState({ status: 'unknown', user: null, accessToken: null, accessExpiresAt: 0, pairing: null, ...partial });
}

beforeEach(() => {
  resetAuth();
  sessionStorage.clear();
  window.history.replaceState(null, '', '/');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('bootstrap', () => {
  it('restores a signed-in session from the refresh cookie', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(session('t1')));
    vi.stubGlobal('fetch', fetchMock);
    await useAuthStore.getState().bootstrap();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/refresh', expect.objectContaining({ method: 'POST', credentials: 'same-origin' }));
    expect(useAuthStore.getState().status).toBe('signed_in');
    expect(useAuthStore.getState().accessToken).toBe('t1');
  });

  it('is signed out when there is no refresh cookie', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'Not signed in' }, 401)));
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState().status).toBe('signed_out');
  });

  it('takes a pairing token from the fragment, strips it from the URL and never calls refresh', async () => {
    const token = jwt({ type: 'pairing', sid: 's-123', exp: Math.floor(Date.now() / 1000) + 3600 });
    window.history.replaceState(null, '', `/session/s-123?panel=1#pairing=${token}`);
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().bootstrap();

    expect(useAuthStore.getState().status).toBe('paired');
    expect(useAuthStore.getState().pairing?.sessionId).toBe('s-123');
    expect(window.location.hash).toBe('');
    expect(window.location.search).toBe('?panel=1');
    expect(sessionStorage.getItem('ip_pairing')).toBe(token);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('ignores an expired stored pairing token', async () => {
    sessionStorage.setItem('ip_pairing', jwt({ type: 'pairing', sid: 's', exp: Math.floor(Date.now() / 1000) - 1 }));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({}, 401)));
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState().status).toBe('signed_out');
  });

  it('rejects non-pairing tokens in the fragment', () => {
    expect(pairingFromToken(jwt({ type: 'access', sub: 'u', exp: 9e9 }))).toBeNull();
    expect(pairingFromToken('not-a-jwt')).toBeNull();
  });
});

describe('sign-in racing the startup refresh', () => {
  it('a stale refresh 401 that lands after sign-in does not sign the user out', async () => {
    let resolveRefresh!: (r: Response) => void;
    const fetchMock = vi.fn((input: string) => {
      if (input === '/api/auth/refresh') return new Promise<Response>((r) => (resolveRefresh = r));
      return Promise.resolve(json(session('from-callback')));
    });
    vi.stubGlobal('fetch', fetchMock);

    const booting = useAuthStore.getState().bootstrap(); // refresh in flight (no cookie yet)
    await useAuthStore.getState().completeSignIn('code', 'state'); // callback wins the race
    resolveRefresh(json({ detail: 'Not signed in' }, 401)); // stale answer arrives last
    await booting;

    expect(useAuthStore.getState().status).toBe('signed_in');
    expect(useAuthStore.getState().accessToken).toBe('from-callback');
  });

  it('does not attempt a refresh while on the OAuth callback page', async () => {
    window.history.replaceState(null, '', '/auth/callback?code=c&state=s');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await useAuthStore.getState().bootstrap();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('getValidToken', () => {
  it('reuses a token that is not about to expire', async () => {
    resetAuth({ status: 'signed_in', accessToken: 'fresh', accessExpiresAt: Date.now() + 10 * 60_000 });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    expect(await useAuthStore.getState().getValidToken()).toBe('fresh');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('refreshes a token within the expiry margin', async () => {
    resetAuth({ status: 'signed_in', accessToken: 'stale', accessExpiresAt: Date.now() + 10_000 });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(session('renewed'))));
    expect(await useAuthStore.getState().getValidToken()).toBe('renewed');
  });

  it('shares one refresh between concurrent callers', async () => {
    resetAuth({ status: 'signed_in', accessToken: null, accessExpiresAt: 0 });
    const fetchMock = vi.fn().mockResolvedValue(json(session('shared')));
    vi.stubGlobal('fetch', fetchMock);
    const tokens = await Promise.all([1, 2, 3].map(() => useAuthStore.getState().getValidToken()));
    expect(tokens).toEqual(['shared', 'shared', 'shared']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('authFetch', () => {
  it('attaches the bearer token', async () => {
    resetAuth({ status: 'signed_in', accessToken: 'tok', accessExpiresAt: Date.now() + 10 * 60_000 });
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await authFetch('/api/x');
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer tok');
  });

  it('refreshes once after a 401 and retries with the new token', async () => {
    resetAuth({ status: 'signed_in', accessToken: 'revoked', accessExpiresAt: Date.now() + 10 * 60_000 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({}, 401)) // /api/x with revoked token
      .mockResolvedValueOnce(json(session('new'))) // /api/auth/refresh
      .mockResolvedValueOnce(json({ ok: true })); // retry
    vi.stubGlobal('fetch', fetchMock);

    const res = await authFetch('/api/x');

    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/refresh');
    expect(new Headers(fetchMock.mock.calls[2][1].headers).get('Authorization')).toBe('Bearer new');
  });

  it('signs out when the refresh also fails', async () => {
    resetAuth({ status: 'signed_in', accessToken: 'revoked', accessExpiresAt: Date.now() + 10 * 60_000 });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({}, 401)));
    const res = await authFetch('/api/x');
    expect(res.status).toBe(401);
    expect(useAuthStore.getState().status).toBe('signed_out');
  });

  it('never tries to refresh a pairing token', async () => {
    resetAuth({
      status: 'paired',
      pairing: { sessionId: 's', token: 'pair', expiresAt: Date.now() + 3_600_000 },
    });
    const fetchMock = vi.fn().mockResolvedValue(json({}, 401));
    vi.stubGlobal('fetch', fetchMock);
    await authFetch('/api/x');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer pair');
  });
});

describe('streamSSE', () => {
  it('parses events split across chunks', async () => {
    resetAuth({ status: 'signed_in', accessToken: 'tok', accessExpiresAt: Date.now() + 10 * 60_000 });
    const chunks = [
      'event: status\ndata: {"status":"proc',
      'essing"}\n\nevent: completed\r\ndata: {"status":"completed"}\r\n\r\n',
    ];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(new TextEncoder().encode(c));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const events: { event: string; data: string }[] = [];
    await streamSSE('/api/upload/r/stream', (e) => events.push(e));

    expect(events).toEqual([
      { event: 'status', data: '{"status":"processing"}' },
      { event: 'completed', data: '{"status":"completed"}' },
    ]);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer tok');
  });
});

describe('RequireAuth', () => {
  const renderAt = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route element={<RequireAuth />}>
            <Route path="/session/:sessionId" element={<div>live page</div>} />
            <Route path="/reports" element={<div>reports page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

  it('redirects signed-out users to login', () => {
    resetAuth({ status: 'signed_out' });
    renderAt('/reports');
    expect(screen.getByText('login page')).toBeInTheDocument();
  });

  it('lets signed-in users through', () => {
    resetAuth({ status: 'signed_in', accessToken: 't', accessExpiresAt: Date.now() + 60_000 });
    renderAt('/reports');
    expect(screen.getByText('reports page')).toBeInTheDocument();
  });

  it('confines a paired panel to its own session', () => {
    resetAuth({ status: 'paired', pairing: { sessionId: 's1', token: 'p', expiresAt: Date.now() + 60_000 } });
    renderAt('/session/s1');
    expect(screen.getByText('live page')).toBeInTheDocument();
  });

  it.each(['/session/s2', '/reports'])('blocks a paired panel from %s', (path) => {
    resetAuth({ status: 'paired', pairing: { sessionId: 's1', token: 'p', expiresAt: Date.now() + 60_000 } });
    renderAt(path);
    expect(screen.getByText('Not available here')).toBeInTheDocument();
  });
});
