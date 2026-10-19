/**
 * Smart Hint panel — LLM-generated runtime hints with regeneration.
 *
 * Features:
 * - "Generate Smart Hint" button
 * - Loading spinner during generation
 * - Runtime hint card with directness badge
 * - Feedback buttons (well received / partially / not understood)
 * - Regenerate dropdown with reason options
 * - Runtime hint history
 */
import { useState } from 'react';
import { useHintStore, type HintSuggestion, type RuntimeHintEntry, type RegenerationReason } from '@/stores/hint-store';
import { PanelTitle } from './PanelTitle';
import { usePaneHidden } from '@/stores/panel-store';

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
  onUseSuggestion?: (suggestion: HintSuggestion) => void;
  onRequestRuntimeHint: () => void;
  onRegenerateRuntimeHint: (reason: string) => void;
  onAcknowledgeRuntimeHint: (hintId: string, quality: string) => void;
}

export function HintPanel({
  onUseSuggestion,
  onRequestRuntimeHint,
  onRegenerateRuntimeHint,
  onAcknowledgeRuntimeHint,
}: HintPanelProps) {
  const hidden = usePaneHidden('hints');
  const { activeRuntimeHint, runtimeHintHistory, runtimeHintLoading, suggestions, removeSuggestion } = useHintStore();

  return (
    <section className="hint-panel" hidden={hidden} aria-label="Smart Hints">
      <div className="hint-panel__header panel-header">
        <PanelTitle id="hints">Smart Hints</PanelTitle>
      </div>

      {suggestions.map((suggestion) => (
        <div key={suggestion.id} className="hint-panel__suggestion" role="note" aria-label="Suggested hint">
          <div className="hint-panel__suggestion-header">
            <span className="hint-panel__suggestion-label">Suggested hint</span>
            <span className={`hint-card__directness hint-card__directness--${suggestion.directness}`}>
              {DIRECTNESS_LABELS[suggestion.directness] ?? suggestion.directness}
            </span>
          </div>
          <p className="hint-panel__suggestion-text">{suggestion.text}</p>
          <div className="hint-panel__suggestion-actions">
            <button
              className="btn btn--small btn--primary"
              onClick={() => {
                onUseSuggestion?.(suggestion);
                removeSuggestion(suggestion.id);
              }}
            >
              Use
            </button>
            <button className="btn btn--small btn--ghost" onClick={() => removeSuggestion(suggestion.id)}>
              Dismiss
            </button>
          </div>
        </div>
      ))}

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
