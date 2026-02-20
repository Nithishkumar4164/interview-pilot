/**
 * Renders one interview phase with its questions.
 */
import type { PlanPhase, InterviewPlan } from '@/stores/plan-store';
import { QuestionCard } from './QuestionCard';

interface PhaseSectionProps {
  phase: PlanPhase;
  phaseIndex: number;
  planId: string;
  onPlanUpdated: (plan: InterviewPlan) => void;
}

export function PhaseSection({ phase, phaseIndex, planId, onPlanUpdated }: PhaseSectionProps) {
  return (
    <section className="phase-section" aria-label={`${phase.phase} phase`}>
      <div className="phase-section__header">
        <span className={`phase-section__badge phase-section__badge--${phase.phase}`}>
          {phase.phase.replace('_', ' ')}
        </span>
        <span className="phase-section__meta">
          {phase.duration_minutes} min &middot; {phase.questions.length} question
          {phase.questions.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="phase-section__questions">
        {phase.questions.map((question, qIndex) => (
          <QuestionCard
            key={question.id}
            question={question}
            phaseIndex={phaseIndex}
            questionIndex={qIndex}
            planId={planId}
            onPlanUpdated={onPlanUpdated}
          />
        ))}
      </div>
    </section>
  );
}
