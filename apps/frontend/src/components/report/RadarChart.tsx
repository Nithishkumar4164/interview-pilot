/**
 * RadarChart — custom SVG radar/spider chart for 7-criteria scoring.
 *
 * Props:
 *   scores: Array<{ criterion: string; score: number }>
 *   size?: number (default 300)
 *   overlays?: additional score sets for comparison mode
 */
import { useMemo } from 'react';

interface Score {
  criterion: string;
  score: number;
}

interface RadarChartProps {
  scores: Score[];
  size?: number;
  overlays?: Score[][];
}

const COLORS = [
  'var(--color-primary, #6366f1)',
  'var(--color-accent-2, #f59e0b)',
  'var(--color-accent-3, #10b981)',
  'var(--color-accent-4, #ef4444)',
];

function formatLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function RadarChart({ scores, size = 300, overlays }: RadarChartProps) {
  const n = scores.length;
  const cx = size / 2;
  const cy = size / 2;
  const maxR = size / 2 - 40;
  const angleStep = (2 * Math.PI) / n;

  const gridLevels = [1, 2, 3, 4, 5];

  const getPoint = (index: number, value: number): [number, number] => {
    const angle = angleStep * index - Math.PI / 2;
    const r = (value / 5) * maxR;
    return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
  };

  const buildPolygon = (scoreList: Score[]): string => {
    return scoreList
      .map((_, i) => {
        const s = scoreList[i]?.score ?? 0;
        const [x, y] = getPoint(i, s);
        return `${x},${y}`;
      })
      .join(' ');
  };

  const gridPolygons = useMemo(
    () =>
      gridLevels.map((level) => {
        const points = Array.from({ length: n }, (_, i) => {
          const [x, y] = getPoint(i, level);
          return `${x},${y}`;
        }).join(' ');
        return { level, points };
      }),
    [n, size],
  );

  const axes = useMemo(
    () =>
      scores.map((s, i) => {
        const [x, y] = getPoint(i, 5);
        const [lx, ly] = getPoint(i, 5.8);
        return { x, y, lx, ly, label: formatLabel(s.criterion) };
      }),
    [scores, size],
  );

  const mainPolygon = buildPolygon(scores);

  return (
    <div className="radar-chart">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label="Radar chart showing assessment scores"
      >
        {/* Grid lines */}
        {gridPolygons.map(({ level, points }) => (
          <polygon
            key={level}
            points={points}
            fill="none"
            stroke="var(--color-border, #374151)"
            strokeWidth={level === 5 ? 1.5 : 0.5}
            opacity={0.4}
          />
        ))}

        {/* Axes */}
        {axes.map((axis, i) => (
          <line
            key={i}
            x1={cx}
            y1={cy}
            x2={axis.x}
            y2={axis.y}
            stroke="var(--color-border, #374151)"
            strokeWidth={0.5}
            opacity={0.3}
          />
        ))}

        {/* Overlay polygons (comparison mode) */}
        {overlays?.map((overlay, oi) => (
          <polygon
            key={`overlay-${oi}`}
            points={buildPolygon(overlay)}
            fill={COLORS[(oi + 1) % COLORS.length]}
            fillOpacity={0.1}
            stroke={COLORS[(oi + 1) % COLORS.length]}
            strokeWidth={2}
            strokeOpacity={0.6}
          />
        ))}

        {/* Main scored polygon */}
        <polygon
          points={mainPolygon}
          fill={COLORS[0]}
          fillOpacity={0.2}
          stroke={COLORS[0]}
          strokeWidth={2}
        />

        {/* Score dots */}
        {scores.map((s, i) => {
          const [x, y] = getPoint(i, s.score);
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={4}
              fill={COLORS[0]}
            />
          );
        })}

        {/* Axis labels */}
        {axes.map((axis, i) => {
          const score = scores[i]?.score ?? 0;
          return (
            <text
              key={i}
              x={axis.lx}
              y={axis.ly}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={11}
              fill="var(--color-text-secondary, #9ca3af)"
              fontFamily="var(--font-sans)"
            >
              {axis.label} ({score})
            </text>
          );
        })}
      </svg>
    </div>
  );
}
