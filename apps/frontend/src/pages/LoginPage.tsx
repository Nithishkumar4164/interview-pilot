import { useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth-store';

export function LoginPage() {
  const [params] = useSearchParams();
  const next = params.get('next') || '/';
  const status = useAuthStore((s) => s.status);
  const startSignIn = useAuthStore((s) => s.startSignIn);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === 'signed_in') return <Navigate to={next} replace />;

  const onSignIn = async () => {
    setBusy(true);
    setError(null);
    try {
      await startSignIn(next); // navigates away to Google on success
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in is unavailable');
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>InterviewPilot</h1>
        <p className="auth-card__text">Sign in to plan interviews, run live sessions and review reports.</p>
        <button className="btn btn--primary btn--lg auth-card__button" onClick={onSignIn} disabled={busy}>
          {busy ? 'Redirecting…' : 'Sign in with Google'}
        </button>
        {error && (
          <p className="auth-card__error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
