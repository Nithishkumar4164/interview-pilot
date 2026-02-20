/**
 * Live transcript panel — left side of dashboard.
 *
 * Shows real-time utterances with speaker color-coding,
 * inline AI insights, auto-scroll with manual scroll-back.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranscriptStore, type TranscriptEntry } from '@/stores/transcript-store';
import { useAnalysisStore } from '@/stores/analysis-store';

const SPEAKER_COLORS: Record<string, string> = {
  interviewer: 'var(--color-interviewer)',
  candidate: 'var(--color-candidate)',
  unknown: 'var(--color-unknown)',
};

export function TranscriptPanel() {
  const { entries, interimEntry } = useTranscriptStore();
  const { latestAnalysis } = useAnalysisStore();
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Auto-scroll to bottom when new entries arrive
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [entries.length, interimEntry, autoScroll]);

  // Detect manual scroll-back
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 80;
    setAutoScroll(isNearBottom);
  };

  return (
    <section className="transcript-panel" aria-label="Live Transcript">
      <div className="transcript-panel__header">
        <h2>Transcript</h2>
        {!autoScroll && (
          <button
            className="btn btn--small"
            onClick={() => {
              setAutoScroll(true);
              bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
            }}
          >
            Scroll to bottom
          </button>
        )}
      </div>

      <div
        className="transcript-panel__entries"
        ref={containerRef}
        onScroll={handleScroll}
        role="log"
        aria-live="polite"
      >
        {entries.map((entry) => (
          <TranscriptRow key={entry.id} entry={entry} />
        ))}
        {interimEntry && (
          <TranscriptRow entry={interimEntry} isInterim />
        )}
        <div ref={bottomRef} />
      </div>

      {latestAnalysis && (
        <InsightBar analysis={latestAnalysis} />
      )}
    </section>
  );
}

function TranscriptRow({
  entry,
  isInterim = false,
}: {
  entry: TranscriptEntry;
  isInterim?: boolean;
}) {
  const speakerColor = SPEAKER_COLORS[entry.speakerRole] || SPEAKER_COLORS.unknown;

  return (
    <div
      className={`transcript-row ${isInterim ? 'transcript-row--interim' : ''} ${entry.isRetroactive ? 'transcript-row--retroactive' : ''}`}
    >
      <span
        className="transcript-row__speaker"
        style={{ color: speakerColor }}
      >
        {entry.speakerRole === 'interviewer' ? 'You' : entry.speakerRole}
      </span>
      <span className="transcript-row__text">{entry.text}</span>
      {entry.isRetroactive && (
        <span className="transcript-row__badge">catch-up</span>
      )}
    </div>
  );
}

function InsightBar({ analysis }: { analysis: { insight: string; answerQuality: number; onTrack: boolean } }) {
  const quality = analysis.answerQuality;
  const color = quality >= 4 ? 'green' : quality >= 3 ? 'amber' : 'red';

  return (
    <div className={`insight-bar insight-bar--${color}`} aria-live="polite">
      <span className="insight-bar__quality">{quality}/5</span>
      <span className="insight-bar__text">{analysis.insight}</span>
      {!analysis.onTrack && (
        <span className="insight-bar__badge">off-track</span>
      )}
    </div>
  );
}
