"""Token budget management for LLM context windows.

Estimates token counts and enforces per-tier budgets to keep
the assembled context within the provider's context window.
"""


def estimate_tokens(text: str) -> int:
    """Estimate token count using the ~4 chars per token heuristic.

    This is a fast approximation. For production accuracy,
    use tiktoken or the provider's tokenizer.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate_to_budget(text: str, max_tokens: int) -> str:
    """Truncate text to fit within a token budget.

    Truncates from the beginning (keeping the most recent content)
    since in conversation contexts, recent content is more relevant.
    """
    if not text:
        return text

    current = estimate_tokens(text)
    if current <= max_tokens:
        return text

    # Approximate character limit
    max_chars = max_tokens * 4
    return text[-max_chars:]


# ── Budget Allocations ────────────────────────────────────────────────

# Default token budgets per context tier
BUDGET_SYSTEM_PROMPT = 1000
BUDGET_FACT_STORE = 500        # Tier 3
BUDGET_ROLLING_SUMMARY = 1500  # Tier 2
BUDGET_WORKING_MEMORY = 2000   # Tier 1
BUDGET_HINTS = 200
BUDGET_RESPONSE = 600
BUDGET_RUNTIME_HINT = 400
BUDGET_TOTAL = (
    BUDGET_SYSTEM_PROMPT
    + BUDGET_FACT_STORE
    + BUDGET_ROLLING_SUMMARY
    + BUDGET_WORKING_MEMORY
    + BUDGET_HINTS
    + BUDGET_RESPONSE
)
