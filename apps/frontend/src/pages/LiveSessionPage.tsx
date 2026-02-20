/**
 * Live session page — wires all dashboard components together
 * with WebSocket connection and timer.
 */
import { useCallback, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useTimer } from '@/hooks/useTimer';
import { useSessionStore, getAllPlanQuestions } from '@/stores/session-store';
import { useHintStore } from '@/stores/hint-store';
import { usePlanStore } from '@/stores/plan-store';
import { useTranscriptStore } from '@/stores/transcript-store';
import { useAnalysisStore } from '@/stores/analysis-store';
import { useOfflineStore } from '@/stores/offline-store';
import { SessionBar } from '@/components/dashboard/SessionBar';
import { TranscriptPanel } from '@/components/dashboard/TranscriptPanel';
import { CodePanel } from '@/components/dashboard/CodePanel';
import { HintPanel } from '@/components/dashboard/HintPanel';
import { ScoreRings } from '@/components/dashboard/ScoreRings';
import { InterviewPlanPanel } from '@/components/dashboard/InterviewPlanPanel';
import * as api from '@/api/client';

export function LiveSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  const status = useSessionStore((s) => s.status);
  const setSession = useSessionStore((s) => s.setSession);
  const setStatus = useSessionStore((s) => s.setStatus);
  const reset = useSessionStore((s) => s.reset);
  const degradationLevel = useOfflineStore((s) => s.degradationLevel);

  // Initialize WebSocket and timer
  const { send } = useWebSocket(sessionId ?? null);
  useTimer();

  // Initialize session state if entering directly
  useEffect(() => {
    if (sessionId && status === 'idle') {
      setSession(sessionId, '', 45);
      setStatus('live');
    }
  }, [sessionId, status, setSession, setStatus]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      reset();
      useTranscriptStore.getState().clear();
      useAnalysisStore.getState().clear();
      useHintStore.getState().clear();
      useOfflineStore.getState().reset();
    };
  }, [reset]);

  const handlePause = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.pauseSession(sessionId);
      setStatus('paused');
    } catch (err) {
      console.error('Failed to pause session:', err);
    }
  }, [sessionId, setStatus]);

  const handleResume = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.pauseSession(sessionId); // Toggle pause
      setStatus('live');
    } catch (err) {
      console.error('Failed to resume session:', err);
    }
  }, [sessionId, setStatus]);

  const handleEnd = useCallback(async () => {
    if (!sessionId) return;
    if (!window.confirm('Are you sure you want to end this interview?')) return;
    try {
      const result = await api.endSession(sessionId) as { report_id?: string };
      setStatus('ended');
      if (result?.report_id) {
        navigate(`/reports/${result.report_id}`);
      } else {
        navigate('/');
      }
    } catch (err) {
      console.error('Failed to end session:', err);
      setStatus('ended');
      navigate('/');
    }
  }, [sessionId, setStatus, navigate]);

  const handleRequestHint = useCallback(async () => {
    if (!sessionId) return;
    try {
      // Get current question ID from the plan
      const planState = usePlanStore.getState().plan;
      let questionId = '';
      if (planState) {
        const { currentQuestionIndex } = useSessionStore.getState();
        const allQuestions = planState.phases.flatMap((p) => p.questions);
        if (currentQuestionIndex < allQuestions.length) {
          questionId = allQuestions[currentQuestionIndex].id;
        }
      }

      const result = await api.triggerNextHint(sessionId, questionId);
      // Update hint store with real hint data
      if (result.id && result.level > 0) {
        useHintStore.getState().setActiveHint({
          id: result.id,
          questionId: result.question_id,
          level: result.level as 1 | 2 | 3,
          text: result.text,
          displayedAt: new Date().toISOString(),
          interviewerUsed: false,
          candidateResponseQuality: 'none',
          timeToRespondMs: null,
        });
      }
    } catch (err) {
      console.error('Failed to request hint:', err);
    }
  }, [sessionId]);

  const handleAcknowledgeHint = useCallback(
    async (hintId: string, quality: 'none' | 'partial' | 'full') => {
      if (!sessionId) return;
      const { acknowledgeHint: ackStore } = useHintStore.getState();
      ackStore(hintId, quality);
      try {
        await api.acknowledgeHint(sessionId, hintId, {
          interviewer_used: true,
          candidate_response_quality: quality,
        });
      } catch (err) {
        console.error('Failed to acknowledge hint:', err);
      }
    },
    [sessionId],
  );

  // ── Question navigation ────────────────────────────────────────────

  const navigateToQuestion = useCallback(
    async (newIndex: number) => {
      const allQ = getAllPlanQuestions();
      const q = allQ[newIndex];
      if (!q || !sessionId) return;

      useSessionStore.getState().goToQuestion(newIndex);
      useHintStore.getState().clear();

      try {
        await api.triggerNextHint(sessionId, q.id);
      } catch {
        // non-critical — hints will still work via manual trigger
      }
    },
    [sessionId],
  );

  const handleAdvanceQuestion = useCallback(
    (delta: number) => {
      const { currentQuestionIndex } = useSessionStore.getState();
      const allQ = getAllPlanQuestions();
      const newIndex = currentQuestionIndex + delta;
      if (newIndex < 0 || newIndex >= allQ.length) return;
      navigateToQuestion(newIndex);
    },
    [navigateToQuestion],
  );

  const handleGoToQuestion = useCallback(
    (index: number) => {
      navigateToQuestion(index);
    },
    [navigateToQuestion],
  );

  // Keyboard shortcuts: N/ArrowRight = next, P/ArrowLeft = prev
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (e.key === 'n' || e.key === 'N' || e.key === 'ArrowRight') {
        handleAdvanceQuestion(1);
      } else if (e.key === 'p' || e.key === 'P' || e.key === 'ArrowLeft') {
        handleAdvanceQuestion(-1);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [handleAdvanceQuestion]);

  if (!sessionId) {
    return (
      <div className="live-session live-session--error">
        <p>No session ID provided.</p>
        <button className="btn btn--primary" onClick={() => navigate('/')}>
          Go Home
        </button>
      </div>
    );
  }

  return (
    <div className={`live-session live-session--degradation-${degradationLevel}`}>
      <SessionBar
        onPause={handlePause}
        onResume={handleResume}
        onEnd={handleEnd}
      />

      <main className="live-session__body">
        <div className="live-session__left">
          <TranscriptPanel />
        </div>

        <div className="live-session__center">
          <InterviewPlanPanel
            onAdvanceQuestion={handleAdvanceQuestion}
            onGoToQuestion={handleGoToQuestion}
          />
        </div>

        <div className="live-session__right">
          <ScoreRings />
          <CodePanel />
          <HintPanel
            onRequestHint={handleRequestHint}
            onAcknowledge={handleAcknowledgeHint}
          />
        </div>
      </main>

      {/* Offline mode manual notes */}
      {degradationLevel >= 2 && (
        <OfflineNotesPanel send={send} />
      )}
    </div>
  );
}

function OfflineNotesPanel({ send }: { send: (data: Record<string, unknown>) => void }) {
  const manualNotes = useOfflineStore((s) => s.manualNotes);
  const setManualNotes = useOfflineStore((s) => s.setManualNotes);

  return (
    <div className="offline-notes" role="complementary" aria-label="Offline Notes">
      <h3>Manual Notes (Offline Mode)</h3>
      <textarea
        className="offline-notes__textarea"
        value={manualNotes}
        onChange={(e) => setManualNotes(e.target.value)}
        placeholder="Type notes here while offline. They will sync when connection is restored."
        rows={4}
      />
      <button
        className="btn btn--secondary"
        onClick={() => send({ type: 'manual_notes', notes: manualNotes })}
      >
        Sync Notes
      </button>
    </div>
  );
}
