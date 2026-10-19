/**
 * Live transcript panel — left side of dashboard.
 *
 * Shows real-time utterances with speaker color-coding,
 * inline AI insights with dimension pills, auto-scroll with manual scroll-back.
 * Speaker labels are clickable for inline reassignment.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranscriptStore, type TranscriptEntry } from '@/stores/transcript-store';
import { useSpeakerStore } from '@/stores/speaker-store';
import { useAnalysisStore, type DimensionScore, type InsightDimensions } from '@/stores/analysis-store';
import { useWSSend } from '@/contexts/WebSocketContext';
import { PanelTitle } from './PanelTitle';
import { usePaneHidden } from '@/stores/panel-store';

const SPEAKER_COLORS: Record<string, string> = {
  interviewer: 'var(--color-interviewer)',
  candidate: 'var(--color-candidate)',
  unknown: 'var(--color-unknown)',
};

export function TranscriptPanel() {
  const hidden = usePaneHidden('transcript');
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
    <section className="transcript-panel" hidden={hidden} aria-label="Live Transcript">
      <div className="transcript-panel__header panel-header">
        <PanelTitle id="transcript" level="h2">Transcript</PanelTitle>
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

      <InsightBar analysis={latestAnalysis} />
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
  const rootSpeakers = useSpeakerStore((s) => s.rootSpeakers);
  const send = useWSSend();
  const [showDropdown, setShowDropdown] = useState(false);

  // Look up the root speaker for color and label
  const rs = entry.rootSpeakerId ? rootSpeakers[entry.rootSpeakerId] : null;
  const speakerColor = rs ? rs.color : (SPEAKER_COLORS[entry.speakerRole] || SPEAKER_COLORS.unknown);
  const speakerLabel = rs
    ? rs.label
    : entry.speakerRole === 'interviewer'
      ? 'You'
      : entry.speakerRole;

  const handleReassign = (targetRsId: string) => {
    send({
      type: 'reassign_transcript',
      transcript_id: entry.id,
      source_sv_id: entry.speechVectorId,
      target_rs_id: targetRsId,
    });
    setShowDropdown(false);
  };

  const rsEntries = Object.values(rootSpeakers);

  return (
    <div
      className={`transcript-row ${isInterim ? 'transcript-row--interim' : ''} ${entry.isRetroactive ? 'transcript-row--retroactive' : ''}`}
    >
      <span
        className="transcript-row__speaker"
        style={{ color: speakerColor }}
        onClick={() => !isInterim && setShowDropdown(!showDropdown)}
        role={isInterim ? undefined : 'button'}
        tabIndex={isInterim ? undefined : 0}
        onKeyDown={(e) => !isInterim && e.key === 'Enter' && setShowDropdown(!showDropdown)}
      >
        {speakerLabel}
      </span>
      <span className="transcript-row__text">{entry.text}</span>
      {entry.isRetroactive && (
        <span className="transcript-row__badge">catch-up</span>
      )}

      {showDropdown && rsEntries.length > 0 && (
        <TranscriptReassignDropdown
          currentRsId={entry.rootSpeakerId}
          rootSpeakers={rsEntries}
          onSelect={handleReassign}
          onClose={() => setShowDropdown(false)}
        />
      )}
    </div>
  );
}

function TranscriptReassignDropdown({
  currentRsId,
  rootSpeakers,
  onSelect,
  onClose,
}: {
  currentRsId: string;
  rootSpeakers: Array<{ id: string; label: string; color: string }>;
  onSelect: (rsId: string) => void;
  onClose: () => void;
}) {
  const others = rootSpeakers.filter((rs) => rs.id !== currentRsId);

  return (
    <div className="transcript-row__reassign-dropdown" role="listbox">
      {others.map((rs) => (
        <button
          key={rs.id}
          className="transcript-row__reassign-option"
          onClick={(e) => {
            e.stopPropagation();
            onSelect(rs.id);
          }}
          role="option"
          aria-selected={false}
        >
          <span
            className="transcript-row__reassign-dot"
            style={{ backgroundColor: rs.color }}
          />
          {rs.label}
        </button>
      ))}
      <button
        className="transcript-row__reassign-option transcript-row__reassign-option--cancel"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      >
        Cancel
      </button>
    </div>
  );
}

function dimensionColor(score: DimensionScore): string {
  switch (score) {
    case 'excellent':
    case 'good':
      return 'green';
    case 'adequate':
      return 'amber';
    case 'weak':
    case 'poor':
      return 'red';
    default:
      return 'amber';
  }
}

const DIMENSION_LABELS: Array<{ key: keyof InsightDimensions; label: string }> = [
  { key: 'relevance', label: 'Relevance' },
  { key: 'behavioralSignal', label: 'Behavior' },
  { key: 'contextualConsistency', label: 'Consistency' },
  { key: 'hintReception', label: 'Hint' },
  { key: 'depthCompleteness', label: 'Depth' },
  { key: 'communicationQuality', label: 'Communication' },
];

function InsightBar({ analysis }: { analysis: { insight: string; answerQuality: number; onTrack: boolean; dimensions?: InsightDimensions | null } | null }) {
  if (!analysis) {
    return (
      <div className="insight-bar insight-bar--waiting" aria-live="polite">
        <span className="insight-bar__text">Waiting for candidate response to generate insights…</span>
      </div>
    );
  }

  const quality = analysis.answerQuality;
  const color = quality >= 4 ? 'green' : quality >= 3 ? 'amber' : 'red';

  return (
    <div className={`insight-bar insight-bar--${color}${!analysis.onTrack ? ' insight-bar--off-track' : ''}`} aria-live="polite">
      <span className="insight-bar__quality">{quality}/5</span>
      <span className="insight-bar__text">{analysis.insight}</span>
      {!analysis.onTrack && (
        <span className="insight-bar__badge">off-track</span>
      )}
      {analysis.dimensions && (
        <div className="insight-bar__dimensions">
          {DIMENSION_LABELS.map(({ key, label }) => {
            const dim = analysis.dimensions![key];
            if (!dim) return null;
            const score = (dim as { score: DimensionScore }).score;
            return (
              <span
                key={key}
                className={`dimension-pill dimension-pill--${dimensionColor(score)}`}
                title={`${label}: ${score} — ${(dim as { justification: string }).justification}`}
              >
                {label}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
