/**
 * Session controls bar — top bar with status, timer, and controls.
 */
import { useEffect, useRef, useState } from 'react';
import { useSessionStore } from '@/stores/session-store';
import { usePlanStore } from '@/stores/plan-store';
import { useOfflineStore } from '@/stores/offline-store';
import { PANES, usePanelStore } from '@/stores/panel-store';
import type { MeetControlAction, MeetControlsState } from '@/hooks/useExtensionChannel';

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
  /** Ending takes seconds; the button stays disabled until the returned promise settles. */
  onEnd: () => Promise<void> | void;
  onToggleVideo: () => void;
  videoActive: boolean;
  /** The session's Meet tab, as the extension reads it; null before it has reported. */
  meetControls?: MeetControlsState | null;
  onMeetControl?: (action: MeetControlAction) => void;
}

export function SessionBar({
  onPause,
  onResume,
  onEnd,
  onToggleVideo,
  videoActive,
  meetControls = null,
  onMeetControl,
}: SessionBarProps) {
  const { status, level, elapsedMs, durationMinutes, currentQuestionIndex, totalQuestions, currentPhaseIndex } = useSessionStore();
  const plan = usePlanStore((s) => s.plan);
  const { wsStatus, degradationLevel, providerStatuses } = useOfflineStore();
  const [ending, setEnding] = useState(false);

  const handleEnd = async () => {
    if (ending) return;
    setEnding(true);
    try {
      await onEnd();
    } finally {
      setEnding(false);
    }
  };

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
        <PanesMenu />

        {meetControls?.available && onMeetControl && (
          <MeetControls controls={meetControls} onControl={onMeetControl} />
        )}

        {Object.entries(providerStatuses).map(([provider, s]) => (
          <span key={provider} className={`provider-badge provider-badge--${s}`}>
            {provider}
          </span>
        ))}

        <button
          className={`btn btn--secondary video-share-toggle ${videoActive ? 'video-share-toggle--active' : ''}`}
          onClick={onToggleVideo}
          title={videoActive ? 'Stop import' : 'Import meeting video'}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginRight: 4 }}>
            <rect x="1" y="3" width="10" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
            <path d="M11 6.5L14.5 4V12L11 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {videoActive ? 'Stop Import' : 'Import Video'}
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
          <button className="btn btn--danger" onClick={handleEnd} disabled={ending}>
            {ending ? 'Ending…' : 'End Interview'}
          </button>
        )}
      </div>
    </header>
  );
}

/** Show or hide each live-session pane. */
function PanesMenu() {
  const hidden = usePanelStore((s) => s.hidden);
  const setShown = usePanelStore((s) => s.setShown);
  const showAll = usePanelStore((s) => s.showAll);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const hiddenCount = PANES.filter((p) => hidden[p.id]).length;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="panes-menu" ref={ref}>
      <button
        type="button"
        className="btn btn--secondary btn--sm"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}
      >
        Panes{hiddenCount > 0 ? ` (${hiddenCount} hidden)` : ''} ▾
      </button>
      {open && (
        <div className="panes-menu__list" role="group" aria-label="Panes">
          {PANES.map((p) => (
            <label key={p.id} className="panes-menu__item">
              <input type="checkbox" checked={!hidden[p.id]} onChange={(e) => setShown(p.id, e.target.checked)} />
              {p.label}
            </label>
          ))}
          {hiddenCount > 0 && (
            <button type="button" className="btn btn--ghost btn--sm panes-menu__show-all" onClick={showAll}>
              Show all
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Mute, camera and present for the Meet tab. Meet's own state is shown; the buttons click Meet's buttons. */
function MeetControls({
  controls,
  onControl,
}: {
  controls: MeetControlsState;
  onControl: (action: MeetControlAction) => void;
}) {
  return (
    <div className="meet-controls" role="group" aria-label="Meeting controls">
      {controls.mic && (
        <button
          type="button"
          className={`btn btn--secondary btn--sm meet-controls__button ${controls.mic === 'off' ? 'meet-controls__button--off' : ''}`}
          aria-pressed={controls.mic === 'off'}
          title={controls.mic === 'on' ? 'Mute your microphone in Meet' : 'Unmute your microphone in Meet'}
          onClick={() => onControl('toggle-mic')}
        >
          {controls.mic === 'on' ? 'Mute' : 'Unmute'}
        </button>
      )}
      {controls.camera && (
        <button
          type="button"
          className={`btn btn--secondary btn--sm meet-controls__button ${controls.camera === 'off' ? 'meet-controls__button--off' : ''}`}
          aria-pressed={controls.camera === 'off'}
          title={controls.camera === 'on' ? 'Turn your camera off in Meet' : 'Turn your camera on in Meet'}
          onClick={() => onControl('toggle-camera')}
        >
          {controls.camera === 'on' ? 'Camera off' : 'Camera on'}
        </button>
      )}
      {(controls.canPresent || controls.presenting) && (
        <button
          type="button"
          className={`btn btn--secondary btn--sm meet-controls__button ${controls.presenting ? 'meet-controls__button--live' : ''}`}
          title={
            controls.presenting
              ? 'You are presenting in Meet; open Meet to stop'
              : "Opens Meet with its Share screen button highlighted (Chrome needs that click to happen in Meet)"
          }
          onClick={() => onControl('present')}
        >
          {controls.presenting ? 'Presenting' : 'Present'}
        </button>
      )}
    </div>
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
