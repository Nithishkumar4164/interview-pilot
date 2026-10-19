/**
 * Google Meet links. Mirrors normalize_meeting_url in the backend
 * (src/schemas/session.py), which stays the authority.
 */

const MEET_URL = /^(?:https:\/\/)?meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?$/;

/** The canonical https://meet.google.com/<code> link, null for a blank value, or undefined if it isn't a Meet link. */
export function normalizeMeetingUrl(raw: string): string | null | undefined {
  const value = raw.trim().toLowerCase();
  if (!value) return null;
  const match = MEET_URL.exec(value);
  return match ? `https://meet.google.com/${match[1]}` : undefined;
}

export const MEETING_URL_HINT = 'A Google Meet link, like https://meet.google.com/abc-defg-hij';
