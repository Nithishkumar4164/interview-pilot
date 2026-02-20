/**
 * Code Editor DOM Scraper — Content Script
 *
 * Detects and scrapes code from:
 * - CodeMirror 5 (.CodeMirror)
 * - CodeMirror 6 (.cm-editor)
 * - Monaco Editor (.monaco-editor)
 * - Ace Editor (.ace_editor)
 * - Textarea fallback
 *
 * Uses MutationObserver + change listeners with 500ms debounce.
 * Sends code snapshots to the background service worker via chrome.runtime.
 */

import { detectLanguage } from '../utils/language-detect';

interface CodeSnapshot {
  code: string;
  language: string;
  timestamp: number;
  source: 'codemirror5' | 'codemirror6' | 'monaco' | 'ace' | 'textarea';
  diff: string | null;
}

type EditorSource = CodeSnapshot['source'];

let lastCode = '';
let lastSnapshotTime = 0;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
const DEBOUNCE_MS = 500;
const MIN_CHANGE_LENGTH = 2; // Ignore trivial changes

// ── Editor Detection ──────────────────────────────────────────────────────

function findCodeMirror5(): { code: string; source: EditorSource } | null {
  const el = document.querySelector('.CodeMirror') as HTMLElement | null;
  if (!el) return null;
  // CodeMirror 5 attaches the editor instance to the DOM element
  const cm = (el as any).CodeMirror;
  if (cm && typeof cm.getValue === 'function') {
    return { code: cm.getValue(), source: 'codemirror5' };
  }
  return null;
}

function findCodeMirror6(): { code: string; source: EditorSource } | null {
  const el = document.querySelector('.cm-editor') as HTMLElement | null;
  if (!el) return null;
  // CodeMirror 6 uses EditorView attached via cmView
  const view = (el as any).cmView?.view;
  if (view && view.state) {
    return { code: view.state.doc.toString(), source: 'codemirror6' };
  }
  // Fallback: read from the content element
  const content = el.querySelector('.cm-content');
  if (content) {
    return { code: content.textContent || '', source: 'codemirror6' };
  }
  return null;
}

function findMonaco(): { code: string; source: EditorSource } | null {
  const el = document.querySelector('.monaco-editor') as HTMLElement | null;
  if (!el) return null;
  // Monaco exposes models via the global monaco object
  const monacoGlobal = (window as any).monaco;
  if (monacoGlobal?.editor) {
    const editors = monacoGlobal.editor.getEditors?.() || [];
    if (editors.length > 0) {
      const model = editors[0].getModel();
      if (model) {
        return { code: model.getValue(), source: 'monaco' };
      }
    }
  }
  // Fallback: read lines from DOM
  const lines = el.querySelectorAll('.view-line');
  if (lines.length > 0) {
    const text = Array.from(lines).map((l) => l.textContent || '').join('\n');
    return { code: text, source: 'monaco' };
  }
  return null;
}

function findAce(): { code: string; source: EditorSource } | null {
  const el = document.querySelector('.ace_editor') as HTMLElement | null;
  if (!el) return null;
  const aceEditor = (el as any).env?.editor;
  if (aceEditor && typeof aceEditor.getValue === 'function') {
    return { code: aceEditor.getValue(), source: 'ace' };
  }
  return null;
}

function findTextarea(): { code: string; source: EditorSource } | null {
  // Look for textareas that likely contain code
  const textareas = document.querySelectorAll('textarea');
  for (const ta of textareas) {
    if (ta.value.length > 20 && (
      ta.classList.contains('code') ||
      ta.closest('[class*="code"]') ||
      ta.closest('[class*="editor"]') ||
      ta.getAttribute('data-mode') ||
      ta.spellcheck === false
    )) {
      return { code: ta.value, source: 'textarea' };
    }
  }
  // Fallback: largest textarea on the page
  let largest: HTMLTextAreaElement | null = null;
  let maxLen = 20;
  for (const ta of textareas) {
    if (ta.value.length > maxLen) {
      maxLen = ta.value.length;
      largest = ta;
    }
  }
  if (largest) {
    return { code: largest.value, source: 'textarea' };
  }
  return null;
}

function scrapeCode(): { code: string; source: EditorSource } | null {
  // Try editors in order of specificity
  return findCodeMirror5() || findCodeMirror6() || findMonaco() || findAce() || findTextarea();
}

// ── Diff Computation ──────────────────────────────────────────────────────

function computeSimpleDiff(oldCode: string, newCode: string): string | null {
  if (oldCode === newCode) return null;
  if (!oldCode) return '+' + newCode.length + ' chars (initial)';

  const oldLines = oldCode.split('\n');
  const newLines = newCode.split('\n');

  let added = 0;
  let removed = 0;
  let changed = 0;

  const maxLen = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < maxLen; i++) {
    const oldLine = oldLines[i];
    const newLine = newLines[i];
    if (oldLine === undefined && newLine !== undefined) {
      added++;
    } else if (oldLine !== undefined && newLine === undefined) {
      removed++;
    } else if (oldLine !== newLine) {
      changed++;
    }
  }

  const parts: string[] = [];
  if (added > 0) parts.push(`+${added}`);
  if (removed > 0) parts.push(`-${removed}`);
  if (changed > 0) parts.push(`~${changed}`);

  return parts.join(' ') + ' lines';
}

// ── Snapshot Dispatch ─────────────────────────────────────────────────────

function dispatchSnapshot(code: string, source: EditorSource): void {
  const diff = computeSimpleDiff(lastCode, code);
  if (diff === null) return; // No actual change

  const snapshot: CodeSnapshot = {
    code,
    language: detectLanguage(code),
    timestamp: Date.now(),
    source,
    diff,
  };

  lastCode = code;
  lastSnapshotTime = snapshot.timestamp;

  // Send to the background service worker
  chrome.runtime.sendMessage({
    type: 'code-snapshot',
    payload: snapshot,
  }).catch(() => {
    // Extension context might be invalidated
  });
}

function onCodeChange(): void {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
  }

  debounceTimer = setTimeout(() => {
    const result = scrapeCode();
    if (!result) return;

    const { code, source } = result;

    // Skip if the change is trivial
    if (Math.abs(code.length - lastCode.length) < MIN_CHANGE_LENGTH && code === lastCode) {
      return;
    }

    dispatchSnapshot(code, source);
  }, DEBOUNCE_MS);
}

// ── Observer Setup ────────────────────────────────────────────────────────

let observer: MutationObserver | null = null;

function setupObserver(): void {
  if (observer) {
    observer.disconnect();
  }

  // Watch for DOM changes in the editor area
  const editorContainers = [
    '.CodeMirror',
    '.cm-editor',
    '.monaco-editor',
    '.ace_editor',
    '[class*="editor"]',
    '[class*="code"]',
  ];

  const targets: Element[] = [];
  for (const selector of editorContainers) {
    const els = document.querySelectorAll(selector);
    els.forEach((el) => targets.push(el));
  }

  if (targets.length === 0) {
    // No editor found yet — observe the body for editor injection
    observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (node instanceof HTMLElement) {
            if (
              node.classList?.contains('CodeMirror') ||
              node.classList?.contains('cm-editor') ||
              node.classList?.contains('monaco-editor') ||
              node.classList?.contains('ace_editor') ||
              node.querySelector('.CodeMirror, .cm-editor, .monaco-editor, .ace_editor')
            ) {
              // Editor appeared — restart observer targeting it
              setupObserver();
              return;
            }
          }
        }
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    return;
  }

  observer = new MutationObserver(() => {
    onCodeChange();
  });

  for (const target of targets) {
    observer.observe(target, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  // Also listen for input events on textareas
  document.querySelectorAll('textarea').forEach((ta) => {
    ta.addEventListener('input', onCodeChange);
  });

  // Do an initial scrape
  const result = scrapeCode();
  if (result && result.code.trim()) {
    dispatchSnapshot(result.code, result.source);
  }
}

// ── Initialization ────────────────────────────────────────────────────────

// Listen for messages from the background to control scraping
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'scrape-now') {
    const result = scrapeCode();
    sendResponse(result ? { code: result.code, source: result.source, language: detectLanguage(result.code) } : null);
    return true;
  }

  if (message.type === 'start-scraping') {
    setupObserver();
    sendResponse({ status: 'started' });
    return true;
  }

  if (message.type === 'stop-scraping') {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    sendResponse({ status: 'stopped' });
    return true;
  }

  return false;
});

// Auto-start when injected
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupObserver);
} else {
  setupObserver();
}
