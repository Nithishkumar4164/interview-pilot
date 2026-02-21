/**
 * Expandable detail panel for a session card.
 *
 * Shows plan questions, report scores, key moments, and stats.
 * Fetches data lazily on first expand.
 */
import { useEffect, useState } from 'react';
import * as api from '@/api/client';
import type { SessionDetailResponse } from '@/api/client';

interface SessionDetailProps {
  sessionId: string;
}

const DIFFICULTY_LABELS: Record<number, string> = {
  1: 'Easy',
  2: 'Medium',
  3: 'Hard',
  4: 'Expert',
};

export function SessionDetail({ sessionId }: SessionDetailProps) {
  const [data, setData] = useState<SessionDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getSessionDetail(sessionId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (loading) {
    return <div className="session-detail__loading">Loading details...</div>;
  }
  if (error) {
    return <div className="session-detail__error">{error}</div>;
  }
  if (!data) return null;

  return (
    <div className="session-detail">
      {/* Stats row */}
      <div className="session-detail__stats">
        <div className="session-detail__stat">
          <span className="session-detail__stat-value">{data.stats.transcript_count}</span>
          <span className="session-detail__stat-label">Transcripts</span>
        </div>
        <div className="session-detail__stat">
          <span className="session-detail__stat-value">{data.stats.analysis_count}</span>
          <span className="session-detail__stat-label">Analyses</span>
        </div>
        <div className="session-detail__stat">
          <span className="session-detail__stat-value">{data.stats.hint_count}</span>
          <span className="session-detail__stat-label">Hints</span>
        </div>
      </div>

      {/* Plan section */}
      {data.plan && (
        <div className="session-detail__section">
          <h4 className="session-detail__section-title">Interview Plan</h4>
          {data.plan.phases.map((phase, pi) => (
            <div key={pi} className="session-detail__phase">
              <div className="session-detail__phase-header">
                <span className="session-detail__phase-name">{phase.phase}</span>
                <span className="session-detail__phase-time">
                  {phase.duration_minutes}min
                </span>
              </div>
              <ul className="session-detail__questions">
                {phase.questions.map((q, qi) => (
                  <li key={qi} className="session-detail__question">
                    <span className="session-detail__question-text">{q.text}</span>
                    <span className="session-detail__question-meta">
                      <span className="session-detail__question-category">{q.category}</span>
                      <span className="session-detail__question-difficulty">
                        {DIFFICULTY_LABELS[q.difficulty] ?? `L${q.difficulty}`}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {/* Report scores section */}
      {data.report && (
        <div className="session-detail__section">
          <h4 className="session-detail__section-title">Assessment Scores</h4>
          <div className="session-detail__scores">
            {data.report.scores.map((s, i) => (
              <div key={i} className="session-detail__score-row">
                <span className="session-detail__score-criterion">
                  {s.criterion.replace(/_/g, ' ')}
                </span>
                <div className="session-detail__score-bar-bg">
                  <div
                    className="session-detail__score-bar-fill"
                    style={{ width: `${(s.score / 10) * 100}%` }}
                  />
                </div>
                <span className="session-detail__score-value">{s.score}/10</span>
              </div>
            ))}
          </div>

          {data.report.strengths.length > 0 && (
            <div className="session-detail__list-section">
              <h5>Strengths</h5>
              <ul>
                {data.report.strengths.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
          )}

          {data.report.areas_for_growth.length > 0 && (
            <div className="session-detail__list-section">
              <h5>Areas for Growth</h5>
              <ul>
                {data.report.areas_for_growth.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
