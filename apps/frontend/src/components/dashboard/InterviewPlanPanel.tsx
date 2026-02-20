/**
 * Interview Plan Panel — surfaces the generated plan during the live interview.
 *
 * Shows: phase progress bar, question navigator, current question with
 * approaches/pitfalls, and prev/next navigation controls.
 */
import { useState, useMemo, useEffect, useRef } from 'react';
import { usePlanStore } from '@/stores/plan-store';
import { useSessionStore, getAllPlanQuestions } from '@/stores/session-store';
import type { ApproachPattern } from '@/stores/plan-store';
import { QuestionTimer } from './QuestionTimer';

interface InterviewPlanPanelProps {
  onAdvanceQuestion: (delta: number) => void;
  onGoToQuestion: (index: number) => void;
}

export function InterviewPlanPanel({
  onAdvanceQuestion,
  onGoToQuestion,
}: InterviewPlanPanelProps) {
  const plan = usePlanStore((s) => s.plan);
  const currentQuestionIndex = useSessionStore((s) => s.currentQuestionIndex);

  const [showNavigator, setShowNavigator] = useState(false);
  const [showBruteForce, setShowBruteForce] = useState(false);
  const [showOptimal, setShowOptimal] = useState(false);
  const [showPitfalls, setShowPitfalls] = useState(false);

  // Reset expandable sections when question changes
  const prevQuestionIndex = useRef(currentQuestionIndex);
  useEffect(() => {
    if (prevQuestionIndex.current !== currentQuestionIndex) {
      setShowBruteForce(false);
      setShowOptimal(false);
      setShowPitfalls(false);
      prevQuestionIndex.current = currentQuestionIndex;
    }
  }, [currentQuestionIndex]);

  const allQuestions = useMemo(() => getAllPlanQuestions(), [plan]);
  const currentQuestion = allQuestions[currentQuestionIndex] ?? null;

  // Compute first question index for each phase (for phase clicking)
  const phaseStartIndices = useMemo(() => {
    if (!plan) return [];
    const indices: number[] = [];
    let offset = 0;
    for (const phase of plan.phases) {
      indices.push(offset);
      offset += phase.questions.length;
    }
    return indices;
  }, [plan]);

  if (!plan || allQuestions.length === 0) {
    return (
      <div className="interview-plan-panel interview-plan-panel--empty">
        <p className="interview-plan-panel__placeholder">No interview plan loaded.</p>
      </div>
    );
  }

  return (
    <section className="interview-plan-panel" aria-label="Interview Plan">
      {/* Phase Progress Bar — click a phase to jump to its first question */}
      <PhaseProgressBar
        phases={plan.phases}
        currentPhaseIndex={currentQuestion?.phaseIndex ?? 0}
        onPhaseClick={(phaseIndex) => onGoToQuestion(phaseStartIndices[phaseIndex] ?? 0)}
      />

      {/* Question Navigator Toggle */}
      <div className="plan-panel__nav-header">
        <button
          className="plan-panel__nav-toggle"
          onClick={() => setShowNavigator(!showNavigator)}
        >
          {showNavigator ? 'Hide' : 'Show'} Questions ({allQuestions.length})
        </button>
        <span className="plan-panel__position">
          Q{currentQuestionIndex + 1} of {allQuestions.length}
        </span>
      </div>

      {/* Question Navigator List */}
      {showNavigator && (
        <div className="question-navigator">
          {plan.phases.map((phase, pi) => {
            let offset = 0;
            for (let i = 0; i < pi; i++) offset += plan.phases[i].questions.length;
            return (
              <div key={pi} className="question-navigator__phase">
                <div className="question-navigator__phase-label">
                  <span className={`phase-section__badge phase-section__badge--${phase.phase}`}>
                    {phase.phase.replace('_', ' ')}
                  </span>
                </div>
                {phase.questions.map((q, qi) => {
                  const globalIdx = offset + qi;
                  const isCurrent = globalIdx === currentQuestionIndex;
                  return (
                    <button
                      key={q.id}
                      className={`question-navigator__item ${isCurrent ? 'question-navigator__item--current' : ''}`}
                      onClick={() => onGoToQuestion(globalIdx)}
                    >
                      <span className="question-navigator__num">Q{globalIdx + 1}</span>
                      <span className="question-navigator__text">
                        {q.text.length > 80 ? q.text.slice(0, 80) + '...' : q.text}
                      </span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}

      {/* Current Question Card */}
      {currentQuestion && (
        <div className="current-question-card">
          <div className="current-question-card__header">
            <span className={`phase-section__badge phase-section__badge--${currentQuestion.phaseName}`}>
              {currentQuestion.phaseName.replace('_', ' ')}
            </span>
            <QuestionTimer timeMinutes={currentQuestion.time_minutes} />
          </div>

          <p className="current-question-card__text">{currentQuestion.text}</p>

          <div className="current-question-card__meta">
            <span className="question-card__category">{currentQuestion.category}</span>
            <span className="question-card__difficulty">
              {Array.from({ length: 5 }, (_, i) => (
                <span
                  key={i}
                  className={`question-card__difficulty-dot ${
                    i < currentQuestion.difficulty ? 'question-card__difficulty-dot--filled' : ''
                  }`}
                />
              ))}
            </span>
            <span className="question-card__time">{currentQuestion.time_minutes} min</span>
          </div>

          {/* Expandable approach sections */}
          <div className="current-question-card__sections">
            {currentQuestion.brute_force_approach && (
              <>
                <button
                  className="plan-panel__section-toggle"
                  onClick={() => setShowBruteForce(!showBruteForce)}
                >
                  {showBruteForce ? 'Hide' : 'Show'} Brute Force
                </button>
                {showBruteForce && (
                  <ApproachBlock approach={currentQuestion.brute_force_approach} />
                )}
              </>
            )}

            {currentQuestion.optimal_approaches && currentQuestion.optimal_approaches.length > 0 && (
              <>
                <button
                  className="plan-panel__section-toggle"
                  onClick={() => setShowOptimal(!showOptimal)}
                >
                  {showOptimal ? 'Hide' : 'Show'} Optimal Solution
                  {currentQuestion.optimal_approaches.length > 1 ? 's' : ''} (
                  {currentQuestion.optimal_approaches.length})
                </button>
                {showOptimal &&
                  currentQuestion.optimal_approaches.map((approach, i) => (
                    <ApproachBlock key={i} approach={approach} />
                  ))}
              </>
            )}

            {currentQuestion.common_pitfalls && currentQuestion.common_pitfalls.length > 0 && (
              <>
                <button
                  className="plan-panel__section-toggle"
                  onClick={() => setShowPitfalls(!showPitfalls)}
                >
                  {showPitfalls ? 'Hide' : 'Show'} Common Pitfalls (
                  {currentQuestion.common_pitfalls.length})
                </button>
                {showPitfalls && (
                  <ul className="plan-panel__pitfalls">
                    {currentQuestion.common_pitfalls.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Navigation Controls */}
      <div className="navigation-controls">
        <button
          className="btn btn--secondary btn--sm"
          disabled={currentQuestionIndex <= 0}
          onClick={() => onAdvanceQuestion(-1)}
          title="Previous question (P)"
        >
          Prev Question
        </button>
        <button
          className="btn btn--primary btn--sm"
          disabled={currentQuestionIndex >= allQuestions.length - 1}
          onClick={() => onAdvanceQuestion(1)}
          title="Next question (N)"
        >
          Next Question
        </button>
      </div>
    </section>
  );
}

/* ── Sub-components ─────────────────────────────────────────────────── */

function PhaseProgressBar({
  phases,
  currentPhaseIndex,
  onPhaseClick,
}: {
  phases: { phase: string; duration_minutes: number }[];
  currentPhaseIndex: number;
  onPhaseClick: (phaseIndex: number) => void;
}) {
  const totalMinutes = phases.reduce((sum, p) => sum + p.duration_minutes, 0);

  return (
    <div className="phase-progress-bar" role="tablist" aria-label="Interview phases">
      {phases.map((phase, i) => {
        const pct = totalMinutes > 0 ? (phase.duration_minutes / totalMinutes) * 100 : 0;
        const isCurrent = i === currentPhaseIndex;
        return (
          <button
            key={`${phase.phase}-${i}`}
            role="tab"
            aria-selected={isCurrent}
            className={`phase-progress-bar__segment plan-timeline__segment--${phase.phase} ${
              isCurrent ? 'phase-progress-bar__segment--current' : ''
            }`}
            style={{ flex: `${pct} 0 0%` }}
            title={`Switch to ${phase.phase.replace('_', ' ')} — ${phase.duration_minutes} min`}
            onClick={() => onPhaseClick(i)}
          >
            <span className="plan-timeline__label">
              {phase.phase.replace('_', ' ')}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ApproachBlock({ approach }: { approach: ApproachPattern }) {
  return (
    <div className="approach-block">
      <div className="approach-block__name">{approach.name}</div>
      <div className="approach-block__complexity">
        {Object.entries(approach.expected_complexity).map(([key, val]) => (
          <span key={key} className="question-card__complexity-badge">
            {key}: {val}
          </span>
        ))}
      </div>
      {approach.key_steps.length > 0 && (
        <ol className="approach-block__steps">
          {approach.key_steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      {approach.code_snippet && (
        <pre className="approach-block__code">{approach.code_snippet}</pre>
      )}
    </div>
  );
}
