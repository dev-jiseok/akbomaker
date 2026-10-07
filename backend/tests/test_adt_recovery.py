"""Selective decoding contracts; these do not measure musical accuracy."""
from types import SimpleNamespace

import numpy as np
import pytest

from backend import adt_recovery


def setup_sampler(tokens, monkeypatch, retry=(2, 4, 336, 500, 3), stats=None):
    source = np.zeros((1, 61440))
    original = np.array([tokens], dtype=int)
    candidate = np.array([retry], dtype=int)
    calls = []
    model = SimpleNamespace(config=SimpleNamespace(plain=True, input_sec=2.56),
                            _akbo_add_velocity=True, _akbo_decoding_stats={"stale": True},
                            _akbo_recovery_stats={"attempted": True})

    def greedy(**options):
        assert options["src"] is source
        assert model._akbo_decoding_stats is None
        calls.append(("greedy", options))
        return original

    def constrained(current, **options):
        assert current is model and options["src"] is source
        calls.append(("retry", options))
        model._akbo_decoding_stats = {"budget_limited": False, "forced_tokens": 1} if stats is None else stats
        return candidate

    model._akbo_greedy_sample = greedy
    monkeypatch.setattr(adt_recovery, "constrained_sample", constrained)
    return model, source, original, candidate, calls


@pytest.mark.parametrize("tokens", [
    [2, 3], [2, 0, 3], [2, 4, 336, 500, 3],
    [2, 4, 336, 500, 4, 342, 500, 3],
    # This policy is NOT a duplicate filter on complete valid outputs.
    [2, 4, 336, 500, 4, 336, 500, 3],
])
def test_healthy_output_is_same_object_without_retry_or_stale_metadata(monkeypatch, tokens):
    model, source, original, _, calls = setup_sampler(tokens, monkeypatch)
    result = adt_recovery.retrying_sample(model, source, None, None, max_length=512)
    assert result is original
    assert [c[0] for c in calls] == ["greedy"]
    assert model._akbo_decoding_stats is None
    assert model._akbo_recovery_stats == {"method": "guarded-greedy-v2", "attempted": False,
                                         "selected": "greedy", "unresolved": False, "syntax_is_accuracy": False}


@pytest.mark.parametrize("tokens,reason", [
    ([2, 4, 336, 500], "missing_eos"),
    ([2, 4, 336, 3], "incomplete_events"),
    ([2, 999, 3], "invalid_tokens"),
    ([2, 50, 336, 500, 4, 342, 500, 3], "nonmonotonic_events"),
    ([4, 336, 500, 3], "missing_bos"),
    ([], "missing_eos"),
])
def test_only_broken_output_retried_once_and_original_tokens_retained(monkeypatch, tokens, reason):
    model, source, _, candidate, calls = setup_sampler(tokens, monkeypatch)
    result = adt_recovery.retrying_sample(model, source, "source-mask", "target-mask", max_length=512)
    assert result is candidate
    assert [c[0] for c in calls] == ["greedy", "retry"]
    assert calls[0][1] == calls[1][1]
    audit = model._akbo_recovery_stats
    assert audit["attempted"] and not audit["unresolved"]
    assert audit["selected"] == "constrained" and not audit["syntax_is_accuracy"]
    assert audit["original"]["tokens"] == tokens
    assert reason in audit["original"]["reasons"]
    assert audit["retry"]["tokens"] == candidate[0].tolist()


def test_token_budget_truncation_recorded_independently(monkeypatch):
    model, source, _, _, _ = setup_sampler([2, 4, 336, 500], monkeypatch)
    adt_recovery.retrying_sample(model, source, None, None, max_length=4)
    assert model._akbo_recovery_stats["original"]["truncated"]


@pytest.mark.parametrize("retry,stats", [
    ([2, 4, 336, 500], None),
    ([2, 4, 336, 3], None),
    ([2, 3], {"budget_limited": True}),
])
def test_unsuccessful_retry_keeps_original_and_marks_unresolved(monkeypatch, retry, stats):
    model, source, original, _, calls = setup_sampler([2, 4, 336], monkeypatch, retry, stats)
    assert adt_recovery.retrying_sample(model, source, None, None, max_length=512) is original
    assert len(calls) == 2  # No unbounded retry loop.
    assert model._akbo_recovery_stats["unresolved"]
    assert model._akbo_recovery_stats["selected"] == "greedy"
    assert "retry" in model._akbo_recovery_stats
    assert model._akbo_decoding_stats is None


def test_over_budget_retry_cannot_be_reported_as_recovered(monkeypatch):
    model, source, original, _, _ = setup_sampler([2, 4, 336], monkeypatch)
    assert adt_recovery.retrying_sample(model, source, None, None, max_length=4) is original
    assert "over_budget" in model._akbo_recovery_stats["retry"]["reasons"]


@pytest.mark.parametrize("forced", [2, 119, -1, None, True, 1.0])
def test_high_intervention_or_missing_audit_never_replaces_original(monkeypatch, forced):
    model, source, original, _, calls = setup_sampler([2, 4, 336], monkeypatch,
        stats={"budget_limited": False, "forced_tokens": forced})
    assert adt_recovery.retrying_sample(model, source, None, None) is original
    audit = model._akbo_recovery_stats
    assert audit["unresolved"] and audit["selected"] == "greedy"
    assert audit["rejected_reasons"]
    assert audit["maximum_forced_corrections"] == 1
    assert len(calls) == 2 and model._akbo_decoding_stats is None


def test_runtime_failure_is_not_silently_converted_to_success(monkeypatch):
    model, source, _, _, _ = setup_sampler([2, 4, 336], monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError("non-finite model output")
    monkeypatch.setattr(adt_recovery, "constrained_sample", fail)
    with pytest.raises(ValueError, match="non-finite"):
        adt_recovery.retrying_sample(model, source, None, None)


@pytest.mark.parametrize("field,value", [("plain", False), ("velocity", False), ("batch", 2), ("start", 0), ("end", 1)])
def test_unsupported_contract_fails_before_inference(monkeypatch, field, value):
    model, source, _, _, calls = setup_sampler([2, 3], monkeypatch)
    start, end = 2, 3
    if field == "plain":
        model.config.plain = value
    elif field == "velocity":
        model._akbo_add_velocity = value
    elif field == "batch":
        source = np.zeros((value, 10))
    elif field == "start":
        start = value
    else:
        end = value
    with pytest.raises(ValueError, match="Guarded ADT"):
        adt_recovery.retrying_sample(model, source, None, None, start_token=start, end_token=end)
    assert not calls
