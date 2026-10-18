/**
 * A live-session panel's title, doubling as its collapse toggle. The panel
 * adds `is-collapsed` (from usePanelCollapsed) and marks its header row
 * `panel-header`; CSS hides everything else while collapsed, so the panel
 * keeps its state.
 */
import { usePanelStore, type PanelId } from '@/stores/panel-store';

interface PanelTitleProps {
  id: PanelId;
  children: React.ReactNode;
  level?: 'h2' | 'h3';
}

export function PanelTitle({ id, children, level: Heading = 'h3' }: PanelTitleProps) {
  const collapsed = usePanelStore((s) => !!s.collapsed[id]);
  const toggle = usePanelStore((s) => s.toggle);

  return (
    <Heading className="panel-title">
      <button type="button" className="panel-title__toggle" aria-expanded={!collapsed} onClick={() => toggle(id)}>
        <span className="panel-title__chevron" aria-hidden="true">
          {collapsed ? '▸' : '▾'}
        </span>
        {children}
      </button>
    </Heading>
  );
}
