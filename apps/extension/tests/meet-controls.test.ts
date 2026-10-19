// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { meetControls } from '../src/meeting/meet-controls';

/** Meet's in-call bar: toggles carry data-is-muted and flip their label when clicked. */
function inCallBar({ micOn = true, cameraOn = false } = {}) {
  document.body.innerHTML = `
    <button aria-label="Camera settings"></button>
    <button id="mic"></button>
    <button id="cam"></button>
    <button id="share" aria-label="Share screen" aria-pressed="false"></button>
    <button aria-label="Turn on captions"></button>`;
  const device = (id: string, name: string, on: boolean) => {
    const el = document.getElementById(id)!;
    const render = (isOn: boolean) => {
      el.setAttribute('data-is-muted', String(!isOn));
      el.setAttribute('aria-label', `Turn ${isOn ? 'off' : 'on'} ${name} (⌘ + d)`);
    };
    render(on);
    el.addEventListener('click', () => render(el.getAttribute('data-is-muted') === 'true'));
    return el;
  };
  return { mic: device('mic', 'microphone', micOn), cam: device('cam', 'camera', cameraOn) };
}

describe('Meet controls', () => {
  beforeEach(() => {
    (globalThis as { chrome?: unknown }).chrome = { runtime: { sendMessage: vi.fn(() => Promise.resolve()) } };
  });
  afterEach(() => {
    vi.useRealTimers();
    delete (window as { __interviewPilotMeetWatch?: boolean }).__interviewPilotMeetWatch;
  });

  it("reads Meet's mic, camera and share buttons", () => {
    inCallBar({ micOn: true, cameraOn: false });
    expect(meetControls('read')).toEqual({ available: true, mic: 'on', camera: 'off', presenting: false, canPresent: true });
  });

  it("toggles by clicking Meet's own buttons", () => {
    inCallBar({ micOn: true, cameraOn: false });
    expect(meetControls('toggle-mic')).toMatchObject({ mic: 'off', camera: 'off' });
    expect(meetControls('toggle-camera')).toMatchObject({ mic: 'off', camera: 'on' });
    expect(meetControls('toggle-mic')).toMatchObject({ mic: 'on' });
  });

  it('reads the pre-join screen, whose toggles have only labels', () => {
    document.body.innerHTML = `
      <div role="button" aria-label="Turn on microphone"></div>
      <div role="button" aria-label="Turn off camera"></div>`;
    expect(meetControls('read')).toEqual({ available: true, mic: 'off', camera: 'on', presenting: false, canPresent: false });
  });

  it('knows when this user is presenting', () => {
    inCallBar();
    document.body.insertAdjacentHTML('beforeend', '<button aria-label="Stop presenting"></button>');
    expect(meetControls('read').presenting).toBe(true);
  });

  it("is unavailable on a page without Meet's controls", () => {
    document.body.innerHTML = '<p>Ready to join?</p>';
    expect(meetControls('read')).toEqual({ available: false, mic: null, camera: null, presenting: false, canPresent: false });
  });

  it('points at the share button rather than clicking it', () => {
    inCallBar();
    const share = document.getElementById('share')!;
    const click = vi.fn();
    share.addEventListener('click', click);
    share.scrollIntoView = vi.fn();
    meetControls('highlight-share');
    expect(click).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(share);
    expect(share.style.outline).toContain('solid');
  });

  it('reports each change once, from one watcher per page', async () => {
    vi.useFakeTimers();
    const { mic } = inCallBar({ micOn: true });
    meetControls('watch');
    meetControls('watch'); // re-injected on the same page: still one observer
    const send = (globalThis as unknown as { chrome: { runtime: { sendMessage: ReturnType<typeof vi.fn> } } }).chrome
      .runtime.sendMessage;
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({ type: 'meet-controls', state: { mic: 'on' } });

    mic.click(); // the interviewer mutes inside Meet itself
    await vi.advanceTimersByTimeAsync(200);
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1][0]).toMatchObject({ state: { mic: 'off' } });

    document.body.setAttribute('data-unrelated', 'x'); // no state change: nothing sent
    await vi.advanceTimersByTimeAsync(200);
    expect(send).toHaveBeenCalledTimes(2);
  });
});
