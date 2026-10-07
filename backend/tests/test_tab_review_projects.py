"""HTTP contracts for explicitly reviewed PDF TAB, independent of audio/OMR."""
import copy
import hashlib
import io
import json
import threading
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from backend import pipeline, score_omr as omr, store
from backend.tests.test_audio import client
from backend.tests.test_pdf_score_analysis import bar, digit, lines
from backend.pdf_score_analysis import analyze_page_geometry
from backend.tests.test_score_omr import finish, png


SOURCE = b"%PDF-1.4 controlled original TAB fixture"
JOB_ID = "b" * 32


def digest(data):
    return hashlib.sha256(data).hexdigest()


def analysis(source=SOURCE, *, tab=True):
    return {"schema_version": 1, "source_sha256": digest(source), "page_count": 1,
            "instrument": "auto", "editable_musicxml": False, "rhythm_known": False,
            "requires_review": True, "warnings": [], "pages": [analyze_page_geometry(
                page_number=1, width=600, height=800,
                chars=[digit("5", x=70), digit("3", x=150, y=108.5)],
                edges=lines(4 if tab else 5) + [bar(20), bar(200), bar(580)], instrument="auto")]}


def seed(*, document=None, status="ready"):
    document = document or analysis()
    encoded = json.dumps(document, ensure_ascii=False).encode()
    job = {"id": JOB_ID, "source_sha256": digest(SOURCE), "pages": [1], "notation": "tab",
           "source_name": "source.pdf", "source_url": f"/api/score-omr/{JOB_ID}/files/source.pdf",
           "preview_urls": [], "results": [], "warnings": [], "status": status,
           "source_coordinates": {"download_url": f"/api/score-omr/{JOB_ID}/files/source-coordinates.json",
                                  "sha256": digest(encoded), "pages": [1], "tab_staffs": 1,
                                  "digit_candidates": 2, "matched_digits": 1,
                                  "editable_musicxml": False, "rhythm_known": False}}
    omr.save(job)
    folder = omr.directory(JOB_ID)
    (folder / "source.pdf").write_bytes(SOURCE)
    (folder / "source-coordinates.json").write_bytes(encoded)
    return job, encoded


def draft(job, *, incomplete=False):
    return {"source_sha256": job["source_sha256"], "coordinate_sha256": job["source_coordinates"]["sha256"],
            "instrument": "bass", "title": "원본 TAB 검토", "tuning": [43, 38, 33, 28],
            "capo": 0, "beats": 4, "beat_type": 4, "tempo": 90, "staff_ids": ["p1s0"],
            "rows": [{"id": f"p1s0:d{index}", "staff_id": "p1s0", "decision": "note",
                      "measure": 1, "onset": None if incomplete else str(index),
                      "type": None if incomplete else "quarter", "dots": 0,
                      "string": 4, "fret": fret} for index, fret in enumerate((5, 3))]}


def url(suffix=""):
    return f"/api/score-omr/{JOB_ID}/tab-review" + suffix


def reviewed(client, *, payload=None):
    job, encoded = seed()
    document = client.get(url()).json()
    body = {"base_revision": document["revision"], "draft": payload or draft(job), "confirmed": True}
    response = client.post(url("/import"), json=body)
    assert response.status_code == 201, response.text
    return response.json(), body, encoded


def test_first_read_persists_revision_and_incomplete_draft_survives_reload(client):
    job, _ = seed()
    original = client.get(url())
    assert original.status_code == 200, original.text
    document = original.json()
    assert document["draft"] is None and len(document["revision"]) == 32
    assert client.get(url()).json()["revision"] == document["revision"]
    response = client.put(url(), json={"base_revision": document["revision"], "draft": draft(job, incomplete=True)})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] != document["revision"]
    assert saved["draft"]["rows"][0]["onset"] is None
    assert client.get(url()).json() == saved
    persisted = json.loads((omr.directory(JOB_ID) / "tab-review.json").read_bytes())
    assert persisted["revision"] == saved["revision"] and persisted["draft"] == saved["draft"]
    assert not list(store.DATA_DIR.glob("*/job.json")), "Saving a draft must not allocate an audio or score project"
    stale = client.put(url(), json={"base_revision": document["revision"], "draft": draft(job)})
    assert stale.status_code == 409
    assert client.get(url()).json() == saved


@pytest.mark.parametrize("confirmation", [False, None, "true", 1])
def test_import_requires_explicit_strict_confirmation(client, confirmation):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    body = {"base_revision": revision, "draft": draft(job)}
    if confirmation is not None:
        body["confirmed"] = confirmation
    assert client.post(url("/import"), json=body).status_code == 422
    assert not list(store.DATA_DIR.glob("*/job.json"))


@pytest.mark.parametrize("case", ["missing-candidate", "missing-rhythm", "excluded-no-reason", "unknown-id",
                                 "duplicate", "unselected-staff", "same-string-overlap"])
def test_import_requires_complete_nonconflicting_decisions(client, case):
    job, _ = seed()
    payload = draft(job)
    if case == "missing-candidate":
        payload["rows"].pop()
    elif case == "missing-rhythm":
        payload["rows"][0]["type"] = None
    elif case == "excluded-no-reason":
        payload["rows"][1]["decision"] = "exclude"
    elif case == "unknown-id":
        payload["rows"][0]["id"] = "p1s0:d99"
    elif case == "duplicate":
        payload["rows"].append(copy.deepcopy(payload["rows"][0]))
    elif case == "unselected-staff":
        payload["rows"][0]["staff_id"] = "p1s9"
    else:
        payload["rows"][1]["onset"] = "0"
    revision = client.get(url()).json()["revision"]
    response = client.post(url("/import"), json={"base_revision": revision, "draft": payload, "confirmed": True})
    assert response.status_code == 422, response.text
    assert not list(store.DATA_DIR.glob("*/job.json"))


def test_complete_import_is_native_editable_project_without_audio_inference(client, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("PDF TAB review must never invoke audio separation or transcription")
    monkeypatch.setattr(pipeline, "run_separation", unexpected)
    monkeypatch.setattr(pipeline, "run_transcription", unexpected)
    existing = store.create("existing audio", "file")
    before = (store.directory(existing["id"]) / "job.json").read_bytes()
    project, _, _ = reviewed(client)
    assert project["source_type"] == "musicxml" and project["status"] == "completed"
    assert project["original_url"] is None and project["duration"] == 0
    assert project["score_tab_review"]["method"] == "user-reviewed-pdf-tab"
    document = client.get(f'/api/source-scores/{project["id"]}').json()
    visible = [n for n in document["notes"] if n["kind"] != "rest"]
    assert len(visible) == 2 and document["content_check"]["style_preserves_music"] is True
    assert all(note["editable"]["tab_pitch"] for note in visible)
    edited = client.post(f'/api/source-scores/{project["id"]}/edit', json={
        "base_revision": document["revision"],
        "patch": {"note_id": visible[0]["id"], "operation": "tab_pitch", "string": 4, "fret": 7}})
    assert edited.status_code == 200, edited.text
    assert edited.json()["document"]["content_check"]["music_edited"] is True
    assert (store.directory(existing["id"]) / "job.json").read_bytes() == before


def test_attachments_are_source_identical_downloadable_and_in_archive(client):
    project, body, coordinates = reviewed(client)
    document = client.get(f'/api/source-scores/{project["id"]}').json()
    attachments = {a["name"]: a for a in document["attachments"]}
    assert set(attachments) == {"reference.pdf", "source-coordinates.json", "reviewed-tab.json"}
    expected = {"reference.pdf": SOURCE, "source-coordinates.json": coordinates}
    for name, attachment in attachments.items():
        response = client.get(attachment["url"])
        assert response.status_code == 200 and digest(response.content) == attachment["sha256"]
        assert response.headers["x-content-type-options"] == "nosniff"
        if name in expected:
            assert response.content == expected[name]
        else:
            record = response.json()
            assert record["draft"]["source_sha256"] == body["draft"]["source_sha256"]
            assert record["summary"]["exact_pdf_transcription"] is False
            assert record["summary"]["timing_origin"] == "user-confirmed"
            expected[name] = response.content
    archive = client.get(f'/api/jobs/{project["id"]}/archive')
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        for name, content in expected.items():
            assert bundle.read(name) == content
        assert {"source.musicxml", "original.musicxml", "current.musicxml", "working-all-parts.musicxml"} <= set(bundle.namelist())
    for name in ("tab-review.json", "source-score.json", "job.json"):
        assert client.get(f'/api/source-scores/{project["id"]}/files/{name}').status_code == 404


@pytest.mark.parametrize("name", ["reference.pdf", "source-coordinates.json", "reviewed-tab.json"])
def test_modified_attachment_blocks_download_and_archive(client, name):
    project, _, _ = reviewed(client)
    (store.directory(project["id"]) / name).write_bytes(b"changed")
    assert client.get(f'/api/source-scores/{project["id"]}/files/{name}').status_code == 409
    assert client.get(f'/api/jobs/{project["id"]}/archive').status_code == 409


@pytest.mark.parametrize("name", ["source.pdf", "source-coordinates.json"])
def test_original_evidence_tamper_blocks_draft_read_save_and_import(client, name):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    (omr.directory(JOB_ID) / name).write_bytes(b"changed")
    assert client.get(url()).status_code == 409
    body = {"base_revision": revision, "draft": draft(job)}
    assert client.put(url(), json=body).status_code == 409
    assert client.post(url("/import"), json={**body, "confirmed": True}).status_code == 409


@pytest.mark.parametrize("field", ["source_sha256", "coordinate_sha256"])
def test_draft_cannot_be_applied_to_other_original_or_coordinate_hash(client, field):
    job, _ = seed()
    revision = client.get(url()).json()["revision"]
    payload = draft(job)
    payload[field] = "0" * 64
    body = {"base_revision": revision, "draft": payload}
    assert client.put(url(), json=body).status_code == 409
    assert client.post(url("/import"), json={**body, "confirmed": True}).status_code == 409


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64), ("schema_version", True),
                                        ("rhythm_known", True), ("editable_musicxml", True), ("requires_review", False)])
def test_semantically_wrong_evidence_rejected_even_with_matching_file_digest(client, field, value):
    document = analysis()
    document[field] = value
    seed(document=document)
    assert client.get(url()).status_code == 409


def test_same_import_after_lost_response_is_idempotent_but_mutated_stale_import_conflicts(client):
    project, original_body, _ = reviewed(client)
    path = store.directory(project["id"]) / "source-score.json"
    saved = path.read_bytes()
    response = client.post(url("/import"), json=original_body)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == project["id"] and path.read_bytes() == saved
    assert len(list(store.DATA_DIR.glob("*/job.json"))) == 1
    altered = copy.deepcopy(original_body)
    altered["draft"]["rows"][0]["fret"] = 7
    assert client.post(url("/import"), json=altered).status_code == 409
    assert path.read_bytes() == saved


def test_lost_response_retry_never_overwrites_later_score_edits(client):
    project, body, _ = reviewed(client)
    base = f'/api/source-scores/{project["id"]}'
    document = client.get(base).json()
    note = next(n for n in document["notes"] if n["kind"] != "rest")
    response = client.post(base + "/edit", json={"base_revision": document["revision"],
        "patch": {"note_id": note["id"], "operation": "tab_pitch", "string": 4, "fret": 9}})
    assert response.status_code == 200, response.text
    saved = (store.directory(project["id"]) / "source-score.json").read_bytes()
    retry = client.post(url("/import"), json=body)
    assert retry.status_code == 201 and retry.json()["id"] == project["id"]
    assert (store.directory(project["id"]) / "source-score.json").read_bytes() == saved
    assert client.get(base).json()["content_check"]["music_edited"] is True


def test_excluded_candidate_and_manual_missing_note_are_recorded_not_silently_dropped(client):
    job, _ = seed()
    payload = draft(job)
    payload["rows"][1].update(decision="exclude", reason="원본에서는 잘못 묶인 숫자여서 직접 나누어 입력")
    payload["rows"].append({"id": "manual-correction", "staff_id": "p1s0", "decision": "note",
                            "measure": 1, "onset": "1", "type": "eighth", "string": 4, "fret": 10})
    revision = client.get(url()).json()["revision"]
    response = client.post(url("/import"), json={"base_revision": revision, "draft": payload, "confirmed": True})
    assert response.status_code == 201, response.text
    project = response.json()
    record = client.get(f'/api/source-scores/{project["id"]}/files/reviewed-tab.json').json()
    assert record["summary"]["evidence_count"] == 2
    assert record["summary"]["excluded_count"] == 1 and record["summary"]["manual_count"] == 1
    assert record["draft"]["rows"][1]["reason"] == payload["rows"][1]["reason"]
    xml = ET.fromstring(client.get(f'/api/source-scores/{project["id"]}/files/current.musicxml').content)
    assert [n.findtext("notations/technical/fret") for n in xml.findall("part/measure/note") if n.find("rest") is None] == ["5", "10"]


def test_source_project_keeps_independent_reference_copy_after_omr_original_changes(client):
    project, _, coordinates = reviewed(client)
    (omr.directory(JOB_ID) / "source.pdf").write_bytes(b"later OMR source tamper")
    (omr.directory(JOB_ID) / "source-coordinates.json").write_bytes(b"later OMR evidence tamper")
    assert client.get(url()).status_code == 409
    base = f'/api/source-scores/{project["id"]}/files/'
    assert client.get(base + "reference.pdf").content == SOURCE
    assert client.get(base + "source-coordinates.json").content == coordinates
    assert client.get(f'/api/jobs/{project["id"]}/archive').status_code == 200


@pytest.mark.parametrize("case", ["draft-hash", "source-hash", "invalid-revision", "invalid-json"])
def test_draft_state_corruption_is_detected_without_replacing_original(client, case):
    seed()
    assert client.get(url()).status_code == 200
    path = omr.directory(JOB_ID) / "tab-review.json"
    state = json.loads(path.read_bytes())
    if case == "draft-hash":
        state["draft_sha256"] = "0" * 64
    elif case == "source-hash":
        state["source_sha256"] = "0" * 64
    elif case == "invalid-revision":
        state["revision"] = "invalid"
    path.write_bytes(b"invalid" if case == "invalid-json" else json.dumps(state).encode())
    assert client.get(url()).status_code == 409
    assert (omr.directory(JOB_ID) / "source.pdf").read_bytes() == SOURCE


@pytest.mark.parametrize("status,expected", [("queued", 409), ("running", 409)])
def test_review_waits_for_coordinate_job_to_finish(client, monkeypatch, status, expected):
    seed(status=status)
    monkeypatch.setitem(omr.ACTIVE, JOB_ID, threading.Event())
    assert client.get(url()).status_code == expected


@pytest.fixture
def tab_worker(monkeypatch):
    monkeypatch.setattr(omr, "executable", lambda: None)
    monkeypatch.setattr(omr.shutil, "which", lambda name: "/usr/bin/" + name)
    state = {"tab": True, "fail": False, "calls": []}
    def worker(args, event, cwd, **kwargs):
        state["calls"].append(args)
        if args[0].endswith("pdfinfo"):
            return b"Pages: 1\nEncrypted: no\nPage 1 MediaBox: 0 0 600 800\nPage 1 CropBox: 0 0 600 800\n"
        if args[0].endswith("pdftoppm"):
            Path(args[-1] + ".png").write_bytes(png())
            return b""
        if len(args) > 1 and args[1].endswith("pdf_score_analysis.py"):
            if state["fail"]:
                raise ValueError("controlled PDF failure")
            source = Path(args[args.index("--input") + 1]).read_bytes()
            Path(args[args.index("--output") + 1]).write_text(json.dumps(analysis(source, tab=state["tab"])))
            return b""
        pytest.fail("TAB-only mode must not invoke Audiveris or another engine")
    monkeypatch.setattr(omr, "run_command", worker)
    return state


def test_tab_upload_bypasses_unavailable_audiveris_and_opens_review(client, tab_worker):
    config = client.get("/api/score-omr/status").json()
    assert config["available"] is False and config["tab_review_available"] is True
    response = client.post("/api/score-omr", files={"file": ("bass.pdf", SOURCE)}, data={"notation": "tab"})
    assert response.status_code == 202, response.text
    job = finish(client, response.json())
    assert job["status"] == "ready" and job["results"] == []
    assert job["source_coordinates"]["tab_staffs"] == 1
    assert client.get(f'/api/score-omr/{job["id"]}/tab-review').status_code == 200
    assert not any("-batch" in command for command in tab_worker["calls"])
    assert client.post("/api/score-omr", files={"file": ("bass.pdf", SOURCE)}, data={"notation": "staff"}).status_code == 503


@pytest.mark.parametrize("case", ["no-tab", "coordinate-error"])
def test_tab_upload_without_readable_coordinates_does_not_claim_success(client, tab_worker, case):
    tab_worker["tab"] = case != "no-tab"
    tab_worker["fail"] = case == "coordinate-error"
    response = client.post("/api/score-omr", files={"file": ("bass.pdf", SOURCE)}, data={"notation": "tab"})
    assert response.status_code == 202
    job = finish(client, response.json())
    assert job["status"] == "error" and not job["results"]
    assert client.get(f'/api/score-omr/{job["id"]}/tab-review').status_code == 422
    assert not list(store.DATA_DIR.glob("*/job.json"))


def test_tab_upload_rejects_raster_image_instead_of_guessing_digits(client, tab_worker):
    response = client.post("/api/score-omr", files={"file": ("bass.png", png())}, data={"notation": "tab"})
    assert response.status_code == 422
    assert tab_worker["calls"] == []
