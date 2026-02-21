/**
 * Interview Plan Panel — surfaces the generated plan during the live interview.
 *
 * Shows: phase progress bar, question navigator, current question with
 * approaches/pitfalls, and prev/next navigation controls.
 */
import { useState, useMemo, useEffect, useRef } from 'react';
import { usePlanStore } from '@/stores/plan-store';
import { useSessionStore, getAllPlanQuestions } from '@/stores/session-store';
import { useHintStore, type HintEntry } from '@/stores/hint-store';
import type { ApproachPattern } from '@/stores/plan-store';
import { QuestionTimer } from './QuestionTimer';

const LEVEL_LABELS: Record<number, string> = {
  1: 'Nudge',
  2: 'Guide',
  3: 'Direct',
};

interface InterviewPlanPanelProps {
  onAdvanceQuestion: (delta: number) => void;
  onGoToQuestion: (index: number) => void;
  onRequestHint: () => void;
  onAcknowledgeHint: (hintId: string, quality: 'none' | 'partial' | 'full') => void;
}

export function InterviewPlanPanel({
  onAdvanceQuestion,
  onGoToQuestion,
  onRequestHint,
  onAcknowledgeHint,
}: InterviewPlanPanelProps) {
  const plan = usePlanStore((s) => s.plan);
  const currentQuestionIndex = useSessionStore((s) => s.currentQuestionIndex);

  const { currentLevel, activeHint, history: hintHistory, hintSignal } = useHintStore();

  const [showNavigator, setShowNavigator] = useState(false);
  const [showBruteForce, setShowBruteForce] = useState(false);
  const [showOptimal, setShowOptimal] = useState(false);
  const [showPitfalls, setShowPitfalls] = useState(false);

  // Keyboard shortcut: H to show next hint
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (e.key === 'h' || e.key === 'H') {
        onRequestHint();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onRequestHint]);

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

      {/* ── Static Hints ─────────────────────────────────────────── */}
      <div className="plan-panel__hints">
        <div className="hint-panel__header">
          <h3>Hints</h3>
          <span className="hint-panel__level">Level {currentLevel}/3</span>
        </div>

        {/* Hint Signal Indicator */}
        {hintSignal !== 'none' && (
          <div className={`hint-signal hint-signal--${hintSignal}`} role="status">
            {hintSignal === 'nudge' && 'Candidate may need a gentle nudge'}
            {hintSignal === 'guide' && 'Candidate seems stuck — hint recommended'}
            {hintSignal === 'direct' && 'Candidate very stuck — consider direct hint'}
          </div>
        )}

        {/* Active Hint */}
        {activeHint ? (
          <div className="hint-panel__active">
            <div className="hint-card">
              <span className={`hint-card__level hint-card__level--${activeHint.level}`}>
                {LEVEL_LABELS[activeHint.level]}
              </span>
              <p className="hint-card__text">{activeHint.text}</p>
            </div>

            {!activeHint.interviewerUsed && (
              <div className="hint-panel__actions">
                <button
                  className="btn btn--success"
                  onClick={() => onAcknowledgeHint(activeHint.id, 'full')}
                >
                  Got It
                </button>
                <button
                  className="btn btn--secondary"
                  onClick={() => onAcknowledgeHint(activeHint.id, 'partial')}
                >
                  Partially
                </button>
                <button
                  className="btn btn--ghost"
                  onClick={() => onAcknowledgeHint(activeHint.id, 'none')}
                >
                  No Response
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="hint-panel__empty">
            <p>No active hint</p>
          </div>
        )}

        {/* Show Hint Button */}
        {currentLevel < 3 && (
          <button
            className="btn btn--primary hint-panel__trigger"
            onClick={onRequestHint}
            title="Show next hint (H)"
          >
            Show Hint (Level {currentLevel + 1})
          </button>
        )}

        {/* Hint Timeline */}
        {hintHistory.length > 0 && (
          <div className="hint-panel__timeline">
            <h4>Hint History</h4>
            {hintHistory.map((hint) => (
              <StaticHintTimelineEntry key={hint.id} hint={hint} />
            ))}
          </div>
        )}
      </div>

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

function StaticHintTimelineEntry({ hint }: { hint: HintEntry }) {
  const qualityColors: Record<string, string> = {
    full: 'green',
    partial: 'amber',
    none: 'red',
  };

  return (
    <div className="hint-timeline-entry">
      <span className={`hint-timeline-entry__level hint-timeline-entry__level--${hint.level}`}>
        L{hint.level}
      </span>
      <span className="hint-timeline-entry__text">{hint.text}</span>
      {hint.interviewerUsed && (
        <span
          className={`hint-timeline-entry__quality hint-timeline-entry__quality--${qualityColors[hint.candidateResponseQuality]}`}
        >
          {hint.candidateResponseQuality}
        </span>
      )}
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
