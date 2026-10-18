/**
 * Dashboard page — landing page with resume upload and plan generation.
 */
import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '@/stores/session-store';
import { usePlanStore, type InterviewPlan } from '@/stores/plan-store';
import { ResumeUploader } from '@/components/planning/ResumeUploader';
import * as api from '@/api/client';
import { MEETING_URL_HINT, normalizeMeetingUrl } from '@/lib/meeting';

const LEVELS = [
  { value: 'intern', label: 'Intern' },
  { value: 'sde1', label: 'SDE-1 (Junior)' },
  { value: 'sde2', label: 'SDE-2 (Mid)' },
  { value: 'senior', label: 'Senior' },
  { value: 'staff', label: 'Staff Engineer' },
  { value: 'principal', label: 'Principal Engineer' },
];

const FOCUS_OPTIONS = ['coding', 'system_design', 'behavioral', 'debugging', 'api_design', 'dsa', 'aptitude'];

export function DashboardPage() {
  const navigate = useNavigate();
  const setSession = useSessionStore((s) => s.setSession);
  const setPlan = usePlanStore((s) => s.setPlan);
  const setPlanStatus = usePlanStore((s) => s.setPlanStatus);
  const setPlanError = usePlanStore((s) => s.setPlanError);
  const resumeStatus = usePlanStore((s) => s.resume.status);

  const [candidateName, setCandidateName] = useState('');
  const [candidateEmail, setCandidateEmail] = useState('');
  const [meetingUrl, setMeetingUrl] = useState('');
  const [level, setLevel] = useState('sde2');
  const [duration, setDuration] = useState(60);
  const [focusAreas, setFocusAreas] = useState<string[]>(['coding']);
  const [resumeData, setResumeData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleFocus = useCallback((area: string) => {
    setFocusAreas((prev) =>
      prev.includes(area) ? prev.filter((a) => a !== area) : [...prev, area],
    );
  }, []);

  const handleResumeComplete = useCallback(
    (_referenceId: string, data: Record<string, unknown>) => {
      setResumeData(data);
    },
    [],
  );

  /** Wait for an in-progress resume upload/extraction to settle. */
  const waitForResume = useCallback((): Promise<Record<string, unknown> | null> => {
    const snap = usePlanStore.getState().resume;
    if (snap.status === 'idle' || snap.status === 'failed') return Promise.resolve(null);
    if (snap.status === 'completed') return Promise.resolve(snap.structuredData);

    // uploading or processing — wait for it to finish
    return new Promise((resolve) => {
      const unsub = usePlanStore.subscribe((state) => {
        const s = state.resume.status;
        if (s === 'completed') {
          unsub();
          resolve(state.resume.structuredData);
        } else if (s === 'failed' || s === 'idle') {
          unsub();
          resolve(null);
        }
      });
    });
  }, []);

  const handleGeneratePlan = useCallback(async () => {
    if (focusAreas.length === 0) {
      setError('Select at least one focus area');
      return;
    }
    const normalizedMeetingUrl = normalizeMeetingUrl(meetingUrl);
    if (normalizedMeetingUrl === undefined) {
      setError(MEETING_URL_HINT);
      return;
    }
    setLoading(true);
    setError(null);
    setPlanStatus('generating');

    try {
      // Wait for any in-progress resume upload/extraction before proceeding
      const resolvedResumeData = await waitForResume();

      // 1. Create session (starts in PLANNING status)
      const sessionResult = await api.startSession({
        level,
        duration_minutes: duration,
        focus_areas: focusAreas,
        candidate_name: candidateName || undefined,
        candidate_email: candidateEmail || undefined,
        resume_data: resolvedResumeData,
        meeting_url: normalizedMeetingUrl,
      });
      const sessionId = sessionResult.session_id;
      const label = LEVELS.find((l) => l.value === level)?.label ?? level;
      setSession(sessionId, label, duration);

      // 2. Navigate to plan page immediately (shows loading spinner)
      navigate(`/session/${sessionId}/plan`);

      // 3. Generate plan (async — PlanReviewPage shows spinner)
      const planResult = await api.createPlan({
        session_id: sessionId,
        level,
        duration_minutes: duration,
        focus_areas: focusAreas,
        resume_data: resolvedResumeData,
      });
      setPlan(planResult as unknown as InterviewPlan);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate plan';
      setPlanError(msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [level, duration, focusAreas, candidateName, candidateEmail, meetingUrl, navigate, setSession, setPlan, setPlanStatus, setPlanError, waitForResume]);

  return (
    <div className="dashboard-page">
      <header className="dashboard-page__header">
        <h1>InterviewPilot</h1>
        <p>AI-Powered Real-Time Interview Assistant</p>
        <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
          <button
            className="btn btn--ghost"
            onClick={() => navigate('/')}
          >
            Interview Sessions
          </button>
          <button
            className="btn btn--ghost"
            onClick={() => navigate('/reports')}
          >
            Past Reports
          </button>
        </div>
      </header>

      <section className="dashboard-page__start" aria-label="Start Interview">
        <h2>New Interview</h2>

        <div className="form-group">
          <label htmlFor="candidate-name">Candidate Name</label>
          <input
            id="candidate-name"
            type="text"
            placeholder="e.g. Jane Doe"
            value={candidateName}
            onChange={(e) => setCandidateName(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label htmlFor="candidate-email">Candidate Email</label>
          <input
            id="candidate-email"
            type="email"
            placeholder="e.g. jane@example.com"
            value={candidateEmail}
            onChange={(e) => setCandidateEmail(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label htmlFor="meeting-url">Google Meet Link (Optional)</label>
          <input
            id="meeting-url"
            type="url"
            placeholder="https://meet.google.com/abc-defg-hij"
            value={meetingUrl}
            onChange={(e) => setMeetingUrl(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Resume (Optional)</label>
          <ResumeUploader onUploadComplete={handleResumeComplete} />
        </div>

        <div className="form-group">
          <label htmlFor="level">Candidate Level</label>
          <select
            id="level"
            value={level}
            onChange={(e) => setLevel(e.target.value)}
          >
            {LEVELS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
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

        <div className="form-group">
          <label>Focus Areas</label>
          <div className="focus-chips">
            {FOCUS_OPTIONS.map((area) => (
              <button
                key={area}
                type="button"
                className={`chip ${focusAreas.includes(area) ? 'chip--active' : ''}`}
                onClick={() => toggleFocus(area)}
              >
                {area.replace('_', ' ')}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}

        <button
          className="btn btn--primary btn--lg"
          onClick={handleGeneratePlan}
          disabled={loading}
        >
          {loading
            ? (resumeStatus === 'uploading' || resumeStatus === 'processing')
              ? 'Waiting for resume...'
              : 'Generating Plan...'
            : 'Generate Interview Plan'}
        </button>
      </section>
    </div>
  );
}
