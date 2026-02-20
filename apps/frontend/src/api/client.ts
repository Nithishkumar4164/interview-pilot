/**
 * REST API client for InterviewPilot backend.
 *
 * All endpoints go through the Vite proxy (/api → backend:8000).
 */

export class APIError extends Error {
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
  focus_areas?: string[];
}

export async function startSession(data: StartSessionRequest) {
  return request<{ session_id: string }>('/api/sessions/start', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getSession(sessionId: string) {
  return request<Record<string, unknown>>(`/api/sessions/${sessionId}`);
}

export async function goLive(sessionId: string) {
  return request<{ status: string; session_id: string; started_at: string }>(
    `/api/sessions/${sessionId}/go-live`,
    { method: 'POST' },
  );
}

export async function pauseSession(sessionId: string) {
  return request(`/api/sessions/${sessionId}/pause`, { method: 'PATCH' });
}

export async function endSession(sessionId: string) {
  return request(`/api/sessions/${sessionId}/end`, { method: 'POST' });
}

// ── Hints ─────────────────────────────────────────────────────────────

export interface HintResponse {
  id: string | null;
  session_id: string;
  question_id: string;
  level: number;
  text: string;
}

export async function triggerNextHint(sessionId: string, questionId: string) {
  return request<HintResponse>(`/api/sessions/${sessionId}/hints/next`, {
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

export async function registerPlanHints(sessionId: string, planData: { phases: unknown[] }) {
  return request<{ registered_questions: number }>(
    `/api/sessions/${sessionId}/register-plan-hints`,
    { method: 'POST', body: JSON.stringify(planData) },
  );
}

// ── Upload ────────────────────────────────────────────────────────────

export interface UploadResponse {
  reference_id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
}

export async function uploadResume(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch('/api/upload/resume', {
    method: 'POST',
    body: formData,
    // Do NOT set Content-Type — browser sets multipart boundary
  });

  if (!res.ok) {
    const body = await res.text();
    throw new APIError(res.status, res.statusText, body);
  }
  return res.json();
}

export function getExtractionStreamUrl(referenceId: string): string {
  return `/api/upload/${referenceId}/stream`;
}

// ── Plans ─────────────────────────────────────────────────────────────

export interface CreatePlanRequest {
  session_id: string;
  level: string;
  duration_minutes: number;
  focus_areas: string[];
  resume_data?: Record<string, unknown> | null;
}

export interface PlanQuestionResponse {
  id: string;
  text: string;
  category: string;
  difficulty: number;
  time_minutes: number;
  alternates: string[];
  hints: Array<{ level: number; text: string }>;
  optimal_approaches: Array<{
    name: string;
    data_structures: string[];
    expected_complexity: Record<string, string>;
    key_steps: string[];
  }> | null;
  common_pitfalls: string[] | null;
}

export interface PlanPhaseResponse {
  phase: string;
  start_minute: number;
  duration_minutes: number;
  questions: PlanQuestionResponse[];
}

export interface InterviewPlanResponse {
  id: string;
  session_id: string;
  version: number;
  is_active: boolean;
  phases: PlanPhaseResponse[];
  total_minutes: number;
  created_at: string;
}

export async function createPlan(data: CreatePlanRequest) {
  return request<InterviewPlanResponse>('/api/plans/create', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getPlan(planId: string) {
  return request<InterviewPlanResponse>(`/api/plans/${planId}`);
}

export async function swapQuestion(
  planId: string,
  phaseIndex: number,
  questionIndex: number,
  alternateIndex: number,
) {
  return request<InterviewPlanResponse>(`/api/plans/${planId}/swap`, {
    method: 'PATCH',
    body: JSON.stringify({
      phase_index: phaseIndex,
      question_index: questionIndex,
      alternate_index: alternateIndex,
    }),
  });
}

export async function removeQuestion(
  planId: string,
  phaseIndex: number,
  questionIndex: number,
) {
  return request<InterviewPlanResponse>(`/api/plans/${planId}/remove`, {
    method: 'PATCH',
    body: JSON.stringify({ phase_index: phaseIndex, question_index: questionIndex }),
  });
}

export async function regenerateHints(
  planId: string,
  phaseIndex: number,
  questionIndex: number,
) {
  return request<InterviewPlanResponse>(`/api/plans/${planId}/regenerate-hints`, {
    method: 'POST',
    body: JSON.stringify({ phase_index: phaseIndex, question_index: questionIndex }),
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
