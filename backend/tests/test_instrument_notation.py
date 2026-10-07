import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import xml.etree.ElementTree as ET

import mido
import numpy as np
import pytest
import soundfile as sf

from backend.editing import ScoreEdit, create_document, generate_score, load_document, persist, validate_edit
from backend.evaluation import compare_events
from backend.score import quantize, transcribe
from backend.score_import import import_document
from backend.transcription import PROFILES, clean_events, hz, melody_segments, transcribe_instrument
from backend.drum_worker import read_drum_midi, transcribe_external
from backend.tests.test_audio import client, finish


def edit_for(doc, **changes):
    body = {k: doc[k] for k in ("title", "bpm", "ticks", "meters", "notes", "lyrics", "annotations", "layout", "tab", "keyboard")}
    return ScoreEdit(base_revision=doc["revision"], **{**body, **changes})


@pytest.mark.parametrize("inst", ["piano", "synthesizer"])
def test_grand_staff_hands_lyrics_roundtrip_and_midi_are_independent(tmp_path, inst):
    doc = create_document([(0, 3, 48, .7), (0, 1, 72, .8), (1, 2, 60, .6)], inst, "Hands", 120, 4)
    # Event sorting is independent of pitch. Override the intended notes by pitch.
    next(n for n in doc["notes"] if n["pitch"] == 48)["hand"] = "right"
    next(n for n in doc["notes"] if n["pitch"] == 72)["hand"] = "left"
    doc["lyrics"] = [{"id": "lyric", "start": 2, "text": "보컬 진입"}]
    persist(doc, tmp_path)
    midi_before = (tmp_path / f"{inst}.mid").read_bytes()
    xml = ET.parse(tmp_path / f"{inst}.musicxml")
    attrs = xml.find("part/measure/attributes")
    assert attrs.findtext("staves") == "2" and attrs.findtext("part-symbol") == "brace"
    assert [(c.get("number"), c.findtext("sign")) for c in attrs.findall("clef")] == [("1", "G"), ("2", "F")]
    assert xml.find(".//note[lyric]").findtext("staff") == "2"
    for bar in xml.findall("part/measure"):
        for voice in ("1", "2"):
            assert sum(int(n.findtext("duration")) for n in bar.findall("note") if n.findtext("voice") == voice and n.find("chord") is None) == 16
    restored, _, _ = import_document((tmp_path / f"{inst}.musicxml").read_bytes(), "test.musicxml", "P1", inst)
    assert restored["keyboard"]["mode"] == "grand"
    assert [(n["pitch"], n["hand"]) for n in sorted(restored["notes"], key=lambda n: n["pitch"])] == [(48, "right"), (60, "right"), (72, "left")]
    assert len(restored["notes"]) == len(doc["notes"])
    doc["keyboard"]["mode"] = "single"
    persist(doc, tmp_path)
    assert (tmp_path / f"{inst}.mid").read_bytes() == midi_before
    assert ET.parse(tmp_path / f"{inst}.musicxml").find(".//staves") is None


def test_legacy_keyboard_migration_does_not_modify_file_or_revision(tmp_path):
    doc = create_document([], "piano", "Legacy", 120, 2)
    doc.pop("keyboard")
    path = tmp_path / "piano.score.json"
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    loaded = load_document(tmp_path, "piano", 2)
    assert loaded["keyboard"]["mode"] == "single" and loaded["revision"] == doc["revision"]
    assert path.read_bytes() == before


def test_unrelated_old_client_edit_preserves_keyboard_and_hand():
    doc = create_document([(0, 1, 60, .8)], "piano", "Hands", 120, 2)
    doc["notes"][0]["hand"] = "left"
    body = edit_for(doc).model_dump()
    body.pop("keyboard")
    body["notes"][0].pop("hand")
    result = validate_edit(ScoreEdit(**body), doc)
    assert result["keyboard"] == doc["keyboard"] and result["notes"][0]["hand"] == "left"
    bass = create_document([], "bass", "TAB", 120, 2)
    with pytest.raises(ValueError, match="대보표"):
        validate_edit(edit_for(bass, keyboard={"mode": "grand", "split_pitch": 60}), bass)


def test_keyboard_api_preview_style_and_lyrics_keep_notes_and_original(client):
    job = finish(client, client.post("/api/demo").json())
    endpoint = f'/api/jobs/{job["id"]}/scores/piano'
    doc = client.get(endpoint).json()
    original = client.get(endpoint + "?original=true").json()
    assert doc["keyboard"]["mode"] == "grand"
    body = edit_for(doc, keyboard={"mode": "single", "split_pitch": 55}).model_dump()
    assert client.post(endpoint + "/preview", json=body).status_code == 200
    assert client.get(endpoint).json()["keyboard"]["mode"] == "grand"
    saved = client.put(endpoint, json=body)
    assert saved.status_code == 200
    stem = next(s for s in saved.json()["job"]["stems"] if s["id"] == "piano")
    assert stem["score_keyboard"]["mode"] == "single"
    assert client.get(endpoint + "?original=true").json() == original
    assert [(n["start"], n["length"], n["pitch"]) for n in saved.json()["document"]["notes"]] == [(n["start"], n["length"], n["pitch"]) for n in doc["notes"]]


def test_melody_stabilizes_vibrato_keeps_rests_and_repeated_notes():
    pitches = np.r_[np.full(50, 69) + .35 * np.sin(np.arange(50)), np.full(10, 69), np.full(40, 72)]
    frequency = 440 * 2 ** ((pitches - 69) / 12)
    voiced = np.ones(100, dtype=bool)
    voiced[50:60] = False
    events = melody_segments(frequency, voiced, np.ones(100), np.full(100, .2), .01, 1, onsets=[.25])
    assert [(round(a, 2), round(b, 2), p) for a, b, p, _ in events] == [(0, .25, 69), (.25, .5, 69), (.6, 1, 72)]
    assert all(a[1] <= b[0] for a, b in zip(events, events[1:]))


def test_late_or_invalid_events_never_turn_into_tail_notes():
    assert clean_events([(5, 6, 60, .8), (0, 1, float("nan"), .8), (0, 1, 60, 0)], 2) == []
    assert quantize([(5, 6, 60, .8)], 120, 2) == []


def test_reference_matching_does_not_count_one_prediction_twice():
    result = compare_events([(0, .1, 36, 1), (.03, .1, 36, 1), (0, .1, 42, 1)], [(.01, .1, 36, 1), (0, .1, 38, 1)])
    assert result["matched_notes"] == 1 and result["missing_notes"] == 2 and result["extra_notes"] == 1


def test_drum_model_midi_keeps_simultaneous_hits_and_tempo(tmp_path):
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage("set_tempo", tempo=500000), mido.Message("note_on", channel=9, note=36, velocity=100),
                  mido.Message("note_on", channel=9, note=49, velocity=80), mido.Message("note_off", channel=9, note=36, time=120),
                  mido.Message("note_off", channel=9, note=49)])
    path = tmp_path / "model.mid"
    midi.save(path)
    events = read_drum_midi(path, 1)
    assert [(a, b, p) for a, b, p, _ in events] == [(0, .125, 36), (0, .125, 49)]


@pytest.mark.parametrize("inst,pitch", [("vocal", 69), ("bass", 40)])
def test_actual_cpu_melody_inference_is_monophonic(tmp_path, inst, pitch):
    sr = 22050
    t = np.arange(2 * sr) / sr
    hz = 440 * 2 ** ((pitch - 69) / 12)
    samples = .3 * (np.sin(2 * np.pi * hz * t) + .5 * np.sin(4 * np.pi * hz * t))
    samples *= np.minimum(t / .04, 1) * np.minimum((2 - t) / .04, 1)
    path = tmp_path / f"{inst}.wav"
    sf.write(path, samples, sr)
    events = transcribe(path, inst)
    assert events and {e[2] for e in events} == {pitch}
    assert all(a[1] <= b[0] for a, b in zip(events, events[1:]))


@pytest.mark.parametrize("inst", ["vocal", "bass", "drums", "guitar", "piano", "synthesizer"])
def test_every_instrument_places_lyrics_below_its_last_staff(tmp_path, inst):
    doc = create_document([(0, .5, 42 if inst == "drums" else 60, .8)], inst, "All parts", 120, 2)
    doc["lyrics"] = [{"id": "cue", "start": 0, "text": "함께"}]
    persist(doc, tmp_path)
    xml = ET.parse(tmp_path / f"{inst}.musicxml")
    assert xml.findtext(".//note/lyric/text") == "함께"
    expected = "2" if inst in {"bass", "guitar", "piano", "synthesizer"} else None
    assert xml.findtext(".//note[lyric]/staff") == expected
    if inst in {"bass", "guitar"}:
        assert xml.findtext(".//clef[@number='1']/sign") == "TAB"
        assert xml.findtext(".//clef[@number='2']/sign") == ("F" if inst == "bass" else "G")
        note = doc["notes"][0]
        assert doc["tab"]["tuning"][note["string"] - 1] + note["fret"] == note["pitch"]


@pytest.mark.parametrize("inst", PROFILES)
def test_polyphonic_profiles_send_the_instrument_range_without_octave_folding(tmp_path, monkeypatch, inst):
    inference, package = ModuleType("basic_pitch.inference"), ModuleType("basic_pitch")
    calls = []
    def predict(path, **kwargs):
        calls.append(kwargs)
        return None, None, [(0, .5, 60, .8), (5, 6, 80, .8)]
    inference.predict = predict
    monkeypatch.setitem(sys.modules, "basic_pitch", package)
    monkeypatch.setitem(sys.modules, "basic_pitch.inference", inference)
    path = tmp_path / "audio.wav"
    sf.write(path, np.full(22050, .1), 22050)
    assert transcribe_instrument(path, inst, lambda: "model", "polyphonic") == [(0, .5, 60, .8)]
    assert calls[0]["minimum_frequency"] == hz(PROFILES[inst]["range"][0])
    assert calls[0]["maximum_frequency"] == hz(PROFILES[inst]["range"][1])
    assert calls[0]["minimum_note_length"] == PROFILES[inst]["minimum_ms"]


def test_drum_fallback_keeps_simultaneous_hits_and_rejects_cutoff_attacks(tmp_path, monkeypatch):
    from backend import demo
    monkeypatch.delenv("AKBO_DRUM_WORKER", raising=False)
    samples = demo.generate()[0]["drums"]
    from backend.config import SAMPLE_RATE
    path = tmp_path / "drums.wav"
    sf.write(path, samples[:round(2 * SAMPLE_RATE)], SAMPLE_RATE)
    events = transcribe(path, "drums")
    kick = [e for e in events if e[2] == 36]
    hats = [e for e in events if e[2] == 42]
    assert len(kick) == 2 and len(hats) == 8
    assert all(any(abs(k[0] - h[0]) < .035 for h in hats) for k in kick)
    assert kick[0][0] < .05  # Do not lose a hit at the beginning of the file.


def test_external_worker_uses_argument_array_and_reports_failure(tmp_path, monkeypatch):
    source, model = tmp_path / "source with spaces", tmp_path / "model with spaces"
    source.mkdir(); model.mkdir()
    (source / "adt_transcriber.py").write_text("# trusted test fixture")
    (model / "adt_config.yaml").write_text("# test fixture")
    monkeypatch.setenv("AKBO_DRUM_WORKER", sys.executable)
    monkeypatch.setenv("AKBO_DRUM_SOURCE_DIR", str(source))
    monkeypatch.setenv("AKBO_DRUM_MODEL_DIR", str(model))
    def worker(command, **options):
        assert command[0] == sys.executable and options["timeout"] == 600
        assert command[command.index("--source") + 1] == str(source)
        assert not options.get("shell")
        output = Path(command[command.index("--output") + 1])
        midi = mido.MidiFile()
        track = mido.MidiTrack([mido.Message("note_on", channel=9, note=36, velocity=90), mido.Message("note_off", channel=9, note=36, time=96)])
        midi.tracks.append(track); midi.save(output)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", worker)
    assert transcribe_external(tmp_path / "clip.wav", 1)[0][:3] == (0, .1, 36)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="결과"):
        transcribe_external(tmp_path / "clip.wav", 1)


def test_unsupported_drum_model_notes_fail_explicitly(tmp_path):
    midi = mido.MidiFile()
    midi.tracks.append(mido.MidiTrack([mido.Message("note_on", channel=0, note=60, velocity=90)]))
    path = tmp_path / "not-drums.mid"
    midi.save(path)
    with pytest.raises(ValueError, match="타악기 채널"):
        read_drum_midi(path, 1)


@pytest.mark.parametrize("event", [(0, 1, 60.5), (0, 1, float("nan")), (1, 0, 60), (-1, 1, 60)])
def test_invalid_reference_does_not_report_misleading_accuracy(event):
    with pytest.raises(ValueError):
        compare_events([event], [])


def test_monophonic_api_does_not_require_basic_pitch_and_records_engine(client, monkeypatch):
    from backend import app as api, store, pipeline
    job = finish(client, client.post("/api/demo").json())
    store.update(job["id"], demo=False)  # A local test fixture, not a production job.
    monkeypatch.setattr(api, "engine_status", lambda: {"transcription_available": False})
    calls = []
    def transcribe(path, inst, profile="instrument", *, details=None):
        calls.append((inst, profile))
        return [(0, .5, 40, .8)]
    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    endpoint = f'/api/jobs/{job["id"]}/transcribe'
    response = client.post(endpoint, json={"instruments": ["bass"], "bpm": 120})
    assert response.status_code == 202
    finish(client, response.json())
    doc = client.get(f'/api/jobs/{job["id"]}/scores/bass').json()
    assert doc["transcription"]["engine"] == "pyin-monophonic-v1"
    assert calls == [("bass", "instrument")]
    assert client.post(endpoint, json={"instruments": ["bass"], "profile": "polyphonic"}).status_code == 503
    assert client.post(endpoint, json={"instruments": ["bass"], "profile": "invalid"}).status_code == 422
    monkeypatch.setattr(api, "engine_status", lambda: {"transcription_available": True})
    response = client.post(endpoint, json={"instruments": ["bass"], "profile": "polyphonic"})
    assert response.status_code == 202
    finish(client, response.json())
    assert calls[-1] == ("bass", "polyphonic")
    doc = client.get(f'/api/jobs/{job["id"]}/scores/bass').json()
    assert doc["transcription"]["profile"] == "polyphonic"


def test_empty_lower_keyboard_staff_survives_import_and_third_staff_is_rejected(tmp_path):
    doc = create_document([], "piano", "Empty grand", 120, 2)
    persist(doc, tmp_path)
    xml = ET.parse(tmp_path / "piano.musicxml")
    restored, _, _ = import_document(ET.tostring(xml.getroot()), "empty.musicxml", "P1", "piano")
    assert restored["keyboard"]["mode"] == "grand"
    xml.find(".//staves").text = "3"
    with pytest.raises(ValueError, match="두 보표"):
        import_document(ET.tostring(xml.getroot()), "three.musicxml", "P1", "piano")


@pytest.mark.parametrize("step,length", [(.25, 2), (.125, 1)])
def test_short_drum_decay_becomes_beamed_eighths_or_sixteenths_only_at_generation(tmp_path, step, length):
    events = [(t, t + .02, 42, .7) for t in np.arange(0, 2, step)] + [(0, .03, 36, .8), (1, 1.03, 36, .8)]
    doc = generate_score(events, "drums", "Groove", 120, 2, tmp_path)
    assert {n["length"] for n in doc["notes"] if n["pitch"] == 42} == {length}
    assert {n["length"] for n in doc["notes"] if n["pitch"] == 36} == {4}
    xml = ET.parse(tmp_path / "drums.musicxml")
    upper = xml.findall(".//note[voice='1']")
    assert all(n.find("rest") is None for n in upper)
    assert {n.findtext("type") for n in upper} == {"eighth" if length == 2 else "16th"}
    assert upper[0].findtext("beam") == "begin"
    # Persisting an intentionally shorter manual note must not run cleanup again.
    note = next(n for n in doc["notes"] if n["pitch"] == 36)
    note["length"] = 1
    persist(doc, tmp_path)
    assert load_document(tmp_path, "drums", 2)["notes"] == doc["notes"]


@pytest.mark.parametrize("inst", ["piano", "synthesizer", "bass", "drums", "guitar", "vocal"])
def test_first_beat_lyric_on_silent_staff_is_not_centered_on_whole_rest(tmp_path, inst):
    doc = create_document([], inst, "Entry cue", 120, 2)
    doc["lyrics"] = [{"id": "cue", "start": 0, "text": "함께"}]
    persist(doc, tmp_path)
    note = ET.parse(tmp_path / f"{inst}.musicxml").find(".//note[lyric]")
    assert note.find("rest") is not None and note.findtext("duration") == "4"
    assert note.findtext("type") == "quarter"
    restored, _, _ = import_document((tmp_path / f"{inst}.musicxml").read_bytes(), "entry.musicxml", "P1", inst)
    assert restored["lyrics"][0]["start"] == 0 and restored["notes"] == []
