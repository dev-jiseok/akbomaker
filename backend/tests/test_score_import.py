import io
import copy
import zipfile
import xml.etree.ElementTree as ET

import pytest

from backend import store
from backend.editing import create_document, persist
from backend.score_import import import_document, inspect, unpack
from backend.tests.test_audio import client, finish


def exported(tmp_path, inst="bass", order="tab-first"):
    doc = create_document([(0, 3, 36 if inst == "drums" else 43 if inst == "bass" else 64, .8)], inst, "가져오기 & 합주", 120, 4)
    if doc["tab"]:
        doc["tab"]["order"] = order
    doc["lyrics"] = [{"id": "l", "start": 5, "text": "보컬 진입"}]
    doc["annotations"] = [{"measure": 2, "section": "B", "cue": "함께 시작"}]
    persist(doc, tmp_path)
    return doc, (tmp_path / f"{inst}.musicxml").read_bytes()


@pytest.mark.parametrize("inst,order", [("bass", "tab-first"), ("bass", "staff-first"), ("guitar", "tab-first"), ("drums", "tab-first"), ("piano", "tab-first"), ("vocal", "tab-first")])
def test_roundtrip_concert_notes_ties_tabs_lyrics_and_annotations(tmp_path, inst, order):
    original, data = exported(tmp_path, inst, order)
    assert inspect(data, "score.musicxml")["parts"] == [{"id": "P1", "name": inst.capitalize(), "measures": 2}]
    result, source, warnings = import_document(data, "score.musicxml", "P1", inst)
    assert source == data and warnings
    assert result["title"] == original["title"] and result["ticks"] == 32 and result["bpm"] == 120
    assert [(n["start"], n["length"], n["pitch"]) for n in result["notes"]] == [(n["start"], n["length"], n["pitch"]) for n in original["notes"]]
    assert [(l["start"], l["text"]) for l in result["lyrics"]] == [(5, "보컬 진입")]
    assert result["annotations"] == original["annotations"]
    if original["tab"]:
        assert result["tab"]["order"] == "tab-first"
        assert result["notes"][0]["string"] == original["notes"][0]["string"]
        assert result["notes"][0]["fret"] == original["notes"][0]["fret"]


def test_namespaced_xml_and_compressed_mxl(tmp_path):
    _, data = exported(tmp_path)
    data = data.replace(b'<score-partwise version="4.0">', b'<score-partwise xmlns="http://www.musicxml.org/ns/musicxml" version="4.0">')
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.musicxml" /></rootfiles></container>')
        archive.writestr("score.musicxml", data)
    doc, source, _ = import_document(packed.getvalue(), "song.mxl", "P1", "bass")
    assert source == data and doc["notes"][0]["pitch"] == 43


@pytest.mark.parametrize("kind", ["time-modification", "repeat", "ending", "grace", "harmony", "dynamics", "slide", "hammer-on", "slur", "cue", "swing"])
def test_unsupported_notation_is_rejected_not_lost(tmp_path, kind):
    _, data = exported(tmp_path)
    tree = ET.fromstring(data)
    ET.SubElement(tree.find("part/measure"), kind)
    with pytest.raises(ValueError, match="아직 보존하지 못하는"):
        import_document(ET.tostring(tree), "score.xml", "P1", "bass")


@pytest.mark.parametrize("case", ["fractional", "meter", "tempo", "unclosed_tie", "negative_backup", "huge_numeric"])
def test_unrepresentable_timing_rejected(tmp_path, case):
    _, data = exported(tmp_path)
    tree = ET.fromstring(data)
    if case == "fractional":
        tree.find(".//divisions").text = "3"
    elif case == "meter":
        tree.find(".//beats").text = "5"
        tree.find(".//beat-type").text = "8"
    elif case == "tempo":
        ET.SubElement(tree.find("part/measure[@number='2']"), "sound", tempo="100")
    elif case == "unclosed_tie":
        for note in tree.findall(".//note"):
            for tie in list(note.findall("tie[@type='stop']")):
                note.remove(tie)
    elif case == "negative_backup":
        tree.find(".//backup/duration").text = "20"
    else:
        tree.find(".//sound").set("tempo", "1e1000000000")
    with pytest.raises(ValueError):
        import_document(ET.tostring(tree), "score.xml", "P1", "bass")


@pytest.mark.parametrize("case", ["fermata", "fingering", "octave_shift", "syllabic"])
def test_additional_symbols_not_silently_dropped(tmp_path, case):
    _, data = exported(tmp_path)
    tree = ET.fromstring(data)
    if case == "fermata":
        ET.SubElement(tree.find(".//notations"), "fermata")
    elif case == "fingering":
        ET.SubElement(tree.find(".//technical"), "fingering").text = "2"
    elif case == "octave_shift":
        ET.SubElement(tree.find(".//direction-type"), "octave-shift", type="up")
    else:
        tree.find(".//lyric/syllabic").text = "begin"
    with pytest.raises(ValueError):
        import_document(ET.tostring(tree), "score.xml", "P1", "bass")


def test_security_boundaries_and_pdf_rejection(tmp_path):
    with pytest.raises(ValueError, match="PDF"):
        unpack(b"%PDF-1.4", "score.pdf")
    with pytest.raises(ValueError, match="엔티티"):
        inspect(b'<!DOCTYPE score-partwise [<!ENTITY x "test">]><score-partwise>&x;</score-partwise>', "score.xml")
    with pytest.raises(ValueError, match="2MB"):
        unpack(b"a" * (2 * 1024 * 1024 + 1), "score.xml")
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="../score.xml" /></rootfiles></container>')
        archive.writestr("../score.xml", "<score-partwise />")
    with pytest.raises(ValueError, match="경로"):
        unpack(packed.getvalue(), "score.mxl")
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("oversized.xml", " " * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="크기"):
        unpack(packed.getvalue(), "score.mxl")


def test_import_project_no_fake_audio_original_xml_and_archive(client, tmp_path):
    _, data = exported(tmp_path)
    files = {"file": ("score.musicxml", data, "application/xml")}
    listed = client.post("/api/score-import/inspect", files=files)
    assert listed.status_code == 200 and listed.json()["parts"][0]["id"] == "P1"
    response = client.post("/api/score-import", files=files, data={"part_id": "P1", "instrument": "bass"})
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["source_type"] == "musicxml" and job["status"] == "completed" and job["original_url"] is None
    assert all(s["status"] == "pending" and not s.get("audio_url") for s in job["stems"])
    stem = next(s for s in job["stems"] if s["id"] == "bass")
    assert stem["score_status"] == "ready" and client.get(stem["score_url"]).status_code == 200
    source = client.get(stem["score_source_url"])
    assert source.content == data and "attachment" in source.headers["content-disposition"]
    doc = client.get(f'/api/jobs/{job["id"]}/scores/bass').json()
    assert doc["tab"]["order"] == "tab-first"
    archive = zipfile.ZipFile(io.BytesIO(client.get(f'/api/jobs/{job["id"]}/archive').content))
    assert "bass.source.musicxml" in archive.namelist()
    assert "No audio separation" in archive.read("README.txt").decode()
    assert "original.wav" not in archive.namelist()


def test_import_preview_revision_guard_non_destructive_save_and_original(client, tmp_path):
    _, data = exported(tmp_path)
    job = finish(client, client.post("/api/demo").json())
    endpoint = f'/api/jobs/{job["id"]}/scores/bass'
    original = client.get(endpoint).json()
    before = client.get(f'/api/jobs/{job["id"]}/files/bass.musicxml').content
    files = {"file": ("score.musicxml", data, "application/xml")}
    response = client.post(endpoint + "/import-preview", files=files, data={"part_id": "P1", "base_revision": "stale"})
    assert response.status_code == 409
    response = client.post(endpoint + "/import-preview", files=files, data={"part_id": "P1", "base_revision": original["revision"]})
    assert response.status_code == 200, response.text
    imported = response.json()["document"]
    assert imported["revision"] == original["revision"] and imported["ticks"] == 32
    assert imported["timing_bpm"] == original["timing_bpm"]
    assert client.get(endpoint).json() == original
    assert client.get(f'/api/jobs/{job["id"]}/files/bass.musicxml').content == before
    body = {"base_revision": imported["revision"], **{k: imported[k] for k in ("title", "bpm", "ticks", "notes", "lyrics", "annotations", "tab", "layout")}}
    saved = client.put(endpoint, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["document"]["ticks"] == 32
    assert client.get(endpoint + "?original=true").json() == original


def test_invalid_import_never_creates_a_project(client, tmp_path):
    before = list(store.DATA_DIR.glob("*/job.json"))
    response = client.post("/api/score-import", files={"file": ("score.musicxml", b"invalid")}, data={"part_id": "P1", "instrument": "bass"})
    assert response.status_code == 422
    assert list(store.DATA_DIR.glob("*/job.json")) == before


@pytest.mark.parametrize("case", ["duplicate_midi", "duplicate_score", "missing_midi_id", "missing_score_id", "undefined_midi_reference", "undefined_note_reference", "multiple_note_references"])
def test_ambiguous_or_undefined_instrument_ids_are_rejected(tmp_path, case):
    _, data = exported(tmp_path, "drums")
    tree = ET.fromstring(data)
    info = tree.find("part-list/score-part")
    if case == "duplicate_midi":
        duplicate = copy.deepcopy(info.find("midi-instrument"))
        duplicate.find("midi-unpitched").text = "43"  # last-wins would silently change kick to hi-hat
        info.append(duplicate)
    elif case == "duplicate_score":
        info.append(copy.deepcopy(info.find("score-instrument")))
    elif case == "missing_midi_id":
        info.find("midi-instrument").attrib.pop("id")
    elif case == "missing_score_id":
        info.find("score-instrument").attrib.pop("id")
    elif case == "undefined_midi_reference":
        info.remove(info.find("score-instrument"))
    elif case == "undefined_note_reference":
        tree.find(".//note/instrument").set("id", "undeclared-kit-piece")
    else:
        first = tree.find(".//note[instrument]")
        first.append(copy.deepcopy(first.find("instrument")))
    with pytest.raises(ValueError, match="원본 표기 유지 · 스타일 미리보기"):
        import_document(ET.tostring(tree), "score.xml", "P1", "drums")


@pytest.mark.parametrize("inst", ["bass", "guitar", "piano", "vocal"])
def test_pitched_notes_cannot_reference_an_undefined_score_instrument(tmp_path, inst):
    _, data = exported(tmp_path, inst)
    tree = ET.fromstring(data)
    ET.SubElement(tree.find(".//note[pitch]"), "instrument", id="undeclared")
    with pytest.raises(ValueError, match="score-instrument"):
        import_document(ET.tostring(tree), "score.xml", "P1", inst)


@pytest.mark.parametrize("case", ["visual_only", "mismatched", "continue", "duplicate"])
def test_visual_ties_must_match_supported_playback_ties(tmp_path, case):
    _, data = exported(tmp_path, "vocal")
    tree = ET.fromstring(data)
    tied = tree.find(".//note[tie]")
    assert tied is not None
    if case == "visual_only":
        for note in tree.findall(".//note"):
            for tie in list(note.findall("tie")):
                note.remove(tie)
    elif case == "mismatched":
        tied.find("notations/tied").set("type", "stop")
    elif case == "continue":
        tied.find("notations/tied").set("type", "continue")
    else:
        tied.find("notations").append(copy.deepcopy(tied.find("notations/tied")))
    with pytest.raises(ValueError, match="타이 표기.*원본 표기 유지"):
        import_document(ET.tostring(tree), "score.xml", "P1", "vocal")


def test_supported_playback_ties_do_not_require_redundant_visual_tied_elements(tmp_path):
    original, data = exported(tmp_path, "vocal")
    tree = ET.fromstring(data)
    for notation in tree.findall(".//notations"):
        for tie in list(notation.findall("tied")):
            notation.remove(tie)
    result, _, _ = import_document(ET.tostring(tree), "score.xml", "P1", "vocal")
    assert [(n["start"], n["length"], n["pitch"]) for n in result["notes"]] == [(n["start"], n["length"], n["pitch"]) for n in original["notes"]]


@pytest.mark.parametrize("case", ["ghost", "note_dynamics", "sound_dynamics"])
def test_ghost_and_dynamics_are_not_silently_imported_at_default_velocity(tmp_path, case):
    _, data = exported(tmp_path, "drums")
    tree = ET.fromstring(data)
    note = tree.find(".//note[unpitched]")
    if case == "ghost":
        ET.SubElement(note, "notehead", parentheses="yes").text = "normal"
    elif case == "note_dynamics":
        note.set("dynamics", "24")
    else:
        tree.find(".//sound").set("dynamics", "24")
    with pytest.raises(ValueError, match="원본 표기 유지 · 스타일 미리보기"):
        import_document(ET.tostring(tree), "score.xml", "P1", "drums")


def test_explicitly_non_parenthesized_drum_note_preserves_standard_one_based_mapping(tmp_path):
    _, data = exported(tmp_path, "drums")
    tree = ET.fromstring(data)
    ET.SubElement(tree.find(".//note[unpitched]"), "notehead", parentheses="no").text = "normal"
    assert tree.find(".//midi-unpitched").text == "37"
    result, _, _ = import_document(ET.tostring(tree), "score.xml", "P1", "drums")
    assert result["notes"][0]["pitch"] == 36


@pytest.mark.parametrize("style", ["slash", "measure-repeat", "beat-repeat", "multiple-rest"])
def test_measure_styles_are_rejected_instead_of_losing_repeat_or_slash_semantics(tmp_path, style):
    _, data = exported(tmp_path)
    tree = ET.fromstring(data)
    measure_style = ET.SubElement(tree.find(".//attributes"), "measure-style")
    ET.SubElement(measure_style, style, type="start").text = "1"
    with pytest.raises(ValueError, match="마디.*원본 표기 유지"):
        import_document(ET.tostring(tree), "score.xml", "P1", "bass")


def test_unsafe_import_is_non_destructive_and_preservation_preview_remains_available(client, tmp_path):
    _, data = exported(tmp_path, "drums")
    tree = ET.fromstring(data)
    ET.SubElement(tree.find(".//note[unpitched]"), "notehead", parentheses="yes").text = "normal"
    source = ET.tostring(tree)
    before = list(store.DATA_DIR.glob("*/job.json"))
    files = {"file": ("ghost.musicxml", source, "application/xml")}
    rejected = client.post("/api/score-import", files=files, data={"part_id": "P1", "instrument": "drums"})
    assert rejected.status_code == 422
    assert "원본 표기 유지" in rejected.json()["detail"]
    assert list(store.DATA_DIR.glob("*/job.json")) == before
    preserved = client.post("/api/score-import/preserve-preview", files=files, data={"part_id": "P1", "preset": "practice", "measures_per_line": "4"})
    assert preserved.status_code == 200, preserved.text
    assert ET.fromstring(preserved.json()["xml"]).find(".//notehead").get("parentheses") == "yes"
