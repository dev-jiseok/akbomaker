"""Concert-pitch string/fret assignments. Never octave-fold unplayable notes."""
TUNINGS = {"bass": [43, 38, 33, 28], "guitar": [64, 59, 55, 50, 45, 40]}


def default_tab(inst):
    return {"mode": "both", "tuning": TUNINGS[inst].copy()} if inst in TUNINGS else None


def assign_positions(notes, tuning):
    """Retain manual positions, match simultaneous onsets to distinct free strings.

    Small bounded search favours low frets and a stable hand position. This is a
    fingering suggestion, not inference of the player's actual string choice.
    Unplayable / over-polyphonic detections remain unassigned and visible in staff.
    """
    result = [dict(n) for n in notes]
    active, hand = [], 3
    groups = {}
    for note in result:
        groups.setdefault(note["start"], []).append(note)
    for start, notes_at_start in sorted(groups.items()):
        active = [n for n in active if n["start"] + n["length"] > start]
        group = sorted(notes_at_start, key=lambda n: -n["pitch"])
        used = {n["string"] for n in active if n.get("string") is not None}
        fixed = [n for n in group if n.get("string") is not None]
        used.update(n["string"] for n in fixed)
        pending = [n for n in group if n.get("string") is None]
        candidates = [[(s + 1, n["pitch"] - p) for s, p in enumerate(tuning)
                       if s + 1 not in used and 0 <= n["pitch"] - p <= 24] for n in pending]
        # Dynamic programming over occupied strings has at most 2^7 states,
        # including for very dense detections. A large unmatched penalty gives
        # maximum-cardinality matching before optimizing fret/hand movement.
        initial = sum(1 << (s - 1) for s in used)
        states = {initial: (0, [])}
        for choices in candidates:
            following = {}
            for mask, (cost, path) in states.items():
                for string, fret in [*choices, (None, None)]:
                    bit = 1 << (string - 1) if string is not None else 0
                    if bit & mask:
                        continue
                    next_mask = mask | bit
                    next_cost = cost + (1000 if fret is None else fret * .2 + (abs(fret - hand) if fret else 1))
                    if next_mask not in following or next_cost < following[next_mask][0]:
                        following[next_mask] = next_cost, [*path, (string, fret)]
            states = following
        positions = min(states.values(), key=lambda state: state[0])[1]
        for note, (string, fret) in zip(pending, positions):
            note.update(string=string, fret=fret)
        frets = [n["fret"] for n in group if n.get("fret")]
        if frets:
            hand = sum(frets) / len(frets)
        active.extend(group)
    return result
