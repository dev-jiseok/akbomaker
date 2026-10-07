"""Strict parsing of ADT_STR's onset/pitch/velocity token triples.

Do not zip independent onset/pitch/velocity dictionaries: a malformed token
would associate later pitches with earlier onsets. Incomplete predictions are
reported and retained as raw tokens for inspection, never filled with notes.
No torch dependency, so the main app and unit tests stay independent of ADT.
"""
import math

from .drum_mapping import ADT_TO_GM


def decode_tokens(tokens, *, input_seconds=2.56, add_velocity=True, max_length=512):
    tokens = [int(t) for t in tokens]
    notes, invalid, incomplete, backwards = [], 0, 0, 0
    i, previous = 0, -1
    width = 3 if add_velocity else 2
    while i < len(tokens):
        token = tokens[i]
        if token == 3:  # EOS is terminal, even if a padded batch follows.
            break
        if (i == 0 and token == 2) or (i == 1 and token == 0):
            i += 1
            continue
        if not 4 <= token < 4 + math.ceil(input_seconds * 100):
            invalid += 1
            i += 1
            continue
        if i + width > len(tokens) or 3 in tokens[i:i + width]:
            incomplete += 1
            break
        pitch = tokens[i + 1] - 300
        velocity = tokens[i + 2] - 400 if add_velocity else 100
        if pitch not in ADT_TO_GM or not 1 <= velocity <= 127:
            invalid += 1
            i += 1
            continue
        onset = (token - 4) / 100
        if onset < previous:
            backwards += 1
        previous = onset
        notes.append((onset, onset + .1, pitch, velocity))
        i += width
    return notes, {"invalid_tokens": invalid, "incomplete_events": incomplete, "nonmonotonic_events": backwards,
                   "truncated": 3 not in tokens and len(tokens) >= max_length, "tokens": tokens}
