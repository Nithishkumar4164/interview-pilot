/**
 * Per-question countdown timer.
 *
 * Shows remaining time for the current question based on its time_minutes
 * allocation. Flips to an overtime indicator when exceeded.
 */
import { useSessionStore } from '@/stores/session-store';

function formatQTime(ms: number): string {
  const totalSecs = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(totalSecs / 60);
  const s = totalSecs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

interface QuestionTimerProps {
  timeMinutes: number;
}

export function QuestionTimer({ timeMinutes }: QuestionTimerProps) {
  const elapsedMs = useSessionStore((s) => s.elapsedMs);
  const questionStartedAtMs = useSessionStore((s) => s.questionStartedAtMs);

  const questionElapsedMs = elapsedMs - questionStartedAtMs;
  const questionTotalMs = timeMinutes * 60 * 1000;
  const remainingMs = questionTotalMs - questionElapsedMs;
  const isOvertime = remainingMs < 0;

  return (
    <div className={`question-timer ${isOvertime ? 'question-timer--overtime' : ''}`}>
      <span className="question-timer__label">
        {isOvertime ? 'Overtime' : 'Remaining'}
      </span>
      <span className="question-timer__value">
        {isOvertime ? `+${formatQTime(-remainingMs)}` : formatQTime(remainingMs)}
      </span>
    </div>
  );
}
