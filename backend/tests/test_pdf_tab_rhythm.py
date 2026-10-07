"""Vector rhythm suggestions must have notation evidence, never spacing guesses."""
import copy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pdf_tab_rhythm import extract_page_rhythm


def head(x, y=112):
    width, height = 4.72, 4
    endpoints = [(.329, 1), (1, .332), (.671, 0), (0, .668), (.329, 1)]
    points = [(x + a * width, y + b * height) for a, b in endpoints]
    return {"x0": x, "x1": x + width, "top": y, "bottom": y + height, "fill": True,
            "path": [("m", points[0])] + [("c", point, point, point) for point in points[1:]]}


def stem(note, down=False):
    x = note["x0"] + .24 if down else note["x1"] - .24
    return {"x0": x, "x1": x, "top": note["top"] + 2.7 if down else note["top"] - 11,
            "bottom": note["bottom"] + 11 if down else note["top"] + 1.3, "stroke": True}


def beam(first, last, level=0, down=False):
    a, b = stem(first, down), stem(last, down)
    y = (a["bottom"] - 3 * level) if down else a["top"] + 3 * level
    x0, x1 = a["x0"], b["x0"]
    return {"x0": x0, "x1": x1, "top": y, "bottom": y + 2, "fill": True,
            "path": [("m", (x0, y)), ("l", (x1, y)), ("l", (x1, y + 2)), ("l", (x0, y + 2))]}


def circle(x, y, size=1.6):
    pts = [(x + size, y + size / 2), (x + size / 2, y), (x, y + size / 2),
           (x + size / 2, y + size), (x + size, y + size / 2)]
    return {"x0": x, "x1": x + size, "top": y, "bottom": y + size, "fill": True,
            "path": [("m", pts[0])] + [("c", p, p, p) for p in pts[1:]]}


def tie(first, last):
    x0, x1 = first["x1"] + .16, last["x0"] - .64
    y = first["top"] + 2.8
    return {"x0": x0, "x1": x1, "top": y, "bottom": y, "fill": True,
            "path": [("m", (x0, y)), ("c", (x0 + 2, y + 3), (x1 - 2, y + 3), (x1, y)),
                     ("c", (x1 - 2, y + 4), (x0 + 2, y + 4), (x0, y))]}


def fixture(xs=(40, 70, 180, 230), *, skip_digits=(), down=False):
    heads = [head(x) for x in xs]
    digits = [{"id": f"d{i}", "accepted": True, "fret": i + 1, "string": 4,
               "bbox": [n["x0"] + .5, 143, n["x1"] - .5, 149]}
              for i, n in enumerate(heads) if i not in skip_digits]
    geometry = {"page": 1, "staffs": [
        {"id": "p1s0", "kind": "staff", "line_count": 5, "spacing": 4, "bbox": [20, 100, 580, 116]},
        {"id": "p1s1", "kind": "tab", "line_count": 4, "spacing": 6, "bbox": [20, 134, 580, 152],
         "measure_boundaries_verified": True, "symbols": [], "digits": digits,
         "measures": [{"id": "p1s1m1", "index": 1, "bbox": [20, 134, 300, 152],
                       "digit_ids": [d["id"] for d in digits]}]}]}
    return geometry, [stem(n, down) for n in heads], heads, []


def run(data, **kwargs):
    geometry, lines, curves, chars = data
    return extract_page_rhythm(geometry, lines=lines, curves=curves, chars=chars,
                               beats=kwargs.get("beats", 4), beat_type=kwargs.get("beat_type", 4))


def rows(data, **kwargs):
    result = run(data, **kwargs)
    assert result["requires_review"] and result["rhythm_known"] is False
    measure = result["measures"][0]
    assert measure["status"] == "suggested", measure["unresolved"]
    return measure["rows"]


def unresolved(data, **kwargs):
    result = run(data, **kwargs)["measures"][0]
    assert result["status"] == "unresolved" and result["unresolved"]
    assert result["rows"] == []


@pytest.mark.parametrize("xs", [(40, 70, 180, 230), (80, 100, 120, 140), (40, 47, 180, 250)])
@pytest.mark.parametrize("down", [False, True])
def test_quarter_rhythms_ignore_unequal_horizontal_spacing(xs, down):
    data = fixture(xs, down=down)
    before = copy.deepcopy(data)
    result = rows(data)
    assert [(r["onset"], r["duration"]) for r in result] == [(str(i), "1") for i in range(4)]
    assert result[0]["row_id"] == "p1s1:d0"
    assert data == before


def test_observed_primary_and_secondary_beams_produce_eighths_and_sixteenths():
    data = fixture(tuple(40 + 13 * i for i in range(16)))
    heads = data[2][:]
    data[2].extend([beam(heads[0], heads[-1]), beam(heads[0], heads[-1], 1)])
    result = rows(data)
    assert all(r["type"] == "16th" and r["dots"] == 0 for r in result)
    assert [r["onset"] for r in result[:5]] == ["0", "1/4", "1/2", "3/4", "1"]
    assert all(r["evidence"]["notes"][0]["beam_count"] == 2 for r in result)


@pytest.mark.parametrize("down", [False, True])
def test_primary_beams_have_eight_eighths(down):
    data = fixture(tuple(40 + 25 * i for i in range(8)), down=down)
    data[2].append(beam(data[2][0], data[2][-1], down=down))
    assert [r["duration"] for r in rows(data)] == ["1/2"] * 8


def test_dot_and_secondary_beam_hook_are_not_read_from_note_spacing():
    data = fixture((40, 85, 130, 180, 220))
    notes = data[2][:]
    # Dotted quarter, sixteenth, sixteenth, quarter, quarter = four beats.
    data[2].append(circle(notes[0]["x1"] + 1, 112))
    data[2].extend([beam(notes[1], notes[2]), beam(notes[1], notes[2], 1)])
    result = rows(data)
    assert [r["duration"] for r in result] == ["3/2", "1/4", "1/4", "1", "1"]
    assert result[0]["dots"] == 1


def test_staccato_dot_does_not_become_augmentation_dot():
    data = fixture()
    for n in data[2][:]:
        data[2].append(circle(n["x0"] + 1, n["bottom"] + 3, 1.352))
    assert [r["dots"] for r in rows(data)] == [0] * 4


def test_tied_continuation_without_tab_digit_is_attached_to_original_attack():
    data = fixture((40, 80, 130, 180, 230), skip_digits=(1,))
    notes = data[2][:]
    data[2].extend([beam(notes[0], notes[1]), tie(notes[0], notes[1])])
    result = rows(data)
    assert [(r["row_id"], r["onset"], r["duration"]) for r in result] == [
        ("p1s1:d0", "0", "1"), ("p1s1:d2", "1", "1"),
        ("p1s1:d3", "2", "1"), ("p1s1:d4", "3", "1")]
    assert result[0]["evidence"]["tie_count"] == 1
    assert len(result[0]["evidence"]["notes"]) == 2


def test_missing_continuation_fret_without_observed_tie_is_not_guessed():
    data = fixture((40, 80, 130, 180, 230), skip_digits=(1,))
    data[2].append(beam(data[2][0], data[2][1]))
    unresolved(data)


def test_same_pitch_slur_with_frets_on_both_ends_is_ambiguous():
    data = fixture((40, 80, 130, 180, 230))
    data[2].extend([beam(data[2][0], data[2][1]), tie(data[2][0], data[2][1])])
    unresolved(data)


def test_tie_chain_with_nonrepresentable_duration_is_withheld_not_rounded():
    data = fixture((40, 85, 130, 180, 230), skip_digits=(1,))
    notes = data[2][:]
    short_hook_end = head(89)
    data[2].extend([beam(notes[1], notes[2]), beam(notes[1], short_hook_end, 1),
                    circle(notes[2]["x1"] + 1, 112), tie(notes[0], notes[1])])
    result = run(data)["measures"][0]
    assert result["status"] == "unresolved" and result["rows"] == []
    assert "5/4" in result["unresolved"][0]


@pytest.mark.parametrize("x", [27, 110, 260])
def test_unknown_leading_middle_trailing_glyph_is_not_silently_omitted(x):
    data = fixture()
    data[2].append({"x0": x, "x1": x + 4, "top": 105, "bottom": 112,
                    "fill": True, "path": [("m", (x, 105)), ("l", (x + 4, 112))]})
    unresolved(data)


def test_unattached_beam_abstains_instead_of_leaving_implicit_quarter_notes():
    data = fixture()
    data[2].append(beam(head(110), head(135)))
    unresolved(data)


def test_unattached_augmentation_dot_requires_review():
    data = fixture()
    data[2].append(circle(115, 112))
    unresolved(data)


@pytest.mark.parametrize("mutation", ["stem", "head", "hollow", "unknown_symbol", "fret", "mute", "tuplet", "chord", "extra_digit"])
def test_incomplete_or_unsupported_notation_abstains_entire_measure(mutation):
    data = fixture()
    tab = data[0]["staffs"][1]
    if mutation == "stem":
        data[1].pop()
    elif mutation == "head":
        data[2].pop()
    elif mutation == "hollow":
        data[2][0]["fill"] = False
    elif mutation == "unknown_symbol":
        data[2].append({"x0": 115, "x1": 119, "top": 105, "bottom": 112,
                        "fill": True, "path": [("m", (115, 105)), ("l", (119, 112))]})
    elif mutation == "fret":
        tab["digits"][0]["accepted"] = False
    elif mutation == "mute":
        tab["symbols"].append({"text": "X", "bbox": [115, 140, 120, 146]})
    elif mutation == "tuplet":
        data[3].append({"text": "3", "x0": 110, "x1": 114, "top": 94, "bottom": 102})
    elif mutation == "chord":
        data[2].append(head(40, 108))
    elif mutation == "extra_digit":
        tab["digits"].append({**tab["digits"][0], "id": "extra", "bbox": [160, 140, 164, 146]})
        tab["measures"][0]["digit_ids"].append("extra")
    unresolved(data)


def test_wrong_confirmed_meter_is_not_forced_to_fit():
    unresolved(fixture(), beats=3)


def test_unverified_barlines_and_unpaired_staves_never_get_suggestions():
    data = fixture()
    data[0]["staffs"][1]["measure_boundaries_verified"] = False
    assert run(data)["measures"] == []
    data = fixture()
    data[0]["staffs"].pop(0)
    unresolved(data)


def test_two_equally_close_standard_staves_require_manual_selection():
    data = fixture()
    data[0]["staffs"].append(copy.deepcopy(data[0]["staffs"][0]))
    unresolved(data)


def test_cross_bar_tie_is_not_silently_clipped_even_if_center_in_neighbor_bar():
    data = fixture()
    external = head(400)
    data[2].append(tie(data[2][-1], external))
    unresolved(data)


def test_bar_number_outside_note_span_is_not_a_tuplet():
    data = fixture()
    data[3].extend([{"text": "1", "x0": 23, "x1": 27, "top": 94, "bottom": 102},
                    {"text": "2", "x0": 297, "x1": 301, "top": 94, "bottom": 102}])
    assert len(rows(data)) == 4


@pytest.mark.parametrize("beats, beat_type", [(True, 4), (4, True), (0, 4), (13, 4), (4, 3), (4.0, 4)])
def test_confirmed_meter_is_strict(beats, beat_type):
    with pytest.raises(ValueError, match="박자표"):
        run(fixture(), beats=beats, beat_type=beat_type)


def test_rejects_object_limit_before_geometry_processing():
    data = fixture()
    data[2].extend([{}] * 50_000)
    with pytest.raises(ValueError, match="제한"):
        run(data)


@pytest.mark.parametrize("filename,sha256,expected", [
    ("0+0[tab].pdf", "e0aab47c7f29f372c0c8fa5bb2cb4e8bb1d8571a1f0e136dca41832fe6932caf",
     [["7/4", "1/4", "1/2", "1", "1/2"], ["7/4", "1/4", "1/2", "3/2"]] * 2),
    ("Creep [tab].pdf", "53f7a8c511430416e1ab63bb71cba3460ebec32742071b1ea160143c6799a22f",
     [["7/4", "1/4", "1/2", "1", "1/2"]] * 4),
])
def test_opt_in_private_reference_first_four_bars(filename, sha256, expected):
    """Private copyrighted scores stay outside Git and CI; labels read visually.

    Set AKBO_PRIVATE_BASS_PDF_DIR to the original bass score directory to run.
    These assertions cover only the first four manually inspected bars, not a
    claimed whole-score accuracy benchmark.
    """
    directory = os.environ.get("AKBO_PRIVATE_BASS_PDF_DIR")
    if not directory:
        pytest.skip("private user PDF fixture not configured")
    import pdfplumber
    from backend.pdf_score_analysis import analyze_page_geometry

    source = Path(directory) / filename
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha256
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[0]
        geometry = analyze_page_geometry(page_number=1, width=page.width, height=page.height,
                                         edges=page.edges, chars=page.chars, instrument="bass")
        report = extract_page_rhythm(geometry, lines=page.lines, curves=page.curves,
                                      chars=page.chars, beats=4, beat_type=4)
        measures = report["measures"][:4]
        assert all(m["status"] == "suggested" for m in measures)
        assert [[r["duration"] for r in m["rows"]] for m in measures] == expected
        for measure in measures:
            from fractions import Fraction
            cursor = Fraction()
            for row in measure["rows"]:
                assert Fraction(row["onset"]) == cursor
                cursor += Fraction(row["duration"])
            assert cursor == 4
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha256
