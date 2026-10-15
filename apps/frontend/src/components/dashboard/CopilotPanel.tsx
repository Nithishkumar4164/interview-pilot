/**
 * Copilot panel — the peer interviewer's whispers.
 *
 * Each card is advice for the interviewer, never shown to the candidate.
 * "Mark as asked" tells the backend the interviewer relayed it (the report
 * can then tell copilot-prompted moves from the interviewer's own);
 * "Dismiss" just drops it. Kinds can be muted from the panel header.
 */
import { useState } from 'react';
import {
  ALL_CARD_KINDS,
  CARD_KIND_LABELS,
  useCopilotStore,
  type CopilotCard,
  type CopilotCardKind,
} from '@/stores/copilot-store';

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
