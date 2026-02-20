/**
 * Horizontal timeline bar showing phase time allocation.
 */
import type { PlanPhase } from '@/stores/plan-store';

interface PlanTimelineProps {
  phases: PlanPhase[];
  totalMinutes: number;
}

export function PlanTimeline({ phases, totalMinutes }: PlanTimelineProps) {
  const description = phases
    .map((p) => `${p.phase.replace('_', ' ')} ${p.duration_minutes}min`)
    .join(', ');

  return (
    <div className="plan-timeline" role="img" aria-label={`Time allocation: ${description}`}>
      <div className="plan-timeline__bar">
        {phases.map((phase, i) => {
          const pct = totalMinutes > 0 ? (phase.duration_minutes / totalMinutes) * 100 : 0;
          return (
            <div
              key={`${phase.phase}-${i}`}
              className={`plan-timeline__segment plan-timeline__segment--${phase.phase}`}
              style={{ flex: `${pct} 0 0%` }}
              title={`${phase.phase.replace('_', ' ')} — ${phase.duration_minutes} min`}
            >
              <span className="plan-timeline__label">
                {phase.phase.replace('_', ' ')} ({phase.duration_minutes}m)
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
