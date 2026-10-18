import { describe, expect, it } from 'vitest';
import { canonicalMeetingUrl, meetingCode } from '../src/meeting/meet-link';

describe('Meet links', () => {
  it.each([
    ['https://meet.google.com/abc-defg-hij', 'abc-defg-hij'],
    ['https://meet.google.com/abc-defg-hij?authuser=1', 'abc-defg-hij'],
    ['https://meet.google.com/ABC-DEFG-HIJ/', 'abc-defg-hij'],
    ['meet.google.com/abc-defg-hij#x', 'abc-defg-hij'],
  ])('reads the meeting code of %s', (url, code) => {
    expect(meetingCode(url)).toBe(code);
    expect(canonicalMeetingUrl(url)).toBe(`https://meet.google.com/${code}`);
  });

  it.each([
    'https://meet.google.com/',
    'https://meet.google.com/landing',
    'http://meet.google.com/abc-defg-hij',
    'https://meet.google.com.evil.test/abc-defg-hij',
    'https://zoom.us/j/123',
    '',
    undefined,
    null,
  ])('is not a meeting: %s', (url) => {
    expect(meetingCode(url)).toBeNull();
    expect(canonicalMeetingUrl(url)).toBeNull();
  });
});
