/**
 * The "press the shortcut to start capture" banner on the session's Meet tab.
 *
 * Chrome only allows tab capture after the user invokes the extension on that
 * tab, so the banner can't start capture itself; it tells the interviewer which
 * keys to press. Both functions are injected with chrome.scripting.executeScript,
 * so each must be self-contained (no imports, no closures).
 */

export const CAPTURE_BANNER_ID = 'interviewpilot-capture-banner';

export function injectCaptureBanner(bannerId: string): void {
  if (document.getElementById(bannerId)) return;
  const isMac = /Mac/i.test(navigator.platform) || /Mac OS X/i.test(navigator.userAgent);
  const keys = isMac ? 'Option + Shift + I' : 'Alt + Shift + I';

  const banner = document.createElement('div');
  banner.id = bannerId;
  banner.setAttribute('role', 'status');
  Object.assign(banner.style, {
    position: 'fixed',
    top: '16px',
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: '2147483647',
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '10px 14px',
    borderRadius: '10px',
    background: '#1e1b4b',
    color: '#ffffff',
    font: '500 14px/1.4 system-ui, -apple-system, sans-serif',
    boxShadow: '0 8px 24px rgba(0, 0, 0, 0.35)',
  });

  const text = document.createElement('span');
  text.append('InterviewPilot: press ');
  const kbd = document.createElement('kbd');
  kbd.textContent = keys;
  Object.assign(kbd.style, {
    padding: '2px 6px',
    borderRadius: '4px',
    background: '#4338ca',
    font: '600 13px/1.4 ui-monospace, monospace',
  });
  text.append(kbd, ' to start capturing this interview');

  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = '×';
  close.setAttribute('aria-label', 'Dismiss');
  Object.assign(close.style, {
    border: '0',
    background: 'transparent',
    color: '#c7d2fe',
    font: '600 18px/1 system-ui, sans-serif',
    cursor: 'pointer',
    padding: '0 2px',
  });
  close.addEventListener('click', () => banner.remove());

  banner.append(text, close);
  document.body.append(banner);
}

export function removeCaptureBanner(bannerId: string): void {
  document.getElementById(bannerId)?.remove();
}
