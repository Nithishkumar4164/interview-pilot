/**
 * Score rings — animated circular score displays for 4 criteria.
 *
 * Problem Solving, Communication, Code Quality, Hint Responsiveness
 */
import { useAnalysisStore } from '@/stores/analysis-store';

interface ScoreRingProps {
  label: string;
  score: number; // 0-5
  maxScore?: number;
  color: string;
}

const RING_SIZE = 64;
const RING_STROKE = 6;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

function ScoreRing({ label, score, maxScore = 5, color }: ScoreRingProps) {
  const pct = maxScore > 0 ? score / maxScore : 0;
  const offset = RING_CIRCUMFERENCE * (1 - pct);

  return (
    <div className="score-ring" aria-label={`${label}: ${score} out of ${maxScore}`}>
      <svg width={RING_SIZE} height={RING_SIZE} className="score-ring__svg">
        {/* Background ring */}
        <circle
          cx={RING_SIZE / 2}
          cy={RING_SIZE / 2}
          r={RING_RADIUS}
          fill="none"
          stroke="var(--color-ring-bg)"
          strokeWidth={RING_STROKE}
        />
        {/* Foreground ring */}
        <circle
          cx={RING_SIZE / 2}
          cy={RING_SIZE / 2}
          r={RING_RADIUS}
          fill="none"
          stroke={color}
          strokeWidth={RING_STROKE}
          strokeDasharray={RING_CIRCUMFERENCE}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="score-ring__progress"
          transform={`rotate(-90 ${RING_SIZE / 2} ${RING_SIZE / 2})`}
        />
      </svg>
      <div className="score-ring__value">
        {score > 0 ? score.toFixed(1) : '—'}
      </div>
      <div className="score-ring__label">{label}</div>
    </div>
  );
}

export function ScoreRings() {
  const { scores } = useAnalysisStore();

  return (
    <div className="score-rings" aria-label="Performance Scores">
      <ScoreRing
        label="Problem Solving"
        score={scores.problemSolving}
        color="var(--color-score-ps)"
      />
      <ScoreRing
        label="Communication"
        score={scores.communication}
        color="var(--color-score-comm)"
      />
      <ScoreRing
        label="Code Quality"
        score={scores.codeQuality}
        color="var(--color-score-cq)"
      />
      <ScoreRing
        label="Hint Response"
        score={scores.hintResponsiveness}
        color="var(--color-score-hint)"
      />
    </div>
  );
}
