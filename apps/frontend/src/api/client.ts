/**
 * REST API client for InterviewPilot backend.
 *
 * All endpoints go through the Vite proxy (/api → backend:8000).
 */

class APIError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    message: string,
  ) {
    super(message);
    this.name = 'APIError';
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!res.ok) {
    const body = await res.text();
    throw new APIError(res.status, res.statusText, body);
  }

  return res.json();
}

// ── Sessions ──────────────────────────────────────────────────────────

export interface StartSessionRequest {
  plan_id?: string;
  level?: string;
  duration_minutes?: number;
}

export async function startSession(data: StartSessionRequest) {
  return request<{ session_id: string }>('/api/sessions/start', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function pauseSession(sessionId: string) {
  return request(`/api/sessions/${sessionId}/pause`, { method: 'PATCH' });
}

export async function endSession(sessionId: string) {
  return request(`/api/sessions/${sessionId}/end`, { method: 'POST' });
}

export async function getSession(sessionId: string) {
  return request(`/api/sessions/${sessionId}`);
}

// ── Hints ─────────────────────────────────────────────────────────────

export async function triggerNextHint(sessionId: string, questionId: string) {
  return request(`/api/sessions/${sessionId}/hints/next`, {
    method: 'POST',
    body: JSON.stringify({ question_id: questionId }),
  });
}

export async function acknowledgeHint(
  sessionId: string,
  hintId: string,
  data: { interviewer_used: boolean; candidate_response_quality: string },
) {
  return request(`/api/sessions/${sessionId}/hints/${hintId}/ack`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// ── Reports ───────────────────────────────────────────────────────────

export async function getReport(reportId: string) {
  return request(`/api/reports/${reportId}`);
}

export async function addReportNote(reportId: string, noteText: string) {
  return request(`/api/reports/${reportId}/notes`, {
    method: 'POST',
    body: JSON.stringify({ note_text: noteText }),
  });
}

// ── Health ────────────────────────────────────────────────────────────

export async function checkHealth() {
  return request<{ status: string }>('/health');
}

export async function checkProviderHealth() {
  return request<{ providers: Record<string, unknown> }>('/health/providers');
}
