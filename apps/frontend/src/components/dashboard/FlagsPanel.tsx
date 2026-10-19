/**
 * Flags panel — red flags from utterance analysis, newest first.
 *
 * Contradictions and code mismatches arrive as copilot cards (Copilot panel).
 */
import { useAnalysisStore } from '@/stores/analysis-store';
import { PanelTitle } from './PanelTitle';
import { usePaneHidden } from '@/stores/panel-store';

export function FlagsPanel() {
  const hidden = usePaneHidden('flags');
  const flags = useAnalysisStore((s) => s.redFlags);
  const newestFirst = [...flags].reverse();

  return (
    <section className="flags-panel" hidden={hidden} aria-label="Flags">
      <div className="flags-panel__header panel-header">
        <PanelTitle id="flags">Flags</PanelTitle>
        {flags.length > 0 && <span className="flags-panel__count">{flags.length}</span>}
      </div>
      {newestFirst.length === 0 ? (
        <p className="flags-panel__empty">No flags yet.</p>
      ) : (
        <ul className="flags-panel__list">
          {newestFirst.map((flag) => (
            <li key={flag.id} className="flags-panel__item">
              <span className="flags-panel__kind">Red flag</span>
              <p className="flags-panel__text">{flag.text}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
