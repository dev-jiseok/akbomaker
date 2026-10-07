"""Cross-context drum agreement. Agreement is not a calibrated confidence."""
from collections import defaultdict
import math
import statistics

from .drum_mapping import DRUM_MAP


def consensus_events(passes, *, tolerance=.05, minimum_votes=2):
    """Match same drum types one-to-one across shifted chunk boundaries.

    No beat pattern or reference labels participate. One pass cannot vote twice
    for an event, and cluster diameter is bounded to avoid chaining fast hits.
    All candidates, including rejected ones, remain in the audit report.
    """
    if not 2 <= len(passes) <= 5 or not 2 <= minimum_votes <= len(passes):
        raise ValueError("Consensus requires 2–5 passes and at least two votes")
    if not math.isfinite(tolerance) or not 0 < tolerance <= .1:
        raise ValueError("Invalid consensus matching window")
    by_pitch = defaultdict(list)
    for pass_id, events in enumerate(passes):
        if len(events) > 30_000:
            raise ValueError("Too many drum candidates")
        for event in events:
            if len(event) != 4 or not all(math.isfinite(v) for v in event):
                raise ValueError("Invalid drum candidate")
            a, b, pitch, amp = event
            if not 0 <= a < b or pitch != int(pitch) or not 35 <= pitch <= 81 or not 0 < amp <= 1:
                raise ValueError("Invalid drum candidate")
            by_pitch[DRUM_MAP.get(pitch, pitch)].append((a, pass_id, tuple(event)))
    groups = []
    for pitch, candidates in sorted(by_pitch.items()):
        active = []
        for start, pass_id, event in sorted(candidates):
            active = [g for g in active if start - g["members"][0][1][0] <= tolerance + 1e-9]
            choices = [g for g in active if pass_id not in {p for p, _ in g["members"]}]
            if choices:
                group = min(choices, key=lambda g: (abs(start - statistics.median(e[0] for _, e in g["members"])), g["index"]))
            else:
                group = {"pitch": pitch, "members": [], "index": len(groups)}
                groups.append(group)
                active.append(group)
            group["members"].append((pass_id, event))
    accepted, review = [], []
    for group in groups:
        members = group["members"]
        start = statistics.median(e[0] for _, e in members)
        # Preserve a representative original GM subtype; aliases only affect voting.
        representative = min(members, key=lambda m: (abs(m[1][0] - start), m[0]))[1]
        event = (start, start + statistics.median(e[1] - e[0] for _, e in members),
                 representative[2], statistics.median(e[3] for _, e in members))
        keep = len(members) >= minimum_votes
        if keep:
            accepted.append(event)
        review.append({"event": event, "votes": len(members), "pass_ids": [p for p, _ in members], "accepted": keep})
    return sorted(accepted), {"method": "shifted-context-consensus-v1", "passes": len(passes),
                              "minimum_votes": minimum_votes, "tolerance_seconds": tolerance,
                              "accepted_count": len(accepted), "rejected_count": len(groups) - len(accepted),
                              "agreement_is_confidence": False, "candidates": sorted(review, key=lambda g: (g["event"][0], g["event"][2])),
                              "pass_events": passes}
