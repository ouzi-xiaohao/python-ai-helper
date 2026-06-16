from __future__ import annotations

"""Token and cost estimation utilities.

Production systems should prefer provider-returned usage fields when
available. These helpers provide a deterministic fallback so local demo calls
and providers without usage metadata can still be audited.
"""

from app.schemas import ChatMessage, CostInfo, TokenUsage


MODEL_PRICING_PER_MILLION = {
    "deepseek-chat": {"prompt": 0.27, "completion": 1.10},
    "doubao-pro-32k": {"prompt": 0.80, "completion": 2.00},
    "qwen-plus": {"prompt": 0.40, "completion": 1.20},
    "aethervoice-demo": {"prompt": 0.0, "completion": 0.0},
}


def estimate_tokens(text: str) -> int:
    """Approximate token count for mixed Chinese/English text."""
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, round(ascii_chars / 4 + non_ascii_chars / 1.6))


def estimate_usage(messages: list[ChatMessage], reply: str) -> TokenUsage:
    """Estimate prompt, completion, and total tokens for one model call."""
    prompt_tokens = sum(estimate_tokens(message.content) + 4 for message in messages)
    completion_tokens = estimate_tokens(reply)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def estimate_cost(model: str, usage: TokenUsage) -> CostInfo:
    """Calculate approximate cost from the local per-million-token price table."""
    pricing = MODEL_PRICING_PER_MILLION.get(
        model,
        {"prompt": 0.0, "completion": 0.0},
    )
    prompt_cost = usage.prompt_tokens * pricing["prompt"] / 1_000_000
    completion_cost = usage.completion_tokens * pricing["completion"] / 1_000_000
    return CostInfo(
        prompt_cost=round(prompt_cost, 8),
        completion_cost=round(completion_cost, 8),
        total_cost=round(prompt_cost + completion_cost, 8),
    )
