/**
 * Meeting controls on the session's Google Meet tab, driven from the dashboard.
 *
 * The service worker injects meetControls() into the Meet tab with
 * chrome.scripting.executeScript, so it must be self-contained (no imports,
 * no closures over module scope). It finds Meet's own buttons by their
 * accessible labels ("Turn off microphone", "Turn on camera", "Share screen")
 * and clicks them, so Meet's state stays the source of truth.
 *
 * Presenting can't be started this way: Chrome opens the screen-share picker
 * only after a real click inside Meet. 'highlight-share' points at Meet's
 * button instead, for the interviewer to click.
 */

export type MeetControlAction = 'read' | 'watch' | 'toggle-mic' | 'toggle-camera' | 'highlight-share';

export interface MeetControlsState {
  /** Meet's call controls were found on the page. */
  available: boolean;
  mic: 'on' | 'off' | null;
  camera: 'on' | 'off' | null;
  /** Meet shows this user presenting. */
  presenting: boolean;
  /** Meet's share-screen button was found. */
  canPresent: boolean;
}

export const MEET_CONTROLS_MESSAGE = 'meet-controls';

export function meetControls(action: MeetControlAction): MeetControlsState {
  const HIGHLIGHT_MS = 6000;

  const buttons = (): HTMLElement[] =>
    Array.from(document.querySelectorAll<HTMLElement>('button[aria-label], [role="button"][aria-label]'));

  const label = (el: HTMLElement) => el.getAttribute('aria-label') ?? '';

  /** Meet's on/off toggle for a device: its label reads "Turn on …" / "Turn off …". */
  const toggleFor = (device: RegExp): HTMLElement | null => {
    const matches = buttons().filter((el) => device.test(label(el)) && /turn (on|off)/i.test(label(el)));
    // In a call Meet marks its toggles with data-is-muted; prefer those over other matches.
    return matches.find((el) => el.hasAttribute('data-is-muted')) ?? matches[0] ?? null;
  };

  const stateOf = (el: HTMLElement | null): 'on' | 'off' | null => {
    if (!el) return null;
    const muted = el.getAttribute('data-is-muted');
    if (muted === 'true') return 'off';
    if (muted === 'false') return 'on';
    // "Turn on microphone" is offered while it is off.
    return /turn on/i.test(label(el)) ? 'off' : 'on';
  };

  const shareButton = (): HTMLElement | null =>
    buttons().find((el) => /share screen|present now/i.test(label(el))) ?? null;

  const read = (): MeetControlsState => {
    const mic = toggleFor(/microphone/i);
    const camera = toggleFor(/camera/i);
    const share = shareButton();
    const presenting =
      buttons().some((el) => /stop presenting|stop sharing/i.test(label(el))) ||
      share?.getAttribute('aria-pressed') === 'true';
    return {
      available: !!(mic || camera || share),
      mic: stateOf(mic),
      camera: stateOf(camera),
      presenting,
      canPresent: !!share,
    };
  };

  if (action === 'toggle-mic') toggleFor(/microphone/i)?.click();
  if (action === 'toggle-camera') toggleFor(/camera/i)?.click();

  if (action === 'highlight-share') {
    const share = shareButton();
    if (share) {
      share.scrollIntoView({ block: 'center' });
      share.focus();
      const previous = share.style.outline;
      share.style.outline = '3px solid #f59e0b';
      share.style.outlineOffset = '3px';
      setTimeout(() => {
        share.style.outline = previous;
      }, HIGHLIGHT_MS);
    }
  }

  if (action === 'watch') {
    // One observer per page; re-injecting after a reload starts a fresh one.
    const flagged = window as unknown as { __interviewPilotMeetWatch?: boolean };
    if (!flagged.__interviewPilotMeetWatch) {
      flagged.__interviewPilotMeetWatch = true;
      let last = '';
      let timer: ReturnType<typeof setTimeout> | undefined;
      const report = () => {
        const state = read();
        const key = JSON.stringify(state);
        if (key === last) return;
        last = key;
        chrome.runtime.sendMessage({ type: 'meet-controls', state }).catch(() => {});
      };
      new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(report, 150);
      }).observe(document.body, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['aria-label', 'data-is-muted', 'aria-pressed'],
      });
      report();
    }
  }

  return read();
}
