"""Evidence for reviewing pitched transcription, never automatic octave removal."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
from pathlib import Path

from .note_artifacts import audio_sha256, safe_provenance

PITCHED_INSTRUMENTS = {"guitar", "piano", "synthesizer"}
MAX_REVIEW_BYTES = 16 * 1024 * 1024


def octave_overlaps(events, *, maximum=10_000):
    """Flag coincident octave notes as ambiguous, including genuine octaves.

    A harmonic and a real, softly played octave can share the same onset and
    envelope. No event is removed or relabelled on the strength of this test.
    Pairs refer to indices in the preserved input-to-notation event list.
    """
    groups = defaultdict(list)
    for index, event in enumerate(events):
        groups[event[2]].append((event[0], index))
    for group in groups.values():
        group.sort()
    starts = {pitch: [a for a, _ in group] for pitch, group in groups.items()}
    pairs = []
    for upper_index, (a, b, pitch, _) in enumerate(events):
        for lower_pitch in (pitch - 12, pitch - 24):
            times = starts.get(lower_pitch, [])
            group = groups.get(lower_pitch, [])
            for position in range(bisect_left(times, a - .08), bisect_right(times, a + .08)):
                lower_index = group[position][1]
                lo, hi, _, _ = events[lower_index]
                if min(b, hi) - max(a, lo) < .7 * min(b - a, hi - lo):
                    continue
                if len(pairs) >= maximum:
                    return pairs, True
                pairs.append({"lower_event_index": lower_index, "upper_event_index": upper_index,
                              "start_seconds": min(a, lo), "lower_pitch": lower_pitch, "upper_pitch": pitch})
    return pairs, False


def write_pitched_review(folder, *, instrument, source, duration, baseline, events, decoding, description):
    if instrument not in PITCHED_INSTRUMENTS:
        raise ValueError("지원하지 않는 다성 악기 검토 데이터예요.")
    if len(baseline) > 30_000 or len(events) > 30_000:
        raise ValueError("채보 검토 음표 수가 제한을 초과해요.")
    pairs, truncated = octave_overlaps(events)
    summary = {"method": "onset-relative-sustain-v1", "baseline_count": len(baseline),
               "output_count": len(events), "octave_overlap_count": len(pairs), "octave_overlap_truncated": truncated}
    if folder is not None:
        source = Path(source)
        payload = {"schema": "akbo.pitched-transcription-review", "schema_version": 1,
                   "instrument": instrument, "duration": duration, "source": {"file": source.name, "sha256": audio_sha256(source)},
                   "provenance": safe_provenance(description), "summary": summary,
                   "semantics": {"time_origin": "source-audio-start-before-score-offset",
                                 "reflects_manual_score_edits": False, "ground_truth": False,
                                 "activations_are_confidence": False,
                                 "octave_overlaps_are_errors": False},
                   "baseline_events": baseline, "events": events, "decoding": decoding,
                   "possible_octave_overlaps": pairs}
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > MAX_REVIEW_BYTES:
            raise ValueError("채보 검토 데이터가 파일 크기 제한을 초과해요.")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{instrument}.transcription.json").write_bytes(data)
    return summary
