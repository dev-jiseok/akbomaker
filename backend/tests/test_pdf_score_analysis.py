"""PDF evidence extraction must not fabricate rhythms, digits or string indices."""
import copy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from backend import pdf_score_analysis as analysis


def lines(count=4, y=100, spacing=6):
    return [{"x0": 20, "x1": 580, "top": y + i * spacing, "bottom": y + i * spacing,
             "linewidth": .25, "stroke": True} for i in range(count)]


def digit(text, x=100, y=118, **kwargs):
    return {"text": text, "x0": x, "x1": x + 3.5, "top": y - 2, "bottom": y + 4,
            "fontname": "OriginalFont", "upright": True, **kwargs}


def page(chars=None, edges=None, instrument="bass", **kwargs):
    return analysis.analyze_page_geometry(page_number=1, width=600, height=800,
                                         chars=chars or [], edges=edges if edges is not None else lines(),
                                         instrument=instrument, **kwargs)


@pytest.mark.parametrize("instrument", ["auto", "bass", "guitar"])
@pytest.mark.parametrize("count", [4, 6])
def test_tab_digits_and_string_numbers_come_only_from_source_coordinates(instrument, count):
    source_lines = lines(count)
    chars = [digit("5", y=100), digit("0", x=150, y=100 + 6 * (count - 1))]
    before = copy.deepcopy((source_lines, chars))
    result = page(chars, source_lines, instrument)
    assert (source_lines, chars) == before
    assert len(result["staffs"]) == 1
    staff = result["staffs"][0]
    assert staff["kind"] == "tab" and staff["line_count"] == count
    assert [(d["text"], d["fret"], d["string"]) for d in staff["digits"]] == [("5", 5, 1), ("0", 0, count)]
    assert all(d["accepted"] and d["requires_review"] for d in staff["digits"])
    assert staff["digits"][0]["bbox"] == [100, 98, 103.5, 104]
    assert not any("duration" in d or "pitch" in d for d in staff["digits"])


@pytest.mark.parametrize("instrument", ["drums", "piano", "synthesizer", "vocal"])
def test_non_tab_instruments_never_turn_numbers_into_frets(instrument):
    result = page([digit("5")], instrument=instrument)
    assert result["staffs"][0]["kind"] == "ambiguous"
    assert result["statistics"]["tab_digits"] == 0


def test_five_line_staff_is_not_sliced_into_four_line_tab():
    result = page([digit("5", y=124)], lines(5), "auto")
    assert len(result["staffs"]) == 1
    assert result["staffs"][0]["kind"] == "staff"
    assert result["staffs"][0]["line_count"] == 5
    assert result["statistics"]["tab_digits"] == 0


def test_seven_line_and_irregular_staves_are_not_guessed_as_tab():
    result = page([digit("5")], lines(7))
    assert result["staffs"][0]["kind"] == "ambiguous"
    irregular = lines()
    irregular[2].update(top=114, bottom=114)
    assert not page([digit("5")], irregular)["staffs"]


def test_adjacent_digits_form_one_fret_but_spaced_notes_stay_separate():
    chars = [digit("1", x=100), digit("2", x=103.5), digit("3", x=120), digit("5", x=127)]
    digits = page(chars)["staffs"][0]["digits"]
    assert [d["text"] for d in digits] == ["12", "3", "5"]
    assert [d["fret"] for d in digits] == [12, 3, 5]
    assert digits[0]["bbox"] == [100, 116, 107, 122]


def test_small_font_kerning_overlap_in_eleven_is_not_two_conflicting_notes():
    digits = page([digit("1", x=100), digit("1", x=103)])["staffs"][0]["digits"]
    assert len(digits) == 1 and digits[0]["fret"] == 11 and digits[0]["accepted"]


def test_unusually_wide_digit_box_requires_manual_review():
    item = page([digit("5", x1=140)])["staffs"][0]["digits"][0]
    assert not item["accepted"] and item["string"] is None


@pytest.mark.parametrize("text", ["99", "00", "012"])
def test_ambiguous_or_out_of_range_fret_numbers_are_not_accepted(text):
    chars = [digit(c, x=100 + 3.5 * i) for i, c in enumerate(text)]
    item = page(chars)["staffs"][0]["digits"][0]
    assert item["text"] == text and item["fret"] is None
    assert item["string"] is None and not item["accepted"] and item["reasons"]


def test_numbers_between_lines_are_review_candidates_not_string_assignments():
    item = page([digit("5", y=108.5)])["staffs"][0]["digits"][0]
    assert item["fret"] == 5 and item["string"] is None and not item["accepted"]


def test_exact_overprint_is_deduplicated_but_conflicting_overprint_is_ambiguous():
    assert len(page([digit("5"), digit("5")])["staffs"][0]["digits"]) == 1
    digits = page([digit("5"), digit("3")])["staffs"][0]["digits"]
    assert len(digits) == 2 and not any(item["accepted"] for item in digits)


def test_chord_digits_on_different_strings_do_not_look_like_overprints():
    digits = page([digit("5", y=100), digit("7", y=106)])["staffs"][0]["digits"]
    assert len(digits) == 2 and all(item["accepted"] for item in digits)
    assert [item["string"] for item in digits] == [1, 2]


def test_fragmented_lines_around_numbers_keep_original_coordinate_envelopes():
    fragmented = []
    for line in lines():
        fragmented.extend([{**line, "x1": 98}, {**line, "x0": 106, "x1": 230}, {**line, "x0": 230}])
    result = page([digit("5")], fragmented)
    assert result["staffs"][0]["bbox"] == [20, 100, 580, 118]
    assert all(.9 < line["coverage"] < 1 for line in result["staffs"][0]["lines"])
    assert result["staffs"][0]["digits"][0]["string"] == 4


def test_short_decorations_wide_gaps_and_steep_lines_do_not_create_staves():
    short = [{**line, "x1": 90} for line in lines()]
    assert not page([digit("5")], short)["staffs"]
    steep = [{**line, "bottom": line["top"] + 5} for line in lines()]
    assert not page([digit("5")], steep)["staffs"]
    sparse = [{**line, "x1": 80} for line in lines()] + [{**line, "x0": 520} for line in lines()]
    assert not page([digit("5")], sparse)["staffs"]


def test_large_titles_measure_numbers_and_non_digit_symbols_are_not_invented():
    chars = [digit("5"), digit("100", y=40, top=10, bottom=70), digit("2", y=80), digit("X", x=140), digit("５", x=170)]
    digits = page(chars)["staffs"][0]["digits"]
    assert len(digits) == 1 and digits[0]["text"] == "5"


def bar(x, *, top=100, bottom=118, object_type="line", **kwargs):
    return {"x0": x, "x1": x, "top": top, "bottom": bottom,
            "object_type": object_type, "stroke": True, "linewidth": .25, **kwargs}


def test_tab_measure_boundaries_and_digit_membership_use_observed_full_height_lines():
    edges = lines() + [bar(20), bar(200), bar(380), bar(580)]
    chars = [digit("5", x=50), digit("0", x=250), digit("7", x=450)]
    before = copy.deepcopy((edges, chars))
    staff = page(chars, edges)["staffs"][0]
    assert (edges, chars) == before
    assert staff["measure_boundaries_verified"] is True
    assert staff["measures"] == [
        {"id": "p1s0m1", "index": 1, "bbox": [20, 100, 200, 118], "digit_ids": ["d0"]},
        {"id": "p1s0m2", "index": 2, "bbox": [200, 100, 380, 118], "digit_ids": ["d1"]},
        {"id": "p1s0m3", "index": 3, "bbox": [380, 100, 580, 118], "digit_ids": ["d2"]},
    ]
    assert not any("duration" in measure or "beats" in measure for measure in staff["measures"])
    assert any("박자" in warning for warning in staff["warnings"])


def test_double_bars_and_filled_rect_edges_are_one_geometric_boundary():
    rect = {"object_type": "rect_edge", "stroke": False, "fill": True}
    edges = lines() + [bar(20, top=75), bar(198, **rect), bar(198.7, **rect),
                       bar(201, **rect), bar(201.7, **rect),
                       bar(577.4, **rect), bar(578.1, **rect), bar(580, **rect)]
    staff = page([digit("5", x=50), digit("7", x=400)], edges)["staffs"][0]
    assert staff["measure_boundaries_verified"]
    assert len(staff["measures"]) == 2
    assert 198 < staff["measures"][0]["bbox"][2] < 202
    assert staff["measures"][1]["bbox"][2] == 580
    assert [measure["digit_ids"] for measure in staff["measures"]] == [["d0"], ["d1"]]


@pytest.mark.parametrize("bars", [[], [bar(20)], [bar(580)], [bar(200), bar(380)]])
def test_missing_system_boundaries_never_invent_measure_edges(bars):
    staff = page([digit("5")], lines() + bars)["staffs"][0]
    assert staff["measure_boundaries_verified"] is False and staff["measures"] == []
    assert any("추정하지" in warning for warning in staff["warnings"])


def test_short_stems_curves_invisible_and_invalid_edges_are_not_barlines():
    noise = [bar(200, top=103), bar(220, bottom=115),
             bar(240, object_type="curve_edge"), bar(260, stroke=False),
             bar(280, stroke=None), bar(300, x1=302), bar(320, linewidth=8),
             bar(float("nan")), bar(350, top=120, bottom=100)]
    staff = page([], lines() + [bar(20), bar(580)] + noise)["staffs"][0]
    assert staff["measure_boundaries_verified"] and len(staff["measures"]) == 1


def test_adjacent_staff_strokes_do_not_provide_missing_tab_boundaries():
    staff = page([], lines() + [bar(20, top=60, bottom=78), bar(580, top=60, bottom=78)])["staffs"][0]
    assert staff["measures"] == [] and not staff["measure_boundaries_verified"]


def test_staff_without_digits_can_have_empty_measure_evidence_not_invented_rests():
    staff = page([], lines() + [bar(20), bar(200), bar(580)])["staffs"][0]
    assert staff["measure_boundaries_verified"]
    assert len(staff["measures"]) == 2 and all(not m["digit_ids"] for m in staff["measures"])
    assert not staff["digits"]


def test_non_numeric_tab_symbols_are_preserved_for_review_not_converted_to_notes():
    chars = [digit("5"), digit("X", x=150), digit("(", x=190), digit("3", x=194),
             digit(")", x=198), digit("H", x=250), digit("５", x=280),
             digit("Title", x=300, y=40), digit(" ", x=320)]
    before = copy.deepcopy(chars)
    staff = page(chars)["staffs"][0]
    assert chars == before
    assert [s["text"] for s in staff["symbols"]] == ["X", "(", ")", "H", "５"]
    assert all(s["requires_review"] and s["reason"] and "fret" not in s for s in staff["symbols"])
    assert [d["fret"] for d in staff["digits"]] == [5, 3]
    assert any("숫자가 아닌" in warning for warning in staff["warnings"])


def test_symbol_overprints_deduplicate_and_oversized_glyphs_are_not_tab_marks():
    staff = page([digit("X"), digit("X"), digit("Title", top=98, bottom=130)])["staffs"][0]
    assert [s["text"] for s in staff["symbols"]] == ["X"]


def test_five_line_notation_is_not_turned_into_tab_measures_or_symbols():
    staff = page([digit("X")], lines(5) + [bar(20, bottom=124), bar(580, bottom=124)])["staffs"][0]
    assert staff["kind"] == "staff"
    assert not staff["measure_boundaries_verified"] and not staff["measures"] and not staff["symbols"]


def test_rotated_pages_and_rotated_characters_fail_closed():
    rotated_page = page([digit("5")], rotation=90)
    assert not rotated_page["staffs"] and any("회전" in warning for warning in rotated_page["warnings"])
    char = page([digit("5", upright=False)])["staffs"][0]["digits"][0]
    assert not char["accepted"] and char["string"] is None


def test_scanned_page_is_explicitly_not_an_ocr_success():
    result = page([], [], image_count=1)
    assert result["statistics"]["image_objects"] == 1 and not result["staffs"]
    assert any("스캔" in warning for warning in result["warnings"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1, 2000])
def test_page_dimensions_are_bounded(bad):
    with pytest.raises(ValueError):
        analysis.analyze_page_geometry(page_number=1, width=bad, height=800, chars=[], edges=[], instrument="auto")


def test_object_character_and_output_limits(monkeypatch):
    monkeypatch.setattr(analysis, "MAX_CHARS", 1)
    with pytest.raises(ValueError, match="문자"):
        page([digit("5"), digit("3", x=120)])
    monkeypatch.setattr(analysis, "MAX_OBJECTS", 3)
    with pytest.raises(ValueError, match="벡터"):
        page([])


@pytest.mark.parametrize("pages", [[], [1, 1], [0], [101], [1, 2, 3, 4, 5], [True], [1.0], [[1]], "1", None])
def test_page_selection_is_bounded_before_parsing(pages):
    with pytest.raises(ValueError, match="페이지"):
        analysis.analyze_pdf(b"%PDF-1.4", pages)


@pytest.mark.parametrize("data", [b"", b"not-pdf", "%PDF-1.4"])
def test_pdf_signature_and_byte_type_are_required(data):
    with pytest.raises(ValueError):
        analysis.analyze_pdf(data, [1])


class FakePage:
    width, height, rotation = 600, 800, 0
    def __init__(self):
        self.page_obj = SimpleNamespace(contents=[])
        self.chars, self.edges, self.images = [digit("5")], lines(), []
        self.objects = {"char": self.chars, "line": self.edges}
        self.closed = False
    def close(self):
        self.closed = True


@pytest.fixture
def fake_pdf(monkeypatch):
    import pdfplumber
    class Document:
        def __init__(self):
            self.pages = [FakePage(), FakePage()]
            self.doc = SimpleNamespace(is_extractable=True, encryption=None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    document = Document()
    monkeypatch.setattr(pdfplumber, "open", lambda *args, **kwargs: document)
    return document


def test_pdf_contract_identifies_original_and_never_claims_musicxml(fake_pdf):
    data = b"%PDF-1.4 test double"
    result = analysis.analyze_pdf(data, [2])
    assert result["schema_version"] == 1 and result["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["page_count"] == 2 and [page["page"] for page in result["pages"]] == [2]
    assert result["editable_musicxml"] is False and result["rhythm_known"] is False and result["requires_review"] is True
    assert fake_pdf.pages[1].closed and not fake_pdf.pages[0].closed
    assert "원본" in result["warnings"][0]


@pytest.mark.parametrize("case", ["encrypted", "restricted", "page-count", "selected-page"])
def test_pdf_encryption_and_document_limits(fake_pdf, case):
    if case == "encrypted":
        fake_pdf.doc.encryption = {"key": "secret"}
    elif case == "restricted":
        fake_pdf.doc.is_extractable = False
    elif case == "page-count":
        fake_pdf.pages = [FakePage()] * 101
    with pytest.raises(ValueError):
        analysis.analyze_pdf(b"%PDF-1.4", [3] if case == "selected-page" else [1])


def test_missing_parser_dependency_is_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    with pytest.raises(ValueError, match="패키지"):
        analysis.analyze_pdf(b"%PDF-1.4", [1])


def test_result_limit_and_parser_failures_are_bounded(fake_pdf, monkeypatch):
    monkeypatch.setattr(analysis, "MAX_OUTPUT_BYTES", 100)
    with pytest.raises(ValueError, match="결과"):
        analysis.analyze_pdf(b"%PDF-1.4", [1])
    import pdfplumber
    def fail(*args, **kwargs):
        raise RuntimeError("internal file path or document data should not leak")
    monkeypatch.setattr(pdfplumber, "open", fail)
    with pytest.raises(ValueError, match="안전하게") as error:
        analysis.analyze_pdf(b"%PDF-1.4", [1])
    assert "internal" not in str(error.value)


def test_cli_writes_json_and_refuses_to_overwrite_original(tmp_path, fake_pdf, monkeypatch, capsys):
    source, target = tmp_path / "source.pdf", tmp_path / "analysis.json"
    source.write_bytes(b"%PDF-1.4 fixture")
    monkeypatch.setattr(analysis, "_worker_limits", lambda: {"test": True})
    monkeypatch.setattr(sys, "argv", ["worker", "--input", str(source), "--output", str(target), "--pages", "1", "--instrument", "auto"])
    analysis.main()
    assert json.loads(target.read_text())["editable_musicxml"] is False
    assert json.loads(capsys.readouterr().out)["bytes"] == target.stat().st_size
    assert source.read_bytes() == b"%PDF-1.4 fixture"
    monkeypatch.setattr(sys, "argv", ["worker", "--input", str(source), "--output", str(source)])
    with pytest.raises(SystemExit) as error:
        analysis.main()
    assert error.value.code == 2 and source.read_bytes() == b"%PDF-1.4 fixture"


def test_cli_temporary_destination_cannot_alias_original(tmp_path, fake_pdf, monkeypatch):
    source = tmp_path / "analysis.json.tmp"
    source.write_bytes(b"%PDF-1.4 fixture")
    monkeypatch.setattr(analysis, "_worker_limits", lambda: {})
    monkeypatch.setattr(sys, "argv", ["worker", "--input", str(source), "--output", str(tmp_path / "analysis.json")])
    with pytest.raises(SystemExit) as error:
        analysis.main()
    assert error.value.code == 2 and source.read_bytes() == b"%PDF-1.4 fixture"
