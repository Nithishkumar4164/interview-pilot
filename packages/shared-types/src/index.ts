// @copilot/shared-types — Cross-service type definitions
// All types defined here are the source of truth for frontend, backend, and extension.

export type { InterviewSession, InterviewPlan, InterviewLevel } from './interview';
export type { TranscriptEvent, SpeakerRole } from './transcript';
export type { AnalysisEvent, CodeAnalysis } from './analysis';
export type { HintEvent, HintLevel, HintResponseQuality, HintResponseMetrics } from './hints';
export type { ReportData, Assessment, Recommendation } from './reports';
export type { WSMessage, WSMessageType } from './websocket';
export type { OfflineStateSnapshot, CatchUpSummary } from './resilience';
export type { LLMProviderConfig, LLMTaskType, ProviderStatus } from './llm';
