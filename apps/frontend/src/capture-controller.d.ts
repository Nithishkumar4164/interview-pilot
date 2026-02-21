/**
 * Screen Capture API — CaptureController
 * Not yet in TypeScript's default DOM lib.
 * See: https://developer.mozilla.org/en-US/docs/Web/API/CaptureController
 */
declare class CaptureController {
  constructor();
  setFocusBehavior(focusBehavior: 'focus-captured-surface' | 'no-focus-change'): void;
}
