"""Optional hi-hat recall aid, never a reference-assisted correction."""
from bisect import bisect_left, insort

# Include GM variants even when the score editor simplifies them later.
CYMBALS = {42, 44, 46, 49, 51, 52, 53, 55, 57, 59}


def recover_hihats(neural, spectral):
    """Preserve every model event and add isolated spectral closed-hat candidates.

    The spectral detector cannot classify open hats/ride/crash. Suppress candidates
    near any model cymbal, but do not replace or delete model notes. No beat grid,
    known pattern or reference score participates in this decision.
    """
    occupied = sorted(e[0] for e in neural if e[2] in CYMBALS)
    additions = []
    candidates = [e for e in spectral if e[2] == 42]
    for event in sorted(candidates):
        start = event[0]
        index = bisect_left(occupied, start)
        if any(abs(t - start) <= .05 + 1e-9 for t in occupied[max(0, index - 1):index + 1]):
            continue
        additions.append(event)
        insort(occupied, start)
    return sorted([*neural, *additions]), {"method": "spectral-hihat-recovery-v1",
                                          "candidate_count": len(candidates), "added_count": len(additions),
                                          "added_events": additions, "suppression_seconds": .05}
