/**
 * Individual question card with edit controls.
 */
import { useState, useCallback } from 'react';
import type { PlannedQuestion, InterviewPlan } from '@/stores/plan-store';
import * as api from '@/api/client';

interface QuestionCardProps {
  question: PlannedQuestion;
  phaseIndex: number;
  questionIndex: number;
  planId: string;
  onPlanUpdated: (plan: InterviewPlan) => void;
}

export function QuestionCard({
  question,
  phaseIndex,
  questionIndex,
  planId,
  onPlanUpdated,
}: QuestionCardProps) {
  const [showAlternates, setShowAlternates] = useState(false);
  const [showHints, setShowHints] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSwap = useCallback(
    async (alternateIndex: number) => {
      setLoading(true);
      try {
        const updated = await api.swapQuestion(planId, phaseIndex, questionIndex, alternateIndex);
        onPlanUpdated(updated as unknown as InterviewPlan);
      } catch (err) {
        console.error('Failed to swap question:', err);
      } finally {
        setLoading(false);
      }
    },
    [planId, phaseIndex, questionIndex, onPlanUpdated],
  );

  const handleRemove = useCallback(async () => {
    if (!window.confirm('Remove this question from the plan?')) return;
    setLoading(true);
    try {
      const updated = await api.removeQuestion(planId, phaseIndex, questionIndex);
      onPlanUpdated(updated as unknown as InterviewPlan);
    } catch (err) {
      console.error('Failed to remove question:', err);
    } finally {
      setLoading(false);
    }
  }, [planId, phaseIndex, questionIndex, onPlanUpdated]);

  const handleRegenerateHints = useCallback(async () => {
    setLoading(true);
    try {
      const updated = await api.regenerateHints(planId, phaseIndex, questionIndex);
      onPlanUpdated(updated as unknown as InterviewPlan);
    } catch (err) {
      console.error('Failed to regenerate hints:', err);
    } finally {
      setLoading(false);
    }
  }, [planId, phaseIndex, questionIndex, onPlanUpdated]);

  return (
    <div className={`question-card ${loading ? 'question-card--removing' : ''}`}>
      <div className="question-card__text">{question.text}</div>

      <div className="question-card__meta">
        <span className="question-card__category">{question.category}</span>
        <span className="question-card__difficulty">
          {Array.from({ length: 5 }, (_, i) => (
            <span
              key={i}
              className={`question-card__difficulty-dot ${
                i < question.difficulty ? 'question-card__difficulty-dot--filled' : ''
              }`}
            />
          ))}
        </span>
        <span className="question-card__time">{question.time_minutes} min</span>
      </div>

      {/* Expandable sections */}
      <div className="question-card__expandable">
        {question.alternates.length > 0 && (
          <>
            <button
              className="question-card__expand-btn"
              onClick={() => setShowAlternates(!showAlternates)}
            >
              {showAlternates ? 'Hide' : 'Show'} alternates ({question.alternates.length})
            </button>
            {showAlternates && (
              <div className="question-card__alternates">
                {question.alternates.map((alt, i) => (
                  <div
                    key={i}
                    className="question-card__alternate-item"
                    onClick={() => handleSwap(i)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && handleSwap(i)}
                  >
                    {alt}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {question.hints.length > 0 && (
          <>
            <button
              className="question-card__expand-btn"
              onClick={() => setShowHints(!showHints)}
              style={{ marginLeft: question.alternates.length > 0 ? 'var(--space-3)' : 0 }}
            >
              {showHints ? 'Hide' : 'Show'} hints ({question.hints.length})
            </button>
            {showHints && (
              <div className="question-card__hints">
                {question.hints.map((hint) => (
                  <div key={hint.level} className="question-card__hint-item">
                    <span className={`question-card__hint-level question-card__hint-level--${hint.level}`}>
                      L{hint.level}
                    </span>
                    <span className="question-card__hint-text">{hint.text}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <div className="question-card__actions">
        <button className="btn btn--ghost btn--sm" onClick={handleRemove} disabled={loading}>
          Remove
        </button>
        <button className="btn btn--ghost btn--sm" onClick={handleRegenerateHints} disabled={loading}>
          Regenerate Hints
        </button>
      </div>
    </div>
  );
}
