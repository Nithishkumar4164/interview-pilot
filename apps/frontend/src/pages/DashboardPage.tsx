/**
 * Dashboard page — landing page showing session controls.
 *
 * Allows starting a new live session or viewing past reports.
 */
import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '@/stores/session-store';
import * as api from '@/api/client';

export function DashboardPage() {
  const navigate = useNavigate();
  const setSession = useSessionStore((s) => s.setSession);
  const [level, setLevel] = useState('SDE-2');
  const [duration, setDuration] = useState(45);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStartSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.startSession({
        level,
        duration_minutes: duration,
      });
      setSession(result.session_id, level, duration);
      navigate(`/session/${result.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start session');
    } finally {
      setLoading(false);
    }
  }, [level, duration, navigate, setSession]);

  return (
    <div className="dashboard-page">
      <header className="dashboard-page__header">
        <h1>InterviewPilot</h1>
        <p>AI-Powered Real-Time Interview Assistant</p>
      </header>

      <section className="dashboard-page__start" aria-label="Start Interview">
        <h2>Start New Interview</h2>

        <div className="form-group">
          <label htmlFor="level">Candidate Level</label>
          <select
            id="level"
            value={level}
            onChange={(e) => setLevel(e.target.value)}
          >
            <option value="SDE-1">SDE-1 (Junior)</option>
            <option value="SDE-2">SDE-2 (Mid)</option>
            <option value="SDE-3">SDE-3 (Senior)</option>
            <option value="Staff">Staff Engineer</option>
          </select>
        </div>

        <div className="form-group">
          <label htmlFor="duration">Duration (minutes)</label>
          <input
            id="duration"
            type="number"
            min={15}
            max={120}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          />
        </div>

        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}

        <button
          className="btn btn--primary btn--lg"
          onClick={handleStartSession}
          disabled={loading}
        >
          {loading ? 'Starting...' : 'Start Live Session'}
        </button>
      </section>
    </div>
  );
}
