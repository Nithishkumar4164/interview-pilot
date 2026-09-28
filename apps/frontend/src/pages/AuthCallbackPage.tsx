/**
 * Google redirects here with ?code&state. The backend verifies both,
 * sets the refresh cookie and returns the access token plus where to go next.
 */
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AuthError, useAuthStore } from '@/stores/auth-store';

export function AuthCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const completeSignIn = useAuthStore((s) => s.completeSignIn);
  const [error, setError] = useState<string | null>(null);
  // StrictMode runs effects twice in dev; the state is single-use, so exchange once.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const code = params.get('code');
    const state = params.get('state');
    const providerError = params.get('error');
    if (providerError || !code || !state) {
      setError(providerError === 'access_denied' ? 'Sign-in was cancelled.' : 'Sign-in failed.');
      return;
    }
    completeSignIn(code, state)
      .then((next) => navigate(next, { replace: true }))
      .catch((err: unknown) => {
        if (err instanceof AuthError && err.status === 403) {
          setError('This Google account is not allowed to use InterviewPilot.');
        } else if (err instanceof AuthError && err.status === 400) {
          setError('This sign-in link expired or was already used.');
        } else {
          setError('Sign-in failed.');
        }
      });
  }, [params, completeSignIn, navigate]);

  return (
    <div className="auth-page">
      <div className="auth-card">
        {error ? (
          <>
            <h1>Couldn&apos;t sign you in</h1>
            <p className="auth-card__error" role="alert">
              {error}
            </p>
            <Link className="btn btn--secondary" to="/login">
              Try again
            </Link>
          </>
        ) : (
          <p className="auth-card__text">Signing you in…</p>
        )}
      </div>
    </div>
  );
}
