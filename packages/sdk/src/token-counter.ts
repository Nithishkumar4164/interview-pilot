/**
 * Rough token count estimation.
 * Uses the ~4 chars per token heuristic for English text.
 * For production accuracy, use tiktoken or the provider's tokenizer.
 */
export function estimateTokenCount(text: string): number {
  return Math.ceil(text.length / 4);
}
