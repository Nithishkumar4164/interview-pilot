/**
 * Live red flags as the dashboard shows them, from the backend's `red_flag` messages.
 *
 * The backend sends three shapes on that channel:
 *   - analysis red flags:   { flags: string[], timestamp }
 *   - contradictions:       { type: "contradiction", claim, new_statement, explanation,
 *                             suggested_probe, confidence, timestamp }
 *   - code mismatches:      { type: "code_mismatch", explanation, suggested_probe, timestamp }
 */
export type FlagKind = 'red_flag' | 'contradiction' | 'code_mismatch';

export interface LiveFlag {
  id: string;
  kind: FlagKind;
  text: string;
  timestamp: string;
  claim?: string;
  newStatement?: string;
  probe?: string;
  confidence?: number;
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

export function flagsFromPayload(payload: Record<string, unknown>, fallbackTimestamp: string): LiveFlag[] {
  const timestamp = str(payload.timestamp) ?? fallbackTimestamp;
  const type = str(payload.type);

  if (type === 'contradiction' || type === 'code_mismatch') {
    return [
      {
        id: crypto.randomUUID(),
        kind: type,
        text: str(payload.explanation) ?? (type === 'contradiction' ? 'Contradiction detected' : 'Code mismatch'),
        timestamp,
        claim: str(payload.claim),
        newStatement: str(payload.new_statement),
        probe: str(payload.suggested_probe),
        confidence: typeof payload.confidence === 'number' ? payload.confidence : undefined,
      },
    ];
  }

  const flags = Array.isArray(payload.flags) ? payload.flags : [];
  return flags
    .map(str)
    .filter((text): text is string => text !== undefined)
    .map((text) => ({ id: crypto.randomUUID(), kind: 'red_flag' as const, text, timestamp }));
}
