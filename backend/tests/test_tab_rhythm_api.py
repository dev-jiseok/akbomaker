"""Rhythm suggestions are read-only, bounded, and tied to the exact PDF draft."""
import json
from pathlib import Path

import pytest

from backend import score_omr, tab_review_projects as review
from backend.tests.test_audio import client
from backend.tests.test_tab_review_projects import seed, url


def setup_request(client):
    job, _ = seed()
    state = client.get(url()).json()
    return job, {"base_revision": state["revision"], "staff_id": "p1s0",
                 "beats": 4, "beat_type": 4, "meter_confirmed": True}


def worker(job, *, changes=None):
    def run(command, event, folder, timeout):
        assert timeout == 12 and not event.is_set()
        result = {"schema_version": 1, "method": "paired-vector-staff-rhythm", "requires_review": True,
                  "source_sha256": job["source_sha256"], "coordinate_sha256": job["source_coordinates"]["sha256"],
                  "staff_id": "p1s0", "meter": {"beats": 4, "beat_type": 4, "source": "caller-confirmed"},
                  "measures": [{"staff_id": "p1s0", "staff_measure_id": "p1s0m1", "measure_index": 1,
                                "status": "unresolved", "rows": [], "unresolved": ["기호 확인 필요"]}]}
        result.update(changes or {})
        Path(command[command.index("--output") + 1]).write_text(json.dumps(result), encoding="utf-8")
    return run


def test_readonly_suggestions_preserve_draft_source_and_no_project(client, monkeypatch):
    job, body = setup_request(client)
    folder = score_omr.directory(job["id"])
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    monkeypatch.setattr(score_omr, "run_command", worker(job))
    result = client.post(url("/rhythm-suggestions"), json=body)
    assert result.status_code == 200, result.text
    assert result.json()["requires_review"] is True
    assert result.json()["base_revision"] == body["base_revision"]
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


@pytest.mark.parametrize("patch", [{"meter_confirmed": False}, {"meter_confirmed": "true"}, {"beat_type": 3},
                                    {"beats": 0}, {"beats": 13}, {"staff_id": "../../bad"}, {"staff_id": "p1s999"}])
def test_invalid_requests_never_spawn_parser(client, monkeypatch, patch):
    _, body = setup_request(client)
    monkeypatch.setattr(score_omr, "run_command", lambda *a, **k: pytest.fail("worker must not run"))
    assert client.post(url("/rhythm-suggestions"), json=body | patch).status_code == 422


def test_stale_draft_does_not_start_parser(client, monkeypatch):
    _, body = setup_request(client)
    body["base_revision"] = "f" * 32
    monkeypatch.setattr(score_omr, "run_command", lambda *a, **k: pytest.fail("worker must not run"))
    assert client.post(url("/rhythm-suggestions"), json=body).status_code == 409


def test_single_cpu_slot_returns_retryable_busy_response(client):
    _, body = setup_request(client)
    assert review.RHYTHM_SLOT.acquire(blocking=False)
    try:
        assert client.post(url("/rhythm-suggestions"), json=body).status_code == 429
    finally:
        review.RHYTHM_SLOT.release()


@pytest.mark.parametrize("changes", [{"source_sha256": "f" * 64}, {"coordinate_sha256": "f" * 64},
                                       {"schema_version": True}, {"method": "spacing-guess"},
                                       {"requires_review": False}, {"staff_id": "p1s3"},
                                       {"meter": {"beats": 3, "beat_type": 4}}, {"meter": None},
                                       {"measures": None}, {"measures": [None]}])
def test_invalid_worker_output_is_rejected_and_scratch_removed(client, monkeypatch, changes):
    job, body = setup_request(client)
    monkeypatch.setattr(score_omr, "run_command", worker(job, changes=changes))
    assert client.post(url("/rhythm-suggestions"), json=body).status_code == 422
    assert not list(score_omr.directory(job["id"]).glob("rhythm-proposal-*"))
    monkeypatch.setattr(score_omr, "run_command", worker(job))
    assert client.post(url("/rhythm-suggestions"), json=body).status_code == 200


def test_parser_failure_does_not_destroy_existing_draft_and_releases_slot(client, monkeypatch):
    job, body = setup_request(client)
    before = client.get(url()).json()
    def fail(*args, **kwargs):
        raise ValueError("시간 제한")
    monkeypatch.setattr(score_omr, "run_command", fail)
    response = client.post(url("/rhythm-suggestions"), json=body)
    assert response.status_code == 422 and "시간 제한" in response.text
    assert client.get(url()).json() == before
    monkeypatch.setattr(score_omr, "run_command", worker(job))
    assert client.post(url("/rhythm-suggestions"), json=body).status_code == 200


def test_concurrent_revision_change_discards_obsolete_proposal(client, monkeypatch):
    job, body = setup_request(client)
    run = worker(job)
    def mutate(command, event, folder, timeout):
        run(command, event, folder, timeout)
        state = review._state(job)
        review._write(job["id"], state | {"revision": "f" * 32})
    monkeypatch.setattr(score_omr, "run_command", mutate)
    assert client.post(url("/rhythm-suggestions"), json=body).status_code == 409
    assert client.get(url()).json()["revision"] == "f" * 32
