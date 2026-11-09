import logging

import pytest

from talk_autopsy.core import token_summary
from talk_autopsy.core.token_summary import call_cost, estimate_cost, log_token_summary


def test_plain_input_and_output_at_the_model_price():
    # Sonnet 4.6: 3 USD / 15 USD per million tokens
    assert estimate_cost(1_000_000, 1_000_000, "claude-sonnet-4-6") == pytest.approx(18.0)


def test_cache_write_costs_more_and_cache_read_less_than_input():
    write = estimate_cost(0, 0, "claude-sonnet-4-6", cache_creation_input_tokens=1_000_000)
    read = estimate_cost(0, 0, "claude-sonnet-4-6", cache_read_input_tokens=1_000_000)
    assert write == pytest.approx(3.0 * 1.25)
    assert read == pytest.approx(3.0 * 0.10)


def test_cache_read_multiplier_is_model_specific():
    read = estimate_cost(0, 0, "claude-opus-5-5", cache_read_input_tokens=1_000_000)
    assert read == pytest.approx(4.0 * 0.05)


def test_unknown_model_has_no_estimate():
    assert estimate_cost(100, 100, "claude-unknown") is None


def test_batch_pricing_halves_token_cost(monkeypatch):
    full = estimate_cost(1_000_000, 0, "claude-sonnet-4-6")
    monkeypatch.setattr(token_summary, "token_price_factor", token_summary.BATCH_TOKEN_DISCOUNT)
    assert estimate_cost(1_000_000, 0, "claude-sonnet-4-6") == pytest.approx(full / 2)


def test_call_cost_adds_web_searches_at_full_price():
    result = {"input_tokens": 0, "output_tokens": 0, "web_searches": 3, "model": "claude-sonnet-4-6"}
    assert call_cost(result) == pytest.approx(0.03)


def test_summary_counts_cached_input_and_its_share(caplog):
    results = [
        {"label": "a", "input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 900},
        {"label": "b", "input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 900},
        {"label": "failed before any tokens", "status": "error"},
    ]
    with caplog.at_level(logging.INFO):
        log_token_summary(results)
    assert "2000 in, 45% of input read from cache / 20 out" in caplog.text
