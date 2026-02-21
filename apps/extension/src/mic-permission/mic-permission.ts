const statusEl = document.getElementById('status') as HTMLDivElement;

navigator.mediaDevices.getUserMedia({ audio: true })
  .then((stream) => {
    stream.getTracks().forEach((t) => t.stop());
    statusEl.textContent = 'Permission granted! This tab will close...';
    statusEl.className = 'success';
    chrome.runtime.sendMessage({ type: 'mic-permission-granted' });
    setTimeout(() => window.close(), 800);
  })
  .catch((err) => {
    statusEl.textContent = 'Permission denied. Please try again.';
    statusEl.className = 'error';
    console.error('[MicPermission] getUserMedia failed:', err);
    chrome.runtime.sendMessage({ type: 'mic-permission-denied' });
  });

export {}; // Ensure this is treated as a module
