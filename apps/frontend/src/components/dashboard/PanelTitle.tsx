/**
 * A live-session pane's title, with a button that hides the pane. The pane
 * sets `hidden` (from usePaneHidden) so it stays mounted and keeps its state;
 * the Panes menu in the session bar shows it again.
 */
import { paneLabel, usePanelStore, type PanelId } from '@/stores/panel-store';

interface PanelTitleProps {
  id: PanelId;
  children: React.ReactNode;
  level?: 'h2' | 'h3';
}

export function PanelTitle({ id, children, level: Heading = 'h3' }: PanelTitleProps) {
  const setShown = usePanelStore((s) => s.setShown);

  return (
    <Heading className="panel-title">
      {children}
      <button
        type="button"
        className="panel-title__hide"
        aria-label={`Hide ${paneLabel(id)}`}
        title="Hide this pane (show it again from Panes)"
        onClick={() => setShown(id, false)}
      >
        ×
      </button>
    </Heading>
  );
}
