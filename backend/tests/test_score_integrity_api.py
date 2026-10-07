"""Consistency inspection is advisory and cannot certify source PDF accuracy."""
import hashlib

import pytest

from backend.tests.test_audio import client
from backend.tests.test_source_projects import create, fixture_score


@pytest.mark.parametrize("instrument", ["drums", "bass", "guitar", "vocal", "piano", "synthesizer"])
def test_structural_report_attached_to_original_score_and_json_without_changing_music(client, instrument):
    job, document, original = create(client, instrument)
    report = document["integrity_report"]
    assert report["scope"] == "musicxml-structure-not-pdf-accuracy"
    assert report["checked_measures"] == 1
    assert report["checked_notes"] == len(document["notes"])
    assert "accuracy" not in report and "pdf_verified" not in report
    assert client.get(document["source_url"]).content == original
    assert document["source_sha256"] == hashlib.sha256(original).hexdigest()
    payload = client.get(f'/api/source-scores/{job["id"]}/files/notes.json').json()
    assert payload["integrity_report"] == report
    assert client.get(f'/api/source-scores/{job["id"]}').json()["revision"] == document["revision"]


def test_bad_lengths_are_flagged_with_original_note_position_without_auto_fixing(client):
    original = fixture_score().replace(b"<duration>12</duration>", b"<duration>6</duration>", 1)
    job, document, _ = create(client, data=original)
    mismatch = next(issue for issue in document["integrity_report"]["issues"] if issue["code"] == "note-duration-mismatch")
    assert mismatch["note_id"] == "m0n0" and mismatch["measure_index"] == 1
    assert document["notes"][0]["duration"] == "1/2"
    assert document["content_check"]["style_preserves_music"] is True
    before = document["integrity_report"]
    response = client.put(f'/api/source-scores/{job["id"]}/layout', json={"base_revision": document["revision"], "preset": "large", "measures_per_line": 2})
    assert response.status_code == 200
    assert response.json()["document"]["integrity_report"] == before
    assert client.get(document["source_url"]).content == original
