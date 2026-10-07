"""Grammar tests do not require the optional torch/model environment."""

from types import SimpleNamespace

import pytest

from backend.adt_decoding import ADTTokenGrammar, PITCH_TOKENS, constrained_sample
from backend.adt_tokens import decode_tokens


def append(grammar, tokens):
    for token in tokens:
        grammar.consume(token)


def test_complete_triples_have_correct_token_domains():
    grammar = ADTTokenGrammar()
    assert set(grammar.allowed_tokens()) == {0, 3, *range(4, 260)}
    grammar.consume(4)
    assert grammar.allowed_tokens() == tuple(range(335, 361))
    grammar.consume(336)
    assert grammar.allowed_tokens() == tuple(range(401, 528))
    append(grammar, [500, 3])
    notes, review = decode_tokens(grammar.tokens)
    assert notes == [(0.0, 0.1, 36, 100)]
    assert review["invalid_tokens"] == review["incomplete_events"] == 0
    assert grammar.finished and grammar.allowed_tokens() == ()


def test_simultaneous_events_keep_any_pitch_order_and_all_percussion_classes():
    grammar = ADTTokenGrammar()
    for pitch in reversed(PITCH_TOKENS):
        append(grammar, [4, pitch, 500])
    assert len(grammar.seen) == 26
    assert 4 not in grammar.allowed_tokens()  # all pitches at this timestamp are exhausted
    append(grammar, [5, 360, 401, 3])
    notes, review = decode_tokens(grammar.tokens)
    assert len(notes) == 27
    assert notes[-1][0] == .01
    assert review["nonmonotonic_events"] == 0


def test_same_onset_pitch_duplicate_forbidden_but_later_repeat_allowed():
    grammar = ADTTokenGrammar()
    append(grammar, [4, 336, 500, 4])
    assert 336 not in grammar.allowed_tokens()
    with pytest.raises(ValueError, match="invalid"):
        grammar.consume(336)
    append(grammar, [342, 500, 5, 336, 500, 3])
    assert len(grammar.seen) == 3


def test_onsets_never_go_backwards_and_eos_cannot_cut_a_triple():
    grammar = ADTTokenGrammar()
    grammar.consume(100)
    for invalid in [0, 1, 2, 3, 99, 500]:
        with pytest.raises(ValueError, match="invalid"):
            grammar.consume(invalid)
    grammar.consume(340)
    with pytest.raises(ValueError, match="invalid"):
        grammar.consume(3)
    grammar.consume(501)
    assert 100 in grammar.allowed_tokens()
    assert not any(4 <= token < 100 for token in grammar.allowed_tokens())
    assert 0 not in grammar.allowed_tokens()


@pytest.mark.parametrize("budget", range(2, 30))
def test_every_budget_can_finish_without_incomplete_events(budget):
    grammar = ADTTokenGrammar(max_length=budget)
    while not grammar.finished:
        allowed = grammar.allowed_tokens()
        if grammar.phase == "onset":
            token = next((token for token in allowed if token >= 4), 3)
        else:
            token = allowed[0]
        grammar.consume(token)
    assert grammar.tokens[-1] == 3 and len(grammar.tokens) <= budget
    assert (len(grammar.tokens) - 2) % 3 == 0
    _, review = decode_tokens(grammar.tokens, max_length=budget)
    assert review["invalid_tokens"] == review["incomplete_events"] == 0
    assert not review["truncated"]


def test_silent_sequence_is_initial_only_and_terminal():
    grammar = ADTTokenGrammar(max_length=3)
    grammar.consume(0)
    assert grammar.allowed_tokens() == (3,)
    with pytest.raises(ValueError, match="invalid"):
        grammar.consume(4)
    grammar.consume(3)
    assert grammar.tokens == [2, 0, 3]
    with pytest.raises(ValueError, match="invalid"):
        grammar.consume(3)
    assert ADTTokenGrammar(max_length=2).allowed_tokens() == (3,)


@pytest.mark.parametrize("budget", [0, 1, -1, 2.5, True])
def test_invalid_budget_rejected(budget):
    with pytest.raises(ValueError, match="max_length"):
        ADTTokenGrammar(max_length=budget)


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), 3.0])
def test_invalid_or_overlapping_onset_range_rejected(seconds):
    with pytest.raises(ValueError, match="input_seconds"):
        ADTTokenGrammar(input_seconds=seconds)


def test_triple_format_and_sampling_configuration_fail_clearly_without_torch():
    with pytest.raises(ValueError, match="add_velocity"):
        ADTTokenGrammar(add_velocity=False)
    model = SimpleNamespace(config=SimpleNamespace(plain=False, input_sec=2.56))
    source = SimpleNamespace(ndim=2, shape=(1, 61440))
    with pytest.raises(ValueError, match="plain"):
        constrained_sample(model, source, None, None)
    model.config.plain = True
    with pytest.raises(ValueError, match="BOS"):
        constrained_sample(model, source, None, None, start_token=0)
    source.shape = (2, 61440)
    with pytest.raises(ValueError, match="batch size 1"):
        constrained_sample(model, source, None, None)
    source.shape = (1, 61440)
    model._akbo_add_velocity = False
    with pytest.raises(ValueError, match="add_velocity"):
        constrained_sample(model, source, None, None)
