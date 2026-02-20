/**
 * Format milliseconds into a human-readable duration string.
 * e.g., 125000 → "2m 5s"
 */
export function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

/**
 * Format an ISO timestamp to a short time string (HH:MM:SS).
 */
export function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  return date.toTimeString().slice(0, 8);
}
