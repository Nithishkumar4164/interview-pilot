/**
 * Report page — displays the post-interview report.
 */
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import * as api from '@/api/client';

interface ReportData {
  id: string;
  session_id: string;
  recommendation: string;
  confidence_level: number;
  scores: Record<string, number>;
  key_moments: Array<{ text: string; type: string; timestamp: string }>;
  strengths: string[];
  growth_areas: string[];
  annotated_transcript: Array<{ text: string; speaker: string; annotation?: string }>;
  notes: Array<{ id: string; note_text: string; created_at: string }>;
}

export function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noteText, setNoteText] = useState('');

  useEffect(() => {
    if (!reportId) return;
    setLoading(true);
    api.getReport(reportId)
      .then((data) => setReport(data as ReportData))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load report'))
      .finally(() => setLoading(false));
  }, [reportId]);

  const handleAddNote = useCallback(async () => {
    if (!reportId || !noteText.trim()) return;
    try {
      await api.addReportNote(reportId, noteText.trim());
      setNoteText('');
      // Refresh report to get updated notes
      const data = await api.getReport(reportId);
      setReport(data as ReportData);
    } catch (err) {
      console.error('Failed to add note:', err);
    }
  }, [reportId, noteText]);

  if (loading) {
    return (
      <div className="report-page report-page--loading">
        <p>Loading report...</p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="report-page report-page--error">
        <p>{error || 'Report not found'}</p>
        <button className="btn btn--primary" onClick={() => navigate('/')}>
          Go Home
        </button>
      </div>
    );
  }

  const recColor = getRecommendationColor(report.recommendation);

  return (
    <div className="report-page">
      <header className="report-page__header">
        <button className="btn btn--ghost" onClick={() => navigate('/')}>
          &larr; Back
        </button>
        <h1>Interview Report</h1>
      </header>

      {/* Executive Summary */}
      <section className="report-section" aria-label="Summary">
        <div className={`recommendation recommendation--${recColor}`}>
          <span className="recommendation__label">Recommendation</span>
          <span className="recommendation__value">{report.recommendation}</span>
          <span className="recommendation__confidence">
            Confidence: {Math.round(report.confidence_level * 100)}%
          </span>
        </div>
      </section>

      {/* Scores */}
      <section className="report-section" aria-label="Scores">
        <h2>Scores</h2>
        <div className="score-matrix">
          {Object.entries(report.scores).map(([criterion, score]) => (
            <div key={criterion} className="score-matrix__row">
              <span className="score-matrix__label">{formatCriterion(criterion)}</span>
              <div className="score-matrix__bar">
                <div
                  className="score-matrix__fill"
                  style={{ width: `${(score / 5) * 100}%` }}
                />
              </div>
              <span className="score-matrix__value">{score.toFixed(1)}/5</span>
            </div>
          ))}
        </div>
      </section>

      {/* Key Moments */}
      {report.key_moments?.length > 0 && (
        <section className="report-section" aria-label="Key Moments">
          <h2>Key Moments</h2>
          <ul className="key-moments">
            {report.key_moments.map((moment, i) => (
              <li key={i} className={`key-moment key-moment--${moment.type}`}>
                <span className="key-moment__type">{moment.type}</span>
                <span className="key-moment__text">{moment.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Strengths & Growth Areas */}
      <section className="report-section" aria-label="Assessment">
        <div className="assessment-grid">
          <div className="assessment-col">
            <h3>Strengths</h3>
            <ul>
              {report.strengths?.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </div>
          <div className="assessment-col">
            <h3>Growth Areas</h3>
            <ul>
              {report.growth_areas?.map((g, i) => <li key={i}>{g}</li>)}
            </ul>
          </div>
        </div>
      </section>

      {/* Notes */}
      <section className="report-section" aria-label="Notes">
        <h2>Notes</h2>
        {report.notes?.map((note) => (
          <div key={note.id} className="report-note">
            <p>{note.note_text}</p>
            <time>{new Date(note.created_at).toLocaleString()}</time>
          </div>
        ))}
        <div className="report-note__form">
          <textarea
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            placeholder="Add a note..."
            rows={3}
          />
          <button
            className="btn btn--primary"
            onClick={handleAddNote}
            disabled={!noteText.trim()}
          >
            Add Note
          </button>
        </div>
      </section>
    </div>
  );
}

function formatCriterion(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function getRecommendationColor(rec: string): string {
  const lower = rec.toLowerCase();
  if (lower.includes('strong hire')) return 'green';
  if (lower.includes('hire')) return 'green';
  if (lower.includes('lean hire')) return 'amber';
  if (lower.includes('lean no')) return 'orange';
  if (lower.includes('no hire')) return 'red';
  return 'neutral';
}
