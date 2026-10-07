"""User-reviewed PDF TAB compilation preserves explicit time and fingering."""
import copy
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest
from pydantic import ValidationError

from backend import source_score
from backend.tab_review import ReviewDraft, build


def fixture(instrument="bass", count=3):
    analysis = {"schema_version": 1, "source_sha256": "a" * 64,
                "pages": [{"page": 1, "staffs": [{"id": "p1s0", "kind": "tab", "line_count": 4 if instrument == "bass" else 6,
                            "digits": [{"id": f"d{i}", "text": str(i + 3), "fret": i + 3, "string": 1,
                                        "accepted": i != 1, "requires_review": True} for i in range(count)]}]}]}
    payload = {"source_sha256": "a" * 64, "coordinate_sha256": "b" * 64, "instrument": instrument,
               "title": "직접 확인한 TAB", "tuning": [43, 38, 33, 28] if instrument == "bass" else [64, 59, 55, 50, 45, 40],
               "capo": 0, "beats": 4, "beat_type": 4, "tempo": 100, "staff_ids": ["p1s0"],
               "rows": [{"id": f"p1s0:d{i}", "staff_id": "p1s0", "decision": "note", "measure": 1,
                         "onset": str(i), "type": "quarter", "dots": 0, "string": 1, "fret": i + 3} for i in range(count)]}
    return analysis, payload


def described(data, instrument="bass"):
    prepared = source_score.prepare(data, "review.musicxml", "P1", instrument)
    return prepared, source_score.describe(prepared["xml"], "P1", instrument)["notes"]


@pytest.mark.parametrize("instrument", ["bass", "guitar"])
def test_reconstructs_only_explicit_music_and_is_editable(instrument):
    analysis, payload = fixture(instrument)
    before = copy.deepcopy(analysis), copy.deepcopy(payload)
    xml, summary = build(analysis, payload)
    prepared, notes = described(xml, instrument)
    assert before == (analysis, payload)
    visible = [note for note in notes if note["kind"] != "rest"]
    assert [note["fingering"] for note in visible] == [{"string": 1, "fret": fret} for fret in (3, 4, 5)]
    assert [note["onset"] for note in visible] == ["0", "1", "2"]
    assert all(note["editable"]["tab_pitch"] for note in visible)
    edited = source_score.apply_edit(prepared["xml"], "P1", instrument,
                                    {"note_id": visible[0]["id"], "operation": "tab_pitch", "string": 1, "fret": 7})
    assert source_score.describe(edited, "P1", instrument)["notes"][0]["fingering"]["fret"] == 7
    assert summary["exact_pdf_transcription"] is False
    assert summary["evidence_count"] == summary["note_count"] == 3
    assert summary["review"]["tuning"] == payload["tuning"]


def test_explicit_capo_tuning_not_hidden_transpose():
    analysis, payload = fixture("guitar", 1)
    payload["tuning"] = [63, 58, 54, 49, 44, 39]
    payload["capo"] = 2
    payload["rows"][0]["fret"] = 4
    root = ET.fromstring(build(analysis, payload)[0])
    pitch = root.find("part/measure/note/pitch")
    assert (pitch.findtext("step"), pitch.findtext("alter"), pitch.findtext("octave")) == ("A", None, "4")
    assert root.find(".//transpose") is None
    assert root.findtext(".//staff-tuning[@line='6']/tuning-step") == "D"
    assert root.findtext(".//staff-tuning[@line='6']/tuning-alter") == "1"
    assert root.findtext(".//capo") == "2"


def test_ambiguous_candidate_is_not_dropped_and_can_be_corrected():
    analysis, payload = fixture()
    digit = analysis["pages"][0]["staffs"][0]["digits"][1]
    digit.update(text="109", fret=None, string=None, accepted=False)
    payload["rows"][1].update(fret=10, string=2)
    xml, summary = build(analysis, payload)
    assert any(n["fingering"] == {"string": 2, "fret": 10} for n in described(xml)[1] if n["kind"] != "rest")
    assert summary["evidence_count"] == 3
    payload["rows"].pop(1)
    with pytest.raises(ValueError, match="모든 숫자"):
        build(analysis, payload)


def test_reasoned_exclusion_and_manual_missing_digit():
    analysis, payload = fixture()
    payload["rows"][1] = {"id": "p1s0:d1", "staff_id": "p1s0", "decision": "exclude", "reason": "마디 번호"}
    payload["rows"].append({"id": "manual-added-1", "staff_id": "p1s0", "decision": "note", "measure": 2,
                            "onset": "1/2", "type": "eighth", "string": 4, "fret": 0})
    xml, summary = build(analysis, payload)
    assert summary["manual_count"] == 1
    assert summary["excluded_count"] == 1
    assert summary["note_count"] == 3
    assert summary["measure_count"] == 2
    assert len([n for n in described(xml)[1] if n["kind"] != "rest"]) == 3


def test_same_string_overlap_rejected_but_other_string_simultaneous_supported():
    analysis, payload = fixture(count=2)
    payload["rows"][1]["onset"] = "0"
    with pytest.raises(ValueError, match="겹쳐"):
        build(analysis, payload)
    payload["rows"][1]["string"] = 2
    xml, _ = build(analysis, payload)
    root = ET.fromstring(xml)
    assert root.findtext("part/measure/backup/duration") == "256"
    visible = [n for n in described(xml)[1] if n["kind"] != "rest"]
    assert [n["onset"] for n in visible] == ["0", "0"]
    assert [n["voice"] for n in visible] == ["1", "2"]


def test_cross_bar_splits_with_ties_no_time_loss():
    analysis, payload = fixture(count=1)
    payload["rows"][0].update(onset="7/2", type="quarter", dots=1)
    xml, summary = build(analysis, payload)
    root = ET.fromstring(xml)
    sounding = root.findall("part/measure/note[pitch]")
    assert [note.findtext("duration") for note in sounding] == ["32", "64"]
    assert [note.find("tie").get("type") for note in sounding] == ["start", "stop"]
    assert [note.find("notations/tied").get("type") for note in sounding] == ["start", "stop"]
    assert sum(Fraction(note.findtext("duration")) / 64 for note in sounding) == Fraction(3, 2)
    assert summary["measure_count"] == 2
    for measure in root.findall("part/measure"):
        assert sum(int(n.findtext("duration")) for n in measure.findall("note")) == 256


@pytest.mark.parametrize("onset", ["0", "1/64", "3/32", "15/16", "255/64"])
@pytest.mark.parametrize("kind,dots", [("quarter", 0), ("eighth", 1), ("64th", 2), ("whole", 2)])
def test_exact_divisions_for_fractional_gaps_and_splits(onset, kind, dots):
    analysis, payload = fixture(count=1)
    payload["rows"][0].update(onset=onset, type=kind, dots=dots)
    xml, _ = build(analysis, payload)
    root = ET.fromstring(xml)
    expected = {"quarter": Fraction(1), "eighth": Fraction(1, 2), "64th": Fraction(1, 16), "whole": Fraction(4)}[kind]
    expected *= sum(Fraction(1, 2 ** d) for d in range(dots + 1))
    sounding = root.findall("part/measure/note[pitch]")
    assert sum(Fraction(n.findtext("duration")) / 64 for n in sounding) == expected
    for measure in root.findall("part/measure"):
        assert sum(int(n.findtext("duration")) for n in measure.findall("note")) == 256
    notes = [n for n in described(xml)[1] if n["kind"] != "rest"]
    assert Fraction(notes[0]["onset"]) == Fraction(onset)


def test_basic_eighth_and_sixteenth_beams():
    analysis, payload = fixture(count=3)
    for row, onset, kind in zip(payload["rows"], ("0", "1/2", "3/4"), ("eighth", "16th", "16th")):
        row.update(onset=onset, type=kind)
    root = ET.fromstring(build(analysis, payload)[0])
    notes = root.findall("part/measure/note[pitch]")
    assert [n.findtext("beam[@number='1']") for n in notes] == ["begin", "continue", "end"]
    assert [n.findtext("beam[@number='2']") for n in notes] == [None, "begin", "end"]


def test_compound_beats_group_three_eighths():
    analysis, payload = fixture(count=3)
    payload.update(beats=6, beat_type=8)
    for row, onset in zip(payload["rows"], ("0", "1/2", "1")):
        row.update(onset=onset, type="eighth")
    root = ET.fromstring(build(analysis, payload)[0])
    assert [n.findtext("beam") for n in root.findall("part/measure/note[pitch]")] == ["begin", "continue", "end"]


def test_muted_is_explicit_not_inferred():
    analysis, payload = fixture(count=1)
    payload["rows"][0]["muted"] = True
    xml, summary = build(analysis, payload)
    root = ET.fromstring(xml)
    assert root.findtext("part/measure/note[pitch]/notehead") == "x"
    assert root.findtext(".//technical/fret") == "3"
    assert any("뮤트" in warning for warning in summary["warnings"])


def test_draft_permits_incomplete_rows_but_build_rejects():
    analysis, payload = fixture(count=1)
    payload["rows"] = [{"id": "p1s0:d0", "staff_id": "p1s0", "decision": "note"}]
    draft = ReviewDraft.model_validate(payload)
    assert draft.rows[0].onset is None
    with pytest.raises(ValueError, match="직접 확인"):
        build(analysis, draft)


@pytest.mark.parametrize("mutate", [
    lambda a, p: p.update(unknown=True),
    lambda a, p: p.update(confirmed=True),
    lambda a, p: p.update(tempo=True),
    lambda a, p: p.update(beat_type=True),
    lambda a, p: p.update(beat_type=4.0),
    lambda a, p: p.update(beats="4"),
    lambda a, p: p.update(capo=13),
    lambda a, p: p.update(tempo=301),
    lambda a, p: p.update(title="bad\x00title"),
    lambda a, p: p.update(title="bad\ufffftitle"),
    lambda a, p: p.update(title="x" * 161),
    lambda a, p: p.update(source_sha256="A" * 64),
    lambda a, p: p.update(coordinate_sha256="../evidence"),
    lambda a, p: p["rows"][0].update(string=True),
    lambda a, p: p["rows"][0].update(measure=601),
    lambda a, p: p["rows"][0].update(measure=1.0),
    lambda a, p: p["rows"][0].update(muted=1),
    lambda a, p: p["rows"][0].update(fret=37),
    lambda a, p: p["rows"][0].update(type="triplet"),
    lambda a, p: p["rows"][0].update(dots=3),
    lambda a, p: p["rows"][0].update(unknown="ignored"),
    lambda a, p: p["rows"][0].update(reason="bad\udffftext"),
    lambda a, p: p["rows"][0].update(reason="bad\x00text"),
])
def test_strict_request_shape(mutate):
    analysis, payload = fixture()
    mutate(analysis, payload)
    with pytest.raises(ValidationError):
        build(analysis, payload)


@pytest.mark.parametrize("mutate", [
    lambda a, p: p.update(source_sha256="c" * 64),
    lambda a, p: p.update(tuning=[43, 28, 38, 33]),
    lambda a, p: p.update(tuning=[43, 38, 33, 28, 20]),
    lambda a, p: p.update(staff_ids=["p1s0", "p1s0"]),
    lambda a, p: p.update(staff_ids=["p2s0"]),
    lambda a, p: p["rows"][0].update(staff_id="p2s0"),
    lambda a, p: p["rows"][0].update(id="fabricated"),
    lambda a, p: p["rows"][0].update(decision="exclude", reason=" "),
    lambda a, p: p["rows"][0].update(string=5),
    lambda a, p: p["rows"][0].update(onset="-1"),
    lambda a, p: p["rows"][0].update(onset="1/3"),
    lambda a, p: p["rows"][0].update(onset="1/128"),
    lambda a, p: p["rows"][0].update(onset="0.5"),
    lambda a, p: p["rows"][0].update(onset="4"),
    lambda a, p: p["rows"][0].update(onset="0/0"),
    lambda a, p: p["rows"][0].update(onset=" 0"),
    lambda a, p: p["rows"][0].update(measure=600, onset="7/2", type="whole"),
    lambda a, p: p["rows"].append(copy.deepcopy(p["rows"][0])),
    lambda a, p: p["rows"].pop(),
    lambda a, p: a["pages"][0]["staffs"][0].update(line_count=6),
    lambda a, p: a["pages"][0]["staffs"][0].update(kind="staff"),
    lambda a, p: a["pages"][0]["staffs"][0]["digits"].append(copy.deepcopy(a["pages"][0]["staffs"][0]["digits"][0])),
    lambda a, p: p.update(tuning=[120, 119, 118, 117], capo=12),
])
def test_rejects_incomplete_inconsistent_or_unsupported_music(mutate):
    analysis, payload = fixture()
    mutate(analysis, payload)
    with pytest.raises(ValueError):
        build(analysis, payload)


def test_all_excluded_cannot_be_presented_as_a_score():
    analysis, payload = fixture()
    for row in payload["rows"]:
        row.update(decision="exclude", reason="원본 확인 후 제외")
    with pytest.raises(ValueError, match="하나 이상"):
        build(analysis, payload)


def test_last_measure_boundary_and_whitespace_title_are_safe():
    analysis, payload = fixture(count=1)
    payload.update(title=" ")
    payload["rows"][0].update(measure=600, onset="3", type="quarter")
    xml, summary = build(analysis, payload)
    assert summary["measure_count"] == 1
    assert summary["first_measure"] == summary["last_measure"] == 600
    assert summary["omitted_leading_measures"] == 599
    assert ET.fromstring(xml).find("part/measure").get("number") == "600"
    assert ET.fromstring(xml).findtext("work/work-title") == "검토한 TAB 악보"


@pytest.mark.parametrize("instrument", ["bass", "guitar"])
@pytest.mark.parametrize("preset", ["practice", "standard", "large"])
def test_style_preserves_compiled_tab_data(instrument, preset):
    from backend.score_preservation import restyle_working_xml
    from backend.source_fidelity import verify_layout

    analysis, payload = fixture(instrument)
    xml, _ = build(analysis, payload)
    styled, _ = restyle_working_xml(xml, "P1", preset, 4)
    check = verify_layout(xml.decode(), xml.decode(), styled, "P1")
    assert check["style_preserves_music"] is True
    assert check["music_edited"] is False


@pytest.mark.parametrize("instrument", ["bass", "guitar"])
def test_selected_excerpt_starts_at_original_bar_number_and_editor_uses_local_index(instrument):
    analysis, payload = fixture(instrument, count=2)
    payload["rows"][0].update(measure=21, onset="1/2", type="eighth")
    payload["rows"][1].update(measure=22, onset="0", type="quarter")
    xml, summary = build(analysis, payload)
    root = ET.fromstring(xml)
    measures = root.findall("part/measure")
    assert [measure.get("number") for measure in measures] == ["21", "22"]
    assert measures[0].findtext("attributes/divisions") == "64"
    assert measures[0].findtext("attributes/clef/sign") == "TAB"
    assert measures[0].findtext("attributes/time/beats") == "4"
    assert measures[0].find("direction/sound").get("tempo") == "100"
    assert summary["measure_count"] == 2
    assert summary["first_measure"] == 21 and summary["last_measure"] == 22
    assert summary["omitted_leading_measures"] == 20
    assert summary["silent_gap_measures"] == []
    prepared, notes = described(xml, instrument)
    visible = [note for note in notes if note["kind"] != "rest"]
    assert [(n["measure_index"], n["measure_number"], n["onset"]) for n in visible] == [(1, "21", "1/2"), (2, "22", "0")]
    assert visible[0]["id"] == "m0n1", "The within-bar leading half-beat rest is retained"
    edited = source_score.apply_edit(prepared["xml"], "P1", instrument,
                                    {"note_id": visible[0]["id"], "operation": "tab_pitch", "string": 1, "fret": 7})
    edited_notes = source_score.describe(edited, "P1", instrument)["notes"]
    changed = next(n for n in edited_notes if n["id"] == visible[0]["id"])
    assert changed["measure_index"] == 1 and changed["measure_number"] == "21"
    assert changed["fingering"]["fret"] == 7


def test_internal_unfilled_bars_remain_silent_instead_of_collapsing_time():
    analysis, payload = fixture(count=2)
    payload["rows"][0].update(measure=21, onset="0")
    payload["rows"][1].update(measure=24, onset="0")
    xml, summary = build(analysis, payload)
    measures = ET.fromstring(xml).findall("part/measure")
    assert [m.get("number") for m in measures] == ["21", "22", "23", "24"]
    assert summary["measure_count"] == 4
    assert summary["silent_gap_measures"] == [22, 23]
    assert any("빈 마디" in warning for warning in summary["warnings"])
    for measure in measures[1:3]:
        assert not measure.findall("note/pitch")
        assert all(note.find("rest") is not None for note in measure.findall("note"))
        assert sum(int(note.findtext("duration")) for note in measure.findall("note")) == 256
    visible = [n for n in described(xml)[1] if n["kind"] != "rest"]
    assert [n["measure_index"] for n in visible] == [1, 4]
    assert [n["measure_number"] for n in visible] == ["21", "24"]


def test_cross_bar_tie_in_late_excerpt_retains_source_numbers_and_exact_duration():
    analysis, payload = fixture(count=1)
    payload["rows"][0].update(measure=21, onset="7/2", type="quarter", dots=1)
    xml, summary = build(analysis, payload)
    measures = ET.fromstring(xml).findall("part/measure")
    assert [measure.get("number") for measure in measures] == ["21", "22"]
    sounding = [note for measure in measures for note in measure.findall("note[pitch]")]
    assert [note.findtext("duration") for note in sounding] == ["32", "64"]
    assert [note.find("tie").get("type") for note in sounding] == ["start", "stop"]
    assert summary["silent_gap_measures"] == []
    assert summary["first_measure"] == 21 and summary["last_measure"] == 22


def test_excluded_earlier_candidate_does_not_create_empty_leading_bars():
    analysis, payload = fixture(count=2)
    payload["rows"][0].update(measure=1, decision="exclude", reason="마디 번호라 제외")
    payload["rows"][1].update(measure=21, onset="0")
    xml, summary = build(analysis, payload)
    assert [m.get("number") for m in ET.fromstring(xml).findall("part/measure")] == ["21"]
    assert summary["measure_count"] == 1
