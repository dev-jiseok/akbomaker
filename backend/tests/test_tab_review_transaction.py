"""A failed deterministic TAB import must be retryable without overwriting data."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app as api, source_projects, store, tab_review
from backend.tests.test_audio import client
from backend.tests.test_tab_review_projects import SOURCE, draft, seed, url


@pytest.mark.parametrize("failure", ["source", "attachment", "state", "validation", "job", "publish"])
def test_failed_staged_import_can_retry_identical_review(client, monkeypatch, failure):
    job, coordinates = seed()
    revision = client.get(url()).json()["revision"]
    body = {"base_revision": revision, "draft": draft(job), "confirmed": True}
    audio = store.create("existing music", "file")
    audio_path = store.directory(audio["id"]) / "job.json"
    audio_before = audio_path.read_bytes()
    fired = False

    def once():
        nonlocal fired
        if not fired:
            fired = True
            raise OSError("controlled transient write failure")

    if failure in {"source", "attachment"}:
        target = "source.musicxml" if failure == "source" else "reference.pdf"
        original = Path.write_bytes
        def write(path, data):
            if path.name == target and path.parent.name.startswith(".source-"):
                once()
            return original(path, data)
        monkeypatch.setattr(Path, "write_bytes", write)
    elif failure == "state":
        original = source_projects._write_state
        def write(state, **kwargs):
            once()
            return original(state, **kwargs)
        monkeypatch.setattr(source_projects, "_write_state", write)
    elif failure == "validation":
        original = source_projects._document
        def document(state):
            once()
            return original(state)
        monkeypatch.setattr(source_projects, "_document", document)
    elif failure == "job":
        original = Path.write_text
        def write(path, *args, **kwargs):
            if path.name == "job.json" and path.parent.name.startswith(".source-"):
                once()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", write)
    else:
        original = Path.rename
        def rename(path, target):
            if path.name.startswith(".source-"):
                once()
            return original(path, target)
        monkeypatch.setattr(Path, "rename", rename)

    response_client = TestClient(api.app, raise_server_exceptions=False)
    assert response_client.post(url("/import"), json=body).status_code == 500
    assert fired
    assert {p.parent.name for p in store.DATA_DIR.glob("*/job.json")} == {audio["id"]}
    assert not list(store.DATA_DIR.glob(".source-*"))
    assert client.get(url()).json()["draft"] == body["draft"] | {
        "rows": [row | {"muted": False, "reason": ""} for row in body["draft"]["rows"]]}
    retry = client.post(url("/import"), json=body)
    assert retry.status_code == 201, retry.text
    project = retry.json()
    assert project["status"] == "completed"
    folder = store.directory(project["id"])
    assert (folder / "reference.pdf").read_bytes() == SOURCE
    assert (folder / "source-coordinates.json").read_bytes() == coordinates
    assert client.get(f'/api/source-scores/{project["id"]}').status_code == 200
    assert audio_path.read_bytes() == audio_before
    assert not list(store.DATA_DIR.glob(".source-*"))


def test_failure_after_atomic_publish_leaves_completed_project_for_retry(client, monkeypatch):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    body = {"base_revision": revision, "draft": draft(job), "confirmed": True}
    original = source_projects.project
    fired = False
    def response(job_id, state=None):
        nonlocal fired
        if not fired:
            fired = True
            raise OSError("controlled lost response after commit")
        return original(job_id, state)
    monkeypatch.setattr(source_projects, "project", response)
    response_client = TestClient(api.app, raise_server_exceptions=False)
    assert response_client.post(url("/import"), json=body).status_code == 500
    paths = list(store.DATA_DIR.glob("*/job.json"))
    assert len(paths) == 1
    before = {path.name: path.read_bytes() for path in paths[0].parent.iterdir()}
    assert store.get(paths[0].parent.name)["status"] == "completed"
    retry = client.post(url("/import"), json=body)
    assert retry.status_code == 201
    assert retry.json()["id"] == paths[0].parent.name
    assert {path.name: path.read_bytes() for path in paths[0].parent.iterdir()} == before


def test_published_project_is_never_replaced_after_user_edits(client):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    body = {"base_revision": revision, "draft": draft(job), "confirmed": True}
    project = client.post(url("/import"), json=body).json()
    document = client.get(f'/api/source-scores/{project["id"]}').json()
    note = next(n for n in document["notes"] if n["kind"] != "rest")
    edited = client.post(f'/api/source-scores/{project["id"]}/edit', json={
        "base_revision": document["revision"], "patch": {"note_id": note["id"], "operation": "tab_pitch", "string": 4, "fret": 7}})
    assert edited.status_code == 200
    before = (store.directory(project["id"]) / "source-score.json").read_bytes()
    assert client.post(url("/import"), json=body).json()["id"] == project["id"]
    assert (store.directory(project["id"]) / "source-score.json").read_bytes() == before


def test_unsaved_job_does_not_reserve_id_or_publish_directory(client):
    ident = "e" * 32
    pending = store.create("not committed", "musicxml", job_id=ident, persist=False)
    assert pending["id"] == ident
    assert not store.directory(ident).exists()
    committed = store.create("committed", "musicxml", job_id=ident)
    assert store.get(ident) == committed
    with pytest.raises(ValueError, match="덮어쓸"):
        store.create("no overwrite", "musicxml", job_id=ident, persist=False)


@pytest.mark.parametrize("edited", [False, True])
def test_warning_copy_update_retries_existing_project_without_changing_any_files(client, monkeypatch, edited):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    body = {"base_revision": revision, "draft": draft(job), "confirmed": True}
    created = client.post(url("/import"), json=body)
    assert created.status_code == 201
    project = created.json()
    if edited:
        document = client.get(f'/api/source-scores/{project["id"]}').json()
        note = next(n for n in document["notes"] if n["kind"] != "rest")
        changed = client.post(f'/api/source-scores/{project["id"]}/edit', json={
            "base_revision": document["revision"], "patch": {"note_id": note["id"], "operation": "tab_pitch", "string": 4, "fret": 9}})
        assert changed.status_code == 200
    folder = store.directory(project["id"])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    monkeypatch.setattr(tab_review, "WARNINGS", [*tab_review.WARNINGS, "새 버전의 안내 문구"])
    retry = client.post(url("/import"), json=body)
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == project["id"]
    assert retry.json()["score_tab_review"] == project["score_tab_review"]
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before
    assert len(list(store.DATA_DIR.glob("*/job.json"))) == 1


@pytest.mark.parametrize("field,value", [
    ("id", "c" * 32), ("source_sha256", "c" * 64), ("coordinate_sha256", "c" * 64),
    ("review_sha256", "c" * 64), ("method", "different-method"), ("future_version", "v2"),
])
def test_non_warning_provenance_difference_remains_a_conflict(client, field, value):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    body = {"base_revision": revision, "draft": draft(job), "confirmed": True}
    project = client.post(url("/import"), json=body).json()
    stored = store.get(project["id"])
    stored["score_tab_review"][field] = value
    store.save(stored)
    folder = store.directory(project["id"])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    retry = client.post(url("/import"), json=body)
    assert retry.status_code == 409
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


@pytest.mark.parametrize("field", ["id", "source_sha256", "coordinate_sha256", "review_sha256", "method"])
def test_missing_stable_identity_is_not_equivalent_even_if_both_sides_missing(field):
    provenance = {"id": "a" * 32, "source_sha256": "b" * 64, "coordinate_sha256": "c" * 64,
                  "review_sha256": "d" * 64, "method": "user-reviewed-pdf-tab", "warnings": []}
    provenance.pop(field)
    assert not source_projects._same_tab_provenance(provenance, dict(provenance))


def test_future_provenance_fields_are_compared_not_allowlisted_away():
    existing = {"id": "a" * 32, "source_sha256": "b" * 64, "coordinate_sha256": "c" * 64,
                "review_sha256": "d" * 64, "method": "user-reviewed-pdf-tab", "warnings": ["old"],
                "future_setting": {"version": 2}}
    assert source_projects._same_tab_provenance(existing, {**existing, "warnings": ["new"]})
    assert not source_projects._same_tab_provenance(existing, {**existing, "future_setting": {"version": 3}})
