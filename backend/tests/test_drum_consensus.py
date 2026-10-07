import pytest

from backend.drum_consensus import consensus_events


def hit(t, p=36):
    return (t, t + .1, p, .8)


def test_one_pass_cannot_self_vote_and_kick_is_not_relabelled_hihat():
    result, review = consensus_events([[hit(0), hit(0)], [hit(0, 42)], [hit(.01, 42)]])
    assert len(result) == 1 and result[0][2] == 42
    assert review["rejected_count"] == 2
    assert not review["agreement_is_confidence"]


def test_simultaneous_kick_hat_and_gm_aliases_survive():
    result, _ = consensus_events([[hit(.1, 35), hit(.1, 42)], [hit(.11), hit(.11, 42)], [hit(.09), hit(.09, 42)]])
    assert len(result) == 2 and result[0][0] == pytest.approx(.1)
    assert {e[2] for e in result} == {35, 42}


def test_no_transitive_chain_and_repeated_hits_are_one_to_one():
    result, review = consensus_events([[hit(0), hit(.04)], [hit(.01), hit(.05)], [hit(.02), hit(.06)]])
    assert len(result) == 2 and all(g["votes"] == 3 for g in review["candidates"])
    result, review = consensus_events([[hit(0)], [hit(.04)], [hit(.08)]])
    assert len(result) == 1 and review["rejected_count"] == 1
    assert result[0][0] == pytest.approx(.02)


def test_absence_and_unsupported_gm_percussion_are_not_fabricated_or_dropped():
    assert consensus_events([[], [], []])[0] == []
    assert consensus_events([[hit(.2, 56)], [hit(.2, 56)], []])[0][0][2] == 56


@pytest.mark.parametrize("event", [(float("nan"), .1, 36, .8), (0, 0, 36, .8), (-1, 0, 36, .8), (0, .1, 34, .8), (0, .1, 36, 2)])
def test_rejects_invalid_candidates(event):
    with pytest.raises(ValueError):
        consensus_events([[event], []])
