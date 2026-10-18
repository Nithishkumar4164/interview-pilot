/** One solution approach of a question: name, complexity, steps, optional code. */
import type { ApproachPattern } from '@/stores/plan-store';

export function ApproachBlock({ approach }: { approach: ApproachPattern }) {
  // Generated approaches may leave out complexity or steps (design questions often do).
  const complexity = Object.entries(approach.expected_complexity ?? {});
  const steps = approach.key_steps ?? [];
  return (
    <div className="approach-block">
      <div className="approach-block__name">{approach.name}</div>
      <div className="approach-block__complexity">
        {complexity.map(([key, val]) => (
          <span key={key} className="question-card__complexity-badge">
            {key}: {val}
          </span>
        ))}
      </div>
      {steps.length > 0 && (
        <ol className="approach-block__steps">
          {steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      {approach.code_snippet && (
        <pre className="approach-block__code">{approach.code_snippet}</pre>
      )}
    </div>
  );
}
