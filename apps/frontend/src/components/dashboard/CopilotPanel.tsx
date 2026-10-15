/**
 * Copilot panel — the peer interviewer's whispers.
 *
 * Each card is advice for the interviewer, never shown to the candidate.
 * "Mark as asked" tells the backend the interviewer relayed it (the report
 * can then tell copilot-prompted moves from the interviewer's own);
 * "Dismiss" just drops it. Kinds can be muted from the panel header.
 */
import { useMemo, useState } from 'react';
import {
  ALL_CARD_KINDS,
  CARD_KIND_LABELS,
  CRITERION_CATEGORIES,
  CRITERION_LABELS,
  useCopilotStore,
  type CopilotCard,
  type CopilotCardKind,
} from '@/stores/copilot-store';
import { useSessionStore, getAllPlanQuestions } from '@/stores/session-store';
import { usePlanStore } from '@/stores/plan-store';

/** Cards whose primary action is relaying something to the candidate. */
const RELAY_KINDS: ReadonlySet<CopilotCardKind> = new Set(['probe', 'clarification', 'claim_check', 'edge_cases']);

interface CopilotPanelProps {
  onUseCard: (card: CopilotCard) => void;
}

export function CopilotPanel({ onUseCard }: CopilotPanelProps) {
  const cards = useCopilotStore((s) => s.cards);
  const mutedKinds = useCopilotStore((s) => s.mutedKinds);
  const dismissCard = useCopilotStore((s) => s.dismissCard);
  const markUsed = useCopilotStore((s) => s.markUsed);
  const toggleMute = useCopilotStore((s) => s.toggleMute);
  const [showMutes, setShowMutes] = useState(false);

  const visible = cards.filter((c) => !mutedKinds.has(c.kind));

  return (
    <section className="copilot-panel" aria-label="Copilot">
      <div className="copilot-panel__header">
        <h3>Copilot</h3>
        <div className="copilot-panel__header-right">
          {visible.length > 0 && <span className="copilot-panel__count">{visible.length}</span>}
          <button
            className="btn btn--ghost btn--small"
            aria-expanded={showMutes}
            onClick={() => setShowMutes((v) => !v)}
          >
            Mute…
          </button>
        </div>
      </div>

      {showMutes && (
        <div className="copilot-panel__mutes" role="group" aria-label="Muted kinds">
          {ALL_CARD_KINDS.map((kind) => (
            <label key={kind} className="copilot-panel__mute">
              <input type="checkbox" checked={mutedKinds.has(kind)} onChange={() => toggleMute(kind)} />
              {CARD_KIND_LABELS[kind]}
            </label>
          ))}
        </div>
      )}

      <CoverageStrip />

      {visible.length === 0 ? (
        <p className="copilot-panel__empty">Listening. Suggestions appear here as the interview unfolds.</p>
      ) : (
        <ul className="copilot-panel__list">
          {visible.map((card) => (
            <li
              key={card.id}
              className={`copilot-card copilot-card--${card.kind} copilot-card--${card.priority}`}
              role="note"
              aria-label={CARD_KIND_LABELS[card.kind]}
            >
              <div className="copilot-card__header">
                <span className="copilot-card__kind">{CARD_KIND_LABELS[card.kind]}</span>
                {card.source === 'rule' && <span className="copilot-card__source">signal</span>}
              </div>
              <p className="copilot-card__title">{card.title}</p>
              {card.body && <p className="copilot-card__body">{card.body}</p>}
              {card.items.length > 0 && (
                <ul className="copilot-card__items">
                  {card.items.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              )}
              <div className="copilot-card__actions">
                {RELAY_KINDS.has(card.kind) ? (
                  <button
                    className="btn btn--small btn--primary"
                    onClick={() => {
                      onUseCard(card);
                      markUsed(card.id);
                    }}
                  >
                    Mark as asked
                  </button>
                ) : (
                  <button
                    className="btn btn--small btn--secondary"
                    onClick={() => {
                      onUseCard(card);
                      markUsed(card.id);
                    }}
                  >
                    Done
                  </button>
                )}
                <button className="btn btn--small btn--ghost" onClick={() => dismissCard(card.id)}>
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Evidence gathered per report criterion, and the next planned question that
 * would fill the biggest gap (by plan category).
 */
function CoverageStrip() {
  const coverage = useCopilotStore((s) => s.coverage);
  const plan = usePlanStore((s) => s.plan);
  const currentQuestionIndex = useSessionStore((s) => s.currentQuestionIndex);

  const suggestion = useMemo(() => {
    if (!coverage || coverage.gaps.length === 0) return null;
    const upcoming = getAllPlanQuestions()
      .map((q, index) => ({ q, index }))
      .filter(({ index }) => index > currentQuestionIndex);
    for (const gap of coverage.gaps) {
      const categories = CRITERION_CATEGORIES[gap] ?? [];
      const hit = upcoming.find(({ q }) => categories.includes(q.category));
      if (hit) return { gap, index: hit.index, text: hit.q.text };
    }
    return null;
    // getAllPlanQuestions reads the plan store; recompute when the plan changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverage, currentQuestionIndex, plan]);

  if (!coverage) return null;

  return (
    <div className="coverage-strip" aria-label="Rubric coverage">
      <ul className="coverage-strip__chips">
        {Object.entries(coverage.criteria).map(([name, c]) => (
          <li
            key={name}
            className={`coverage-chip coverage-chip--${c.status}`}
            title={`${CRITERION_LABELS[name] ?? name}: ${c.evidence} evidence point${c.evidence === 1 ? '' : 's'}`}
          >
            {CRITERION_LABELS[name] ?? name}
          </li>
        ))}
      </ul>
      {suggestion && (
        <p className="coverage-strip__suggestion">
          No evidence yet for <strong>{CRITERION_LABELS[suggestion.gap] ?? suggestion.gap}</strong>. Q
          {suggestion.index + 1} covers it: {suggestion.text.length > 70 ? suggestion.text.slice(0, 70) + '…' : suggestion.text}
        </p>
      )}
    </div>
  );
}
