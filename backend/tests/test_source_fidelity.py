import copy
import xml.etree.ElementTree as ET

import pytest

from backend import source_projects, store
from backend.source_fidelity import music_fingerprint, verify_layout
from backend.score_preservation import restyle_musicxml
from backend.tests.test_audio import client
from backend.tests.test_source_projects import create, edit, fixture_score, INSTRUMENTS


@pytest.mark.parametrize("instrument", INSTRUMENTS)
@pytest.mark.parametrize("preset", ["standard", "practice", "large"])
def test_style_fingerprint_ignores_layout_not_musical_content(instrument, preset):
    original = fixture_score(instrument).decode()
    rendered, _ = restyle_musicxml(original.encode(), "test.xml", "P1", preset, 2)
    proof = verify_layout(original, original, rendered, "P1")
    assert proof["style_preserves_music"] and not proof["music_edited"]
    assert proof["current_music_sha256"] == proof["styled_music_sha256"]


@pytest.mark.parametrize("path,value", [
    ("part/measure/note/pitch/step", "D"),
    ("part/measure/note/duration", "24"),
    ("part/measure/note/voice", "7"),
    ("part/measure/note/lyric/text", "바뀐 가사"),
])
def test_fingerprint_detects_changed_musical_fields(path, value):
    original = fixture_score().decode()
    changed = ET.fromstring(original)
    changed.find(path).text = value
    with pytest.raises(ValueError, match="음악 내용"):
        verify_layout(original, original, ET.tostring(changed), "P1")


def test_drum_instrument_reference_and_bank_are_checked():
    original = fixture_score("drums").decode()
    changed = ET.fromstring(original)
    changed.find("part-list/score-part/midi-instrument/midi-unpitched").text = "36"
    assert music_fingerprint(original, "P1") != music_fingerprint(ET.tostring(changed), "P1")


def test_style_verification_tracks_explicit_edit_separately_from_layout(client):
    job, document, _ = create(client)
    assert document["content_check"]["style_preserves_music"]
    assert not document["content_check"]["music_edited"]
    changed = edit(client, job["id"], document, operation="pitch", note_id="m0n0", step="D", alter=0, octave=4)
    assert changed.status_code == 200
    proof = changed.json()["document"]["content_check"]
    assert proof["music_edited"] and proof["style_preserves_music"]
    assert proof["current_music_sha256"] == proof["styled_music_sha256"]
    downloaded = client.get(f'/api/source-scores/{job["id"]}/files/notes.json')
    assert downloaded.status_code == 200
    assert downloaded.json()["time_unit"] == "quarter-note-fraction"
    assert downloaded.json()["notes"][0]["pitch"]["step"] == "D"
    assert downloaded.json()["content_check"] == proof
    assert "attachment" in downloaded.headers["content-disposition"]


def test_faulty_restyler_cannot_commit_changed_notes(client, monkeypatch):
    job, document, _ = create(client)
    previous = (store.directory(job["id"]) / "source-score.json").read_bytes()
    actual = source_projects._styled

    def faulty(state, **options):
        xml, warnings = actual(state, **options)
        root = ET.fromstring(xml)
        root.find("part/measure/note/duration").text = "999"
        return ET.tostring(root), warnings

    monkeypatch.setattr(source_projects, "_styled", faulty)
    result = client.put(f'/api/source-scores/{job["id"]}/layout', json={
        "base_revision": document["revision"], "preset": "large", "measures_per_line": 2})
    assert result.status_code == 422
    assert (store.directory(job["id"]) / "source-score.json").read_bytes() == previous
