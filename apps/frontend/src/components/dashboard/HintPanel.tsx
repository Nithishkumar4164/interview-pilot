/**
 * Hint control panel — shows progressive hints and runtime smart hints.
 *
 * Features:
 * - Current hint level display (1/2/3)
 * - "Show Hint" button
 * - Quotable hint text
 * - "Candidate Got It" / quality feedback buttons
 * - Hint timeline
 * - Keyboard shortcut (H)
 * - Smart Hints: LLM-generated runtime hints with regeneration
 */
import { useEffect, useState } from 'react';
import { useHintStore, type HintEntry, type RuntimeHintEntry, type RegenerationReason } from '@/stores/hint-store';

const LEVEL_LABELS: Record<number, string> = {
  1: 'Nudge',
  2: 'Guide',
  3: 'Direct',
};

const DIRECTNESS_LABELS: Record<string, string> = {
  subtle: 'Subtle',
  moderate: 'Moderate',
  direct: 'Direct',
};

const REGENERATION_OPTIONS: Array<{ value: RegenerationReason; label: string }> = [
  { value: 'too_direct', label: 'Too direct' },
  { value: 'not_useful', label: 'Not useful' },
  { value: 'off_topic', label: 'Off topic' },
  { value: 'other', label: 'Other' },
];

interface HintPanelProps {
  onRequestHint: () => void;
  onAcknowledge: (hintId: string, quality: 'none' | 'partial' | 'full') => void;
  onRequestRuntimeHint: () => void;
  onRegenerateRuntimeHint: (reason: string) => void;
  onAcknowledgeRuntimeHint: (hintId: string, quality: string) => void;
}

export function HintPanel({
  onRequestHint,
  onAcknowledge,
  onRequestRuntimeHint,
  onRegenerateRuntimeHint,
  onAcknowledgeRuntimeHint,
}: HintPanelProps) {
  const { currentLevel, activeHint, history, hintSignal } = useHintStore();
  const { activeRuntimeHint, runtimeHintHistory, runtimeHintLoading } = useHintStore();

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

      {/* ── Smart Hints (Runtime) ────────────────────────────────── */}
      <div className="hint-panel__runtime">
        <h4>Smart Hints</h4>

        <button
          className="btn btn--primary hint-panel__trigger"
          onClick={onRequestRuntimeHint}
          disabled={runtimeHintLoading}
        >
          {runtimeHintLoading ? 'Generating...' : 'Generate Smart Hint'}
        </button>

        {runtimeHintLoading && (
          <div className="hint-panel__loading" role="status">
            <span className="hint-panel__spinner" />
            Generating hint...
          </div>
        )}

        {activeRuntimeHint && !runtimeHintLoading && (
          <RuntimeHintCard
            hint={activeRuntimeHint}
            onAcknowledge={onAcknowledgeRuntimeHint}
            onRegenerate={onRegenerateRuntimeHint}
          />
        )}

        {runtimeHintHistory.length > 1 && (
          <div className="hint-panel__timeline">
            <h4>Smart Hint History</h4>
            {runtimeHintHistory
              .filter((h) => h.hintId !== activeRuntimeHint?.hintId)
              .map((hint) => (
                <RuntimeHintTimelineEntry key={hint.hintId} hint={hint} />
              ))}
          </div>
        )}
      </div>
    </section>
  );
}

function RuntimeHintCard({
  hint,
  onAcknowledge,
  onRegenerate,
}: {
  hint: RuntimeHintEntry;
  onAcknowledge: (hintId: string, quality: string) => void;
  onRegenerate: (reason: string) => void;
}) {
  const [showRegenMenu, setShowRegenMenu] = useState(false);

  return (
    <div className="hint-card hint-card--runtime">
      <span className={`hint-card__directness hint-card__directness--${hint.directness}`}>
        {DIRECTNESS_LABELS[hint.directness] || hint.directness}
      </span>
      <p className="hint-card__text">{hint.text}</p>

      {!hint.feedback && (
        <div className="hint-panel__actions">
          <button
            className="btn btn--success btn--small"
            onClick={() => onAcknowledge(hint.hintId, 'well_received')}
          >
            Well received
          </button>
          <button
            className="btn btn--secondary btn--small"
            onClick={() => onAcknowledge(hint.hintId, 'partially_received')}
          >
            Partially
          </button>
          <button
            className="btn btn--ghost btn--small"
            onClick={() => onAcknowledge(hint.hintId, 'not_understood')}
          >
            Not understood
          </button>
        </div>
      )}

      {hint.feedback && (
        <div className="hint-card__feedback-badge">
          {hint.feedback.replace('_', ' ')}
        </div>
      )}

      <div className="regenerate-dropdown">
        <button
          className="btn btn--ghost btn--small"
          onClick={() => setShowRegenMenu(!showRegenMenu)}
        >
          Regenerate
        </button>
        {showRegenMenu && (
          <div className="regenerate-dropdown__menu">
            {REGENERATION_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                className="regenerate-dropdown__item"
                onClick={() => {
                  onRegenerate(opt.value);
                  setShowRegenMenu(false);
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RuntimeHintTimelineEntry({ hint }: { hint: RuntimeHintEntry }) {
  return (
    <div className="hint-timeline-entry">
      <span className={`hint-card__directness hint-card__directness--${hint.directness}`} style={{ fontSize: 'var(--text-xs)', padding: '1px 4px' }}>
        {DIRECTNESS_LABELS[hint.directness]?.[0] || '?'}
      </span>
      <span className="hint-timeline-entry__text">{hint.text}</span>
      {hint.feedback && (
        <span className="hint-timeline-entry__quality hint-timeline-entry__quality--green">
          {hint.feedback.replace('_', ' ')}
        </span>
      )}
    </div>
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
