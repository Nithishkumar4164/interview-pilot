/**
 * Sessions listing page — browse all interview sessions.
 *
 * Shows session cards with status, focus areas, report summary,
 * expandable detail view, and context-dependent actions.
 */
import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import * as api from '@/api/client';
import type { SessionListItem } from '@/api/client';
import { SessionDetail } from '@/components/sessions/SessionDetail';

const STATUS_FILTERS = ['all', 'planning', 'ready', 'live', 'paused', 'ended'] as const;

const STATUS_LABELS: Record<string, string> = {
  all: 'All',
  planning: 'Planning',
  ready: 'Ready',
  live: 'Live',
  paused: 'Paused',
  ended: 'Ended',
};

const LEVEL_LABELS: Record<string, string> = {
  intern: 'Intern',
  sde1: 'SDE-1',
  sde2: 'SDE-2',
  senior: 'Senior',
  staff: 'Staff',
  principal: 'Principal',
};

const RECOMMENDATION_LABELS: Record<string, { text: string; className: string }> = {
  strong_hire: { text: 'Strong Hire', className: 'session-card__rec--green' },
  hire: { text: 'Hire', className: 'session-card__rec--green' },
  lean_hire: { text: 'Lean Hire', className: 'session-card__rec--amber' },
  lean_no_hire: { text: 'Lean No Hire', className: 'session-card__rec--orange' },
  no_hire: { text: 'No Hire', className: 'session-card__rec--red' },
  strong_no_hire: { text: 'Strong No Hire', className: 'session-card__rec--red' },
};

function formatDate(iso: string) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function computeDuration(s: SessionListItem): string {
  if (s.started_at && s.ended_at) {
    const mins = Math.round(
      (new Date(s.ended_at).getTime() - new Date(s.started_at).getTime()) / 60000,
    );
    return `${mins}min`;
  }
  return `${s.duration_minutes}min planned`;
}

export function SessionsPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchSessions = useCallback(async () => {
    try {
      setLoading(true);
      const result = await api.listSessions();
      setSessions(result.sessions);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load sessions');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const filtered = sessions.filter((s) => {
    if (filter !== 'all' && s.status !== filter) return false;
    if (search && !s.id.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const handleEndSession = async (sessionId: string) => {
    setActionLoading((prev) => ({ ...prev, [sessionId]: true }));
    try {
      const result = await api.endSession(sessionId);
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId
            ? {
                ...s,
                status: 'ended',
                report_id: result.report_id ?? null,
                report_status: result.report_id ? 'pending' : null,
              }
            : s,
        ),
      );
      // Poll for report completion if we got a report_id
      if (result.report_id) {
        pollReportStatus(sessionId, result.report_id);
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to end session');
    } finally {
      setActionLoading((prev) => ({ ...prev, [sessionId]: false }));
    }
  };

  const handleGenerateReport = async (sessionId: string) => {
    setActionLoading((prev) => ({ ...prev, [sessionId]: true }));
    try {
      const result = await api.generateReport(sessionId);
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId
            ? { ...s, report_id: result.report_id, report_status: 'pending' }
            : s,
        ),
      );
      pollReportStatus(sessionId, result.report_id);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to generate report');
    } finally {
      setActionLoading((prev) => ({ ...prev, [sessionId]: false }));
    }
  };

  const handleDelete = async (sessionId: string) => {
    if (!confirm('Delete this session?')) return;
    setActionLoading((prev) => ({ ...prev, [sessionId]: true }));
    try {
      await api.deleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to delete session');
    } finally {
      setActionLoading((prev) => ({ ...prev, [sessionId]: false }));
    }
  };

  const pollReportStatus = (sessionId: string, reportId: string) => {
    const interval = setInterval(async () => {
      try {
        const status = await api.getReportStatus(reportId);
        if (status.status === 'completed' || status.status === 'failed') {
          clearInterval(interval);
          setSessions((prev) =>
            prev.map((s) =>
              s.id === sessionId
                ? { ...s, report_status: status.status }
                : s,
            ),
          );
          // Refresh to get recommendation etc.
          if (status.status === 'completed') {
            fetchSessions();
          }
        }
      } catch {
        clearInterval(interval);
      }
    }, 2000);
  };

  return (
    <div className="sessions-page">
      <header className="sessions-page__header">
        <div className="sessions-page__header-row">
          <div>
            <h1>Interview Sessions</h1>
            <p className="sessions-page__subtitle">
              {sessions.length} session{sessions.length !== 1 ? 's' : ''}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <button className="btn btn--ghost" onClick={() => navigate('/reports')}>
              Reports
            </button>
            <button className="btn btn--primary" onClick={() => navigate('/new')}>
              New Interview
            </button>
          </div>
        </div>
      </header>

      {/* Search + filter bar */}
      <div className="sessions-page__toolbar">
        <input
          className="sessions-page__search"
          type="text"
          placeholder="Enter Session ID"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="sessions-page__filters">
          {STATUS_FILTERS.map((f) => {
            const count =
              f === 'all' ? sessions.length : sessions.filter((s) => s.status === f).length;
            return (
              <button
                key={f}
                className={`chip ${filter === f ? 'chip--active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {STATUS_LABELS[f]} {count > 0 && <span className="chip__count">({count})</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <div className="sessions-page__loading">Loading sessions...</div>
      ) : error ? (
        <div className="sessions-page__error">{error}</div>
      ) : filtered.length === 0 ? (
        <div className="sessions-page__empty">
          {filter === 'all' ? 'No sessions yet. Start a new interview!' : `No ${filter} sessions.`}
        </div>
      ) : (
        <div className="sessions-page__list">
          {filtered.map((s) => {
            const isExpanded = expandedId === s.id;
            const isActionLoading = actionLoading[s.id] ?? false;
            const rec = s.recommendation ? RECOMMENDATION_LABELS[s.recommendation] : null;

            return (
              <div
                key={s.id}
                className={`session-card ${isExpanded ? 'session-card--expanded' : ''}`}
              >
                {/* Clickable header */}
                <div
                  className="session-card__header"
                  onClick={() => toggleExpand(s.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && toggleExpand(s.id)}
                >
                  <div className="session-card__top-row">
                    <span className={`session-card__status session-card__status--${s.status}`}>
                      {s.status}
                    </span>
                    <span
                      className="session-card__id"
                      title="Click to copy full ID"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(s.id);
                        setCopiedId(s.id);
                        setTimeout(() => setCopiedId((prev) => prev === s.id ? null : prev), 1500);
                      }}
                    >
                      {copiedId === s.id ? 'Copied!' : s.id.slice(0, 8)}
                    </span>
                    <span className="session-card__level">
                      {LEVEL_LABELS[s.level] ?? s.level}
                    </span>
                    <span className="session-card__duration">{computeDuration(s)}</span>
                    <span className="session-card__date">{formatDate(s.created_at)}</span>
                    <span className={`session-card__chevron ${isExpanded ? 'session-card__chevron--open' : ''}`}>
                      &#9662;
                    </span>
                  </div>

                  {/* Focus areas */}
                  {s.focus_areas.length > 0 && (
                    <div className="session-card__focus-areas">
                      {s.focus_areas.map((area) => (
                        <span key={area} className="session-card__focus-chip">
                          {area.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Report summary (ended sessions) */}
                  {s.status === 'ended' && s.report_status === 'completed' && rec && (
                    <div className="session-card__report-summary">
                      <span className={`session-card__rec ${rec.className}`}>{rec.text}</span>
                      {s.confidence_level != null && (
                        <span className="session-card__confidence">
                          {Math.round(s.confidence_level * 100)}% confidence
                        </span>
                      )}
                    </div>
                  )}

                  {s.status === 'ended' && s.report_status === 'pending' && (
                    <div className="session-card__report-summary">
                      <span className="session-card__rec session-card__rec--pending">
                        Report generating...
                      </span>
                    </div>
                  )}

                  {s.status === 'ended' && s.report_status === 'failed' && (
                    <div className="session-card__report-summary">
                      <span className="session-card__rec session-card__rec--red">
                        Report failed
                      </span>
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="session-card__actions">
                  {(s.status === 'planning' || s.status === 'ready') && (
                    <>
                      <button
                        className="btn btn--sm btn--primary"
                        onClick={() => navigate(`/session/${s.id}/plan`)}
                      >
                        Resume Planning
                      </button>
                      <button
                        className="btn btn--sm btn--ghost btn--danger-text"
                        onClick={() => handleDelete(s.id)}
                        disabled={isActionLoading}
                      >
                        Delete
                      </button>
                    </>
                  )}

                  {(s.status === 'live' || s.status === 'paused') && (
                    <>
                      <button
                        className="btn btn--sm btn--primary"
                        onClick={() => navigate(`/session/${s.id}`)}
                      >
                        Resume Interview
                      </button>
                      <button
                        className="btn btn--sm btn--ghost"
                        onClick={() => handleEndSession(s.id)}
                        disabled={isActionLoading}
                      >
                        {isActionLoading ? 'Ending...' : 'End Session'}
                      </button>
                    </>
                  )}

                  {s.status === 'ended' && (
                    <>
                      {s.report_id && s.report_status === 'completed' && (
                        <button
                          className="btn btn--sm btn--primary"
                          onClick={() => navigate(`/reports/${s.report_id}`)}
                        >
                          View Report
                        </button>
                      )}
                      {(!s.report_id || s.report_status === 'failed') && (
                        <button
                          className="btn btn--sm btn--primary"
                          onClick={() => handleGenerateReport(s.id)}
                          disabled={isActionLoading}
                        >
                          {isActionLoading ? 'Generating...' : 'Generate Report'}
                        </button>
                      )}
                      {s.report_id && s.report_status === 'completed' && (
                        <button
                          className="btn btn--sm btn--ghost"
                          onClick={() => handleGenerateReport(s.id)}
                          disabled={isActionLoading}
                        >
                          Regenerate Report
                        </button>
                      )}
                    </>
                  )}
                </div>

                {/* Expandable detail */}
                {isExpanded && (
                  <div className="session-card__detail">
                    <SessionDetail sessionId={s.id} />

                    {/* Links */}
                    <div className="session-card__links">
                      {s.report_id && s.report_status === 'completed' && (
                        <button
                          className="btn btn--sm btn--ghost"
                          onClick={() => navigate(`/reports/${s.report_id}`)}
                        >
                          Full Report &rarr;
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
