"""New optional repair operations retain HTTP persistence and undo semantics."""
import xml.etree.ElementTree as ET

import pytest

from backend.tests.test_audio import client
from backend.tests.test_source_projects import create, edit, INSTRUMENTS
from backend.tests.test_source_score import straight, combined_tab


def undo(client, job, document):
    response = client.post(f'/api/source-scores/{job["id"]}/history', json={
        "base_revision": document["revision"], "action": "undo"})
    assert response.status_code == 200, response.text
    return response.json()["document"]


@pytest.mark.parametrize("instrument", INSTRUMENTS)
def test_delete_reinsert_and_undo_persists_all_instruments(client, instrument):
    job, initial, raw = create(client, instrument, ET.tostring(straight(instrument)))
    original = initial["notes"][0]
    result = edit(client, job["id"], initial, note_id=original["id"], operation="delete")
    assert result.status_code == 200, result.text
    rest = result.json()["document"]
    assert rest["notes"][0]["kind"] == "rest"
    assert rest["notes"][0]["duration"] == original["duration"]
    assert rest["content_check"]["style_preserves_music"] and rest["content_check"]["music_edited"]
    if instrument in {"guitar", "bass"}:
        patch = {"kind": "tab", **original["fingering"]}
    elif instrument == "drums":
        patch = {"kind": "unpitched", "drum_id": original["drum_id"]}
    else:
        patch = {"kind": "pitched", **original["pitch"]}
    result = edit(client, job["id"], rest, note_id=original["id"], operation="insert", **patch)
    assert result.status_code == 200, result.text
    inserted = result.json()["document"]
    assert inserted["notes"][0]["kind"] == original["kind"]
    assert inserted["notes"][0]["duration"] == original["duration"]
    assert client.get(initial["source_url"]).content == raw
    assert undo(client, job, inserted)["xml"] == rest["xml"]


@pytest.mark.parametrize("instrument", ["guitar", "bass"])
def test_tab_sound_change_is_atomic_in_saved_standard_and_tab_parts(client, instrument):
    job, initial, raw = create(client, instrument, ET.tostring(combined_tab(instrument)))
    selected = next(n for n in initial["notes"] if n["staff"] == "2" and n["kind"] == "pitched")
    result = edit(client, job["id"], initial, note_id=selected["id"], operation="tab_pitch", string=1, fret=3)
    assert result.status_code == 200, result.text
    document = result.json()["document"]
    tab = next(n for n in document["notes"] if n["id"] == selected["id"])
    standard = next(n for n in document["notes"] if n["staff"] == "1" and n["onset"] == tab["onset"])
    assert tab["fingering"] == {"string": 1, "fret": 3}
    assert tab["pitch"]["step"] == standard["pitch"]["step"]
    assert tab["pitch"]["octave"] + 1 == standard["pitch"]["octave"]
    assert client.get(f'/api/source-scores/{job["id"]}').json() == document
    assert client.get(initial["source_url"]).content == raw
    assert undo(client, job, document)["xml"] == initial["xml"]


def test_chord_insertion_updates_project_count_and_undo_restores_it(client):
    job, initial, _ = create(client, "vocal", ET.tostring(straight()))
    result = edit(client, job["id"], initial, note_id="m0n0", operation="chord", kind="pitched", step="G", alter=0, octave=4)
    assert result.status_code == 200, result.text
    document = result.json()["document"]
    assert len(document["notes"]) == len(initial["notes"]) + 1
    projected = client.get(f'/api/jobs/{job["id"]}').json()
    assert next(s for s in projected["stems"] if s["id"] == "vocal")["note_count"] == len(document["notes"])
    assert undo(client, job, document)["xml"] == initial["xml"]


def test_rhythm_and_added_lyric_survive_reload_with_exact_undo(client):
    job, initial, _ = create(client, "vocal", ET.tostring(straight()))
    changed = edit(client, job["id"], initial, note_id="m0n0", operation="rhythm", type="eighth", dots=0)
    assert changed.status_code == 200, changed.text
    rhythm = changed.json()["document"]
    assert rhythm["notes"][0]["duration"] == "1/2"
    assert len(rhythm["notes"]) == len(initial["notes"]) + 1
    added = edit(client, job["id"], rhythm, note_id="m0n0", operation="lyric_add", text="합주 진입")
    assert added.status_code == 200, added.text
    document = added.json()["document"]
    assert document["notes"][0]["lyrics"][-1]["text"] == "합주 진입"
    assert client.get(f'/api/source-scores/{job["id"]}').json() == document
    assert undo(client, job, document)["xml"] == rhythm["xml"]
