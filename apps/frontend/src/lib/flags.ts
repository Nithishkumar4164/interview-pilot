/**
 * Live red flags as the dashboard shows them, from the backend's `red_flag` messages:
 * the flags an utterance analysis raised, `{ flags: string[], timestamp }`.
 *
 * Contradictions and code mismatches are copilot cards (`claim_check`, `code_check`).
 */
export interface LiveFlag {
  id: string;
  text: string;
  timestamp: string;
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

export function flagsFromPayload(payload: Record<string, unknown>, fallbackTimestamp: string): LiveFlag[] {
  const timestamp = str(payload.timestamp) ?? fallbackTimestamp;
  const flags = Array.isArray(payload.flags) ? payload.flags : [];
  return flags
    .map(str)
    .filter((text): text is string => text !== undefined)
    .map((text) => ({ id: crypto.randomUUID(), text, timestamp }));
}
