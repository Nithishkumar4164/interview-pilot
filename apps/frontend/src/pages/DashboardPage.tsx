/**
 * Dashboard page — start a new interview.
 *
 * Nothing is planned up front: the interviewer picks how the candidate is
 * identified (a parsed resume that prefills editable details, details typed by
 * hand, or a guest with none) and the session goes live straight away.
 * Questions are generated during the interview.
 */
import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '@/stores/session-store';
import { usePlanStore } from '@/stores/plan-store';
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

type CandidateMode = 'resume' | 'manual' | 'guest';

const CANDIDATE_MODES: { value: CandidateMode; label: string }[] = [
  { value: 'resume', label: 'From resume' },
  { value: 'manual', label: 'Enter manually' },
  { value: 'guest', label: 'Guest' },
];

interface CandidateDetails {
  name: string;
  email: string;
  phone: string;
  role: string;
}

const EMPTY_DETAILS: CandidateDetails = { name: '', email: '', phone: '', role: '' };

const DETAIL_FIELDS: { key: keyof CandidateDetails; label: string; type: string; placeholder: string }[] = [
  { key: 'name', label: 'Candidate Name', type: 'text', placeholder: 'e.g. Jane Doe' },
  { key: 'email', label: 'Candidate Email', type: 'email', placeholder: 'e.g. jane@example.com' },
  { key: 'phone', label: 'Phone', type: 'tel', placeholder: 'e.g. +1 555 0100' },
  { key: 'role', label: 'Current Role', type: 'text', placeholder: 'e.g. Backend Engineer' },
];

/** The candidate details a parsed resume provides. */
function detailsFromResume(data: Record<string, unknown> | null): CandidateDetails {
  const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '');
  return {
    name: text(data?.name),
    email: text(data?.email),
    phone: text(data?.phone),
    role: text(data?.current_role),
  };
}

/** Fill the fields the interviewer left empty; never overwrite what they typed. */
function fillEmpty(current: CandidateDetails, extracted: CandidateDetails): CandidateDetails {
  return {
    name: current.name || extracted.name,
    email: current.email || extracted.email,
    phone: current.phone || extracted.phone,
    role: current.role || extracted.role,
  };
}

export function DashboardPage() {
  const navigate = useNavigate();
  const setSession = useSessionStore((s) => s.setSession);
  const setStarted = useSessionStore((s) => s.setStarted);
  const resumeStatus = usePlanStore((s) => s.resume.status);
  const resumeData = usePlanStore((s) => s.resume.structuredData);

  const [mode, setMode] = useState<CandidateMode>('resume');
  const [details, setDetails] = useState<CandidateDetails>(EMPTY_DETAILS);
  const [meetingUrl, setMeetingUrl] = useState('');
  const [level, setLevel] = useState('sde2');
  const [duration, setDuration] = useState(60);
  const [focusAreas, setFocusAreas] = useState<string[]>(['coding']);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Prefill from the parsed resume as soon as extraction completes.
  useEffect(() => {
    if (resumeData) setDetails((d) => fillEmpty(d, detailsFromResume(resumeData)));
  }, [resumeData]);

  const setDetail = useCallback((key: keyof CandidateDetails, value: string) => {
    setDetails((d) => ({ ...d, [key]: value }));
  }, []);

  const toggleFocus = useCallback((area: string) => {
    setFocusAreas((prev) =>
      prev.includes(area) ? prev.filter((a) => a !== area) : [...prev, area],
    );
  }, []);

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

  const handleStart = useCallback(async () => {
    if (focusAreas.length === 0) {
      setError('Select at least one focus area');
      return;
    }
    const normalizedMeetingUrl = normalizeMeetingUrl(meetingUrl);
    if (normalizedMeetingUrl === undefined) {
      setError(MEETING_URL_HINT);
      return;
    }
    if (mode === 'resume' && usePlanStore.getState().resume.status === 'idle') {
      setError('Upload a resume, or choose Enter manually or Guest');
      return;
    }
    setLoading(true);
    setError(null);

    try {
      let candidate: CandidateDetails = EMPTY_DETAILS;
      let resolvedResumeData: Record<string, unknown> | null = null;
      if (mode === 'resume') {
        // Clicking Start mid-extraction waits, then uses what the resume provides
        resolvedResumeData = await waitForResume();
        if (!resolvedResumeData) {
          setError('The resume could not be read. Try again, or choose Enter manually or Guest');
          return;
        }
        candidate = fillEmpty(details, detailsFromResume(resolvedResumeData));
      } else if (mode === 'manual') {
        candidate = details;
      }

      const { session_id: sessionId } = await api.startSession({
        level,
        duration_minutes: duration,
        focus_areas: focusAreas,
        candidate_name: candidate.name || undefined,
        candidate_email: candidate.email || undefined,
        candidate_phone: candidate.phone || undefined,
        candidate_role: candidate.role || undefined,
        resume_data: resolvedResumeData,
        meeting_url: normalizedMeetingUrl,
      });
      const live = await api.goLive(sessionId);

      // The new session has no plan; don't carry the last one (or its resume) into it.
      usePlanStore.getState().reset();
      const label = LEVELS.find((l) => l.value === level)?.label ?? level;
      setSession(sessionId, label, duration);
      setStarted(live.started_at);
      navigate(`/session/${sessionId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start the interview');
    } finally {
      setLoading(false);
    }
  }, [mode, details, level, duration, focusAreas, meetingUrl, navigate, setSession, setStarted, waitForResume]);

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
          <label id="candidate-mode-label">Candidate</label>
          <div className="segmented" role="radiogroup" aria-labelledby="candidate-mode-label">
            {CANDIDATE_MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={mode === m.value}
                className={`segmented__option ${mode === m.value ? 'segmented__option--active' : ''}`}
                onClick={() => setMode(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
          {mode === 'guest' && (
            <p className="form-hint">No candidate details are saved for a guest interview.</p>
          )}
        </div>

        {mode === 'resume' && (
          <div className="form-group">
            <label>Resume</label>
            <ResumeUploader />
          </div>
        )}

        {(mode === 'manual' || (mode === 'resume' && resumeStatus === 'completed')) && (
          <div className="candidate-details">
            {mode === 'resume' && (
              <p className="form-hint">Filled from the resume. Edit anything it got wrong.</p>
            )}
            {DETAIL_FIELDS.map((f) => (
              <div className="form-group" key={f.key}>
                <label htmlFor={`candidate-${f.key}`}>{f.label}</label>
                <input
                  id={`candidate-${f.key}`}
                  type={f.type}
                  placeholder={f.placeholder}
                  value={details[f.key]}
                  onChange={(e) => setDetail(f.key, e.target.value)}
                />
              </div>
            ))}
          </div>
        )}

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
          onClick={handleStart}
          disabled={loading}
        >
          {loading
            ? mode === 'resume' && (resumeStatus === 'uploading' || resumeStatus === 'processing')
              ? 'Waiting for resume...'
              : 'Starting...'
            : 'Start Interview'}
        </button>
      </section>
    </div>
  );
}
