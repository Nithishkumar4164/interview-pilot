/**
 * AnnotatedTranscript — collapsible transcript viewer with inline annotation badges.
 */
import { useState } from 'react';

interface Annotation {
  type: string;
  text: string;
}

interface TranscriptEntry {
  timestamp: string;
  speaker: string;
  text: string;
  annotations: Annotation[];
}

interface AnnotatedTranscriptProps {
  entries: TranscriptEntry[];
}

const ANNOTATION_COLORS: Record<string, string> = {
  analysis: 'var(--color-primary, #6366f1)',
  red_flag: 'var(--color-danger, #ef4444)',
  hint: 'var(--color-warning, #f59e0b)',
  contradiction: 'var(--color-danger, #dc2626)',
  code_milestone: 'var(--color-success, #10b981)',
};

const SPEAKER_COLORS: Record<string, string> = {
  interviewer: 'var(--color-speaker-interviewer, #3b82f6)',
  candidate: 'var(--color-speaker-candidate, #8b5cf6)',
  system: 'var(--color-text-secondary, #6b7280)',
};

export function AnnotatedTranscript({ entries }: AnnotatedTranscriptProps) {
  const [expanded, setExpanded] = useState(false);

  if (!entries || entries.length === 0) {
    return null;
  }

  const annotatedCount = entries.filter((e) => e.annotations.length > 0).length;

  return (
    <div className="annotated-transcript">
      <button
        className="annotated-transcript__toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span>{expanded ? '\u25BC' : '\u25B6'}</span>
        <span>
          Annotated Transcript ({entries.length} entries, {annotatedCount} annotated)
        </span>
      </button>

      {expanded && (
        <div className="annotated-transcript__content">
          {entries.map((entry, i) => (
            <div key={i} className="annotated-transcript__entry">
              <div className="annotated-transcript__meta">
                <time className="annotated-transcript__time">{entry.timestamp}</time>
                <span
                  className="annotated-transcript__speaker"
                  style={{ color: SPEAKER_COLORS[entry.speaker] ?? SPEAKER_COLORS.system }}
                >
                  {entry.speaker}
                </span>
              </div>

              <p className="annotated-transcript__text">{entry.text}</p>

              {entry.annotations.length > 0 && (
                <div className="annotated-transcript__annotations">
                  {entry.annotations.map((ann, j) => (
                    <span
                      key={j}
                      className={`annotated-transcript__annotation annotated-transcript__annotation--${ann.type}`}
                      style={{
                        borderLeftColor: ANNOTATION_COLORS[ann.type] ?? 'var(--color-border)',
                      }}
                    >
                      <strong>{ann.type.replace(/_/g, ' ')}</strong>: {ann.text}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
