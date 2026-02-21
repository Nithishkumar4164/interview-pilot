/**
 * Live session page — wires all dashboard components together
 * with WebSocket connection and timer.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useTimer } from '@/hooks/useTimer';
import { useSessionStore, getAllPlanQuestions } from '@/stores/session-store';
import type { SessionStatus } from '@/stores/session-store';
import { useHintStore } from '@/stores/hint-store';
import type { HintEntry, HintLevel, HintResponseQuality } from '@/stores/hint-store';
import { usePlanStore } from '@/stores/plan-store';
import { useTranscriptStore } from '@/stores/transcript-store';
import type { TranscriptEntry, SpeakerRole } from '@/stores/transcript-store';
import { useAnalysisStore, mapDimensions } from '@/stores/analysis-store';
import type { AnalysisEntry } from '@/stores/analysis-store';
import { useOfflineStore } from '@/stores/offline-store';
import { useSpeakerStore } from '@/stores/speaker-store';
import { WebSocketProvider } from '@/contexts/WebSocketContext';
import { SessionBar } from '@/components/dashboard/SessionBar';
import { VideoOverlay } from '@/components/dashboard/VideoOverlay';
import type { CropRegion, OverlayMode } from '@/components/dashboard/VideoOverlay';
import { TranscriptPanel } from '@/components/dashboard/TranscriptPanel';
import { SpeakersPanel } from '@/components/dashboard/SpeakersPanel';
import { HintPanel } from '@/components/dashboard/HintPanel';
import { ScoreRings } from '@/components/dashboard/ScoreRings';
import { InterviewPlanPanel } from '@/components/dashboard/InterviewPlanPanel';
import * as api from '@/api/client';

interface OverlayEntry {
  id: string;
  cropRegion: CropRegion | null;
  mode: OverlayMode;
}

export function LiveSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  const status = useSessionStore((s) => s.status);
  const setSession = useSessionStore((s) => s.setSession);
  const setStatus = useSessionStore((s) => s.setStatus);
  const reset = useSessionStore((s) => s.reset);
  const degradationLevel = useOfflineStore((s) => s.degradationLevel);
  const [videoStream, setVideoStream] = useState<MediaStream | null>(null);
  const [overlays, setOverlays] = useState<OverlayEntry[]>([]);

  // Initialize WebSocket and timer
  const { send } = useWebSocket(sessionId ?? null);
  useTimer();

  // Track whether recovery has been attempted
  const recoveryAttempted = useRef(false);

  // Initialize session state — attempt recovery from server first
  useEffect(() => {
    if (!sessionId || status !== 'idle' || recoveryAttempted.current) return;
    recoveryAttempted.current = true;

    (async () => {
      try {
        const state = await api.getSessionState(sessionId);

        // Restore plan first (other stores depend on it)
        if (state.plan) {
          const planData = state.plan as Record<string, unknown>;
          usePlanStore.getState().setPlan({
            id: (state.plan_id ?? '') as string,
            sessionId,
            version: (planData.version as number) ?? 1,
            isActive: true,
            phases: (planData.phases as Array<Record<string, unknown>>) ?? [],
            totalMinutes: (planData.total_minutes as number) ?? 0,
            createdAt: '',
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          } as any);
        }

        // Restore session state
        const sessionStatus = (state.session.status === 'live' ? 'live' :
          state.session.status === 'paused' ? 'paused' :
          state.session.status === 'ended' ? 'ended' : 'live') as SessionStatus;

        const snapshotQuestionIndex = state.snapshot
          ? ((state.snapshot as Record<string, unknown>).plan_progress as Record<string, number> | undefined)?.current_question_index ?? 0
          : 0;

        useSessionStore.getState().restoreFromServer({
          sessionId,
          status: sessionStatus,
          level: state.session.level,
          durationMinutes: state.session.duration_minutes,
          elapsedMs: state.session.elapsed_ms,
          startedAt: state.session.started_at,
          currentQuestionIndex: snapshotQuestionIndex,
        });

        // Restore transcripts
        if (state.transcripts.length > 0) {
          const entries: TranscriptEntry[] = state.transcripts.map((t) => ({
            id: t.id,
            speakerRole: t.speaker_role as SpeakerRole,
            text: t.text,
            isFinal: t.is_final,
            confidence: t.confidence,
            timestamp: t.timestamp,
            channel: t.channel,
            isRetroactive: t.is_retroactive,
            rootSpeakerId: '',
            speechVectorId: '',
          }));
          useTranscriptStore.getState().restoreTranscripts(entries);
        }

        // Restore analyses
        if (state.analyses.length > 0) {
          const analysisEntries: AnalysisEntry[] = state.analyses.map((a) => ({
            answerQuality: a.answer_quality,
            onTrack: a.on_track,
            insight: a.insight,
            redFlags: a.red_flags,
            suggestionForInterviewer: a.suggestion ?? '',
            hintSignal: a.hint_signal ?? 'none',
            timestamp: a.timestamp,
            provider: '',
            dimensions: null,
          }));
          useAnalysisStore.getState().restoreAnalyses(analysisEntries);
        }

        // Restore hints
        if (state.hints.length > 0) {
          const hintEntries: HintEntry[] = state.hints.map((h) => ({
            id: h.id,
            questionId: h.question_id,
            level: h.level as HintLevel,
            text: h.text,
            displayedAt: h.displayed_at,
            interviewerUsed: h.interviewer_used,
            candidateResponseQuality: h.candidate_response_quality as HintResponseQuality,
            timeToRespondMs: null,
          }));
          useHintStore.getState().restoreHints(hintEntries);
        }
      } catch {
        // Recovery failed — fall back to fresh session state
        setSession(sessionId, '', 45);
        setStatus('live');
      }
    })();
  }, [sessionId, status, setSession, setStatus]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      reset();
      useTranscriptStore.getState().clear();
      useAnalysisStore.getState().clear();
      useHintStore.getState().clear();
      useOfflineStore.getState().reset();
      useSpeakerStore.getState().clear();
      videoStream?.getTracks().forEach(t => t.stop());
    };
  }, [reset]);

  // Clean up if stream becomes inactive (user clicks Chrome's "Stop sharing")
  useEffect(() => {
    if (!videoStream) return;
    const track = videoStream.getVideoTracks()[0];
    if (!track) return;
    const onEnded = () => { setVideoStream(null); setOverlays([]); };
    track.addEventListener('ended', onEnded);
    return () => track.removeEventListener('ended', onEnded);
  }, [videoStream]);

  // ── Overlay callbacks ──────────────────────────────────────────────

  const handleCloneOverlay = useCallback(() => {
    setOverlays(prev => [...prev, { id: crypto.randomUUID(), cropRegion: null, mode: 'floating' }]);
  }, []);

  const handleCloseOverlay = useCallback((id: string) => {
    setOverlays(prev => {
      const next = prev.filter(o => o.id !== id);
      if (next.length === 0 && videoStream) {
        videoStream.getTracks().forEach(t => t.stop());
        setVideoStream(null);
      }
      return next;
    });
  }, [videoStream]);

  const handleCropChange = useCallback((id: string, region: CropRegion | null) => {
    setOverlays(prev => prev.map(o => o.id === id ? { ...o, cropRegion: region } : o));
  }, []);

  const handleModeChange = useCallback((id: string, newMode: OverlayMode) => {
    setOverlays(prev => prev.map(o => o.id === id ? { ...o, mode: newMode } : o));
  }, []);

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

  const handleToggleVideo = useCallback(async () => {
    if (videoStream) {
      videoStream.getTracks().forEach((t) => t.stop());
      setVideoStream(null);
      setOverlays([]);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: { displaySurface: 'browser' } as MediaTrackConstraints,
        audio: false,
      });
      setVideoStream(stream);
      setOverlays([{ id: crypto.randomUUID(), cropRegion: null, mode: 'floating' }]);
    } catch {
      // User cancelled the picker — do nothing
    }
  }, [videoStream]);

  const isPanel = new URLSearchParams(window.location.search).has('panel');

  const handleEnd = useCallback(async () => {
    if (!sessionId) return;
    if (!isPanel && !window.confirm('Are you sure you want to end this interview?')) return;
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
  }, [sessionId, isPanel, setStatus, navigate]);

  const handleRequestHint = useCallback(
    (level: number) => {
      if (!sessionId) return;

      const planState = usePlanStore.getState().plan;
      if (!planState) return;

      const { currentQuestionIndex } = useSessionStore.getState();
      const allQuestions = planState.phases.flatMap((p) => p.questions);
      const question = allQuestions[currentQuestionIndex];
      if (!question) return;

      const hint = question.hints?.find((h: { level: number; text: string }) => h.level === level);
      if (!hint) return;

      useHintStore.getState().setActiveHint({
        id: crypto.randomUUID(),
        questionId: question.id,
        level: level as 1 | 2 | 3,
        text: hint.text,
        displayedAt: new Date().toISOString(),
        interviewerUsed: false,
        candidateResponseQuality: 'none',
        timeToRespondMs: null,
      });

      // Notify backend for tracking (fire-and-forget)
      api.triggerNextHint(sessionId, question.id).catch(() => {});
    },
    [sessionId],
  );

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

  // ── Runtime Hint Handlers ──────────────────────────────────────────

  const handleRequestRuntimeHint = useCallback(() => {
    if (!sessionId) return;
    const planState = usePlanStore.getState().plan;
    let questionId = '';
    if (planState) {
      const { currentQuestionIndex } = useSessionStore.getState();
      const allQuestions = planState.phases.flatMap((p) => p.questions);
      if (currentQuestionIndex < allQuestions.length) {
        questionId = allQuestions[currentQuestionIndex].id;
      }
    }
    useHintStore.getState().setRuntimeHintLoading(true);
    send({ type: 'request_runtime_hint', question_id: questionId });
  }, [sessionId, send]);

  const handleRegenerateRuntimeHint = useCallback(
    (reason: string) => {
      if (!sessionId) return;
      const planState = usePlanStore.getState().plan;
      let questionId = '';
      if (planState) {
        const { currentQuestionIndex } = useSessionStore.getState();
        const allQuestions = planState.phases.flatMap((p) => p.questions);
        if (currentQuestionIndex < allQuestions.length) {
          questionId = allQuestions[currentQuestionIndex].id;
        }
      }
      useHintStore.getState().setRuntimeHintLoading(true);
      send({
        type: 'request_runtime_hint',
        question_id: questionId,
        regenerate: true,
        regeneration_reason: reason,
      });
    },
    [sessionId, send],
  );

  const handleAcknowledgeRuntimeHint = useCallback(
    (hintId: string, quality: string) => {
      useHintStore.getState().acknowledgeRuntimeHint(
        hintId,
        quality as 'well_received' | 'partially_received' | 'not_understood',
      );
      send({
        type: 'acknowledge_runtime_hint',
        hint_id: hintId,
        quality,
      });
    },
    [send],
  );

  // ── Code Rating Handler ────────────────────────────────────────────

  const handleRateCode = useCallback(
    (code: string, language: string) => {
      if (!sessionId) return;
      const planState = usePlanStore.getState().plan;
      let questionId = '';
      if (planState) {
        const { currentQuestionIndex } = useSessionStore.getState();
        const allQuestions = planState.phases.flatMap((p) => p.questions);
        if (currentQuestionIndex < allQuestions.length) {
          questionId = allQuestions[currentQuestionIndex].id;
        }
      }
      useAnalysisStore.getState().setCodeRatingLoading(true);
      send({ type: 'rate_code', code, language, question_id: questionId });
    },
    [sessionId, send],
  );

  // ── Question navigation ────────────────────────────────────────────

  const navigateToQuestion = useCallback(
    async (newIndex: number) => {
      const allQ = getAllPlanQuestions();
      const q = allQ[newIndex];
      if (!q || !sessionId) return;

      useSessionStore.getState().goToQuestion(newIndex);
      useHintStore.getState().clear();
      useAnalysisStore.getState().clearCodeRating();
      useSessionStore.getState().setContextReloading(true);

      // Notify backend so analysis pipeline evaluates against the new question
      send({
        type: 'change_question',
        question_index: newIndex,
        question_id: q.id,
        question_text: q.text,
        question_plan: {
          id: q.id,
          text: q.text,
          category: q.category,
          difficulty: q.difficulty,
          time_minutes: q.time_minutes,
          hints: q.hints,
          brute_force_approach: q.brute_force_approach,
          optimal_approaches: q.optimal_approaches,
          common_pitfalls: q.common_pitfalls,
        },
      });

    },
    [sessionId, send],
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
    <WebSocketProvider value={{ send }}>
      <div className={`live-session live-session--degradation-${degradationLevel}`}>
        <SessionBar
          onPause={handlePause}
          onResume={handleResume}
          onEnd={handleEnd}
          onToggleVideo={handleToggleVideo}
          videoActive={!!videoStream}
        />

        {/* Floating + fullscreen overlays render outside layout */}
        {videoStream && overlays.filter(o => o.mode !== 'docked').map(overlay => (
          <VideoOverlay
            key={overlay.id}
            stream={videoStream}
            cropRegion={overlay.cropRegion}
            onCropChange={region => handleCropChange(overlay.id, region)}
            onClone={handleCloneOverlay}
            onClose={() => handleCloseOverlay(overlay.id)}
            mode={overlay.mode}
            onModeChange={mode => handleModeChange(overlay.id, mode)}
          />
        ))}

        <main className="live-session__body">
          <div className="live-session__left">
            {/* Docked overlays render above transcript */}
            {videoStream && overlays.filter(o => o.mode === 'docked').map(overlay => (
              <VideoOverlay
                key={overlay.id}
                stream={videoStream}
                cropRegion={overlay.cropRegion}
                onCropChange={region => handleCropChange(overlay.id, region)}
                onClone={handleCloneOverlay}
                onClose={() => handleCloseOverlay(overlay.id)}
                mode={overlay.mode}
                onModeChange={mode => handleModeChange(overlay.id, mode)}
              />
            ))}
            <TranscriptPanel />
          </div>

          <div className="live-session__center">
            <InterviewPlanPanel
              onAdvanceQuestion={handleAdvanceQuestion}
              onGoToQuestion={handleGoToQuestion}
              onRequestHint={handleRequestHint}
              onAcknowledgeHint={handleAcknowledgeHint}
              onRateCode={handleRateCode}
            />
          </div>

          <div className="live-session__right">
            <SpeakersPanel />
            <ScoreRings />
            <HintPanel
              onRequestRuntimeHint={handleRequestRuntimeHint}
              onRegenerateRuntimeHint={handleRegenerateRuntimeHint}
              onAcknowledgeRuntimeHint={handleAcknowledgeRuntimeHint}
            />
          </div>
        </main>

        {/* Offline mode manual notes */}
        {degradationLevel >= 2 && (
          <OfflineNotesPanel send={send} />
        )}
      </div>
    </WebSocketProvider>
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
