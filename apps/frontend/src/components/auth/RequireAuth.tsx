/**
 * Route guard.
 *
 * - While the session is being restored, render a placeholder.
 * - Signed out: send the user to /login, remembering where they were going.
 * - Paired (extension side panel): only that session's live page is reachable.
 */
import { Navigate, Outlet, useLocation, matchPath } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth-store';

export function RequireAuth() {
  const status = useAuthStore((s) => s.status);
  const pairing = useAuthStore((s) => s.pairing);
  const location = useLocation();

  if (status === 'unknown') {
    return <div className="auth-page__loading">Loading…</div>;
  }

  if (status === 'signed_out') {
    const next = location.pathname + location.search;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  if (status === 'paired' && pairing) {
    const match = matchPath('/session/:sessionId', location.pathname);
    if (match?.params.sessionId !== pairing.sessionId) {
      return (
        <div className="auth-page">
          <div className="auth-card">
            <h1>Not available here</h1>
            <p className="auth-card__text">
              The extension panel only has access to the session it was paired with.
            </p>
          </div>
        </div>
      );
    }
  }

  return <Outlet />;
}
