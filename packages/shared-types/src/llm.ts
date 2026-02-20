export type LLMTaskType =
  | 'real_time_analysis'
  | 'code_analysis'
  | 'report_generation'
  | 'hint_generation'
  | 'summary_compression'
  | 'resume_extraction'
  | 'question_generation';

export type LLMProviderType = 'anthropic' | 'openai' | 'mock';

export interface LLMProviderConfig {
  id: string;
  taskType: LLMTaskType;
  primaryProvider: LLMProviderType;
  primaryModel: string;
  fallbackProvider: LLMProviderType;
  fallbackModel: string;
  isActive: boolean;
}

export type ProviderHealthStatus = 'healthy' | 'degraded' | 'down';

export interface ProviderStatus {
  provider: LLMProviderType;
  status: ProviderHealthStatus;
  lastCheckAt: string;
  consecutiveFailures: number;
  averageLatencyMs: number | null;
}
