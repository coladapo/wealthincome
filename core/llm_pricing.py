"""
LLM Pricing Table — USD per 1M tokens for each supported model.
No imports from anywhere in this project — safe to import from db.py.

Structure per model:
    (input_per_m, output_per_m, cache_read_per_m, cache_write_per_m)
"""

from typing import Dict, Tuple

# (input, output, cache_read, cache_write) — USD per 1M tokens
MODEL_PRICING: Dict[str, Tuple[float, float, float, float]] = {
    # ── Anthropic ──────────────────────────────────────────────────────────────
    "claude-sonnet-4-6":            (3.00,  15.00, 0.30,  3.75),
    "claude-sonnet-4-5":            (3.00,  15.00, 0.30,  3.75),
    "claude-opus-4-6":              (15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-5":              (15.00, 75.00, 1.50, 18.75),
    "claude-haiku-4-5":             (0.80,   4.00, 0.08,  1.00),
    "claude-haiku-3-5":             (0.80,   4.00, 0.08,  1.00),
    "claude-3-5-sonnet-20241022":   (3.00,  15.00, 0.30,  3.75),
    "claude-3-5-haiku-20241022":    (0.80,   4.00, 0.08,  1.00),
    "claude-3-opus-20240229":       (15.00, 75.00, 1.50, 18.75),

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    "gpt-4o":                       (2.50,  10.00, 1.25,  0.00),
    "gpt-4o-mini":                  (0.15,   0.60, 0.075, 0.00),
    "gpt-4-turbo":                  (10.00, 30.00, 0.00,  0.00),
    "gpt-4":                        (30.00, 60.00, 0.00,  0.00),
    "o1":                           (15.00, 60.00, 7.50,  0.00),
    "o1-mini":                      (3.00,  12.00, 1.50,  0.00),
    "o3":                           (10.00, 40.00, 2.50,  0.00),
    "o3-mini":                      (1.10,   4.40, 0.55,  0.00),
    "o4-mini":                      (1.10,   4.40, 0.275, 0.00),

    # ── Google Gemini ──────────────────────────────────────────────────────────
    "gemini-2.5-pro":               (1.25,  10.00, 0.31,  0.00),
    "gemini-2.5-flash":             (0.15,   0.60, 0.0375, 0.00),
    "gemini-2.0-flash":             (0.10,   0.40, 0.025,  0.00),
    "gemini-1.5-pro":               (1.25,   5.00, 0.00,   0.00),
    "gemini-1.5-flash":             (0.075,  0.30, 0.0375, 0.00),

    # ── xAI Grok ──────────────────────────────────────────────────────────────
    "grok-3":                       (3.00,  15.00, 0.00,  0.00),
    "grok-3-mini":                  (0.30,   0.50, 0.00,  0.00),
    "grok-2":                       (2.00,  10.00, 0.00,  0.00),

    # ── Local (Ollama) ─────────────────────────────────────────────────────────
    # Local models have zero API cost — token tracking still works for context budgeting
    "llama3.3":                     (0.00,  0.00, 0.00,  0.00),
    "llama3.1:70b":                 (0.00,  0.00, 0.00,  0.00),
    "mistral":                      (0.00,  0.00, 0.00,  0.00),
    "mixtral":                      (0.00,  0.00, 0.00,  0.00),
    "deepseek-r1":                  (0.00,  0.00, 0.00,  0.00),
    "qwen2.5:72b":                  (0.00,  0.00, 0.00,  0.00),
}

_FALLBACK_PRICING = MODEL_PRICING["claude-sonnet-4-6"]


def get_model_pricing(model: str) -> Tuple[float, float, float, float]:
    """Return (input, output, cache_read, cache_write) per 1M tokens.
    Falls back to claude-sonnet-4-6 rates if model not in table."""
    # Normalize: strip org prefix (e.g. "anthropic/claude-sonnet-4-6" → look up full then short)
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        # Try stripping prefix (openai/gpt-4o → gpt-4o)
        short = model.split("/")[-1]
        pricing = MODEL_PRICING.get(short, _FALLBACK_PRICING)
    return pricing


def compute_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Return USD cost for a single LLM call."""
    inp, out, cr, cw = get_model_pricing(model)
    return (
        (input_tokens       * inp / 1_000_000)
        + (output_tokens    * out / 1_000_000)
        + (cache_read_tokens  * cr  / 1_000_000)
        + (cache_write_tokens * cw  / 1_000_000)
    )
