import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { DashboardPage } from '@/pages/DashboardPage';
import { usePlanStore } from '@/stores/plan-store';
import { useSessionStore } from '@/stores/session-store';
import * as api from '@/api/client';

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  startSession: vi.fn(),
  goLive: vi.fn(),
  uploadResume: vi.fn(),
  streamSSE: vi.fn(),
}));

const RESUME = {
  name: 'Jane Doe',
  email: 'jane@example.com',
  phone: '+1 555 0100',
  current_role: 'Backend Engineer',
  tech_stack: ['Go'],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/new']}>
      <Routes>
        <Route path="/new" element={<DashboardPage />} />
        <Route path="/session/:sessionId" element={<div>live page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Drop a resume and let the extraction stream report RESUME. */
function uploadResume() {
  vi.mocked(api.uploadResume).mockResolvedValue({ reference_id: 'ref-1' } as never);
  vi.mocked(api.streamSSE).mockImplementation(async (_url, onEvent) => {
    onEvent({ event: 'completed', data: JSON.stringify({ structured_data: RESUME }) });
  });
  const file = new File(['%PDF'], 'jane.pdf', { type: 'application/pdf' });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe('new interview', () => {
  beforeEach(() => {
    vi.mocked(api.startSession).mockReset().mockResolvedValue({ session_id: 's-1' });
    vi.mocked(api.goLive).mockReset().mockResolvedValue({ status: 'live', session_id: 's-1', started_at: '2026-10-18T10:00:00Z' });
    usePlanStore.getState().reset();
    useSessionStore.getState().reset();
  });

  it('prefills editable details from the resume and goes live without planning', async () => {
    renderPage();
    uploadResume();

    const name = await screen.findByLabelText('Candidate Name');
    expect(name).toHaveValue('Jane Doe');
    expect(screen.getByLabelText('Candidate Email')).toHaveValue('jane@example.com');
    expect(screen.getByLabelText('Phone')).toHaveValue('+1 555 0100');
    expect(screen.getByLabelText('Current Role')).toHaveValue('Backend Engineer');

    fireEvent.change(name, { target: { value: 'Jane Q. Doe' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Interview' }));

    expect(await screen.findByText('live page')).toBeInTheDocument();
    expect(api.startSession).toHaveBeenCalledWith(
      expect.objectContaining({
        candidate_name: 'Jane Q. Doe',
        candidate_email: 'jane@example.com',
        candidate_phone: '+1 555 0100',
        candidate_role: 'Backend Engineer',
        resume_data: RESUME,
      }),
    );
    expect(api.goLive).toHaveBeenCalledWith('s-1');
    expect(useSessionStore.getState().status).toBe('live');
    expect(usePlanStore.getState().plan).toBeNull();
  });

  it('asks for a resume before starting in resume mode', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Start Interview' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/upload a resume/i);
    expect(api.startSession).not.toHaveBeenCalled();
  });

  it('sends typed details and no resume when entered manually', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('radio', { name: 'Enter manually' }));
    fireEvent.change(screen.getByLabelText('Candidate Name'), { target: { value: 'Sam Lee' } });
    fireEvent.change(screen.getByLabelText('Candidate Email'), { target: { value: 'sam@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Interview' }));

    await waitFor(() => expect(api.startSession).toHaveBeenCalled());
    const sent = vi.mocked(api.startSession).mock.calls[0][0];
    expect(sent).toMatchObject({ candidate_name: 'Sam Lee', candidate_email: 'sam@example.com', resume_data: null });
    expect(sent.candidate_phone).toBeUndefined();
  });

  it('sends no candidate data for a guest, even after a resume was parsed', async () => {
    renderPage();
    uploadResume();
    await screen.findByLabelText('Candidate Name');

    fireEvent.click(screen.getByRole('radio', { name: 'Guest' }));
    expect(screen.queryByLabelText('Candidate Name')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Start Interview' }));

    await waitFor(() => expect(api.startSession).toHaveBeenCalled());
    const sent = vi.mocked(api.startSession).mock.calls[0][0];
    expect(sent).toMatchObject({ resume_data: null });
    for (const k of ['candidate_name', 'candidate_email', 'candidate_phone', 'candidate_role'] as const) {
      expect(sent[k]).toBeUndefined();
    }
  });
});
