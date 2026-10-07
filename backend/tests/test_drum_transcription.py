import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import mido
import pytest

from backend.adt_tokens import decode_tokens
from backend.drum_mapping import ADT_TO_GM, adt_to_gm, normalize_drums
from backend.drum_worker import read_drum_midi, transcribe_external, worker_status
from backend.editing import create_document, generate_score, persist
from backend.evaluation import compare_events
from backend.score_import import import_document
from backend.tests.test_audio import client, finish


@pytest.mark.parametrize("custom,gm", [(43, 44), (44, 46), (45, 47), (46, 49), (47, 50), (48, 51), (49, 52), (50, 54), (51, 55), (52, 56)])
def test_adt_custom_vocabulary_is_not_mistaken_for_gm(custom, gm):
    assert adt_to_gm(custom) == gm
    assert set(ADT_TO_GM) == set(range(35, 61))
    with pytest.raises(ValueError, match="Unexpected"):
        adt_to_gm(61)


def test_token_parser_does_not_shift_later_pitches_to_malformed_earlier_onsets():
    notes, review = decode_tokens([2, 4, 335, 500, 20, 25, 338, 480, 3, 30, 342, 490])
    assert notes == [(0., .1, 35, 100), (.21, .31, 38, 80)]
    assert review["invalid_tokens"] == 1
    assert review["tokens"][4:7] == [20, 25, 338]
    assert len(notes) == 2  # EOS is terminal.


def test_tokens_keep_simultaneous_drums_and_flag_invalid_truncated_output():
    notes, review = decode_tokens([2, 4, 336, 500, 4, 342, 470, 3])
    assert len(notes) == 2 and notes[0][0] == notes[1][0]
    assert not review["truncated"]
    assert decode_tokens([2, 0, 3])[0] == []
    notes, review = decode_tokens([2, 4, 399, 500, 25, 338], max_length=6)
    assert notes == [] and review["truncated"] and review["incomplete_events"] == 1
    assert decode_tokens([2, 260, 338, 500, 3])[0] == []  # Outside the 2.56s chunk.
    assert decode_tokens([2, 4, 338, 400, 3])[0] == []  # No invented zero-velocity note.
    assert decode_tokens([2, 4, 338, 3], add_velocity=False)[0] == [(0, .1, 38, 100)]


def test_kit_mapping_preserves_feet_side_stick_and_reports_every_omission():
    events = [(0, .1, 35, .5), (0, .1, 36, .8), (.2, .3, 37, .7), (.2, .3, 44, .7),
              (.4, .5, 43, .6), (.4, .5, 48, .6), (.6, .7, 52, .6), (.8, .9, 56, .9)]
    kit, review = normalize_drums(events)
    assert [e[2] for e in kit] == [36, 37, 44, 45, 47, 49]
    assert kit[0][3] == .8
    assert review["merged_count"] == 1 and review["unsupported_count"] == 1
    assert review["unsupported_events"] == [events[-1]]
    assert review["simplified_counts"] == {"35->36": 1, "43->45": 1, "48->47": 1, "52->49": 1}


def midi_file(path, pitches):
    midi = mido.MidiFile()
    midi.tracks.append(mido.MidiTrack([*[mido.Message("note_on", channel=9, note=p, velocity=90) for p in pitches],
                                     *[mido.Message("note_off", channel=9, note=p, time=96 if i == 0 else 0) for i, p in enumerate(pitches)]]))
    midi.save(path)


def test_raw_midi_keeps_non_kit_percussion_but_default_reader_requires_review(tmp_path):
    path = tmp_path / "raw.mid"
    midi_file(path, [36, 44, 56])
    assert [e[2] for e in read_drum_midi(path, 1, raw=True)] == [36, 44, 56]
    with pytest.raises(ValueError, match="지원하지 않는"):
        read_drum_midi(path, 1)


def test_worker_artifacts_preserve_unsupported_events_and_safe_metadata(tmp_path, monkeypatch):
    import sys
    import backend.drum_worker as worker
    source, model, output = tmp_path / "source", tmp_path / "model", tmp_path / "output"
    source.mkdir(); model.mkdir()
    (source / "adt_transcriber.py").write_text("# test")
    for filename in ("adt_config.yaml", "config.json", "model.safetensors"):
        (model / filename).write_text("test")
    for key, value in {"AKBO_DRUM_WORKER": sys.executable, "AKBO_DRUM_SOURCE_DIR": str(source), "AKBO_DRUM_MODEL_DIR": str(model)}.items():
        monkeypatch.setenv(key, value)
    assert worker_status()["paths_ready"]
    assert str(tmp_path) not in json.dumps(worker_status())
    def run(command, **kwargs):
        path = Path(command[command.index("--output") + 1])
        midi_file(path, [36, 56])
        path.with_suffix(".json").write_text(json.dumps({"mapping": "adt-custom-26-to-gm-v1", "device": "cpu"}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker.subprocess, "run", run)
    details = {}
    assert [e[2] for e in transcribe_external(tmp_path / "audio.wav", 1, artifacts=output, details=details)] == [36]
    assert details["review"]["unsupported_count"] == 1
    assert [e[2] for e in read_drum_midi(output / "drums.raw.mid", 1, raw=True)] == [36, 56]
    assert json.loads((output / "drums.transcription.json").read_text())["review"]["unsupported_events"][0][2] == 56


def test_unconfigured_neural_request_does_not_silently_fall_back(tmp_path, monkeypatch):
    import backend.transcription as transcription
    monkeypatch.delenv("AKBO_DRUM_WORKER", raising=False)
    monkeypatch.setattr(transcription, "multiband_drums", lambda p: [(0, .1, 36, 1)])
    assert not worker_status()["configured"]
    assert transcription.transcribe_drums(tmp_path / "audio.wav", 1)[0][0][2] == 36
    with pytest.raises(ValueError, match="실행 환경"):
        transcription.transcribe_drums(tmp_path / "audio.wav", 1, engine="neural")
    monkeypatch.setenv("AKBO_DRUM_WORKER", str(tmp_path / "missing-python"))
    with pytest.raises(ValueError, match="실행 환경"):
        transcription.transcribe_drums(tmp_path / "audio.wav", 1, engine="auto")
    assert transcription.transcribe_drums(tmp_path / "audio.wav", 1, engine="spectral")[1]["engine"] == "multiband-onsets-v2"


def test_pedal_hat_and_side_stick_round_trip_as_distinct_voices(tmp_path):
    doc = create_document([(0, .25, 37, .8), (0, .25, 44, .8), (0, .25, 36, .8)], "drums", "Foot and side", 120, 2)
    persist(doc, tmp_path)
    root = ET.parse(tmp_path / "drums.musicxml")
    for pitch, stem, step in [(37, "up", "C"), (44, "down", "D"), (36, "down", "F")]:
        note = next(n for n in root.findall(".//note") if n.find("instrument") is not None and n.find("instrument").get("id") == f"I{pitch}")
        assert note.findtext("stem") == stem and note.findtext("unpitched/display-step") == step
    restored, _, _ = import_document((tmp_path / "drums.musicxml").read_bytes(), "test.musicxml", "P1", "drums")
    assert {n["pitch"] for n in restored["notes"]} == {36, 37, 44}


@pytest.mark.parametrize("beats,beat_type,start", [(4, 4, .375), (6, 8, .625)])
def test_offbeat_drum_hit_does_not_invent_a_tie_into_next_beat(tmp_path, beats, beat_type, start):
    doc = generate_score([(start, start + .1, 36, .8)], "drums", "Offbeat hit", 120, 2, tmp_path,
                         meters=[{"measure": 1, "beats": beats, "beat_type": beat_type}])
    assert doc["notes"][0]["length"] == 1
    assert not ET.parse(tmp_path / "drums.musicxml").findall(".//tie")


def test_error_report_is_one_to_one_and_locates_missing_and_extra_hits():
    metrics = compare_events([(0, .1, 36, 1), (.03, .1, 36, 1), (.7, .8, 42, 1)], [(0, .1, 36, 1), (.9, 1, 38, 1)], include_errors=True)
    assert len(metrics["missing_events"]) == metrics["missing_notes"] == 2
    assert len(metrics["extra_events"]) == metrics["extra_notes"] == 1
    assert metrics["missing_events"][0]["start"] == .03
    assert metrics["extra_events"][0]["pitch"] == 38


def test_drum_api_records_engine_source_raw_artifacts_and_never_serves_stale_raw(client, monkeypatch):
    from backend import app as api, store, pipeline
    job = finish(client, client.post("/api/demo").json())
    job = store.update(job["id"], demo=False)
    monkeypatch.setattr(api, "worker_status", lambda: {"configured": True, "paths_ready": True})
    calls = []
    def run(path, duration, *, engine, artifacts):
        calls.append((path.name, engine))
        if engine == "neural":
            midi_file(artifacts / "drums.raw.mid", [36, 56])
            (artifacts / "drums.transcription.json").write_text('{"test": true}')
        return [(0, .1, 36, .8)], {"engine": "adt-str" if engine == "neural" else "multiband-onsets-v2", "profile": "instrument", "warning": "test"}
    monkeypatch.setattr(pipeline, "transcribe_drums", run)
    endpoint = f'/api/jobs/{job["id"]}'
    response = client.post(endpoint + "/transcribe", json={"instruments": ["drums"], "drum_engine": "neural", "drum_source": "original"})
    assert response.status_code == 202
    finish(client, response.json())
    doc = client.get(endpoint + "/scores/drums").json()
    assert calls == [("original.wav", "neural")]
    assert doc["transcription"]["source"] == "original" and doc["transcription"]["raw_midi"]
    assert client.get(endpoint + "/files/drums.raw.mid").status_code == 200
    assert client.get(endpoint + "/files/drums.transcription.json").headers["content-type"] == "application/json"
    bundle = zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content))
    assert "drums.raw.mid" in bundle.namelist()
    response = client.post(endpoint + "/transcribe", json={"instruments": ["drums"], "drum_engine": "spectral"})
    finish(client, response.json())
    assert calls[-1] == ("drums.wav", "spectral")
    assert client.get(endpoint + "/files/drums.raw.mid").status_code == 404
    assert "drums.raw.mid" not in zipfile.ZipFile(io.BytesIO(client.get(endpoint + "/archive").content)).namelist()


@pytest.mark.parametrize("engine", ["neural", "hybrid", "consensus"])
def test_neural_api_preflight_fails_before_starting_job(client, monkeypatch, engine):
    from backend import store
    job = finish(client, client.post("/api/demo").json())
    store.update(job["id"], demo=False)
    monkeypatch.delenv("AKBO_DRUM_WORKER", raising=False)
    endpoint = f'/api/jobs/{job["id"]}/transcribe'
    assert client.post(endpoint, json={"instruments": ["drums"], "drum_engine": engine}).status_code == 503
    assert store.get(job["id"])["status"] == "completed"
    assert client.post(endpoint, json={"instruments": ["drums"], "drum_source": "../../file"}).status_code == 422


def test_original_only_drum_route_does_not_pretend_separation_ran(client, monkeypatch):
    from backend import store, pipeline
    job = finish(client, client.post("/api/demo").json())
    stems = [{**s, "status": "pending", "score_status": "pending"} for s in job["stems"]]
    store.update(job["id"], demo=False, analysis_only=True, stems=stems)
    monkeypatch.setattr(pipeline, "transcribe_drums", lambda *a, **kw: ([], {"engine": "multiband-onsets-v2", "profile": "instrument", "warning": "test"}))
    endpoint = f'/api/jobs/{job["id"]}/transcribe'
    assert client.post(endpoint, json={"instruments": ["drums"], "drum_engine": "spectral"}).status_code == 409
    assert client.post(endpoint, json={"instruments": ["bass"], "drum_source": "original"}).status_code == 409
    result = client.post(endpoint, json={"instruments": ["drums"], "drum_engine": "spectral", "drum_source": "original"})
    assert result.status_code == 202
    job = finish(client, result.json())
    assert all(s["status"] == "pending" for s in job["stems"])
    assert next(s for s in job["stems"] if s["id"] == "drums")["score_status"] == "ready"


def test_saved_and_legacy_first_beat_offset_is_returned_without_rewriting(client):
    from backend import store
    job = finish(client, client.post("/api/demo").json())
    endpoint = f'/api/jobs/{job["id"]}'
    response = client.post(endpoint + "/transcribe", json={"instruments": ["drums"], "bpm": 120, "audio_offset": .5})
    assert response.status_code == 202
    finish(client, response.json())
    current = client.get(endpoint).json()
    assert next(s for s in current["stems"] if s["id"] == "drums")["score_audio_offset"] == .5
    folder = store.directory(job["id"])
    job = store.get(job["id"])
    for stem in job["stems"]:
        stem.pop("score_audio_offset", None)
    store.save(job)
    before = {p.name: p.read_bytes() for p in [folder / "job.json", folder / "drums.score.json"]}
    current = client.get(endpoint).json()
    assert next(s for s in current["stems"] if s["id"] == "drums")["score_audio_offset"] == .5
    assert before == {p.name: p.read_bytes() for p in [folder / "job.json", folder / "drums.score.json"]}


def test_mdb_annotation_adapter_rejects_unknown_labels_and_preserves_simultaneous_hits():
    spec = importlib.util.spec_from_file_location("prepare_benchmark", Path(__file__).resolve().parents[2] / "scripts/prepare-drum-benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert [e[2] for e in module.parse_annotations("0.0 KD\n0.0 CHH\n0.5 SST\n2.0 SD", 1)] == [35, 42, 37]
    with pytest.raises(ValueError, match="Unsupported"):
        module.parse_annotations("0.0 unknown", 1)
