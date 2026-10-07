"""Explicit ADT_STR reduced-vocabulary → General MIDI conversion.

The upstream decoder writes its *custom* 35..60 pitches, NOT standard GM.
In particular custom 44/46/48 mean open hat/crash/ride, not pedal hat/open
hat/high-mid tom. Do not apply a GM kit map before this conversion.
Source: pier-maker92/ADT_STR utils/mapping_utils.py @ 77dbef225e7029478ebfb916f20c7a00274f0f12.
"""

# Some custom classes combine GM instruments. The value is a representative,
# not a claim that the model distinguishes every GM articulation.
ADT_TO_GM = {
    35: 35, 36: 36, 37: 37, 38: 38, 39: 39, 40: 40,
    41: 41, 42: 42, 43: 44, 44: 46, 45: 47, 46: 49,
    47: 50, 48: 51, 49: 52, 50: 54, 51: 55, 52: 56,
    53: 58, 54: 64, 55: 70, 56: 71, 57: 73, 58: 75,
    59: 78, 60: 80,
}

# Eleven editable kit voices. Related cymbal/tom variants are simplified;
# non-kit percussion is kept in the raw MIDI/review data, not invented as snare.
DRUM_MAP = {35: 36, 36: 36, 37: 37, 38: 38, 40: 38, 42: 42, 44: 44, 46: 46,
            41: 45, 43: 45, 45: 45, 47: 47, 48: 47, 50: 50,
            49: 49, 52: 49, 55: 49, 57: 49, 51: 51, 53: 51, 59: 51}
DRUM_PITCHES = set(DRUM_MAP.values())
FOOT_PITCHES = {36, 44}


def normalize_drums(events):
    """Return representable notes plus explicit simplification/omission details."""
    from collections import Counter
    normalized, unsupported, simplified = [], [], Counter()
    # Aliases that cannot have been separately distinguished by ADT_STR itself
    # are still recorded, so imported GM results remain auditable too.
    for start, end, pitch, amp in events:
        if pitch not in DRUM_MAP:
            unsupported.append((start, end, pitch, amp))
            continue
        target = DRUM_MAP[pitch]
        if target != pitch:
            simplified[f"{pitch}->{target}"] += 1
        normalized.append((start, end, target, amp))
    # Two aliases at the same instant become one kit hit. Preserve max velocity.
    merged = {}
    for start, end, pitch, amp in normalized:
        key = (start, pitch)
        previous = merged.get(key, (end, amp))
        merged[key] = (max(previous[0], end), max(previous[1], amp))
    result = sorted((start, end, pitch, amp) for (start, pitch), (end, amp) in merged.items())
    return result, {"raw_note_count": len(events), "kit_note_count": len(result),
                    "unsupported_count": len(unsupported), "unsupported_events": unsupported,
                    "simplified_counts": dict(simplified), "merged_count": len(normalized) - len(result)}


def adt_to_gm(pitch):
    if pitch not in ADT_TO_GM:
        raise ValueError(f"Unexpected ADT_STR custom drum pitch: {pitch}")
    return ADT_TO_GM[pitch]
