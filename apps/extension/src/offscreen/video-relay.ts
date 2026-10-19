/**
 * Meeting-tab video for the live session page.
 *
 * Chrome lets a tab be captured once, so the offscreen document captures
 * audio and video together and keeps the video here. Each dashboard page
 * (a "viewer") gets it over a local WebRTC connection; the service worker
 * relays the offer and the answer between the two.
 */

// No ICE servers: both ends run in this browser.
const ICE_GATHERING_TIMEOUT_MS = 2000;

const peers = new Map<string, RTCPeerConnection>();

function iceGatheringComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(done, ICE_GATHERING_TIMEOUT_MS);
    function done(): void {
      clearTimeout(timer);
      pc.removeEventListener('icegatheringstatechange', check);
      resolve();
    }
    function check(): void {
      if (pc.iceGatheringState === 'complete') done();
    }
    pc.addEventListener('icegatheringstatechange', check);
  });
}

export function closeVideoPeer(viewerId: string): void {
  peers.get(viewerId)?.close();
  peers.delete(viewerId);
}

export function closeAllVideoPeers(): void {
  for (const viewerId of [...peers.keys()]) closeVideoPeer(viewerId);
}

/** Offer the tab's video to a viewer; the offer goes back to the service worker. */
export async function offerVideo(viewerId: string, stream: MediaStream | null): Promise<void> {
  const track = stream?.getVideoTracks()[0];
  if (!stream || !track || track.readyState === 'ended') return;

  closeVideoPeer(viewerId);
  const pc = new RTCPeerConnection();
  peers.set(viewerId, pc);
  pc.addEventListener('connectionstatechange', () => {
    if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
      if (peers.get(viewerId) === pc) closeVideoPeer(viewerId);
    }
  });
  pc.addTrack(track, stream);

  await pc.setLocalDescription(await pc.createOffer());
  await iceGatheringComplete(pc);
  chrome.runtime.sendMessage({ type: 'tab-video-offer', viewerId, sdp: pc.localDescription?.sdp });
}

export async function acceptVideoAnswer(viewerId: string, sdp: string): Promise<void> {
  const pc = peers.get(viewerId);
  if (!pc) return;
  await pc.setRemoteDescription({ type: 'answer', sdp });
}
