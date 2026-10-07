"""Reference-based note evaluation; never infer accuracy from a note count."""
from collections import defaultdict
from bisect import bisect_left
import math
import statistics

# Decimal timestamps such as 1.30 - 1.25 can exceed 0.05 by floating-point
# roundoff. One picosecond handles that representation error, not musical drift.
_TIME_EPSILON = 1e-12


class _OnsetPool:
    """Pitch-indexed onsets with deletion, without materializing pairwise edges."""

    def __init__(self, events):
        self.events = sorted(events, key=lambda event: (event["start"], event["pitch"], event["end"]))
        self.by_pitch = defaultdict(list)
        self.locations = {}
        for event_id, event in enumerate(self.events):
            group = self.by_pitch[event["pitch"]]
            self.locations[event_id] = (event["pitch"], len(group))
            group.append(event_id)
        self.starts = {pitch: [self.events[event_id]["start"] for event_id in group]
                       for pitch, group in self.by_pitch.items()}
        # Disjoint-set successors/predecessors skip deleted notes in amortized
        # near-constant time. Sentinel n is "no successor"; 0 is "no predecessor".
        self.next = {pitch: list(range(len(group) + 1)) for pitch, group in self.by_pitch.items()}
        self.previous = {pitch: list(range(len(group) + 1)) for pitch, group in self.by_pitch.items()}

    @staticmethod
    def _find(parents, index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def remove(self, event_id):
        pitch, index = self.locations[event_id]
        self.next[pitch][index] = self._find(self.next[pitch], index + 1)
        self.previous[pitch][index + 1] = self._find(self.previous[pitch], index)

    def nearest(self, start, excluded_pitch, tolerance):
        """Nearest live onset of a different pitch; ties have a stable ordering."""
        best = None
        for pitch, times in self.starts.items():
            if pitch == excluded_pitch:
                continue
            pivot = bisect_left(times, start)
            following = self._find(self.next[pitch], pivot)
            preceding = self._find(self.previous[pitch], pivot) - 1
            if preceding >= 0:
                # Pick the first live duplicate at this onset for stable end/id ties.
                preceding = self._find(self.next[pitch], bisect_left(times, times[preceding]))
            for index in (preceding, following):
                if not 0 <= index < len(times):
                    continue
                difference = abs(times[index] - start)
                if difference > tolerance + _TIME_EPSILON:
                    continue
                event_id = self.by_pitch[pitch][index]
                event = self.events[event_id]
                key = (difference, event["start"], pitch, event["end"], event_id)
                if best is None or key < best:
                    best = key
        return best[-1] if best is not None else None

    def has_equal_alternative(self, selected_id, start, excluded_pitch, distance, tolerance):
        """Check the original unmatched pool, including subsequently paired notes.

        This deliberately does not use live/deleted state: all pairs in an
        ambiguous simultaneous chord must stay marked, not only the first pair.
        """
        epsilon = _TIME_EPSILON
        for pitch, times in self.starts.items():
            if pitch == excluded_pitch:
                continue
            for target in (start - distance, start + distance):
                index = bisect_left(times, target - epsilon)
                # At most one candidate is the selected note, so two suffice
                # even when thousands of duplicate onsets share this position.
                for candidate_index in range(index, min(index + 2, len(times))):
                    event_id = self.by_pitch[pitch][candidate_index]
                    difference = abs(times[candidate_index] - start)
                    if (event_id != selected_id and difference <= tolerance + epsilon
                            and math.isclose(difference, distance, rel_tol=0, abs_tol=epsilon)):
                        return True
        return False


def _possible_confusions(missing, extra, tolerance):
    """Diagnose possible substitutions without changing the primary matching.

    Chronological, one-to-one greedy nearest-onset matching is intentionally a
    heuristic, not an optimal assignment or evidence of a causal classifier
    error. MIDI has at most 128 pitch groups, so searches are bounded by those
    groups rather than a potentially quadratic number of overlapping notes.
    Storage is linear in the unmatched note count.
    """
    reference_pool, estimated_pool = _OnsetPool(missing), _OnsetPool(extra)
    pairs, counts = [], defaultdict(int)
    for reference_id, reference in enumerate(reference_pool.events):
        estimated_id = estimated_pool.nearest(reference["start"], reference["pitch"], tolerance)
        if estimated_id is None:
            continue
        estimated = estimated_pool.events[estimated_id]
        distance = abs(estimated["start"] - reference["start"])
        ambiguity_sides = []
        if reference_pool.has_equal_alternative(reference_id, estimated["start"], estimated["pitch"], distance, tolerance):
            ambiguity_sides.append("reference")
        if estimated_pool.has_equal_alternative(estimated_id, reference["start"], reference["pitch"], distance, tolerance):
            ambiguity_sides.append("estimated")
        pairs.append({"reference_pitch": reference["pitch"], "estimated_pitch": estimated["pitch"],
                      "reference_start": reference["start"], "estimated_start": estimated["start"],
                      "delta_ms": round((estimated["start"] - reference["start"]) * 1000, 3),
                      "ambiguous": bool(ambiguity_sides), "ambiguity_sides": ambiguity_sides})
        counts[f'{reference["pitch"]}->{estimated["pitch"]}'] += 1
        estimated_pool.remove(estimated_id)
    return pairs, dict(sorted(counts.items()))


def compare_events(reference, estimated, tolerance=.05, *, include_errors=False, duration_tolerance_ratio=None):
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Onset tolerance must be a positive finite number")
    if duration_tolerance_ratio is not None and (isinstance(duration_tolerance_ratio, bool)
            or not math.isfinite(duration_tolerance_ratio) or not 0 <= duration_tolerance_ratio <= 1):
        raise ValueError("Duration tolerance ratio must be a finite number between 0 and 1")
    for event in [*reference, *estimated]:
        if len(event) < 3:
            raise ValueError("An event needs start, end and MIDI pitch")
        start, end, pitch = event[:3]
        if not all(math.isfinite(v) for v in (start, end, pitch)) or start < 0 or end <= start or int(pitch) != pitch or not 0 <= pitch <= 127:
            raise ValueError("Events require finite nonnegative times, positive durations and integer MIDI pitches")
    expected, actual = defaultdict(list), defaultdict(list)
    for start, end, pitch, *_ in reference:
        expected[int(pitch)].append((float(start), float(end)))
    for start, end, pitch, *_ in estimated:
        actual[int(pitch)].append((float(start), float(end)))
    matches, onset_errors, offset_errors, instruments = 0, [], [], {}
    duration_matches, duration_failures = 0, []
    missing, extra = [], []
    for pitch in sorted(expected.keys() | actual.keys()):
        ref, est = sorted(expected[pitch]), sorted(actual[pitch])
        a = b = hits = 0
        while a < len(ref) and b < len(est):
            difference = est[b][0] - ref[a][0]
            if abs(difference) <= tolerance + _TIME_EPSILON:
                hits += 1
                onset_errors.append(abs(difference))
                offset_errors.append(abs(est[b][1] - ref[a][1]))
                if duration_tolerance_ratio is not None:
                    allowed = max(.05, duration_tolerance_ratio * (ref[a][1] - ref[a][0]))
                    if abs(est[b][1] - ref[a][1]) <= allowed + 1e-12:
                        duration_matches += 1
                    else:
                        duration_failures.append({"pitch": pitch, "reference_start": ref[a][0],
                                                  "reference_end": ref[a][1], "estimated_start": est[b][0],
                                                  "estimated_end": est[b][1], "allowed_offset_error_seconds": allowed})
                a += 1
                b += 1
            elif difference < 0:
                extra.append({"start": est[b][0], "end": est[b][1], "pitch": pitch})
                b += 1
            else:
                missing.append({"start": ref[a][0], "end": ref[a][1], "pitch": pitch})
                a += 1
        missing.extend({"start": t, "end": end, "pitch": pitch} for t, end in ref[a:])
        extra.extend({"start": t, "end": end, "pitch": pitch} for t, end in est[b:])
        matches += hits
        instruments[pitch] = {"reference": len(ref), "estimated": len(est), "matched": hits}
    precision = matches / len(estimated) if estimated else 0.
    recall = matches / len(reference) if reference else 0.
    result = {"onset_tolerance_seconds": tolerance, "reference_notes": len(reference), "estimated_notes": len(estimated),
            "matched_notes": matches, "missing_notes": len(reference) - matches, "extra_notes": len(estimated) - matches,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.,
            "median_onset_error_ms": round(statistics.median(onset_errors) * 1000, 1) if onset_errors else None,
            "median_offset_error_ms": round(statistics.median(offset_errors) * 1000, 1) if offset_errors else None,
            "by_pitch": instruments}
    if duration_tolerance_ratio is not None:
        result["duration_diagnostics"] = {
            "method": "existing-onset-pairs-offset-check-v1", "offset_ratio": duration_tolerance_ratio,
            "minimum_offset_tolerance_seconds": .05, "matched_with_valid_offset": duration_matches,
            "onset_matches_with_wrong_offset": matches - duration_matches,
            "precision": round(duration_matches / len(estimated), 4) if estimated else 0.,
            "recall": round(duration_matches / len(reference), 4) if reference else 0.,
            "f1": round(2 * duration_matches / (len(reference) + len(estimated)), 4) if reference or estimated else 0.,
            "warning": "Offset check of existing chronological onset matches, not a new maximum bipartite matching. "
                       "Use only when reference offsets are meaningful; not for drum hit placeholder lengths.",
        }
        if include_errors:
            result["duration_diagnostics"]["wrong_offset_events"] = duration_failures
    if include_errors:
        result["missing_events"] = sorted(missing, key=lambda e: (e["start"], e["pitch"]))
        result["extra_events"] = sorted(extra, key=lambda e: (e["start"], e["pitch"]))
        pairs, counts = _possible_confusions(missing, extra, tolerance)
        result["possible_confusions"] = pairs
        result["confusion_counts"] = counts
        result["confusion_diagnostics"] = {
            "method": "unmatched-different-pitch-nearest-onset-v1",
            "heuristic_only": True,
            "warning": "Possible substitutions are diagnostic heuristics, not proof of instrument confusion; "
                       "simultaneous polyphony and independent missing/extra notes can produce these pairings.",
            "possible_confusion_count": len(pairs),
            "ambiguous_pairs": sum(pair["ambiguous"] for pair in pairs),
        }
    return result
