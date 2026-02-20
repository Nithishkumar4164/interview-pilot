/**
 * Hint control panel — shows progressive hints with controls.
 *
 * Features:
 * - Current hint level display (1/2/3)
 * - "Show Hint" button
 * - Quotable hint text
 * - "Candidate Got It" / quality feedback buttons
 * - Hint timeline
 * - Keyboard shortcut (H)
 */
import { useEffect } from 'react';
import { useHintStore, type HintEntry } from '@/stores/hint-store';

const LEVEL_LABELS: Record<number, string> = {
  1: 'Nudge',
  2: 'Guide',
  3: 'Direct',
};

interface HintPanelProps {
  onRequestHint: () => void;
  onAcknowledge: (hintId: string, quality: 'none' | 'partial' | 'full') => void;
}

export function HintPanel({ onRequestHint, onAcknowledge }: HintPanelProps) {
  const { currentLevel, activeHint, history, hintSignal } = useHintStore();

  // Keyboard shortcut: H to show next hint
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'h' || e.key === 'H') {
        // Don't trigger if user is typing in an input
        if (
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLTextAreaElement
        ) {
          return;
        }
        onRequestHint();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onRequestHint]);

  return (
    <section className="hint-panel" aria-label="Hints">
      <div className="hint-panel__header">
        <h3>Hints</h3>
        <span className="hint-panel__level">
          Level {currentLevel}/3
        </span>
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
                onClick={() => onAcknowledge(activeHint.id, 'full')}
              >
                Got It
              </button>
              <button
                className="btn btn--secondary"
                onClick={() => onAcknowledge(activeHint.id, 'partial')}
              >
                Partially
              </button>
              <button
                className="btn btn--ghost"
                onClick={() => onAcknowledge(activeHint.id, 'none')}
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
      {history.length > 0 && (
        <div className="hint-panel__timeline">
          <h4>Hint History</h4>
          {history.map((hint) => (
            <HintTimelineEntry key={hint.id} hint={hint} />
          ))}
        </div>
      )}
    </section>
  );
}

function HintTimelineEntry({ hint }: { hint: HintEntry }) {
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
