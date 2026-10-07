from copy import deepcopy
import json
import threading

import pytest

from backend import app as api, pipeline, review_cases, store, transcription_review as inspection
from backend.config import INSTRUMENTS
from backend.tests.test_audio import client
from backend.tests.test_transcription_review import ready_review, change_json


def url(job, inst="bass", case_id=None):
    root = f'/api/jobs/{job["id"]}/review-cases/{inst}'
    return root if case_id is None else f"{root}/{case_id}"


def live(client, job, inst="bass", start=0, seconds=8):
    response = client.get(f'/api/jobs/{job["id"]}/transcription-review/{inst}', params={"start": start, "seconds": seconds})
    assert response.status_code == 200, response.text
    return response.json()


def create(client, job, inst="bass", start=0, seconds=8):
    snapshot = live(client, job, inst, start, seconds)
    response = client.post(url(job, inst), json={"start": start, "seconds": seconds, "snapshot_id": snapshot["snapshot_id"]})
    assert response.status_code == 201, response.text
    return response.json()


def payload(case, status="draft"):
    return {"base_revision": case["revision"], "status": status,
            "reference": deepcopy(case["reference"]), "annotations": deepcopy(case["annotations"])}


def candidates(case):
    result = payload(case)
    result["reference"] = {"events": deepcopy(case["snapshot"]["layers"]["recognized"]["events"]),
                           "reviewer": "Ethan", "basis": "채보 입력 음원의 전체 선택 구간을 직접 듣고 확인함",
                           "coverage_complete": True, "seed_layer": "recognized"}
    return result


def case_path(job, case, inst="bass"):
    return store.directory(job["id"]) / "review-cases" / inst / f'{case["id"]}.json'


@pytest.mark.parametrize("inst", INSTRUMENTS)
def test_all_six_create_immutable_empty_drafts_and_list_without_score_writes(client, inst):
    job, _ = ready_review(inst)
    folder = store.directory(job["id"])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    assert client.get(url(job, inst)).json() == {"cases": []}
    case = create(client, job, inst)
    assert case["schema"] == "akbo.review-case" and case["schema_version"] == 1
    assert case["status"] == "draft" and case["confirmation"] is None and case["report"] is None
    assert case["reference"] == {"events": [], "reviewer": "", "basis": "", "coverage_complete": False, "seed_layer": None}
    assert case["annotations"] == [] and case["freshness"]["status"] == "current"
    assert case["snapshot_id"] == inspection.canonical_sha256(case["snapshot"])
    assert "audio" not in case["snapshot"] and "snapshot_id" not in case["snapshot"]
    assert case["snapshot"]["provenance"]["raw_artifact_id"]
    summaries = client.get(url(job, inst)).json()["cases"]
    assert len(summaries) == 1 and summaries[0]["id"] == case["id"]
    assert summaries[0]["reference_event_count"] == summaries[0]["annotation_count"] == 0
    assert "snapshot" not in summaries[0] and "report" not in summaries[0]
    reopened = client.get(url(job, inst, case["id"])).json()
    assert reopened["snapshot"] == case["snapshot"] and reopened["revision"] == case["revision"]
    assert before == {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


def test_snapshot_digest_is_deterministic_across_int_float_requests_and_raw_generation(client):
    job, _ = ready_review()
    a = live(client, job, start=0, seconds=8)
    b = live(client, job, start=0.0, seconds=8.0)
    assert a["snapshot_id"] == b["snapshot_id"]
    case = create(client, job)
    assert case["snapshot_id"] == a["snapshot_id"]
    # Same input/engine/events, but a different inference artifact, is not the
    # same generation. Raw-N event IDs alone must never bind a saved case.
    change_json(job, "bass.notes.json", lambda doc: doc.update(artifact_id="f" * 32))
    changed = live(client, job)
    assert changed["layers"] == a["layers"] and changed["source"] == a["source"]
    assert changed["snapshot_id"] != a["snapshot_id"]
    stale = client.post(url(job), json={"start": 0, "seconds": 8, "snapshot_id": a["snapshot_id"]})
    assert stale.status_code == 409
    reopened = client.get(url(job, case_id=case["id"])).json()
    assert reopened["freshness"]["status"] == "stale" and reopened["snapshot"] == case["snapshot"]


@pytest.mark.parametrize("suffix", ["notes", "auto", "score"])
def test_snapshot_binds_actual_source_json_bytes_not_only_current_revision(client, suffix):
    job, _ = ready_review()
    snapshot = live(client, job)
    path = store.directory(job["id"]) / f"bass.{suffix}.json"
    path.write_text(path.read_text() + "\n ")
    updated = live(client, job)
    assert updated["score"] == snapshot["score"] and updated["layers"] == snapshot["layers"]
    assert updated["snapshot_id"] != snapshot["snapshot_id"]


def test_explicit_draft_then_full_window_confirmation_enables_metrics_and_export(client):
    job, _ = ready_review()
    case = create(client, job)
    candidate = candidates(case)
    candidate["status"] = "reviewed"
    assert client.put(url(job, case_id=case["id"]), json=candidate).status_code == 422
    candidate["status"] = "draft"
    saved = client.put(url(job, case_id=case["id"]), json=candidate)
    assert saved.status_code == 200, saved.text
    draft = saved.json()
    assert draft["revision"] != case["revision"] and draft["report"] is None
    assert draft["snapshot"] == case["snapshot"] and draft["confirmation"] is None
    confirmed = client.put(url(job, case_id=case["id"]), json=payload(draft, "reviewed"))
    assert confirmed.status_code == 200, confirmed.text
    reviewed = confirmed.json()
    assert reviewed["status"] == "reviewed"
    assert reviewed["confirmation"]["revision"] == reviewed["revision"]
    assert reviewed["confirmation"]["reference_sha256"] == inspection.canonical_sha256(reviewed["reference"])
    assert reviewed["report"]["independent_ground_truth"] is False
    assert reviewed["report"]["metrics"]["recognized"]["f1"] == 1
    download = client.get(url(job, case_id=case["id"]) + "/export")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    export = download.json()
    assert export["case"]["snapshot"] == case["snapshot"] and export["report"] == reviewed["report"]
    assert export["audio_included"] is False and export["reference_status"] == "reviewer-confirmed"
    assert "/files/" not in download.text and str(store.directory(job["id"])) not in download.text


def test_draft_export_is_backup_without_metrics_or_ground_truth_claim(client):
    job, _ = ready_review()
    case = create(client, job)
    response = client.get(url(job, case_id=case["id"]) + "/export")
    exported = response.json()
    assert response.status_code == 200 and exported["report"] is None
    assert exported["reference_status"] == "draft-candidate" and exported["case"]["reference"]["events"] == []


def test_editing_reviewed_content_invalidates_confirmation_and_cas_blocks_lost_update(client):
    job, _ = ready_review()
    case = create(client, job)
    draft = client.put(url(job, case_id=case["id"]), json=candidates(case)).json()
    reviewed = client.put(url(job, case_id=case["id"]), json=payload(draft, "reviewed")).json()
    change = payload(reviewed)
    change["reference"]["events"][0]["pitch"] += 1
    saved = client.put(url(job, case_id=case["id"]), json=change).json()
    assert saved["status"] == "draft" and saved["confirmation"] is None and saved["report"] is None
    stale = client.put(url(job, case_id=case["id"]), json=payload(reviewed))
    assert stale.status_code == 409
    assert client.get(url(job, case_id=case["id"])).json()["reference"] == saved["reference"]


@pytest.mark.parametrize("field,value", [("reviewer", ""), ("basis", " "), ("coverage_complete", False)])
def test_confirmation_requires_reviewer_basis_and_whole_window(client, field, value):
    job, _ = ready_review()
    case = create(client, job)
    body = candidates(case)
    body["reference"][field] = value
    saved = client.put(url(job, case_id=case["id"]), json=body).json()
    assert client.put(url(job, case_id=case["id"]), json=payload(saved, "reviewed")).status_code == 422


def test_notes_annotations_and_frozen_baseline_do_not_modify_saved_score(client):
    job, _ = ready_review()
    folder = store.directory(job["id"])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    case = create(client, job)
    body = candidates(case)
    body["reference"]["events"][0].update(id="manual-note", pitch=41)
    body["annotations"] = [{"id": "listen-again", "kind": "pitch", "layer": "recognized", "event_id": "raw-0",
                            "start": .113, "end": .113, "note": "청취 후 음정 확인 필요"}]
    changed = client.put(url(job, case_id=case["id"]), json=body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["snapshot"] == case["snapshot"]
    assert before == {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


@pytest.mark.parametrize("mutation", ["source", "flag", "task", "score_error"])
def test_old_cases_remain_readable_and_editable_when_live_review_unavailable(client, mutation):
    job, _ = ready_review()
    case = create(client, job)
    if mutation == "source":
        (store.directory(job["id"]) / "bass.wav").write_bytes(b"new audio")
    elif mutation == "flag":
        pipeline.stem_update(job["id"], "bass", score_transcription={"raw_events": False})
    elif mutation == "task":
        api.EVENTS[job["id"]] = threading.Event()
    else:
        pipeline.stem_update(job["id"], "bass", score_status="error")
    try:
        got = client.get(url(job, case_id=case["id"]))
        assert got.status_code == 200 and got.json()["freshness"]["status"] == "unavailable"
        assert got.json()["snapshot"] == case["snapshot"]
        assert len(client.get(url(job)).json()["cases"]) == 1
        saved = client.put(url(job, case_id=case["id"]), json=candidates(case))
        assert saved.status_code == 200 and saved.json()["freshness"]["status"] == "unavailable"
        assert client.get(url(job, case_id=case["id"]) + "/export").status_code == 200
    finally:
        api.EVENTS.pop(job["id"], None)


def test_creation_rechecks_generation_under_lock_before_atomic_commit(client, monkeypatch):
    job, _ = ready_review()
    snapshot = live(client, job)
    original_hash = inspection._audio_sha256
    def race(path, fingerprint):
        result = original_hash(path, fingerprint)
        change_json(job, "bass.notes.json", lambda doc: doc.update(artifact_id="f" * 32))
        return result
    monkeypatch.setattr(inspection, "_audio_sha256", race)
    result = client.post(url(job), json={"start": 0, "seconds": 8, "snapshot_id": snapshot["snapshot_id"]})
    assert result.status_code == 409
    assert client.get(url(job)).json() == {"cases": []}


def test_creation_commit_owns_both_locks_and_update_keeps_bounded_previous_drafts(client, monkeypatch):
    job, _ = ready_review()
    writer = review_cases._atomic_write
    def locked_write(*args):
        assert api.TASK_LOCK._is_owned() and store.LOCK._is_owned()
        return writer(*args)
    monkeypatch.setattr(review_cases, "_atomic_write", locked_write)
    case = create(client, job)
    snapshots = []
    for index in range(5):
        snapshots.append(case["revision"])
        body = payload(case)
        body["reference"]["basis"] = f"메모 {index}"
        response = client.put(url(job, case_id=case["id"]), json=body)
        assert response.status_code == 200, response.text
        case = response.json()
    history = json.loads(case_path(job, case).with_name(case["id"] + ".history.json").read_text())
    assert [old["revision"] for old in history["revisions"]] == snapshots[-3:]
    assert all("snapshot" not in old for old in history["revisions"])


def test_identical_save_is_idempotent_and_does_not_consume_history(client):
    job, _ = ready_review()
    case = create(client, job)
    saved = client.put(url(job, case_id=case["id"]), json=payload(case)).json()
    assert saved["revision"] == case["revision"]
    assert not case_path(job, case).with_name(case["id"] + ".history.json").exists()


def test_case_count_limit_and_instrument_names_are_bounded(client, monkeypatch):
    job, _ = ready_review()
    case = create(client, job)
    monkeypatch.setattr(review_cases, "MAX_CASES", 1)
    result = client.post(url(job), json={"start": 0, "seconds": 8, "snapshot_id": case["snapshot_id"]})
    assert result.status_code == 409 and len(client.get(url(job)).json()["cases"]) == 1
    assert client.get(url(job, "unknown")).status_code == 404


@pytest.mark.parametrize("name", ["case", "history", "root"])
def test_symlink_persistence_paths_are_rejected(client, tmp_path, name):
    job, _ = ready_review()
    case = create(client, job)
    path = case_path(job, case)
    target = tmp_path / "external.json"
    target.write_bytes(path.read_bytes())
    if name == "case":
        path.unlink()
        path.symlink_to(target)
        assert client.get(url(job, case_id=case["id"])).status_code == 409
    elif name == "history":
        path.with_name(case["id"] + ".history.json").symlink_to(target)
        assert client.put(url(job, case_id=case["id"]), json=candidates(case)).status_code == 409
    else:
        parent = path.parent
        moved = parent.with_name("old-bass")
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
        assert client.get(url(job)).status_code == 409
    assert target.read_bytes() == json.dumps(json.loads(target.read_bytes()), ensure_ascii=False, allow_nan=False,
                                           sort_keys=True, separators=(",", ":")).encode()


def test_atomic_write_failure_keeps_previous_case_and_cleans_temp(client, monkeypatch):
    job, _ = ready_review()
    case = create(client, job)
    path = case_path(job, case)
    before = path.read_bytes()
    replace = review_cases.os.replace
    def fail_primary(source, destination):
        if destination == path:
            raise OSError("simulated failure")
        return replace(source, destination)
    monkeypatch.setattr(review_cases.os, "replace", fail_primary)
    assert client.put(url(job, case_id=case["id"]), json=candidates(case)).status_code == 409
    assert path.read_bytes() == before and not list(path.parent.glob(".pending-*.tmp"))


@pytest.mark.parametrize("mutate", [
    lambda body: body.update(snapshot={}), lambda body: body.update(revision="b" * 32),
    lambda body: body["reference"].update(events=[{"id": "bad", "start": -1, "end": .4, "pitch": 40, "amplitude": .8}]),
    lambda body: body["reference"].update(coverage_complete="true"),
    lambda body: body["reference"].update(seed_layer="audio"),
    lambda body: body["reference"].update(reviewer="x" * 81),
    lambda body: body["reference"].update(basis="x" * 2001),
    lambda body: body["reference"].update(path="/private/data"),
    lambda body: body.update(annotations=[{"id": "a", "kind": "extra", "layer": "recognized", "event_id": "missing",
                                          "start": 0, "end": 0, "note": "x"}]),
])
def test_invalid_edits_never_overwrite_case(client, mutate):
    job, _ = ready_review()
    case = create(client, job)
    path = case_path(job, case)
    before = path.read_bytes()
    body = payload(case)
    mutate(body)
    assert client.put(url(job, case_id=case["id"]), json=body).status_code == 422
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["nan", "overflow", "duplicate", "large"])
def test_body_rejected_before_unbounded_or_ambiguous_parse(client, kind):
    job, _ = ready_review()
    if kind == "nan":
        data = '{"start":NaN,"seconds":8,"snapshot_id":"' + "0" * 64 + '"}'
    elif kind == "overflow":
        data = '{"start":1e999,"seconds":8,"snapshot_id":"' + "0" * 64 + '"}'
    elif kind == "duplicate":
        data = '{"start":0,"start":1,"seconds":8,"snapshot_id":"' + "0" * 64 + '"}'
    else:
        data = " " * (review_cases.MAX_BYTES + 1)
    response = client.post(url(job), content=data, headers={"content-type": "application/json"})
    assert response.status_code == (413 if kind == "large" else 422)
    assert client.get(url(job)).json() == {"cases": []}


def test_streamed_body_without_content_length_is_bounded(client):
    job, _ = ready_review()
    response = client.post(url(job), content=iter([b" " * (1024 * 1024)] * 3),
                           headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_stored_corruption_and_case_ids_do_not_leak_paths_or_allow_export(client):
    job, _ = ready_review()
    case = create(client, job)
    path = case_path(job, case)
    doc = json.loads(path.read_text())
    doc["snapshot"]["layers"]["recognized"]["events"][0]["pitch"] += 1
    path.write_text(json.dumps(doc))
    for suffix in ("", "/export"):
        response = client.get(url(job, case_id=case["id"]) + suffix)
        assert response.status_code == 409 and str(path) not in response.text
    assert client.get(url(job, case_id="bad-id")).status_code == 404
    assert client.get(url(job, case_id="f" * 32)).status_code == 404


def test_annotation_and_reference_resource_limits(client):
    job, _ = ready_review()
    case = create(client, job)
    body = candidates(case)
    body["reference"]["events"] = [{**body["reference"]["events"][0], "id": f"n-{index}"} for index in range(1501)]
    assert client.put(url(job, case_id=case["id"]), json=body).status_code == 422
    body = payload(case)
    body["annotations"] = [{"id": f"a-{index}", "kind": "other", "layer": None, "event_id": None,
                            "start": 0, "end": 0, "note": "x"} for index in range(301)]
    assert client.put(url(job, case_id=case["id"]), json=body).status_code == 422


def test_reference_requires_onsets_inside_window_without_clipping_context(client):
    job, _ = ready_review(events=[(.1, 2, 40, .8), (1.1, 1.4, 41, .7)])
    case = create(client, job, start=1, seconds=1)
    assert case["snapshot"]["layers"]["recognized"]["events"][0]["start"] == .1
    body = candidates(case)
    assert client.put(url(job, case_id=case["id"]), json=body).status_code == 422
    body["reference"]["events"] = body["reference"]["events"][1:]
    saved = client.put(url(job, case_id=case["id"]), json=body).json()
    reviewed = client.put(url(job, case_id=case["id"]), json=payload(saved, "reviewed")).json()
    assert reviewed["report"]["metrics"]["recognized"]["excluded_context_notes"] == 1
    assert reviewed["report"]["metrics"]["recognized"]["f1"] == 1


@pytest.mark.parametrize("change", ["audio", "generation", "active_task"])
def test_new_confirmation_requires_same_verifiable_frozen_snapshot(client, change):
    job, _ = ready_review()
    case = create(client, job)
    draft = client.put(url(job, case_id=case["id"]), json=candidates(case)).json()
    path = case_path(job, case)
    before = path.read_bytes()
    if change == "audio":
        (store.directory(job["id"]) / "bass.wav").write_bytes(b"different source")
    elif change == "generation":
        change_json(job, "bass.notes.json", lambda doc: doc.update(artifact_id="e" * 32))
    else:
        api.EVENTS[job["id"]] = threading.Event()
    try:
        response = client.put(url(job, case_id=case["id"]), json=payload(draft, "reviewed"))
        assert response.status_code == 409 and path.read_bytes() == before
        saved = client.get(url(job, case_id=case["id"])).json()
        assert saved["status"] == "draft" and saved["confirmation"] is None and saved["report"] is None
    finally:
        api.EVENTS.pop(job["id"], None)


def test_already_confirmed_historical_export_keeps_frozen_metrics_after_retranscription(client):
    job, _ = ready_review()
    case = create(client, job)
    draft = client.put(url(job, case_id=case["id"]), json=candidates(case)).json()
    reviewed = client.put(url(job, case_id=case["id"]), json=payload(draft, "reviewed")).json()
    change_json(job, "bass.notes.json", lambda doc: doc.update(artifact_id="d" * 32))
    response = client.get(url(job, case_id=case["id"]) + "/export")
    assert response.status_code == 200
    export = response.json()
    assert export["freshness_at_export"]["status"] == "stale"
    assert export["report"] == reviewed["report"] and export["case"]["snapshot_id"] == case["snapshot_id"]


@pytest.mark.parametrize("change", ["case", "notes", "audio"])
def test_confirmation_rechecks_revision_and_evidence_after_hash_before_commit(client, monkeypatch, change):
    job, _ = ready_review()
    case = create(client, job)
    draft = client.put(url(job, case_id=case["id"]), json=candidates(case)).json()
    original_hash = inspection._audio_sha256
    def race(path, fingerprint):
        result = original_hash(path, fingerprint)
        if change == "case":
            target = case_path(job, case)
            document = json.loads(target.read_text())
            document["revision"] = "c" * 32
            target.write_text(json.dumps(document))
        elif change == "notes":
            change_json(job, "bass.notes.json", lambda doc: doc.update(artifact_id="c" * 32))
        else:
            path.write_bytes(b"changed during confirmation hash")
        return result
    monkeypatch.setattr(inspection, "_audio_sha256", race)
    response = client.put(url(job, case_id=case["id"]), json=payload(draft, "reviewed"))
    assert response.status_code == 409
    saved = json.loads(case_path(job, case).read_text())
    assert saved["status"] == "draft" and saved["confirmation"] is None


def test_async_write_routes_offload_parsed_body_and_sync_work_to_starlette_pool(client, monkeypatch):
    job, _ = ready_review()
    offloaded, worker_threads = [], []
    run = api.run_in_threadpool
    original_create, original_update = review_cases.create_case, review_cases.update_case
    async def recorded_run(function, *args, **kwargs):
        assert isinstance(args[-1], dict)  # Bounded JSON body is parsed first.
        offloaded.append((function, threading.get_ident()))
        return await run(function, *args, **kwargs)
    def recorded_create(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_create(*args, **kwargs)
    def recorded_update(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_update(*args, **kwargs)
    monkeypatch.setattr(api, "run_in_threadpool", recorded_run)
    monkeypatch.setattr(review_cases, "create_case", recorded_create)
    monkeypatch.setattr(review_cases, "update_case", recorded_update)
    case = create(client, job)
    assert client.put(url(job, case_id=case["id"]), json=candidates(case)).status_code == 200
    assert [call[0] for call in offloaded] == [recorded_create, recorded_update]
    assert all(loop_thread != worker for (_, loop_thread), worker in zip(offloaded, worker_threads))
