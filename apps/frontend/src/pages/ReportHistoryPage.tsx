/**
 * Report History page — lists all past reports with comparison selection.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import * as api from '@/api/client';

interface ReportSummary {
  id: string;
  session_id: string;
  recommendation: string;
  confidence_level: number;
  status: string;
  is_final: boolean;
  created_at: string;
  level: string;
}

function getRecommendationColor(rec: string): string {
  const lower = rec.toLowerCase();
  if (lower === 'strong_hire') return 'green';
  if (lower === 'hire') return 'green';
  if (lower === 'lean_hire') return 'amber';
  if (lower === 'lean_no_hire') return 'orange';
  if (lower === 'no_hire') return 'red';
  return 'neutral';
}

function formatRecommendation(rec: string): string {
  return rec.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ReportHistoryPage() {
  const navigate = useNavigate();
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    api
      .listReports(50, 0)
      .then((data) => setReports(data.reports))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleCompare = useCallback(() => {
    const ids = Array.from(selected);
    if (ids.length >= 2) {
      navigate(`/reports/compare?ids=${ids.join(',')}`);
    }
  }, [selected, navigate]);

  if (loading) {
    return (
      <div className="report-history-page">
        <p>Loading reports...</p>
      </div>
    );
  }

  return (
    <div className="report-history-page">
      <header className="report-history-page__header">
        <div>
          <button className="btn btn--ghost" onClick={() => navigate('/')}>
            &larr; Back
          </button>
          <h1>Report History</h1>
        </div>
      </header>

      {reports.length === 0 ? (
        <p>No reports found. Complete an interview session to generate a report.</p>
      ) : (
        <>
          <div className="report-history-page__list">
            {reports.map((r) => {
              const color = getRecommendationColor(r.recommendation);
              const isSelected = selected.has(r.id);

              return (
                <div
                  key={r.id}
                  className={`report-history-card ${isSelected ? 'report-history-card--selected' : ''}`}
                  onClick={() => navigate(`/reports/${r.id}`)}
                >
                  <input
                    type="checkbox"
                    className="report-history-card__checkbox"
                    checked={isSelected}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleSelect(r.id);
                    }}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <div className="report-history-card__info">
                    <span className="report-history-card__date">
                      {new Date(r.created_at).toLocaleDateString(undefined, {
                        year: 'numeric',
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                    <div className="report-history-card__meta">
                      <span className={`report-history-card__badge report-history-card__badge--${color}`}>
                        {formatRecommendation(r.recommendation)}
                      </span>
                      <span>Confidence: {Math.round(r.confidence_level * 100)}%</span>
                      {r.level && <span>Level: {r.level}</span>}
                      {r.status !== 'completed' && (
                        <span style={{ color: 'var(--color-warning)' }}>({r.status})</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {selected.size >= 2 && (
            <div className="report-history-page__compare-bar">
              <span>{selected.size} reports selected</span>
              <button className="btn btn--primary" onClick={handleCompare}>
                Compare Selected
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
