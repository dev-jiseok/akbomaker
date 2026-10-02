import io
import threading
from types import SimpleNamespace
from uuid import uuid4
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import soundfile as sf

from backend import app as api, lyrics, store
from backend.analysis import analyze_beats
from backend.editing import create_document, generate_score
from backend.lyrics import recognize, timed_lyrics
from backend.score import export_score
from backend.separator import Cancelled
from backend.tests.test_audio import client, finish


def cues():
    return [{"id": "a", "start": .8, "end": 1.0, "text": "보컬"}, {"id": "b", "start": 1.1, "end": 1.4, "text": "진입"}]


def candidate(client, job, items=None):
    response = client.put(f'/api/jobs/{job["id"]}/lyrics/candidate', json={"base_revision": job.get("lyric_candidate", {}).get("revision", ""), "cues": cues() if items is None else items})
    assert response.status_code == 200, response.text
    return response.json()


def apply_body(client, job):
    return {"base_revision": job["lyric_candidate"]["revision"], "score_revisions": {s["id"]: client.get(f'/api/jobs/{job["id"]}/scores/{s["id"]}').json()["revision"] for s in job["stems"] if s.get("score_url") and s["score_status"] == "ready"}}


def test_analysis_only_accepts_real_upload_without_calling_sam_and_can_make_manual_tab(client, monkeypatch):
    monkeypatch.setattr(api, "engine_status", lambda: {"available": False, "issues": ["SAM unavailable"]})
    from backend import pipeline
    monkeypatch.setattr(pipeline.ENGINE, "extract", lambda *args: pytest.fail("Analysis-only must not call SAM"))
    audio = io.BytesIO()
    sf.write(audio, np.zeros(48000 * 3), 48000, format="WAV")
    assert client.post('/api/jobs', files={"file": ("song.wav", audio.getvalue(), "audio/wav")}).status_code == 503
    response = client.post('/api/jobs', data={"analysis_only": "true"}, files={"file": ("song.wav", audio.getvalue(), "audio/wav")})
    assert response.status_code == 202, response.text
    job = finish(client, response.json())
    assert job["analysis_only"] and job["original_url"] and not job["demo"]
    assert all(s["status"] == "pending" and not s.get("audio_url") for s in job["stems"])
    assert not job["residual_url"]
    job = candidate(client, job)
    applied = client.post(f'/api/jobs/{job["id"]}/lyrics/apply', json=apply_body(client, job))
    assert applied.status_code == 200 and applied.json()["applied"] == 0
    response = client.post(f'/api/jobs/{job["id"]}/scores/bass/new', json={"bpm": 120, "audio_offset": .5})
    assert response.status_code == 200, response.text
    stem = next(s for s in response.json()["stems"] if s["id"] == "bass")
    assert stem["status"] == "pending" and not stem.get("audio_url") and stem["score_edited"]
    doc = client.get(f'/api/jobs/{job["id"]}/scores/bass').json()
    assert doc["notes"] == [] and doc["tab"]["mode"] == "both" and doc["edited"]
    assert doc["audio_offset"] == .5 and len(doc["lyrics"]) == 2
    assert client.post(f'/api/jobs/{job["id"]}/scores/bass/new', json={}).status_code == 409
    assert client.post(f'/api/jobs/{job["id"]}/scores/guitar/new', json={"audio_offset": 3}).status_code == 422
    assert client.post(f'/api/jobs/{job["id"]}/transcribe', json={"instruments": ["bass"]}).status_code == 409


def test_real_cpu_beat_tracker_on_synthetic_clicks_and_silence(tmp_path):
    sr = 22050
    samples = np.zeros(sr * 12, dtype=np.float32)
    pulse = np.random.default_rng(4).normal(0, .4, int(sr * .025)) * np.hanning(int(sr * .025))
    for seconds in np.arange(.2, 11.5, .6):
        start = round(seconds * sr)
        samples[start:start + len(pulse)] += pulse
    path = tmp_path / "clicks.wav"
    sf.write(path, samples, sr)
    result = analyze_beats(path)
    assert abs(result["bpm"] - 100) <= 3
    assert len(result["beat_times"]) > 10 and result["regularity"] > .9
    assert result["first_beat_seconds"] == result["beat_times"][0]
    assert result["beat_times"] == sorted(result["beat_times"])
    assert "첫 박" in result["warning"] and result["analyzed_seconds"] == 12
    sf.write(path, np.zeros(sr * 4), sr)
    with pytest.raises(ValueError):
        analyze_beats(path)


def test_cpu_recognition_iterates_word_times_and_honors_cancellation(tmp_path, monkeypatch):
    words = [SimpleNamespace(word=" hello", start=.2, end=.6), SimpleNamespace(word="", start=1, end=2), SimpleNamespace(word="invalid", start=3, end=2), SimpleNamespace(word="tiny", start=1.0001, end=1.0002)]
    segments = [SimpleNamespace(end=2, no_speech_prob=.1, words=words), SimpleNamespace(end=3, no_speech_prob=.99, words=words)]
    calls = []
    def transcribe(path, **options):
        calls.append(options)
        return iter(segments), SimpleNamespace(language="en")
    monkeypatch.setattr(lyrics, "model", lambda: SimpleNamespace(transcribe=transcribe))
    progress = []
    result = recognize(tmp_path / "voice.wav", "auto", threading.Event(), progress.append)
    assert [(c["text"], c["start"], c["end"]) for c in result["cues"]] == [("hello", .2, .6)]
    assert calls[0]["language"] is None and calls[0]["word_timestamps"]
    assert progress == [2, 3]
    event = threading.Event(); event.set()
    with pytest.raises(Cancelled):
        recognize(tmp_path / "voice.wav", "en", event, lambda s: None)


def test_recognition_is_reviewable_not_applied_and_failure_preserves_previous_candidate(client, monkeypatch):
    job = finish(client, client.post('/api/demo').json())
    url = f'/api/jobs/{job["id"]}/lyrics/recognize'
    assert client.post(url, json={"source": "original"}).status_code == 422
    store.update(job["id"], demo=False)
    monkeypatch.setattr(lyrics, "status", lambda: {"available": True})
    monkeypatch.setattr(lyrics, "recognize", lambda *args: {"revision": uuid4().hex, "cues": cues(), "language": "ko"})
    response = client.post(url, json={"source": "original", "language": "ko"})
    assert response.status_code == 202
    job = finish(client, response.json())
    assert job["status"] == "completed" and job["lyric_candidate"]["source"] == "original"
    assert client.get(f'/api/jobs/{job["id"]}/scores/drums').json()["lyrics"] == []
    assert client.post(url, json={"source": "original"}).status_code == 409
    before = job["lyric_candidate"]
    def fail(*args):
        raise ValueError("no vocals")
    monkeypatch.setattr(lyrics, "recognize", fail)
    response = client.post(url, json={"source": "original", "replace_candidate": True})
    job = finish(client, response.json())
    assert job["analysis_error"] == "no vocals" and job["lyric_candidate"] == before and job["status"] == "completed"


def test_all_part_apply_preserves_notes_tab_and_original_with_stale_revision_guards(client):
    job = finish(client, client.post('/api/demo').json())
    before = {s["id"]: client.get(f'/api/jobs/{job["id"]}/scores/{s["id"]}').json() for s in job["stems"]}
    job = candidate(client, job)
    url = f'/api/jobs/{job["id"]}/lyrics/apply'
    body = apply_body(client, job)
    assert client.post(url, json={**body, "score_revisions": {}}).status_code == 409
    assert client.post(url, json={**body, "base_revision": "outdated"}).status_code == 409
    stale = {**body, "score_revisions": {**body["score_revisions"], "drums": "outdated"}}
    assert client.post(url, json=stale).status_code == 409
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 6
    for inst, previous in before.items():
        doc = client.get(f'/api/jobs/{job["id"]}/scores/{inst}').json()
        assert doc["notes"] == previous["notes"] and doc["tab"] == previous["tab"] and doc["annotations"] == previous["annotations"]
        assert [l["text"] for l in doc["lyrics"]] == ["보컬", "진입"]
        assert doc["revision"] != previous["revision"]
        assert client.get(f'/api/jobs/{job["id"]}/scores/{inst}?original=true').json() == previous
    assert client.post(url, json=body).status_code == 409
    latest = response.json()["job"]
    cleared = candidate(client, latest, [])
    response = client.post(url, json=apply_body(client, cleared))
    assert response.status_code == 200
    assert client.get(f'/api/jobs/{job["id"]}/scores/drums').json()["lyrics"] == []


@pytest.mark.parametrize("change", [{"start": -1}, {"end": .7}, {"end": 9999}, {"text": " "}, {"text": "bad\x00text"}])
def test_invalid_lyric_candidates_are_rejected_without_writes(client, change):
    job = finish(client, client.post('/api/demo').json())
    job = candidate(client, job)
    response = client.put(f'/api/jobs/{job["id"]}/lyrics/candidate', json={"base_revision": job["lyric_candidate"]["revision"], "cues": [{**cues()[0], **change}]})
    assert response.status_code == 422
    assert client.get(f'/api/jobs/{job["id"]}').json()["lyric_candidate"] == job["lyric_candidate"]


def test_lyrics_merge_same_cell_and_use_original_timing_and_offset():
    doc = create_document([], "piano", "Test", 120, 5, 2)
    doc["bpm"] = 200
    items, skipped = timed_lyrics([{"start": 1, "text": "intro"}, {"start": 2, "text": "one"}, {"start": 2.01, "text": "two"}, {"start": 3, "text": "three"}], doc)
    assert [(l["start"], l["text"]) for l in items] == [(0, "one two"), (8, "three")]
    assert skipped == 1
    with pytest.raises(ValueError):
        timed_lyrics([{"start": 2, "text": "x" * 80}, {"start": 2.01, "text": "y"}], doc)


def test_offset_transcription_trims_intro_and_keeps_sustained_tail(tmp_path):
    doc = generate_score([(0, 1, 60, .8), (1, 3, 62, .8), (3, 4, 64, .8)], "piano", "Offset", 120, 5, tmp_path, 2)
    assert doc["ticks"] == 32 and doc["audio_offset"] == 2
    assert [(n["start"], n["length"], n["pitch"]) for n in doc["notes"]] == [(0, 8, 62), (8, 8, 64)]


@pytest.mark.parametrize("size,group", [(2, "half"), (2, "beat"), (1, "half")])
def test_drums_primary_beams_follow_selected_group_and_secondary_preserves_beats(tmp_path, size, group):
    notes = [(start, start + size, 42, .8) for start in range(0, 16, size)]
    export_score([], "drums", "Beam", 120, 2, tmp_path, quantized=notes, layout={"preset": "practice", "beam_group": group})
    tree = ET.parse(tmp_path / 'drums.musicxml')
    primary = [n.findtext("beam[@number='1']") for n in tree.findall("part/measure/note") if n.findtext("voice") == "1"]
    length = (8 if group == "half" else 4) // size
    assert primary == (["begin"] + ["continue"] * (length - 2) + ["end"]) * (16 // (length * size))
    if size == 1:
        secondary = [n.findtext("beam[@number='2']") for n in tree.findall("part/measure/note") if n.findtext("voice") == "1"]
        assert secondary == ["begin", "continue", "continue", "end"] * 4


def test_server_restart_preserves_scores_after_interrupting_cpu_analysis(client):
    job = finish(client, client.post('/api/demo').json())
    job = candidate(client, job)
    store.update(job["id"], status="analyzing", analysis_previous_status="completed")
    store.recover()
    recovered = store.get(job["id"])
    assert recovered["status"] == "completed" and recovered["analysis_error"]
    assert recovered["lyric_candidate"] == job["lyric_candidate"]


def test_dotted_eighth_beams_with_sixteenth_but_rest_breaks_beam(tmp_path):
    export_score([], "drums", "Mixed", 120, 2, tmp_path, quantized=[(0, 3, 42, .8), (3, 4, 42, .8), (6, 8, 42, .8)])
    tree = ET.parse(tmp_path / 'drums.musicxml')
    upper = [n for n in tree.findall('part/measure/note') if n.findtext('voice') == '1']
    assert upper[0].find('dot') is not None and upper[0].findtext("beam[@number='1']") == 'begin'
    assert upper[1].findtext("beam[@number='1']") == 'end'
    assert upper[1].findtext("beam[@number='2']") == 'backward hook'
    assert all(n.find('beam') is None for n in upper if n.find('rest') is not None)
    assert upper[3].find('beam') is None
    assert tree.findtext('.//staff-details/staff-lines') == '5'


def test_beam_group_changes_notation_not_midi_audio(tmp_path):
    notes = [(tick, tick + 2, 42, .8) for tick in range(0, 16, 2)]
    export_score([], "drums", "Grouping", 120, 2, tmp_path, quantized=notes, layout={"preset": "practice", "beam_group": "half"})
    midi = (tmp_path / 'drums.mid').read_bytes()
    xml = (tmp_path / 'drums.musicxml').read_bytes()
    export_score([], "drums", "Grouping", 120, 2, tmp_path, quantized=notes, layout={"preset": "practice", "beam_group": "beat"})
    assert (tmp_path / 'drums.mid').read_bytes() == midi
    assert (tmp_path / 'drums.musicxml').read_bytes() != xml
