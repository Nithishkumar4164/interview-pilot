/**
 * Code analysis sidebar — right side of dashboard.
 *
 * Shows detected approach, progress bar, bugs, and stuck indicator.
 */
import { useAnalysisStore, type CodeAnalysisState } from '@/stores/analysis-store';

const PROGRESS_LABELS: Record<string, string> = {
  empty: 'Empty',
  skeleton: 'Skeleton',
  partial: 'Partial',
  near_complete: 'Near Complete',
  complete: 'Complete',
  unknown: 'Unknown',
};

const PROGRESS_PCT: Record<string, number> = {
  empty: 0,
  skeleton: 20,
  partial: 50,
  near_complete: 80,
  complete: 100,
  unknown: 0,
};

export function CodePanel() {
  const { codeAnalysis } = useAnalysisStore();

  if (!codeAnalysis) {
    return (
      <section className="code-panel" aria-label="Code Analysis">
        <h3>Code Analysis</h3>
        <p className="code-panel__empty">Waiting for code snapshots...</p>
      </section>
    );
  }

  return (
    <section className="code-panel" aria-label="Code Analysis">
      <h3>Code Analysis</h3>
      <CodeInfo analysis={codeAnalysis} />
    </section>
  );
}

function CodeInfo({ analysis }: { analysis: CodeAnalysisState }) {
  const progressPct = PROGRESS_PCT[analysis.progressState] ?? 0;
  const progressLabel = PROGRESS_LABELS[analysis.progressState] ?? 'Unknown';

  return (
    <div className="code-info">
      {/* Language & Source */}
      <div className="code-info__meta">
        <span className="code-info__language">{analysis.language}</span>
        <span className="code-info__source">{analysis.source}</span>
        <span className="code-info__size">{analysis.codeLength} chars</span>
      </div>

      {/* Progress Bar */}
      <div className="code-info__progress">
        <div className="code-info__progress-header">
          <span>Progress</span>
          <span>{progressLabel}</span>
        </div>
        <div className="progress-bar" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100}>
          <div
            className={`progress-bar__fill progress-bar__fill--${analysis.progressState}`}
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Approach */}
      {analysis.detectedApproach && (
        <div className="code-info__approach">
          <strong>Approach:</strong> {analysis.detectedApproach.name}
          <span className="code-info__confidence">
            ({Math.round(analysis.detectedApproach.confidence * 100)}%)
          </span>
        </div>
      )}

      {/* Stuck Indicator */}
      {analysis.isStuck && (
        <div className="code-info__stuck" role="alert">
          Candidate appears stuck — consider offering a hint
        </div>
      )}

      {/* Bugs */}
      {analysis.bugs.length > 0 && (
        <div className="code-info__bugs">
          <strong>Issues detected:</strong>
          <ul>
            {analysis.bugs.map((bug, i) => (
              <li key={i} className="code-info__bug">{bug}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
