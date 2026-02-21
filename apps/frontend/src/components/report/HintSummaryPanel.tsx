/**
 * HintSummaryPanel — displays hint interaction summary in the report.
 */

interface HintDetail {
  question_id: string;
  level: number;
  interviewer_used: boolean;
  response_quality: string;
  time_to_respond_ms: number | null;
}

interface HintSummary {
  total_hints_given: number;
  incorporation_rate: number;
  average_response_time_ms: number | null;
  independence_trajectory: string;
  level_distribution: Record<string, number>;
  details: HintDetail[];
}

interface HintSummaryPanelProps {
  summary: HintSummary;
}

export function HintSummaryPanel({ summary }: HintSummaryPanelProps) {
  if (!summary || summary.total_hints_given === 0) {
    return (
      <div className="hint-summary-panel">
        <h3>Hint Usage</h3>
        <p className="hint-summary-panel__empty">No hints were given during this interview.</p>
      </div>
    );
  }

  const incRate = Math.round(summary.incorporation_rate * 100);
  const avgResponse = summary.average_response_time_ms
    ? `${(summary.average_response_time_ms / 1000).toFixed(1)}s`
    : 'N/A';

  const trajectoryLabel: Record<string, string> = {
    improving: 'Improving',
    stable: 'Stable',
    declining: 'Declining',
    unknown: 'Unknown',
  };

  const trajectoryClass: Record<string, string> = {
    improving: 'hint-summary-panel__trajectory--improving',
    stable: 'hint-summary-panel__trajectory--stable',
    declining: 'hint-summary-panel__trajectory--declining',
  };

  return (
    <div className="hint-summary-panel">
      <h3>Hint Usage</h3>

      <div className="hint-summary-panel__stats">
        <div className="hint-summary-panel__stat">
          <span className="hint-summary-panel__stat-value">{summary.total_hints_given}</span>
          <span className="hint-summary-panel__stat-label">Total Hints</span>
        </div>

        <div className="hint-summary-panel__stat">
          <span className="hint-summary-panel__stat-value">{incRate}%</span>
          <span className="hint-summary-panel__stat-label">Incorporation</span>
          <div className="hint-summary-panel__bar">
            <div
              className="hint-summary-panel__bar-fill"
              style={{ width: `${incRate}%` }}
            />
          </div>
        </div>

        <div className="hint-summary-panel__stat">
          <span className="hint-summary-panel__stat-value">{avgResponse}</span>
          <span className="hint-summary-panel__stat-label">Avg Response</span>
        </div>

        <div className="hint-summary-panel__stat">
          <span
            className={`hint-summary-panel__stat-value ${trajectoryClass[summary.independence_trajectory] ?? ''}`}
          >
            {trajectoryLabel[summary.independence_trajectory] ?? summary.independence_trajectory}
          </span>
          <span className="hint-summary-panel__stat-label">Independence</span>
        </div>
      </div>

      {/* Level distribution */}
      {Object.keys(summary.level_distribution).length > 0 && (
        <div className="hint-summary-panel__levels">
          <h4>Level Distribution</h4>
          <div className="hint-summary-panel__level-bars">
            {Object.entries(summary.level_distribution)
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([level, count]) => (
                <div key={level} className="hint-summary-panel__level-row">
                  <span className="hint-summary-panel__level-label">L{level}</span>
                  <div className="hint-summary-panel__level-bar">
                    <div
                      className="hint-summary-panel__level-fill"
                      style={{
                        width: `${(count / summary.total_hints_given) * 100}%`,
                      }}
                    />
                  </div>
                  <span className="hint-summary-panel__level-count">{count}</span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Per-hint details */}
      {summary.details.length > 0 && (
        <details className="hint-summary-panel__details">
          <summary>Hint Details ({summary.details.length})</summary>
          <table className="hint-summary-panel__table">
            <thead>
              <tr>
                <th>Question</th>
                <th>Level</th>
                <th>Used</th>
                <th>Quality</th>
                <th>Response Time</th>
              </tr>
            </thead>
            <tbody>
              {summary.details.map((d, i) => (
                <tr key={i}>
                  <td>{d.question_id.slice(0, 8)}...</td>
                  <td>{d.level}</td>
                  <td>{d.interviewer_used ? 'Yes' : 'No'}</td>
                  <td>{d.response_quality}</td>
                  <td>
                    {d.time_to_respond_ms
                      ? `${(d.time_to_respond_ms / 1000).toFixed(1)}s`
                      : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
