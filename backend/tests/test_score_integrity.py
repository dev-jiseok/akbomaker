"""Structure checks are not OMR correctness tests and never rewrite the score."""
import copy
import json
import xml.etree.ElementTree as ET

import pytest

from backend.score_integrity import audit, SCOPE


def child(parent, tag, text=None, **attributes):
    node = ET.SubElement(parent, tag, attributes)
    if text is not None:
        node.text = str(text)
    return node


def score(instrument="vocal", divisions=12, beats=4, beat_type=4):
    root = ET.Element("score-partwise", version="4.0")
    info = child(child(root, "part-list"), "score-part", id="P1")
    child(info, "part-name", instrument)
    if instrument == "drums":
        child(child(info, "score-instrument", id="hat"), "instrument-name", "Hi hat")
        child(child(info, "midi-instrument", id="hat"), "midi-unpitched", 43)
    part = child(root, "part", id="P1")
    measure = child(part, "measure", number="1")
    attrs = child(measure, "attributes")
    child(attrs, "divisions", divisions)
    time = child(attrs, "time")
    child(time, "beats", beats); child(time, "beat-type", beat_type)
    return root, part, measure, attrs


def note(measure, *, duration=12, typ="quarter", pitch="C", octave=4, alter=0, staff="1", voice="1", chord=False, rest=False, drum=False, dots=0, ratio=None, grace=False):
    item = child(measure, "note")
    if grace:
        child(item, "grace")
    if chord:
        child(item, "chord")
    if rest:
        child(item, "rest")
    elif drum:
        sound = child(item, "unpitched")
        child(sound, "display-step", "G"); child(sound, "display-octave", 5)
    else:
        sound = child(item, "pitch")
        child(sound, "step", pitch); child(sound, "alter", alter); child(sound, "octave", octave)
    if duration is not None:
        child(item, "duration", duration)
    if drum:
        child(item, "instrument", id="hat")
    child(item, "voice", voice)
    if typ is not None:
        child(item, "type", typ)
    for _ in range(dots):
        child(item, "dot")
    if ratio:
        modification = child(item, "time-modification")
        child(modification, "actual-notes", ratio[0]); child(modification, "normal-notes", ratio[1])
    child(item, "staff", staff)
    return item


def result(root, instrument="vocal"):
    text = ET.tostring(root, encoding="unicode")
    original = copy.deepcopy(root)
    report = audit(text, "P1", instrument)
    assert ET.tostring(root) == ET.tostring(original)
    assert text == ET.tostring(root, encoding="unicode")
    assert report["scope"] == SCOPE
    json.dumps(report, allow_nan=False)
    return report


def codes(root, instrument="vocal"):
    return [item["code"] for item in result(root, instrument)["issues"]]


def tie(item, typ, *, sound=True, visual=True, number=None):
    if sound:
        child(item, "tie", type=typ)
    if visual:
        notation = item.find("notations")
        if notation is None:
            notation = child(item, "notations")
        child(notation, "tied", type=typ, **({"number": number} if number else {}))


@pytest.mark.parametrize("instrument", ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"])
def test_valid_notes_all_six_instruments_are_checked_without_invented_missing_lyrics(instrument):
    root, _, measure, _ = score(instrument)
    note(measure, duration=48, typ="whole", drum=instrument == "drums")
    report = result(root, instrument)
    assert report["checked_measures"] == 1 and report["checked_notes"] == 1
    assert all(item["severity"] == "info" for item in report["issues"])
    assert all("lyric" not in item["code"] for item in report["issues"])


def test_known_type_dots_and_ratio_are_compared_using_exact_quarter_units():
    root, _, measure, _ = score(divisions=24)
    note(measure, duration=18, typ="eighth", dots=1)
    note(measure, duration=14, typ="quarter", dots=2, ratio=(3, 1))  # 7/12 quarters
    wrong = note(measure, duration=24, typ="eighth")
    report = result(root)
    mismatch = [item for item in report["issues"] if item["code"] == "note-duration-mismatch"]
    assert len(mismatch) == 1 and mismatch[0]["note_id"] == "m0n2"
    assert wrong.findtext("duration") == "24"


def test_nested_tuplet_cumulative_ratio_and_normal_display_units_do_not_false_alarm():
    root, _, measure, _ = score(divisions=15)
    for _ in range(15):
        item = note(measure, duration=4, typ="eighth", ratio=(15, 8))
        mod = item.find("time-modification")
        child(mod, "normal-type", "quarter"); child(mod, "normal-dot")
        notation = child(item, "notations")
        child(notation, "tuplet", type="start", number="1")
        child(notation, "tuplet", type="start", number="2")
    assert codes(root) == []


def test_whole_measure_rest_and_grace_are_exempt_from_visual_length_comparison():
    root, _, measure, _ = score(beats=3)
    note(measure, duration=None, typ="eighth", grace=True)
    rest = note(measure, duration=36, typ="whole", rest=True)
    rest.find("rest").set("measure", "yes")
    assert codes(root) == []


@pytest.mark.parametrize("attribute", ["implicit", "non-controlling"])
def test_intentionally_short_measure_is_not_marked_underfull(attribute):
    root, _, measure, _ = score()
    measure.set(attribute, "yes")
    note(measure)
    assert "measure-underfull" not in codes(root)


def test_free_meter_and_absent_meter_do_not_guess_required_beats():
    for free in (False, True):
        root, _, measure, attrs = score()
        attrs.remove(attrs.find("time"))
        if free:
            child(child(attrs, "time"), "senza-misura")
        note(measure)
        assert not {"measure-underfull", "measure-overfull"} & set(codes(root))


def test_underfull_is_informational_and_overfull_is_located_warning():
    root, part, first, _ = score()
    note(first)
    second = child(part, "measure", number="A")
    for _ in range(5):
        note(second)
    report = result(root)
    short = next(item for item in report["issues"] if item["code"] == "measure-underfull")
    long = next(item for item in report["issues"] if item["code"] == "measure-overfull")
    assert short["severity"] == "info"
    assert long["severity"] == "warning" and long["measure_index"] == 2 and long["measure_number"] == "A"


def test_polyphonic_chords_partial_second_voice_and_cross_staff_notes_do_not_sum_twice():
    root, _, measure, attrs = score("piano")
    child(attrs, "staves", 2)
    note(measure, duration=24, typ="half")
    note(measure, duration=24, typ="half", chord=True, pitch="E")
    note(measure, duration=24, typ="half", staff="2")  # same voice crosses staff
    child(child(measure, "backup"), "duration", 48)
    note(measure, duration=12, voice="2", staff="2")  # deliberately short second voice
    assert codes(root, "piano") == []


def test_forward_and_last_backup_do_not_create_false_underfill():
    root, _, measure, _ = score()
    note(measure)
    child(child(measure, "forward"), "duration", 36)
    child(child(measure, "backup"), "duration", 48)
    assert "measure-underfull" not in codes(root)


@pytest.mark.parametrize("problem", ["negative-backup", "zero-forward", "missing-duration", "bad-divisions", "orphan-chord"])
def test_malformed_timing_is_reported_without_crashing_or_rewriting(problem):
    root, _, measure, attrs = score()
    if problem == "negative-backup":
        child(child(measure, "backup"), "duration", 1)
    if problem == "zero-forward":
        child(child(measure, "forward"), "duration", 0)
    if problem == "bad-divisions":
        attrs.find("divisions").text = "0"
    note(measure, duration=None if problem == "missing-duration" else 12, chord=problem == "orphan-chord")
    assert {"timeline-movement-invalid", "note-timing-invalid", "divisions-invalid"} & set(codes(root))


def test_additive_meter_and_inherited_meter_changes_are_exact():
    root, part, measure, _ = score(beats="3+2", beat_type=8)
    note(measure, duration=30, typ="half", ratio=(4, 5))
    next_measure = child(part, "measure", number="2")
    note(next_measure, duration=30, typ="half", ratio=(4, 5))
    assert codes(root) == []


def test_global_meter_change_replaces_old_staff_specific_time():
    root, part, measure, attrs = score(beats=3)
    attrs.find("time").set("number", "1")
    note(measure, duration=36, typ="half", dots=1)
    second = child(part, "measure", number="2")
    current = child(child(second, "attributes"), "time")
    child(current, "beats", 4); child(current, "beat-type", 4)
    note(second, duration=48, typ="whole")
    assert codes(root) == []


def test_partially_known_polymeter_does_not_invent_a_global_underfull_measure():
    root, _, measure, attrs = score(beats=3)
    attrs.find("time").set("number", "1")
    note(measure, duration=36, typ="half", dots=1)
    child(child(measure, "backup"), "duration", 36)
    note(measure, duration=12, staff="2", voice="2")
    assert not {"measure-underfull", "measure-overfull"} & set(codes(root))


def test_mid_measure_meter_change_is_information_not_overfull_guess():
    root, _, measure, _ = score()
    note(measure, duration=24, typ="half")
    changed = child(child(measure, "attributes"), "time")
    child(changed, "beats", 1); child(changed, "beat-type", 4)
    note(measure)
    assert codes(root) == ["meter-change-review"]


def test_valid_cross_measure_ties_and_continuations_keep_exact_enharmonic_pitch():
    root, part, measure, _ = score()
    tie(note(measure, duration=48, typ="whole", pitch="C", alter=1), "start")
    middle = child(part, "measure", number="2")
    item = note(middle, duration=48, typ="whole", pitch="D", alter=-1)
    tie(item, "stop"); tie(item, "start")
    final = child(part, "measure", number="3")
    tie(note(final, duration=48, typ="whole", pitch="D", alter=-1), "stop")
    assert codes(root) == []


def test_orphan_tie_and_sound_notation_mismatch_are_distinct_located_issues():
    root, _, measure, _ = score()
    tie(note(measure, duration=48, typ="whole"), "start", visual=False)
    report = result(root)
    assert {"tie-start-unmatched", "tie-sound-notation-mismatch"} <= {item["code"] for item in report["issues"]}
    assert all(item.get("note_id") == "m0n0" for item in report["issues"])


def test_pickup_tie_and_microtonal_tie_are_valid_when_exactly_adjacent():
    root, part, measure, _ = score()
    measure.set("implicit", "yes")
    tie(note(measure, duration=12, alter="0.5"), "start")
    second = child(part, "measure", number="1")
    tie(note(second, duration=48, typ="whole", alter="0.5"), "stop")
    assert codes(root) == []


def test_free_meter_boundary_does_not_turn_unresolved_ties_into_orphan_claims():
    root, part, first, _ = score()
    tie(note(first, duration=48, typ="whole"), "start")
    free = child(part, "measure", number="X")
    child(child(child(free, "attributes"), "time"), "senza-misura")
    tie(note(free), "stop")
    assert "tie-start-unmatched" not in codes(root)


def test_cross_voice_numbered_counterpart_is_manual_review_instead_of_false_orphan():
    root, _, measure, _ = score()
    tie(note(measure, duration=24, typ="half"), "start", number="2")
    tie(note(measure, duration=24, typ="half", voice="2"), "stop")
    assert "tie-start-unmatched" not in codes(root) and "tie-stop-unmatched" not in codes(root)


@pytest.mark.parametrize("complexity", ["number", "repeat", "let-ring", "continue", "unison", "cross-staff", "cross-voice"])
def test_complex_ties_are_not_falsely_claimed_broken(complexity):
    root, _, measure, _ = score()
    first = note(measure, duration=24, typ="half")
    last = note(measure, duration=24, typ="half", staff="2" if complexity == "cross-staff" else "1", voice="2" if complexity == "cross-voice" else "1")
    if complexity == "let-ring":
        tie(first, "let-ring", sound=False)
    elif complexity == "continue":
        tie(first, "continue", sound=False)
    else:
        tie(first, "start", number="2" if complexity == "number" else None)
        tie(last, "stop", number="2" if complexity == "number" else None)
        if complexity == "repeat":
            child(child(measure, "barline"), "repeat", direction="backward")
        if complexity == "unison":
            measure.insert(list(measure).index(first) + 1, copy.deepcopy(first))
            measure[2].insert(0, ET.Element("chord"))
    found = codes(root)
    assert not {"tie-start-unmatched", "tie-stop-unmatched"} & set(found)
    if complexity not in {"let-ring"}:
        assert "tie-manual-review" in found


def test_drum_pitched_missing_and_unknown_instruments_are_not_guessed_from_position():
    root, _, measure, _ = score("drums")
    note(measure)
    missing = note(measure, drum=True); missing.remove(missing.find("instrument"))
    note(measure, drum=True).find("instrument").set("id", "unknown")
    note(measure, drum=True)
    assert set(codes(root, "drums")) == {"drum-pitched-note", "drum-instrument-missing", "drum-instrument-unmapped"}


def tab(root, attrs, item, *, fret=0, capo=0, transpose=0):
    child(child(attrs, "clef"), "sign", "TAB")
    details = child(attrs, "staff-details")
    child(details, "staff-lines", 4)
    for index, (step, octave) in enumerate([("E", 1), ("A", 1), ("D", 2), ("G", 2)], 1):
        tuning = child(details, "staff-tuning", line=str(index))
        child(tuning, "tuning-step", step); child(tuning, "tuning-octave", octave)
    child(details, "capo", capo)
    trans = child(attrs, "transpose"); child(trans, "chromatic", transpose)
    technical = child(child(item, "notations"), "technical")
    child(technical, "string", 1); child(technical, "fret", fret)
    return technical


def test_tab_pitch_comparison_applies_explicit_capo_and_transpose():
    root, _, measure, attrs = score("bass")
    item = note(measure, duration=48, typ="whole", pitch="G", octave=2)
    technical = tab(root, attrs, item, capo=2, transpose=2)
    assert codes(root, "bass") == []
    technical.find("fret").text = "1"
    assert codes(root, "bass") == ["tab-pitch-mismatch"]


@pytest.mark.parametrize("kind", ["harmonic", "bend", "muted", "missing-tuning"])
def test_special_tab_sound_is_manual_review_not_a_false_pitch_mismatch(kind):
    root, _, measure, attrs = score("bass")
    item = note(measure, duration=48, typ="whole", pitch="G", octave=4)
    technical = tab(root, attrs, item)
    if kind in {"harmonic", "bend"}:
        child(technical, kind)
    elif kind == "muted":
        child(item, "notehead", "x")
    else:
        attrs.remove(attrs.find("staff-details"))
    report = result(root, "bass")
    assert "tab-pitch-mismatch" not in {item["code"] for item in report["issues"]}
    assert report["issues"] and all(item["severity"] == "info" for item in report["issues"])


@pytest.mark.parametrize("instrument", ["piano", "synthesizer"])
def test_keyboard_single_staff_is_only_information_based_on_actual_notes(instrument):
    root, _, measure, attrs = score(instrument)
    child(attrs, "staves", 2)  # declaration alone does not prove left-hand notes exist
    note(measure, duration=48, typ="whole")
    assert codes(root, instrument) == ["keyboard-single-staff"]
    child(child(measure, "backup"), "duration", 48)
    note(measure, duration=48, typ="whole", staff="2", voice="2")
    assert codes(root, instrument) == []


def test_report_cap_counts_all_issues_without_truncating_the_source_scan():
    root, part, first, _ = score()
    for index in range(75):
        measure = first if index == 0 else child(part, "measure", number=str(index + 1))
        for _ in range(4):
            note(measure, typ="half")
    report = result(root)
    assert report["total_issues"] == 300 and len(report["issues"]) == 200 and report["truncated"]
    assert report["checked_notes"] == 300 and report["checked_measures"] == 75


def test_large_bounded_score_counts_all_9000_notes_without_pairwise_note_matching():
    root, part, first, _ = score(divisions=15)
    for index in range(600):
        measure = first if index == 0 else child(part, "measure", number=str(index + 1))
        for _ in range(15):
            note(measure, duration=4, typ="quarter")
    report = result(root)
    assert report["checked_notes"] == report["total_issues"] == 9000
    assert report["checked_measures"] == 600 and len(report["issues"]) == 200


def test_report_fields_remain_bounded_even_for_an_oversized_source_measure_label():
    root, _, measure, _ = score()
    measure.set("number", "X" * 10000)
    note(measure, typ="half")
    report = result(root)
    assert all(len(item["measure_number"]) <= 120 for item in report["issues"])
    assert len(measure.get("number")) == 10000


def test_other_parts_are_not_audited_and_bad_or_unsafe_sources_fail_closed():
    root, _, measure, _ = score()
    note(measure, duration=48, typ="whole")
    listing = root.find("part-list")
    child(child(listing, "score-part", id="P2"), "part-name", "Other")
    other = child(child(root, "part", id="P2"), "measure", number="1")
    note(other, duration=-12)
    assert result(root)["checked_notes"] == 1 and codes(root) == []
    with pytest.raises(ValueError):
        audit('<!DOCTYPE score [<!ENTITY x "bad">]><score-partwise/>', "P1", "vocal")
