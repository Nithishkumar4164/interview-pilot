/**
 * Session controls bar — top bar with status, timer, and controls.
 */
import { useSessionStore } from '@/stores/session-store';
import { usePlanStore } from '@/stores/plan-store';
import { useOfflineStore } from '@/stores/offline-store';

function formatTime(ms: number): string {
  const totalSecs = Math.floor(ms / 1000);
  const h = Math.floor(totalSecs / 3600);
  const m = Math.floor((totalSecs % 3600) / 60);
  const s = totalSecs % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

interface SessionBarProps {
  onPause: () => void;
  onResume: () => void;
  onEnd: () => void;
  onToggleVideo: () => void;
  videoActive: boolean;
}

export function SessionBar({ onPause, onResume, onEnd, onToggleVideo, videoActive }: SessionBarProps) {
  const { status, level, elapsedMs, durationMinutes, currentQuestionIndex, totalQuestions, currentPhaseIndex } = useSessionStore();
  const plan = usePlanStore((s) => s.plan);
  const { wsStatus, degradationLevel, providerStatuses } = useOfflineStore();

  const currentPhaseName = plan?.phases[currentPhaseIndex]?.phase?.replace('_', ' ') ?? '';

  const totalMs = durationMinutes * 60 * 1000;
  const pctElapsed = totalMs > 0 ? Math.min(100, (elapsedMs / totalMs) * 100) : 0;
  const isOvertime = elapsedMs > totalMs;

  return (
    <header className="session-bar" role="banner">
      <div className="session-bar__left">
        <StatusIndicator status={wsStatus} degradation={degradationLevel} />
        <span className="session-bar__level">{level}</span>
        {totalQuestions > 0 && (
          <div className="session-bar__question-context">
            {currentPhaseName && (
              <span className={`phase-section__badge phase-section__badge--${plan?.phases[currentPhaseIndex]?.phase ?? ''}`}>
                {currentPhaseName}
              </span>
            )}
            <span className="session-bar__question-progress">
              Q{currentQuestionIndex + 1}/{totalQuestions}
            </span>
          </div>
        )}
      </div>

      <div className="session-bar__center">
        <div className="session-bar__timer" aria-live="polite">
          <span className={`timer__elapsed ${isOvertime ? 'timer--overtime' : ''}`}>
            {formatTime(elapsedMs)}
          </span>
          <span className="timer__separator">/</span>
          <span className="timer__total">{formatTime(totalMs)}</span>
        </div>
        <div className="session-bar__progress">
          <div
            className="session-bar__progress-fill"
            style={{ width: `${pctElapsed}%` }}
            role="progressbar"
            aria-valuenow={Math.round(pctElapsed)}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      </div>

      <div className="session-bar__right">
        {Object.entries(providerStatuses).map(([provider, s]) => (
          <span key={provider} className={`provider-badge provider-badge--${s}`}>
            {provider}
          </span>
        ))}

        <button
          className={`btn btn--secondary video-share-toggle ${videoActive ? 'video-share-toggle--active' : ''}`}
          onClick={onToggleVideo}
          title={videoActive ? 'Stop sharing video' : 'Share meeting video'}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginRight: 4 }}>
            <rect x="1" y="3" width="10" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
            <path d="M11 6.5L14.5 4V12L11 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {videoActive ? 'Stop Video' : 'Share Video'}
        </button>

        {status === 'live' && (
          <button className="btn btn--secondary" onClick={onPause}>
            Pause
          </button>
        )}
        {status === 'paused' && (
          <button className="btn btn--primary" onClick={onResume}>
            Resume
          </button>
        )}
        {(status === 'live' || status === 'paused') && (
          <button className="btn btn--danger" onClick={onEnd}>
            End Interview
          </button>
        )}
      </div>
    </header>
  );
}

function StatusIndicator({
  status,
  degradation,
}: {
  status: string;
  degradation: number;
}) {
  const color = degradation === 0 ? 'green' : degradation <= 1 ? 'amber' : 'red';
  const label = degradation === 0 ? 'LIVE' : degradation <= 1 ? 'DEGRADED' : 'OFFLINE';

  return (
    <span className={`status-indicator status-indicator--${color}`} aria-label={`Status: ${label}`}>
      <span className="status-indicator__dot" />
      {label}
    </span>
  );
}
