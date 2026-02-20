/**
 * Plan review page — shows generated interview plan with editing controls.
 * Route: /session/:sessionId/plan
 */
import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { usePlanStore, type InterviewPlan } from '@/stores/plan-store';
import { useSessionStore } from '@/stores/session-store';
import { PlanTimeline } from '@/components/planning/PlanTimeline';
import { PhaseSection } from '@/components/planning/PhaseSection';
import * as api from '@/api/client';

export function PlanReviewPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [starting, setStarting] = useState(false);

  const plan = usePlanStore((s) => s.plan);
  const planStatus = usePlanStore((s) => s.planStatus);
  const planError = usePlanStore((s) => s.planError);
  const setPlan = usePlanStore((s) => s.setPlan);

  const setStatus = useSessionStore((s) => s.setStatus);
  const setCurrentQuestion = useSessionStore((s) => s.setCurrentQuestion);

  // If navigated directly with no plan in store, try to fetch
  useEffect(() => {
    if (!plan && planStatus === 'idle' && sessionId) {
      api.getSession(sessionId).then((session) => {
        const planId = session.plan_id as string | undefined;
        if (planId) {
          api.getPlan(planId).then((p) => setPlan(p as unknown as InterviewPlan));
        }
      }).catch(() => {});
    }
  }, [sessionId, plan, planStatus, setPlan]);

  const handleStartInterview = useCallback(async () => {
    if (!sessionId || !plan) return;
    setStarting(true);
    try {
      // 1. Register plan hints with the session's HintService
      await api.registerPlanHints(sessionId, { phases: plan.phases });

      // 2. Transition session to LIVE
      await api.goLive(sessionId);
      setStatus('live');

      // 3. Set initial question from plan
      const totalQ = plan.phases.reduce((sum, p) => sum + p.questions.length, 0);
      if (plan.phases.length > 0 && plan.phases[0].questions.length > 0) {
        setCurrentQuestion(0, totalQ, plan.phases[0].questions[0].text);
      }

      // 4. Navigate to live session
      navigate(`/session/${sessionId}`);
    } catch (err) {
      console.error('Failed to start interview:', err);
      setStarting(false);
    }
  }, [sessionId, plan, navigate, setStatus, setCurrentQuestion]);

  const handlePlanUpdated = useCallback(
    (updatedPlan: InterviewPlan) => {
      setPlan(updatedPlan);
    },
    [setPlan],
  );

  // Loading state
  if (planStatus === 'generating') {
    return (
      <div className="plan-review plan-review--loading">
        <div className="plan-review__spinner" />
        <p>Generating your interview plan...</p>
        <p className="plan-review__subtitle">
          This may take 10-15 seconds while we analyze the resume and select questions
        </p>
      </div>
    );
  }

  // Error state
  if (planStatus === 'error') {
    return (
      <div className="plan-review plan-review--error">
        <p>Failed to generate plan</p>
        {planError && <p className="plan-review__subtitle">{planError}</p>}
        <button className="btn btn--primary" onClick={() => navigate('/')}>
          Go Back
        </button>
      </div>
    );
  }

  // Waiting for plan to load
  if (!plan) {
    return (
      <div className="plan-review plan-review--loading">
        <div className="plan-review__spinner" />
        <p>Loading plan...</p>
      </div>
    );
  }

  const totalQuestions = plan.phases.reduce((sum, p) => sum + p.questions.length, 0);

  return (
    <div className="plan-review">
      <header className="plan-review__header">
        <button className="btn btn--ghost" onClick={() => navigate('/')}>
          &larr; Back
        </button>
        <h1>Interview Plan</h1>
        <span className="plan-review__meta">
          {plan.total_minutes} min &middot; {totalQuestions} questions &middot; v{plan.version}
        </span>
      </header>

      <PlanTimeline phases={plan.phases} totalMinutes={plan.total_minutes} />

      <main className="plan-review__body">
        {plan.phases.map((phase, i) => (
          <PhaseSection
            key={`${phase.phase}-${i}`}
            phase={phase}
            phaseIndex={i}
            planId={plan.id}
            onPlanUpdated={handlePlanUpdated}
          />
        ))}
      </main>

      <footer className="plan-review__footer">
        <button
          className="btn btn--primary btn--lg"
          onClick={handleStartInterview}
          disabled={starting}
        >
          {starting ? 'Starting...' : 'Start Interview'}
        </button>
      </footer>
    </div>
  );
}
