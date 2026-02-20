/**
 * Dashboard page — landing page with resume upload and plan generation.
 */
import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '@/stores/session-store';
import { usePlanStore, type InterviewPlan } from '@/stores/plan-store';
import { ResumeUploader } from '@/components/planning/ResumeUploader';
import * as api from '@/api/client';

const LEVELS = [
  { value: 'intern', label: 'Intern' },
  { value: 'sde1', label: 'SDE-1 (Junior)' },
  { value: 'sde2', label: 'SDE-2 (Mid)' },
  { value: 'senior', label: 'Senior' },
  { value: 'staff', label: 'Staff Engineer' },
  { value: 'principal', label: 'Principal Engineer' },
];

const FOCUS_OPTIONS = ['coding', 'system_design', 'behavioral', 'debugging', 'api_design'];

export function DashboardPage() {
  const navigate = useNavigate();
  const setSession = useSessionStore((s) => s.setSession);
  const setPlan = usePlanStore((s) => s.setPlan);
  const setPlanStatus = usePlanStore((s) => s.setPlanStatus);
  const setPlanError = usePlanStore((s) => s.setPlanError);

  const [level, setLevel] = useState('sde2');
  const [duration, setDuration] = useState(45);
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

  const handleGeneratePlan = useCallback(async () => {
    if (focusAreas.length === 0) {
      setError('Select at least one focus area');
      return;
    }
    setLoading(true);
    setError(null);
    setPlanStatus('generating');

    try {
      // 1. Create session (starts in PLANNING status)
      const sessionResult = await api.startSession({
        level,
        duration_minutes: duration,
        focus_areas: focusAreas,
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
        resume_data: resumeData,
      });
      setPlan(planResult as unknown as InterviewPlan);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate plan';
      setPlanError(msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [level, duration, focusAreas, resumeData, navigate, setSession, setPlan, setPlanStatus, setPlanError]);

  return (
    <div className="dashboard-page">
      <header className="dashboard-page__header">
        <h1>InterviewPilot</h1>
        <p>AI-Powered Real-Time Interview Assistant</p>
      </header>

      <section className="dashboard-page__start" aria-label="Start Interview">
        <h2>New Interview</h2>

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
          {loading ? 'Generating Plan...' : 'Generate Interview Plan'}
        </button>
      </section>
    </div>
  );
}
