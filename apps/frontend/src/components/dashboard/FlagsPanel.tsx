/**
 * Flags panel — red flags from utterance analysis, contradictions between the
 * candidate's statements, and mismatches between what they say and their code.
 * Newest first.
 */
import { useAnalysisStore } from '@/stores/analysis-store';
import type { FlagKind } from '@/lib/flags';
import { PanelTitle } from './PanelTitle';
import { usePanelCollapsed } from '@/stores/panel-store';

const KIND_LABELS: Record<FlagKind, string> = {
  red_flag: 'Red flag',
  contradiction: 'Contradiction',
  code_mismatch: 'Code mismatch',
};

export function FlagsPanel() {
  const collapsed = usePanelCollapsed('flags');
  const flags = useAnalysisStore((s) => s.redFlags);
  const newestFirst = [...flags].reverse();

  return (
    <section className={`flags-panel${collapsed ? ' is-collapsed' : ''}`} aria-label="Flags">
      <div className="flags-panel__header panel-header">
        <PanelTitle id="flags">Flags</PanelTitle>
        {flags.length > 0 && <span className="flags-panel__count">{flags.length}</span>}
      </div>
      {newestFirst.length === 0 ? (
        <p className="flags-panel__empty">No flags yet.</p>
      ) : (
        <ul className="flags-panel__list">
          {newestFirst.map((flag) => (
            <li key={flag.id} className={`flags-panel__item flags-panel__item--${flag.kind}`}>
              <span className="flags-panel__kind">{KIND_LABELS[flag.kind]}</span>
              <p className="flags-panel__text">{flag.text}</p>
              {flag.claim && (
                <p className="flags-panel__detail">
                  <span>Earlier:</span> {flag.claim}
                </p>
              )}
              {flag.newStatement && (
                <p className="flags-panel__detail">
                  <span>Now:</span> {flag.newStatement}
                </p>
              )}
              {flag.probe && (
                <p className="flags-panel__probe">
                  <span>Ask:</span> {flag.probe}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
