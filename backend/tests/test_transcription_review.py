"""Review API tests use saved mock evidence, never an audio model."""
from copy import deepcopy
import hashlib
import json
import threading

import pytest

from backend import app as api, pipeline, store, transcription_review as review
from backend.config import INSTRUMENTS
from backend.editing import create_document
from backend.note_artifacts import write_note_artifact
from backend.tests.test_audio import client


def ready_review(inst="bass", *, duration=8, events=None, source="stem", offset=0, bpm=120):
    job = store.create("Review saved evidence", "upload")
    folder = store.directory(job["id"])
    for name in ("original.wav", f"{inst}.wav"):
        (folder / name).write_bytes(f"mock input {name}".encode())
    events = [(.113, .437, 42 if inst == "drums" else 60, .8)] if events is None else events
    info = {"engine": "test-engine", "profile": "instrument", "source": source, "raw_events": True}
    input_path = folder / ("original.wav" if source == "original" else f"{inst}.wav")
    write_note_artifact(folder, events, instrument=inst, duration=duration, source=input_path, description=info)
    document = create_document(events, inst, job["title"], bpm, duration, offset)
    document["transcription"] = info
    for suffix in ("score", "auto"):
        (folder / f"{inst}.{suffix}.json").write_text(json.dumps(document))
    store.update(job["id"], status="completed", duration=duration,
                 original_url=store.asset_url(job["id"], "original.wav"))
    pipeline.stem_update(job["id"], inst, status="ready", score_status="ready", score_edited=False,
                         score_url=store.asset_url(job["id"], f"{inst}.musicxml"),
                         audio_url=store.asset_url(job["id"], f"{inst}.wav"),
                         score_revision=document["revision"], score_transcription=info)
    return store.get(job["id"]), document


def endpoint(job, inst="bass"):
    return f'/api/jobs/{job["id"]}/transcription-review/{inst}'


def change_json(job, name, modify):
    path = store.directory(job["id"]) / name
    document = json.loads(path.read_text())
    modify(document)
    path.write_text(json.dumps(document))


@pytest.mark.parametrize("inst", INSTRUMENTS)
def test_all_six_layers_are_source_linked_bounded_and_read_only(client, monkeypatch, inst):
    job, document = ready_review(inst)
    folder = store.directory(job["id"])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: pytest.fail("must not infer"))
    monkeypatch.setattr(api, "load_document", lambda *args, **kwargs: pytest.fail("must not migrate/write"))
    response = client.get(endpoint(job, inst))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema"] == "akbo.transcription-review" and result["schema_version"] == 1
    assert result["window"] == {"start": 0, "end": 8}
    assert result["instrument"] == inst and result["duration"] == 8
    assert result["source"] == {"kind": "stem", "file": f"{inst}.wav", "verified": True,
                                 "sha256": hashlib.sha256((folder / f"{inst}.wav").read_bytes()).hexdigest()}
    assert result["audio"] == {"original_url": store.asset_url(job["id"], "original.wav"),
                               "stem_url": store.asset_url(job["id"], f"{inst}.wav"),
                               "input_url": store.asset_url(job["id"], f"{inst}.wav")}
    layers = result["layers"]
    assert layers["recognized"]["events"] == [{"id": "raw-0", "start": .113, "end": .437,
                                                "pitch": 42 if inst == "drums" else 60, "amplitude": .8}]
    assert layers["automatic"] == layers["current"]
    assert layers["current"]["events"][0]["id"] == document["notes"][0]["id"]
    assert layers["current"]["events"][0]["amplitude"] == document["notes"][0]["velocity"] / 127
    assert all(layer["total"] == layer["in_window"] == 1 for layer in layers.values())
    assert result["score"]["revision"] == document["revision"]
    assert "정답" in " ".join(result["warnings"]) and "정확도 점수" in " ".join(result["warnings"])
    assert "입력 파일 하나만" in " ".join(result["warnings"])
    assert not any(key in response.text for key in ('"f1":', '"accuracy":', str(folder)))
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}


def test_offset_timing_bpm_and_saved_manual_edit_not_display_bpm(client):
    job, original = ready_review(events=[(1.25, 2.25, 40, .8)], offset=.25, bpm=120)
    edited = deepcopy(original)
    edited.update(revision="edited-revision", edited=True, bpm=60)
    edited["notes"][0].update(start=12, length=8, pitch=41, velocity=127)
    folder = store.directory(job["id"])
    (folder / "bass.score.json").write_text(json.dumps(edited))
    pipeline.stem_update(job["id"], "bass", score_revision=edited["revision"], score_edited=True)
    result = client.get(endpoint(job)).json()
    assert result["score"] == {"revision": "edited-revision", "edited": True, "bpm": 60,
                               "timing_bpm": 120, "audio_offset": .25}
    assert result["layers"]["recognized"]["events"][0]["start"] == 1.25
    assert result["layers"]["automatic"]["events"][0]["start"] == 1.25
    assert result["layers"]["current"]["events"] == [{"id": original["notes"][0]["id"], "start": 1.75,
                                                     "end": 2.75, "pitch": 41, "amplitude": 1}]


def test_each_score_layer_uses_its_own_offset_and_timing_clock(client):
    job, original = ready_review(events=[(1, 2, 40, .8)])
    edited = deepcopy(original)
    edited.update(revision="changed-clock", edited=True, bpm=200, timing_bpm=60, audio_offset=.5)
    path = store.directory(job["id"]) / "bass.score.json"
    path.write_text(json.dumps(edited))
    pipeline.stem_update(job["id"], "bass", score_revision="changed-clock")
    result = client.get(endpoint(job)).json()
    assert result["layers"]["automatic"]["events"][0]["start"] == 1
    assert result["layers"]["current"]["events"][0]["start"] == 2.5


def test_half_open_window_keeps_overlapping_full_events_and_stable_ids(client):
    job, _ = ready_review(duration=2, events=[(0, .5, 40, .8), (.5, .75, 41, .8),
                                            (.4, .9, 42, .8), (1, 1.5, 43, .8)])
    result = client.get(endpoint(job), params={"start": .5, "seconds": .5}).json()
    assert result["window"] == {"start": .5, "end": 1}
    layer = result["layers"]["recognized"]
    assert layer["total"] == 4 and layer["in_window"] == 2
    assert [event["id"] for event in layer["events"]] == ["raw-1", "raw-2"]
    assert layer["events"][1]["start"] == .4  # Never invent a clipped onset.
    assert client.get(endpoint(job)).json()["window"]["end"] == 2  # Short song/default 8 sec.
    assert client.get(endpoint(job), params={"start": 1.9}).json()["window"]["end"] == 2


@pytest.mark.parametrize("params", [{"start": -1}, {"start": 8}, {"start": "nan"}, {"start": "inf"},
                                    {"seconds": "nan"}, {"seconds": "inf"}, {"seconds": 0},
                                    {"seconds": .249}, {"seconds": 30.001}])
def test_invalid_window_is_422(client, params):
    job, _ = ready_review()
    assert client.get(endpoint(job), params=params).status_code == 422


def test_original_source_and_unmapped_gm_percussion_are_preserved(client):
    job, _ = ready_review("drums", source="original", events=[(.1, .2, 33, .8), (.3, .4, 54, .6), (.5, .6, 70, .5)])
    (store.directory(job["id"]) / "drums.wav").unlink()
    response = client.get(endpoint(job, "drums"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"]["kind"] == "original" and result["source"]["file"] == "original.wav"
    assert result["audio"]["stem_url"] is None
    assert result["audio"]["input_url"] == result["audio"]["original_url"]
    assert [event["pitch"] for event in result["layers"]["recognized"]["events"]] == [33, 54, 70]


def test_only_selected_input_hash_is_verified_not_other_audio_lineage(client):
    job, _ = ready_review()
    (store.directory(job["id"]) / "original.wav").write_bytes(b"different original lineage is NOT verified")
    assert client.get(endpoint(job)).status_code == 200


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_strict_availability_flag(client, flag):
    job, _ = ready_review()
    pipeline.stem_update(job["id"], "bass", score_transcription={"raw_events": flag})
    assert client.get(endpoint(job)).status_code == 404


@pytest.mark.parametrize("metadata", [{"demo": True}, {"source_type": "musicxml"}])
def test_sample_and_import_projects_are_not_model_evidence(client, metadata):
    job, _ = ready_review()
    store.update(job["id"], **metadata)
    assert client.get(endpoint(job)).status_code == 404


@pytest.mark.parametrize("name,status", [("bass.notes.json", 404), ("bass.auto.json", 409),
                                         ("bass.score.json", 404), ("bass.wav", 409)])
def test_missing_required_inputs_never_fabricate_baseline_or_source(client, name, status):
    job, _ = ready_review()
    folder = store.directory(job["id"])
    (folder / name).unlink()
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    response = client.get(endpoint(job))
    assert response.status_code == status
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}


@pytest.mark.parametrize("field,value", [("file", "../original.wav"), ("file", "/tmp/private.wav"),
                                        ("file", "guitar.wav"), ("file", "nested/bass.wav"),
                                        ("kind", "unknown"), ("kind", "original"),
                                        ("instrument", "guitar"), ("sha256", None), ("sha256", "x" * 64)])
def test_invalid_source_metadata_is_safe_409(client, field, value):
    job, _ = ready_review()
    change_json(job, "bass.notes.json", lambda doc: doc["source"].update({field: value}))
    response = client.get(endpoint(job))
    assert response.status_code == 409 and "/tmp/private" not in response.text


@pytest.mark.parametrize("source_change", [False, True])
def test_hash_mismatch_rejects_stale_evidence(client, source_change):
    job, _ = ready_review()
    if source_change:
        (store.directory(job["id"]) / "bass.wav").write_bytes(b"replaced source audio")
    else:
        change_json(job, "bass.notes.json", lambda doc: doc["source"].update(sha256="0" * 64))
    response = client.get(endpoint(job))
    assert response.status_code == 409 and "해시" in response.text


@pytest.mark.parametrize("name", ["bass.notes.json", "bass.auto.json", "bass.score.json", "bass.wav"])
def test_symlink_inputs_are_rejected(client, tmp_path, name):
    job, _ = ready_review()
    path = store.directory(job["id"]) / name
    outside = tmp_path / "private-file"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert client.get(endpoint(job)).status_code == 409


@pytest.mark.parametrize("modify", [
    lambda doc: doc.update(schema_version=True), lambda doc: doc.update(schema_version=2),
    lambda doc: doc.update(instrument="guitar"), lambda doc: doc.update(event_count=2),
    lambda doc: doc.update(duration=float("nan")), lambda doc: doc.update(events="bad"),
    lambda doc: doc["events"][0].__setitem__(0, float("inf")),
    lambda doc: doc["events"][0].__setitem__(0, True),
    lambda doc: doc["events"][0].__setitem__(1, 9),
    lambda doc: doc["events"][0].__setitem__(2, 40.5),
    lambda doc: doc["events"][0].__setitem__(3, 1.1),
    lambda doc: doc["semantics"].update(ground_truth=True),
    lambda doc: doc["semantics"].update(stage="after-quantization"),
    lambda doc: doc["semantics"].update(reflects_manual_score_edits=True),
    lambda doc: doc["provenance"].update(engine="different-engine"),
])
def test_malformed_notes_fail_without_partial_layers(client, modify):
    job, _ = ready_review()
    change_json(job, "bass.notes.json", modify)
    response = client.get(endpoint(job))
    assert response.status_code == 409 and "layers" not in response.text


@pytest.mark.parametrize("suffix", ["score", "auto"])
@pytest.mark.parametrize("modify", [
    lambda doc: doc.update(timing_bpm=float("nan")), lambda doc: doc.update(audio_offset=float("inf")),
    lambda doc: doc.update(ticks=10**100), lambda doc: doc.update(notes=None),
    lambda doc: doc["notes"][0].update(start=True), lambda doc: doc["notes"][0].update(length=0),
    lambda doc: doc["notes"][0].update(pitch=128), lambda doc: doc["notes"][0].update(velocity=0),
    lambda doc: doc["notes"].append(deepcopy(doc["notes"][0])),
    lambda doc: doc["notes"][0].update(id="/private/id"),
])
def test_malformed_score_notes_fail(client, suffix, modify):
    job, _ = ready_review()
    change_json(job, f"bass.{suffix}.json", modify)
    assert client.get(endpoint(job)).status_code == 409


def test_duplicate_json_keys_nonfinite_overflow_and_invalid_json_are_rejected(client):
    job, _ = ready_review()
    path = store.directory(job["id"]) / "bass.notes.json"
    original = path.read_text()
    for payload in (original.replace("{", "{\"duplicate\":1,\"duplicate\":2,", 1),
                    original.replace("{", "{\"unused\":1e999,", 1), "[", "[]"):
        path.write_text(payload)
        assert client.get(endpoint(job)).status_code == 409


@pytest.mark.parametrize("layer", ["recognized", "automatic", "current"])
def test_window_limit_fails_not_truncates_and_narrower_window_works(client, monkeypatch, layer):
    job, _ = ready_review(events=[(.1, .2, 40, .8), (1, 1.2, 41, .8)])
    monkeypatch.setattr(review, "MAX_WINDOW_EVENTS", 1)
    # Leave just the target layer with two notes to prove each layer is bounded.
    for suffix in ("notes", "auto", "score"):
        if suffix == {"recognized": "notes", "automatic": "auto", "current": "score"}[layer]:
            continue
        if suffix == "notes":
            change_json(job, "bass.notes.json", lambda doc: doc.update(events=doc["events"][:1], event_count=1))
        else:
            change_json(job, f"bass.{suffix}.json", lambda doc: doc.update(notes=doc["notes"][:1]))
    change_json(job, "bass.score.json", lambda doc: doc.update(edited=True))
    response = client.get(endpoint(job))
    assert response.status_code == 422 and "구간" in response.text
    result = client.get(endpoint(job), params={"start": 0, "seconds": .25})
    assert result.status_code == 200, result.text
    assert all(item["in_window"] == 1 for item in result.json()["layers"].values())


@pytest.mark.parametrize("limit", ["MAX_EVENTS", "MAX_ARTIFACT_BYTES", "MAX_SCORE_BYTES", "MAX_SOURCE_BYTES"])
def test_total_count_and_file_size_bounds(client, monkeypatch, limit):
    job, _ = ready_review()
    monkeypatch.setattr(review, limit, 0 if limit == "MAX_EVENTS" else 1)
    assert client.get(endpoint(job)).status_code == 409


@pytest.mark.parametrize("status", ["queued", "running", "transcribing", "analyzing"])
def test_processing_status_gate_prevents_partial_generation(client, status):
    job, _ = ready_review()
    store.update(job["id"], status=status)
    assert client.get(endpoint(job)).status_code == 409


def test_active_task_gate_even_before_worker_updates_status(client):
    job, _ = ready_review()
    api.EVENTS[job["id"]] = threading.Event()
    try:
        assert client.get(endpoint(job)).status_code == 409
    finally:
        api.EVENTS.pop(job["id"])


@pytest.mark.parametrize("status", [None, "pending", "running", "error"])
def test_incomplete_or_failed_score_publication_is_not_mixed_with_old_evidence(client, status):
    job, _ = ready_review()
    pipeline.stem_update(job["id"], "bass", score_status=status)
    assert client.get(endpoint(job)).status_code == 409


@pytest.mark.parametrize("mutation", ["job", "score", "auto", "notes", "audio", "task"])
def test_hash_outside_global_locks_and_races_rejected(client, monkeypatch, mutation):
    job, _ = ready_review()
    original_hash = review._audio_sha256
    def racing_hash(path, fingerprint):
        assert not api.TASK_LOCK._is_owned() and not store.LOCK._is_owned()
        digest = original_hash(path, fingerprint)
        if mutation == "job":
            pipeline.stem_update(job["id"], "bass", score_revision="concurrent-edit")
        elif mutation == "task":
            api.EVENTS[job["id"]] = threading.Event()
        elif mutation == "audio":
            path.write_bytes(b"changed audio after hash")
        else:
            change_json(job, f"bass.{mutation}.json", lambda doc: doc.update(unrelated="concurrent-write"))
        return digest
    monkeypatch.setattr(review, "_audio_sha256", racing_hash)
    try:
        assert client.get(endpoint(job)).status_code == 409
    finally:
        api.EVENTS.pop(job["id"], None)


def test_revision_or_engine_mismatch_is_rejected_before_hash(client, monkeypatch):
    job, _ = ready_review()
    pipeline.stem_update(job["id"], "bass", score_revision="wrong-generation")
    monkeypatch.setattr(review, "_audio_sha256", lambda *args: pytest.fail("stale revision should fail first"))
    assert client.get(endpoint(job)).status_code == 409


def test_missing_clock_falls_back_read_only_and_padded_tail_not_clipped(client):
    job, _ = ready_review(duration=.4, events=[(.1, .4, 40, .8)])
    folder = store.directory(job["id"])
    for suffix in ("auto", "score"):
        def padded_legacy(doc):
            doc.pop("timing_bpm")
            doc["notes"][0]["length"] = 4
        change_json(job, f"bass.{suffix}.json", padded_legacy)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    result = client.get(endpoint(job)).json()
    assert result["score"]["timing_bpm"] == 120
    assert result["layers"]["current"]["events"][0]["end"] > .4
    assert "끝마디" in " ".join(result["warnings"])
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_empty_valid_evidence_is_not_model_failure(client):
    job, _ = ready_review(events=[])
    result = client.get(endpoint(job))
    assert result.status_code == 200
    assert all(layer == {"events": [], "total": 0, "in_window": 0} for layer in result.json()["layers"].values())


def test_unknown_jobs_and_instruments_are_404(client):
    job, _ = ready_review()
    assert client.get(endpoint(job, "unknown")).status_code == 404
    assert client.get("/api/jobs/not-an-id/transcription-review/bass").status_code == 404
    assert client.get(f'/api/jobs/{"0" * 32}/transcription-review/bass').status_code == 404


def test_actual_mocked_pipeline_publishes_compatible_review_for_all_six(client, monkeypatch):
    from backend.tests.test_audio import finish
    from backend.tests.test_note_artifacts import fake_inference, ready_project
    fake_inference(monkeypatch)
    job = ready_project()
    result = client.post(f'/api/jobs/{job["id"]}/transcribe', json={"drum_engine": "spectral"})
    assert result.status_code == 202
    finish(client, result.json())
    for inst in INSTRUMENTS:
        response = client.get(endpoint(job, inst))
        assert response.status_code == 200, response.text


def test_real_window_limit_accepts_1500_but_rejects_1501(client):
    job, _ = ready_review()
    path = store.directory(job["id"]) / "bass.notes.json"
    doc = json.loads(path.read_text())
    doc.update(events=doc["events"] * 1500, event_count=1500)
    path.write_text(json.dumps(doc))
    accepted = client.get(endpoint(job))
    assert accepted.status_code == 200 and accepted.json()["layers"]["recognized"]["in_window"] == 1500
    doc["events"].append(doc["events"][0])
    doc["event_count"] += 1
    path.write_text(json.dumps(doc))
    assert client.get(endpoint(job)).status_code == 422
