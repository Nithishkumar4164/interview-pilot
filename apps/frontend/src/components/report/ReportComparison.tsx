/**
 * ReportComparison — overlaid radar charts and side-by-side score table.
 */
import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import * as api from '@/api/client';
import { RadarChart } from './RadarChart';

interface ScoreEntry {
  criterion: string;
  score: number;
  evidence: string[];
  trend: string;
}

interface ComparedReport {
  id: string;
  session_id: string;
  recommendation: string;
  confidence_level: number;
  is_final: boolean;
  created_at: string;
  scores: ScoreEntry[];
  strengths: string[];
  areas_for_growth: string[];
}

const COLORS = ['#6366f1', '#f59e0b', '#10b981', '#ef4444'];

function formatCriterion(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatRecommendation(rec: string): string {
  return rec.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ReportComparisonPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [reports, setReports] = useState<ComparedReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ids = searchParams.get('ids');
    if (!ids) {
      setError('No report IDs provided');
      setLoading(false);
      return;
    }

    api
      .compareReports(ids.split(','))
      .then((data) => setReports(data.reports as ComparedReport[]))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [searchParams]);

  if (loading) {
    return (
      <div className="report-comparison">
        <p>Loading comparison...</p>
      </div>
    );
  }

  if (error || reports.length < 2) {
    return (
      <div className="report-comparison">
        <p>{error || 'Need at least 2 reports to compare'}</p>
        <button className="btn btn--primary" onClick={() => navigate('/reports')}>
          Back to Reports
        </button>
      </div>
    );
  }

  // Use first report as primary, rest as overlays
  const primaryScores = reports[0].scores.map((s) => ({
    criterion: s.criterion,
    score: s.score,
  }));

  const overlayScores = reports.slice(1).map((r) =>
    r.scores.map((s) => ({
      criterion: s.criterion,
      score: s.score,
    })),
  );

  // Get all unique criteria
  const allCriteria = Array.from(
    new Set(reports.flatMap((r) => r.scores.map((s) => s.criterion))),
  );

  return (
    <div className="report-comparison">
      <header className="report-comparison__header">
        <button className="btn btn--ghost" onClick={() => navigate('/reports')}>
          &larr; Back to Reports
        </button>
        <h1>Report Comparison</h1>
      </header>

      {/* Legend */}
      <div className="report-comparison__legend">
        {reports.map((r, i) => (
          <div key={r.id} className="report-comparison__legend-item">
            <div
              className="report-comparison__legend-swatch"
              style={{ backgroundColor: COLORS[i % COLORS.length] }}
            />
            <span>
              {new Date(r.created_at).toLocaleDateString()} &mdash;{' '}
              {formatRecommendation(r.recommendation)}
            </span>
          </div>
        ))}
      </div>

      {/* Overlaid Radar Chart */}
      <div className="report-comparison__charts">
        <RadarChart scores={primaryScores} size={400} overlays={overlayScores} />
      </div>

      {/* Score Comparison Table */}
      <table className="report-comparison__table">
        <thead>
          <tr>
            <th>Criterion</th>
            {reports.map((r, i) => (
              <th key={r.id} style={{ color: COLORS[i % COLORS.length] }}>
                {new Date(r.created_at).toLocaleDateString()}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {allCriteria.map((criterion) => (
            <tr key={criterion}>
              <td>{formatCriterion(criterion)}</td>
              {reports.map((r) => {
                const s = r.scores.find((sc) => sc.criterion === criterion);
                return (
                  <td key={r.id}>
                    {s ? `${s.score}/5 (${s.trend})` : '-'}
                  </td>
                );
              })}
            </tr>
          ))}
          <tr>
            <td><strong>Recommendation</strong></td>
            {reports.map((r) => (
              <td key={r.id}>
                <strong>{formatRecommendation(r.recommendation)}</strong>
              </td>
            ))}
          </tr>
          <tr>
            <td><strong>Confidence</strong></td>
            {reports.map((r) => (
              <td key={r.id}>{Math.round(r.confidence_level * 100)}%</td>
            ))}
          </tr>
        </tbody>
      </table>

      {/* Strengths/Growth comparison */}
      <div className="assessment-grid">
        {reports.map((r, i) => (
          <div key={r.id} className="assessment-col">
            <h3 style={{ color: COLORS[i % COLORS.length] }}>
              {new Date(r.created_at).toLocaleDateString()}
            </h3>
            <h4>Strengths</h4>
            <ul>
              {r.strengths.map((s, j) => (
                <li key={j}>{s}</li>
              ))}
            </ul>
            <h4>Growth Areas</h4>
            <ul>
              {r.areas_for_growth.map((g, j) => (
                <li key={j}>{g}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
