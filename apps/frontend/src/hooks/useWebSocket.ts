/**
 * useWebSocket — connects DashboardWSClient to Zustand stores.
 *
 * Creates a WebSocket connection for a live session and routes
 * incoming messages to the appropriate stores.
 */
import { useEffect, useRef, useCallback } from 'react';
import { DashboardWSClient, type WSMessage } from '@/api/websocket';
import { useTranscriptStore } from '@/stores/transcript-store';
import { useSpeakerStore } from '@/stores/speaker-store';
import { useAnalysisStore } from '@/stores/analysis-store';
import { useHintStore } from '@/stores/hint-store';
import { useOfflineStore } from '@/stores/offline-store';
import { useSessionStore } from '@/stores/session-store';

export function useWebSocket(sessionId: string | null) {
  const clientRef = useRef<DashboardWSClient | null>(null);

  // Grab store actions (stable references from Zustand)
  const addEntry = useTranscriptStore((s) => s.addEntry);
  const addCatchupEntries = useTranscriptStore((s) => s.addCatchupEntries);
  const updateEntrySpeaker = useTranscriptStore((s) => s.updateEntrySpeaker);
  const applySnapshot = useSpeakerStore((s) => s.applySnapshot);
  const setSvTranscripts = useSpeakerStore((s) => s.setSvTranscripts);
  const addAnalysis = useAnalysisStore((s) => s.addAnalysis);
  const addRedFlag = useAnalysisStore((s) => s.addRedFlag);
  const setCodeAnalysis = useAnalysisStore((s) => s.setCodeAnalysis);
  const updateScores = useAnalysisStore((s) => s.updateScores);
  const setActiveHint = useHintStore((s) => s.setActiveHint);
  const setHintSignal = useHintStore((s) => s.setHintSignal);
  const setWSStatus = useOfflineStore((s) => s.setWSStatus);
  const setBackendStatus = useOfflineStore((s) => s.setBackendStatus);
  const setProviderStatus = useOfflineStore((s) => s.setProviderStatus);
  const setReconnectAttempts = useOfflineStore((s) => s.setReconnectAttempts);
  const setSessionStatus = useSessionStore((s) => s.setStatus);
  const setCurrentQuestion = useSessionStore((s) => s.setCurrentQuestion);

  const handleMessage = useCallback(
    (message: WSMessage) => {
      console.debug('[useWebSocket] handleMessage:', message.type);
      switch (message.type) {
        case 'transcript': {
          const p = message.payload as Record<string, unknown>;
          addEntry({
            id: (p.id as string) || crypto.randomUUID(),
            speakerRole: (p.speaker_role as 'interviewer' | 'candidate' | 'unknown') || 'unknown',
            text: p.text as string,
            isFinal: (p.is_final as boolean) ?? true,
            confidence: (p.confidence as number) ?? 1,
            timestamp: message.timestamp,
            channel: (p.channel as string) || 'unknown',
            isRetroactive: (p.is_retroactive as boolean) ?? false,
            rootSpeakerId: (p.root_speaker_id as string) || '',
            speechVectorId: (p.speech_vector_id as string) || '',
          });
          break;
        }

        case 'analysis': {
          const p = message.payload as Record<string, unknown>;
          addAnalysis({
            answerQuality: (p.answer_quality as number) ?? 0,
            onTrack: (p.on_track as boolean) ?? true,
            insight: (p.insight as string) || '',
            redFlags: (p.red_flags as string[]) || [],
            suggestionForInterviewer: (p.suggestion as string) || '',
            hintSignal: (p.hint_signal as string) || 'none',
            timestamp: message.timestamp,
            provider: (p.provider as string) || 'unknown',
          });

          // Propagate hint signal
          if (p.hint_signal && p.hint_signal !== 'none') {
            setHintSignal(p.hint_signal as string);
          }

          // Red flags
          const flags = p.red_flags as string[] | undefined;
          if (flags?.length) {
            for (const flag of flags) {
              addRedFlag({ text: flag, timestamp: message.timestamp });
            }
          }

          // Score updates
          if (p.scores) {
            updateScores(p.scores as Record<string, number>);
          }
          break;
        }

        case 'code_analysis': {
          const p = message.payload as Record<string, unknown>;
          setCodeAnalysis({
            codeLength: (p.code_length as number) ?? 0,
            language: (p.language as string) || 'unknown',
            source: (p.source as string) || 'unknown',
            progressState: (p.progress_state as string) || 'unknown',
            detectedApproach: p.detected_approach
              ? {
                  name: (p.detected_approach as Record<string, unknown>).name as string,
                  confidence: (p.detected_approach as Record<string, unknown>).confidence as number,
                }
              : null,
            isStuck: (p.is_stuck as boolean) ?? false,
            bugs: (p.bugs as string[]) || [],
            timestamp: message.timestamp,
          });
          break;
        }

        case 'hint': {
          const p = message.payload as Record<string, unknown>;
          setActiveHint({
            id: (p.id as string) || crypto.randomUUID(),
            questionId: (p.question_id as string) || '',
            level: (p.level as 1 | 2 | 3) || 1,
            text: (p.text as string) || '',
            displayedAt: message.timestamp,
            interviewerUsed: false,
            candidateResponseQuality: 'none',
            timeToRespondMs: null,
          });
          break;
        }

        case 'red_flag': {
          const p = message.payload as Record<string, unknown>;
          addRedFlag({
            text: (p.text as string) || 'Unknown issue',
            timestamp: message.timestamp,
            type: (p.flag_type as string) || undefined,
          });
          break;
        }

        case 'catchup': {
          const p = message.payload as Record<string, unknown>;
          const events = p.events as Array<Record<string, unknown>> | undefined;
          if (events?.length) {
            addCatchupEntries(
              events.map((e) => ({
                id: (e.id as string) || crypto.randomUUID(),
                speakerRole: (e.speaker_role as 'interviewer' | 'candidate' | 'unknown') || 'unknown',
                text: e.text as string,
                isFinal: true,
                confidence: (e.confidence as number) ?? 1,
                timestamp: (e.timestamp as string) || message.timestamp,
                channel: (e.channel as string) || 'unknown',
                isRetroactive: true,
                rootSpeakerId: (e.root_speaker_id as string) || '',
                speechVectorId: (e.speech_vector_id as string) || '',
              })),
            );
          }
          break;
        }

        case 'provider_status': {
          const p = message.payload as Record<string, unknown>;
          const provider = p.provider as string;
          const status = p.status as string;
          if (provider && status) {
            setProviderStatus(provider, status);
          }
          break;
        }

        case 'speaker_snapshot': {
          const p = message.payload as Record<string, unknown>;
          applySnapshot({
            root_speakers: (p.root_speakers as Record<string, unknown>) as any,
            speech_vectors: (p.speech_vectors as Record<string, unknown>) as any,
          });
          break;
        }

        case 'speaker_transcript_assigned': {
          const p = message.payload as Record<string, unknown>;
          const tid = p.transcript_id as string;
          const newRsId = p.new_root_speaker_id as string;
          const newSvId = p.new_speech_vector_id as string;
          if (tid) {
            updateEntrySpeaker(tid, newRsId, newSvId);
          }
          break;
        }

        case 'sv_transcripts': {
          const p = message.payload as Record<string, unknown>;
          const svId = p.sv_id as string;
          const transcripts = p.transcripts as Array<{ id: string; text: string }>;
          if (svId && transcripts) {
            setSvTranscripts(svId, transcripts);
          }
          break;
        }

        case 'error': {
          const p = message.payload as Record<string, unknown>;
          console.error('[WS] Server error:', p.message || p);
          break;
        }
      }

      // Handle phase_change (comes as a separate message type from backend)
      if ((message as unknown as Record<string, unknown>).type === 'phase_change') {
        const p = message.payload as Record<string, unknown>;
        if (p.status) {
          setSessionStatus(p.status as 'live' | 'paused' | 'ended');
        }
        if (p.current_question_index !== undefined) {
          setCurrentQuestion(
            p.current_question_index as number,
            (p.total_questions as number) || 0,
            (p.current_question_text as string) || '',
          );
        }
      }
    },
    [
      addEntry, addCatchupEntries, updateEntrySpeaker, applySnapshot, setSvTranscripts,
      addAnalysis, addRedFlag, setCodeAnalysis, updateScores,
      setActiveHint, setHintSignal, setProviderStatus,
      setSessionStatus, setCurrentQuestion,
    ],
  );

  useEffect(() => {
    if (!sessionId) return;

    console.log('[useWebSocket] Connecting for session:', sessionId);
    const client = new DashboardWSClient({
      sessionId,
      onOpen: () => {
        console.log('[useWebSocket] onOpen — session:', sessionId);
        setWSStatus('connected');
        setBackendStatus('connected');
        setReconnectAttempts(0);
        // Request the speaker snapshot so the panel updates when audio session exists
        client.send({ type: 'get_speaker_snapshot' });
      },
      onClose: () => {
        console.log('[useWebSocket] onClose — session:', sessionId);
        setWSStatus('reconnecting');
        setBackendStatus('reconnecting');
      },
      onError: () => {
        console.error('[useWebSocket] onError — session:', sessionId);
        setWSStatus('reconnecting');
      },
    });

    // Register wildcard handler to dispatch all messages
    const unsub = client.on('*', handleMessage);

    client.connect();
    clientRef.current = client;

    return () => {
      unsub();
      client.disconnect();
      clientRef.current = null;
      setWSStatus('disconnected');
    };
  }, [sessionId, handleMessage, setWSStatus, setBackendStatus, setReconnectAttempts]);

  const send = useCallback((data: Record<string, unknown>) => {
    clientRef.current?.send(data);
  }, []);

  return { send, client: clientRef };
}
