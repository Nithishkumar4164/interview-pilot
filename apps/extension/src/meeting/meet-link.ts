/**
 * Google Meet links. Mirrors normalize_meeting_url in the backend
 * (src/schemas/session.py), which stays the authority.
 */

const MEET_URL = /^(?:https:\/\/)?meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?$/;

/** The meeting code (abc-defg-hij) of a Meet meeting URL, or null for any other URL. */
export function meetingCode(url: string | undefined | null): string | null {
  if (!url) return null;
  const match = MEET_URL.exec(url.trim().toLowerCase());
  return match ? match[1] : null;
}

/** The canonical https://meet.google.com/<code> link, or null if it isn't a Meet meeting. */
export function canonicalMeetingUrl(url: string | undefined | null): string | null {
  const code = meetingCode(url);
  return code ? `https://meet.google.com/${code}` : null;
}
