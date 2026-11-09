"""
Token usage and estimated API cost.

A run can use more than one model, so every call is priced with the model that
served it. Cached input is billed differently from ordinary input: a cache write
costs more than a normal input token, a cache read much less. usage.input_tokens
excludes both, so all three are priced here.
"""

import logging

from talk_autopsy.config import (
    CACHE_READ_MULTIPLIER,
    CACHE_READ_MULTIPLIER_BY_MODEL,
    CACHE_WRITE_MULTIPLIER,
    CORRECTION_MODEL,
    MODEL_PRICING_PER_MTOK,
    WEB_SEARCH_PRICE,
)

logger = logging.getLogger(__name__)

# Token price multiplier: the Message Batches API bills tokens at half price
# (core/batch_client.py). Web searches are priced separately and kept at full price.
BATCH_TOKEN_DISCOUNT = 0.5
token_price_factor = 1.0


def use_batch_pricing() -> None:
    """Price tokens at the batch rate for the rest of the process (a benchmark run in batch mode)."""
    global token_price_factor
    token_price_factor = BATCH_TOKEN_DISCOUNT


def cache_read_multiplier(model: str) -> float:
    return CACHE_READ_MULTIPLIER_BY_MODEL.get(model, CACHE_READ_MULTIPLIER)


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str | None = None,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float | None:
    """
    Estimated cost in USD for the given model (default: CORRECTION_MODEL),
    or None if the model's pricing is not configured.
    """
    model = model or CORRECTION_MODEL
    pricing = MODEL_PRICING_PER_MTOK.get(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    input_equivalent = (
        input_tokens
        + cache_creation_input_tokens * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * cache_read_multiplier(model)
    )
    return token_price_factor * (input_equivalent * input_price + output_tokens * output_price) / 1_000_000


def call_cost(result: dict) -> float | None:
    """Estimated cost of one call: its tokens at its model's prices plus its web searches."""
    cost = estimate_cost(
        result["input_tokens"],
        result["output_tokens"],
        result.get("model", CORRECTION_MODEL),
        result.get("cache_creation_input_tokens", 0),
        result.get("cache_read_input_tokens", 0),
    )
    if cost is None:
        return None
    return cost + result.get("web_searches", 0) * WEB_SEARCH_PRICE


def format_cost(cost: float | None) -> str:
    return "cost n/a" if cost is None else f"est. cost ${cost:.4f}"


def total_input(result: dict) -> int:
    """All input tokens of a call: uncached, written to the cache and read from it."""
    return (
        result["input_tokens"]
        + result.get("cache_creation_input_tokens", 0)
        + result.get("cache_read_input_tokens", 0)
    )


def log_token_summary(results: list[dict]) -> None:
    """
    Log one total line; the per-call breakdown goes to DEBUG.
    Failed calls count when their tokens are known (rejected attempts are billed).
    Each result may name its "model"; otherwise CORRECTION_MODEL is assumed.
    """
    billed = [r for r in results if "input_tokens" in r]
    if not billed:
        logger.info("Tokens: no billed calls")
        return

    costs = []
    for result in billed:
        model = result.get("model", CORRECTION_MODEL)
        cost = call_cost(result)
        costs.append(cost)
        searches = f", {result['web_searches']} web searches" if result.get("web_searches") else ""
        logger.debug(
            f"Tokens {result.get('label', '?')} ({model}): {result['input_tokens']} in "
            f"+ {result.get('cache_creation_input_tokens', 0)} cache write "
            f"+ {result.get('cache_read_input_tokens', 0)} cache read / "
            f"{result['output_tokens']} out{searches}, {format_cost(cost)}"
        )

    all_input = sum(total_input(r) for r in billed)
    cache_read = sum(r.get("cache_read_input_tokens", 0) for r in billed)
    total_output = sum(r["output_tokens"] for r in billed)
    total_cost = None if None in costs else sum(costs)
    models = sorted({r.get("model", CORRECTION_MODEL) for r in billed})
    read_share = f", {cache_read / all_input:.0%} of input read from cache" if all_input else ""
    logger.info(
        f"Tokens: {all_input} in{read_share} / {total_output} out, {format_cost(total_cost)} "
        f"({', '.join(models)})"
    )
    for model in models:
        if model not in MODEL_PRICING_PER_MTOK:
            logger.warning(f"No pricing for {model} in config.MODEL_PRICING_PER_MTOK")
