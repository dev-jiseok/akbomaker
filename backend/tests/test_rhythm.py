import json
import xml.etree.ElementTree as ET

import mido
import pytest

from backend import store
from backend.editing import ScoreEdit, create_document, load_document, persist, validate_edit
from backend.rhythm import measure_map
from backend.score_import import import_document
from backend.tests.test_audio import client, finish


def edit_body(doc):
    return {"base_revision": doc["revision"], **{k: doc[k] for k in ("title", "bpm", "ticks", "meters", "notes", "lyrics", "annotations", "layout", "tab")}}


@pytest.mark.parametrize("beats,unit", [(2, 4), (3, 4), (4, 4), (6, 8), (9, 8), (12, 8)])
@pytest.mark.parametrize("inst", ["bass", "drums"])
def test_all_meters_export_import_notes_lyrics_exact_voices(tmp_path, beats, unit, inst):
    meters = [{"measure": 1, "beats": beats, "beat_type": unit}]
    length = beats * 16 // unit
    pitch = 43 if inst == "bass" else 42
    doc = create_document([(0, 3, pitch, .8)], inst, "박자 테스트", 120, 4, meters=meters)
    doc["lyrics"] = [{"id": "l", "start": length + 1, "text": "같이"}]
    persist(doc, tmp_path)
    tree = ET.parse(tmp_path / f"{inst}.musicxml")
    assert tree.findtext(".//time/beats") == str(beats)
    assert tree.findtext(".//time/beat-type") == str(unit)
    for bar in tree.findall("part/measure"):
        assert bar.findtext("backup/duration") == str(length)
        for voice in ("1", "2"):
            assert sum(int(n.findtext("duration")) for n in bar.findall("note") if n.findtext("voice") == voice and n.find("chord") is None) == length
    imported, _, _ = import_document((tmp_path / f"{inst}.musicxml").read_bytes(), "score.xml", "P1", inst)
    assert imported["meters"] == meters and imported["ticks"] == doc["ticks"]
    assert [(n["start"], n["length"], n["pitch"]) for n in imported["notes"]] == [(n["start"], n["length"], n["pitch"]) for n in doc["notes"]]
    assert [(l["start"], l["text"]) for l in imported["lyrics"]] == [(length + 1, "같이")]
    midi = mido.MidiFile(tmp_path / f"{inst}.mid")
    signature = next(m for m in midi.tracks[0] if m.type == "time_signature")
    assert (signature.numerator, signature.denominator) == (beats, unit)
    assert sum(m.time for m in midi.tracks[0]) == doc["ticks"] * 120


def test_compound_eighth_beams_are_three_plus_three(tmp_path):
    doc = create_document([(i / 4, i / 4 + .1, 42, .8) for i in range(6)], "drums", "6/8", 120, 1.5, meters=[{"measure": 1, "beats": 6, "beat_type": 8}])
    persist(doc, tmp_path)
    notes = ET.parse(tmp_path / "drums.musicxml").findall("part/measure/note[voice='1']")
    assert [n.findtext("beam[@number='1']") for n in notes] == ["begin", "continue", "end", "begin", "continue", "end"]
    assert [n.findtext("duration") for n in notes] == ["2"] * 6


def test_compound_sixteenth_secondary_beams_are_eighth_subdivisions(tmp_path):
    doc = create_document([], "drums", "6/8", 120, 1.5, meters=[{"measure": 1, "beats": 6, "beat_type": 8}])
    doc["notes"] = [{"id": f"hat{i}", "start": i, "length": 1, "pitch": 42, "velocity": 80} for i in range(12)]
    persist(doc, tmp_path)
    notes = ET.parse(tmp_path / "drums.musicxml").findall("part/measure/note[voice='1']")
    assert [n.findtext("beam[@number='1']") for n in notes] == ["begin", "continue", "continue", "continue", "continue", "end"] * 2
    assert [n.findtext("beam[@number='2']") for n in notes] == ["begin", "end"] * 6


def test_mixed_meter_boundaries_ties_lyrics_and_midi_changes(tmp_path):
    meters = [{"measure": 1, "beats": 3, "beat_type": 4}, {"measure": 2, "beats": 6, "beat_type": 8}, {"measure": 3, "beats": 4, "beat_type": 4}]
    doc = create_document([(1.25, 4, 43, .8)], "bass", "변박", 120, 5, meters=meters)
    assert [(b["start"], b["end"]) for b in measure_map(doc["ticks"], meters)] == [(0, 12), (12, 24), (24, 40)]
    doc["lyrics"] = [{"id": "l", "start": 25, "text": "진입"}]
    doc["layout"].update(system_breaks=[2], page_breaks=[3])
    persist(doc, tmp_path)
    tree = ET.parse(tmp_path / "bass.musicxml")
    assert [m.findtext("attributes/time/beats") for m in tree.findall("part/measure")] == ["3", "6", "4"]
    assert tree.find("part/measure[@number='3']/print").get("new-page") == "yes"
    imported, _, _ = import_document((tmp_path / "bass.musicxml").read_bytes(), "score.xml", "P1", "bass")
    assert imported["meters"] == meters
    assert imported["notes"][0]["start"] == 10 and imported["notes"][0]["length"] == 22
    assert imported["lyrics"][0]["start"] == 25
    absolute, signatures = 0, []
    for message in mido.MidiFile(tmp_path / "bass.mid").tracks[0]:
        absolute += message.time
        if message.type == "time_signature":
            signatures.append((absolute, message.numerator, message.denominator))
    assert signatures == [(0, 3, 4), (1440, 6, 8), (2880, 4, 4)]


@pytest.mark.parametrize("meters", [[], [{"measure": 2, "beats": 3, "beat_type": 4}], [{"measure": 1, "beats": 5, "beat_type": 8}], [{"measure": 1, "beats": 3, "beat_type": 4}, {"measure": 1, "beats": 6, "beat_type": 8}], [{"measure": 1, "beats": 3, "beat_type": 4}, {"measure": 3, "beats": 4, "beat_type": 4}], [{"measure": 1, "beats": 3.0, "beat_type": 4}]])
def test_invalid_meters_are_rejected_without_mutation(meters):
    doc = create_document([], "piano", "test", 120, 2)
    before = json.dumps(doc)
    body = edit_body(doc)
    body.update(meters=meters, ticks=24)
    with pytest.raises(ValueError):
        validate_edit(ScoreEdit(**body), doc)
    assert json.dumps(doc) == before


def test_partial_bar_and_more_than_600_bars_rejected():
    with pytest.raises(ValueError, match="완전한"):
        measure_map(16, [{"measure": 1, "beats": 3, "beat_type": 4}])
    with pytest.raises(ValueError, match="600"):
        measure_map(7212, [{"measure": 1, "beats": 3, "beat_type": 4}])


def test_legacy_migration_preserves_revision_notes_and_original_file(tmp_path):
    doc = create_document([(0, .5, 60, .8)], "piano", "legacy", 120, 2)
    doc.pop("meters")
    persist(doc, tmp_path, automatic=True)
    before = (tmp_path / "piano.auto.json").read_bytes()
    loaded = load_document(tmp_path, "piano", 2)
    assert loaded["meters"] == [{"measure": 1, "beats": 4, "beat_type": 4}]
    assert loaded["revision"] == doc["revision"] and loaded["notes"] == doc["notes"]
    assert (tmp_path / "piano.auto.json").read_bytes() == before


def test_older_client_preserves_current_meters():
    doc = create_document([], "piano", "test", 120, 3, meters=[{"measure": 1, "beats": 6, "beat_type": 8}])
    body = edit_body(doc)
    body.pop("meters")
    assert validate_edit(ScoreEdit(**body), doc)["meters"] == doc["meters"]


def test_dotted_quarter_metronome_converts_without_changing_speed(tmp_path):
    doc = create_document([], "piano", "6/8", 90, 4, meters=[{"measure": 1, "beats": 6, "beat_type": 8}])
    persist(doc, tmp_path)
    tree = ET.parse(tmp_path / "piano.musicxml")
    direction = tree.find(".//direction")
    direction.remove(direction.find("sound"))
    metro = direction.find("direction-type/metronome")
    metro.find("per-minute").text = "60"
    metro.insert(1, ET.Element("beat-unit-dot"))
    imported, _, _ = import_document(ET.tostring(tree.getroot()), "score.xml", "P1", "piano")
    assert imported["bpm"] == 90


def test_api_mixed_meter_preview_save_original_revision_and_metadata(client):
    job = finish(client, client.post("/api/demo").json())
    url = f'/api/jobs/{job["id"]}/scores/bass'
    doc = client.get(url).json()
    body = edit_body(doc)
    body.update(ticks=40, meters=[{"measure": 1, "beats": 3, "beat_type": 4}, {"measure": 3, "beats": 4, "beat_type": 4}],
                notes=[{"id": "n", "start": 23, "length": 6, "pitch": 43, "velocity": 80}], lyrics=[{"id": "l", "start": 24, "text": "같이"}], annotations=[])
    preview = client.post(url + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert client.get(url).json() == doc
    saved = client.put(url, json=body)
    assert saved.status_code == 200, saved.text
    assert client.get(url + "?original=true").json() == doc
    stem = next(s for s in saved.json()["job"]["stems"] if s["id"] == "bass")
    assert stem["score_meters"] == body["meters"]
    assert client.put(url, json=body).status_code == 409


def test_new_transcription_and_invalid_request_never_starts_job(client):
    job = finish(client, client.post("/api/demo").json())
    url = f'/api/jobs/{job["id"]}/transcribe'
    invalid = client.post(url, json={"instruments": ["drums"], "meter": {"measure": 1, "beats": 5, "beat_type": 8}})
    assert invalid.status_code == 422
    assert store.get(job["id"])["status"] == "completed"
    response = client.post(url, json={"instruments": ["drums"], "bpm": 108, "meter": {"measure": 1, "beats": 6, "beat_type": 8}})
    assert response.status_code == 202, response.text
    updated = finish(client, response.json())
    doc = client.get(f'/api/jobs/{job["id"]}/scores/drums').json()
    assert doc["meters"] == [{"measure": 1, "beats": 6, "beat_type": 8}]
    assert next(s for s in updated["stems"] if s["id"] == "drums")["score_meters"] == doc["meters"]


def test_empty_score_meter_validation_and_automatic_snapshot(client):
    job = store.create("수동 입력", "file")
    store.update(job["id"], status="completed", duration=3, analysis_only=True)
    url = f'/api/jobs/{job["id"]}/scores/bass/new'
    assert client.post(url, json={"meter": {"measure": 2, "beats": 3, "beat_type": 4}}).status_code == 422
    assert not (store.directory(job["id"]) / "bass.score.json").exists()
    response = client.post(url, json={"bpm": 120, "meter": {"measure": 1, "beats": 3, "beat_type": 4}})
    assert response.status_code == 200, response.text
    doc = client.get(url.removesuffix("/new")).json()
    assert doc["ticks"] == 24 and doc["notes"] == [] and doc["meters"][0]["beats"] == 3
    assert client.get(url.removesuffix("/new") + "?original=true").json()["meters"] == doc["meters"]


@pytest.mark.parametrize("case", ["implicit", "incomplete", "mid_bar", "polymeter"])
def test_unsupported_bar_geometry_in_import_never_retimed(tmp_path, case):
    doc = create_document([], "piano", "3/4", 120, 3, meters=[{"measure": 1, "beats": 3, "beat_type": 4}])
    persist(doc, tmp_path)
    root = ET.parse(tmp_path / "piano.musicxml").getroot()
    bar = root.find("part/measure")
    if case == "implicit":
        bar.set("implicit", "yes")
    elif case == "incomplete":
        bar.find("note/duration").text = "8"
    elif case == "mid_bar":
        attrs = ET.SubElement(bar, "attributes")
        time = ET.SubElement(attrs, "time")
        ET.SubElement(time, "beats").text = "4"
        ET.SubElement(time, "beat-type").text = "4"
    else:
        time = ET.SubElement(bar.find("attributes"), "time", number="2")
        ET.SubElement(time, "beats").text = "4"
        ET.SubElement(time, "beat-type").text = "4"
    with pytest.raises(ValueError):
        import_document(ET.tostring(root), "score.xml", "P1", "piano")
