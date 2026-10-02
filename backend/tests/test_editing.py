import io
import xml.etree.ElementTree as ET

import mido
import pytest

from backend import app as api, store
from backend.editing import ScoreEdit, create_document, generate_score, load_document, validate_edit
from backend.score import export_score
from backend.tests.test_audio import client, finish  # shared API fixtures


def body(doc):
    return {"base_revision": doc["revision"], **{key: doc[key] for key in ("title", "bpm", "notes", "annotations", "layout")}}


def test_save_changes_notation_midi_and_persisted_document(client):
    job = finish(client, client.post("/api/demo").json())
    url = f'/api/jobs/{job["id"]}/scores/drums'
    original = client.get(url).json()
    edit = body(original)
    edit.update(title="나의 연습 악보 & 교정", bpm=100,
                notes=[{"id": "kick", "start": 0, "length": 4, "pitch": 36, "velocity": 92},
                       {"id": "hat", "start": 0, "length": 2, "pitch": 46, "velocity": 65}],
                annotations=[{"measure": 1, "section": "A", "cue": "여기서 필인"}],
                layout={"preset": "practice", "measures_per_line": 4, "show_numbers": True})
    response = client.put(url, json=edit)
    assert response.status_code == 200, response.text
    result = response.json()
    saved = result["document"]
    assert saved["edited"] and saved["revision"] != original["revision"]
    assert client.get(url).json() == saved
    assert client.get(url + "?original=true").json() == original
    stem = next(s for s in result["job"]["stems"] if s["id"] == "drums")
    assert stem["score_edited"] and stem["score_revision"] == saved["revision"]
    tree = ET.fromstring(client.get(stem["score_url"]).content)
    assert tree.findtext("work/work-title") == edit["title"]
    assert tree.findtext(".//rehearsal") == "A"
    assert tree.findtext(".//words") == "여기서 필인"
    assert tree.findtext(".//per-minute") == "100"
    assert tree.findall(".//technical/open-string")
    sounding = [n for n in tree.findall(".//note") if n.find("instrument") is not None]
    assert {(n.find("instrument").get("id"), n.findtext("stem")) for n in sounding} == {("I36", "down"), ("I46", "up")}
    assert tree.find("part/measure[@number='5']/print").get("new-system") == "yes"
    midi = mido.MidiFile(file=io.BytesIO(client.get(stem["midi_url"]).content))
    assert {(m.note, m.velocity) for m in midi.tracks[0] if m.type == "note_on"} == {(36, 92), (46, 65)}
    assert client.put(url, json=edit).status_code == 409
    assert client.get(url).json() == saved
    assert client.post(f'/api/jobs/{job["id"]}/transcribe', json={"instruments": ["drums"], "bpm": 108}).status_code == 409
    response = client.post(f'/api/jobs/{job["id"]}/transcribe', json={"instruments": ["drums"], "bpm": 108, "overwrite_edits": True})
    assert response.status_code == 202
    regenerated = finish(client, response.json())
    assert not next(s for s in regenerated["stems"] if s["id"] == "drums")["score_edited"]


def test_preview_does_not_modify_saved_document_or_files(client):
    job = finish(client, client.post("/api/demo").json())
    url = f'/api/jobs/{job["id"]}/scores/piano'
    original = client.get(url).json()
    before = client.get(f'/api/jobs/{job["id"]}/files/piano.musicxml').content
    edit = body(original)
    edit.update(title="preview only", notes=[], layout={"preset": "large", "measures_per_line": 2, "show_numbers": False})
    preview = client.post(url + "/preview", json=edit)
    assert preview.status_code == 200
    tree = ET.fromstring(preview.json()["musicxml"])
    assert tree.findtext("work/work-title") == "preview only"
    assert tree.findtext(".//measure-numbering") == "none"
    assert tree.find("part/measure[@number='3']/print").get("new-system") == "yes"
    assert client.get(url).json() == original
    assert client.get(f'/api/jobs/{job["id"]}/files/piano.musicxml').content == before


@pytest.mark.parametrize("case", ["overlap", "outside", "duplicate", "drum_pitch", "bad_measure"])
def test_invalid_edits_rejected(case):
    current = create_document([], "drums", "test", 120, 2)
    edit = body(current)
    note = {"id": "n", "start": 0, "length": 4, "pitch": 36, "velocity": 80}
    edit["notes"] = [note]
    if case == "overlap":
        edit["notes"].append({**note, "id": "n2", "start": 2})
    elif case == "outside":
        note["start"] = 15
    elif case == "duplicate":
        edit["notes"].append({**note, "start": 8})
    elif case == "drum_pitch":
        note["pitch"] = 60
    else:
        edit["annotations"] = [{"measure": 2, "section": "A", "cue": ""}]
    with pytest.raises(ValueError):
        validate_edit(ScoreEdit(**edit), current)


def test_active_job_refuses_edit(client):
    job = finish(client, client.post("/api/demo").json())
    url = f'/api/jobs/{job["id"]}/scores/drums'
    doc = client.get(url).json()
    event = api.reserve(job["id"])
    try:
        assert client.put(url, json=body(doc)).status_code == 409
    finally:
        with api.TASK_LOCK:
            api.EVENTS.pop(job["id"], None)
        event.set()


@pytest.mark.parametrize("inst", ["piano", "drums"])
def test_old_musicxml_can_be_opened_without_inference(tmp_path, inst):
    events = [(0, 0.5, 36 if inst == "drums" else 60, 0.8), (0.5, 3.5, 42 if inst == "drums" else 64, 0.7)]
    export_score(events, inst, "Old project", 120, 4, tmp_path)
    current = load_document(tmp_path, inst, 4)
    assert current["title"] == "Old project"
    assert len(current["notes"]) == 2
    assert current == load_document(tmp_path, inst, 4, original=True)
    assert current == load_document(tmp_path, inst, 4)


def test_percussion_each_voice_has_exact_bar_duration(tmp_path):
    generate_score([(0, .1, 36, .8), (0, .1, 42, .8), (.25, .35, 42, .8), (.5, .6, 42, .8), (1, 1.1, 38, .8)], "drums", "Drums", 120, 4, tmp_path)
    tree = ET.parse(tmp_path / "drums.musicxml")
    for measure in tree.findall("part/measure"):
        assert measure.findtext("backup/duration") == "16"
        for voice in ("1", "2"):
            assert sum(int(n.findtext("duration")) for n in measure.findall("note") if n.findtext("voice") == voice and n.find("chord") is None) == 16
    assert tree.findall(".//beam[@number='1']")


def test_repeated_notes_are_not_merged_into_one_midi_attack(tmp_path):
    generate_score([(0, .5, 60, .8), (.5, 1, 60, .8)], "piano", "Repeated", 120, 2, tmp_path)
    midi = mido.MidiFile(tmp_path / "piano.mid")
    assert sum(m.type == "note_on" for m in midi.tracks[0]) == 2


def test_invalid_xml_control_characters_rejected():
    edit = body(create_document([], "piano", "Test", 120, 2))
    edit["title"] = "Invalid\x00title"
    with pytest.raises(ValueError):
        ScoreEdit(**edit)
