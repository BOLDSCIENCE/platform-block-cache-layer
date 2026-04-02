"""Model pricing table for cost savings estimation."""

MODEL_PRICING: dict[str, dict[str, float]] = {
    "anthropic.claude-sonnet-4-5-20250929": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
    "anthropic.claude-haiku-4-5-20251001": {
        "input": 0.80 / 1_000_000,
        "output": 4.00 / 1_000_000,
    },
}

DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929"


def estimate_cost_saved(tokens_input: int, tokens_output: int, model: str = "") -> float:
    """Estimate USD saved by serving from cache instead of invoking the model."""
    rates = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (tokens_input * rates["input"]) + (tokens_output * rates["output"])
