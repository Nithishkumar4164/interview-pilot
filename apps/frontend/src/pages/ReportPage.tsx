/**
 * Report page — displays the post-interview report with radar chart,
 * hint summary, annotated transcript, and PDF export.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import * as api from '@/api/client';
import { RadarChart } from '@/components/report/RadarChart';
import { HintSummaryPanel } from '@/components/report/HintSummaryPanel';
import { AnnotatedTranscript } from '@/components/report/AnnotatedTranscript';

interface ScoreEntry {
  criterion: string;
  score: number;
  evidence: string[];
  trend: string;
}

interface KeyMoment {
  timestamp: string;
  title: string;
  description: string;
  sentiment: string;
  impact_on_assessment: string;
  type: string | null;
}

interface HintSummary {
  total_hints_given: number;
  incorporation_rate: number;
  average_response_time_ms: number | null;
  independence_trajectory: string;
  level_distribution: Record<string, number>;
  details: Array<{
    question_id: string;
    level: number;
    interviewer_used: boolean;
    response_quality: string;
    time_to_respond_ms: number | null;
  }>;
}

interface TranscriptEntry {
  timestamp: string;
  speaker: string;
  text: string;
  annotations: Array<{ type: string; text: string }>;
}

interface ReportData {
  id: string;
  session_id: string;
  meeting_url?: string | null;
  status: string;
  assessment: {
    recommendation: string;
    confidence_level: number;
    scores: ScoreEntry[];
    key_moments: KeyMoment[];
    strengths: string[];
    areas_for_growth: string[];
    level_comparison: string;
  };
  hint_summary: HintSummary;
  annotated_transcript: TranscriptEntry[];
  generated_at: string;
  is_final: boolean;
  notes: Array<{ id: string; note_text: string; created_at: string }>;
}

export function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noteText, setNoteText] = useState('');
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch report or start polling
  useEffect(() => {
    if (!reportId) return;

    const fetchReport = async () => {
      try {
        setLoading(true);
        const data = (await api.getReport(reportId)) as ReportData;

        if (data.status === 'completed' || data.status === 'failed') {
          setReport(data);
          setPolling(false);
          setLoading(false);
        } else {
          // Report is still generating — start polling
          setPolling(true);
          setLoading(false);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load report');
        setLoading(false);
      }
    };

    fetchReport();
  }, [reportId]);

  // Polling for pending reports
  useEffect(() => {
    if (!polling || !reportId) return;

    pollRef.current = setInterval(async () => {
      try {
        const status = await api.getReportStatus(reportId);
        if (status.status === 'completed') {
          setPolling(false);
          const data = (await api.getReport(reportId)) as ReportData;
          setReport(data);
        } else if (status.status === 'failed') {
          setPolling(false);
          setError('Report generation failed. Please try ending the session again.');
        }
      } catch {
        // Ignore polling errors
      }
    }, 2000);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [polling, reportId]);

  const handleAddNote = useCallback(async () => {
    if (!reportId || !noteText.trim()) return;
    try {
      await api.addReportNote(reportId, noteText.trim());
      setNoteText('');
      const data = (await api.getReport(reportId)) as ReportData;
      setReport(data);
    } catch (err) {
      console.error('Failed to add note:', err);
    }
  }, [reportId, noteText]);

  const handleExportPdf = useCallback(() => {
    window.print();
  }, []);

  if (loading) {
    return (
      <div className="report-page report-page--loading">
        <p>Loading report...</p>
      </div>
    );
  }

  if (polling) {
    return (
      <div className="report-page report-page--loading">
        <div className="report-page__generating">
          <div className="report-page__spinner" />
          <h2>Generating Report...</h2>
          <p>Your interview report is being generated. This may take up to a minute.</p>
        </div>
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

  const { assessment } = report;
  const recColor = getRecommendationColor(assessment.recommendation);
  const radarScores = assessment.scores.map((s) => ({
    criterion: s.criterion,
    score: s.score,
  }));

  return (
    <div className="report-page">
      <header className="report-page__header">
        <div className="report-page__nav">
          <button className="btn btn--ghost" onClick={() => navigate('/')}>
            &larr; Back
          </button>
          <button className="btn btn--ghost" onClick={() => navigate('/reports')}>
            Report History
          </button>
        </div>
        <h1>Interview Report</h1>
        {report.meeting_url && (
          <a className="report-page__meeting" href={report.meeting_url} target="_blank" rel="noreferrer">
            {report.meeting_url.replace('https://', '')}
          </a>
        )}
        <button className="btn btn--secondary report-page__export" onClick={handleExportPdf}>
          Export PDF
        </button>
      </header>

      {/* Executive Summary */}
      <section className="report-section" aria-label="Summary">
        <div className={`recommendation recommendation--${recColor}`}>
          <span className="recommendation__label">Recommendation</span>
          <span className="recommendation__value">
            {formatRecommendation(assessment.recommendation)}
          </span>
          <span className="recommendation__confidence">
            Confidence: {Math.round(assessment.confidence_level * 100)}%
          </span>
        </div>
        {assessment.level_comparison && (
          <p className="report-section__level-comparison">{assessment.level_comparison}</p>
        )}
      </section>

      {/* Radar Chart + Scores */}
      <section className="report-section" aria-label="Scores">
        <h2>Assessment Scores</h2>
        <div className="report-section__scores-layout">
          <RadarChart scores={radarScores} size={320} />
          <div className="score-details">
            {assessment.scores.map((s) => (
              <div key={s.criterion} className="score-details__row">
                <div className="score-details__header">
                  <span className="score-details__label">{formatCriterion(s.criterion)}</span>
                  <span className="score-details__value">{s.score}/5</span>
                  <span className={`score-details__trend score-details__trend--${s.trend}`}>
                    {s.trend === 'improving' ? '\u2191' : s.trend === 'declining' ? '\u2193' : '\u2192'}
                  </span>
                </div>
                <div className="score-matrix__bar">
                  <div
                    className="score-matrix__fill"
                    style={{ width: `${(s.score / 5) * 100}%` }}
                  />
                </div>
                {s.evidence.length > 0 && (
                  <ul className="score-details__evidence">
                    {s.evidence.slice(0, 3).map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Key Moments */}
      {assessment.key_moments?.length > 0 && (
        <section className="report-section" aria-label="Key Moments">
          <h2>Key Moments</h2>
          <ul className="key-moments">
            {assessment.key_moments.map((moment, i) => (
              <li key={i} className={`key-moment key-moment--${moment.type || moment.sentiment}`}>
                <div className="key-moment__header">
                  <span className="key-moment__type">{moment.type || 'neutral'}</span>
                  <time className="key-moment__time">{moment.timestamp}</time>
                </div>
                <strong className="key-moment__title">{moment.title}</strong>
                <p className="key-moment__text">{moment.description}</p>
                {moment.impact_on_assessment && (
                  <p className="key-moment__impact">{moment.impact_on_assessment}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Hint Summary */}
      <section className="report-section" aria-label="Hints">
        <HintSummaryPanel summary={report.hint_summary} />
      </section>

      {/* Strengths & Growth Areas */}
      <section className="report-section" aria-label="Assessment">
        <div className="assessment-grid">
          <div className="assessment-col">
            <h3>Strengths</h3>
            <ul>
              {assessment.strengths?.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </div>
          <div className="assessment-col">
            <h3>Growth Areas</h3>
            <ul>
              {assessment.areas_for_growth?.map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* Annotated Transcript */}
      {report.annotated_transcript?.length > 0 && (
        <section className="report-section" aria-label="Transcript">
          <h2>Interview Transcript</h2>
          <AnnotatedTranscript entries={report.annotated_transcript} />
        </section>
      )}

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

function formatRecommendation(rec: string): string {
  return rec.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
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
