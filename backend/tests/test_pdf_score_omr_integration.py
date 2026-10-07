"""PDF coordinate evidence stays source-linked and separate from OMR scores."""
import hashlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import pdf_score_analysis as geometry, score_omr as omr
from backend.tests.test_audio import client
from backend.tests.test_pdf_score_analysis import digit, lines
from backend.tests.test_score_import import exported
from backend.tests.test_score_omr import finish, png


SOURCE = b"%PDF-1.4 controlled worker fixture"


def evidence(pages=(1,), instrument="auto"):
    return {"schema_version": 1, "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
            "page_count": 3, "instrument": instrument, "rhythm_known": False, "editable_musicxml": False,
            "requires_review": True, "warnings": geometry.BASE_WARNINGS.copy(),
            "pages": [geometry.analyze_page_geometry(page_number=number, width=600, height=800,
                       chars=[digit("5"), digit("3", x=120, y=108.5)], edges=lines(),
                       instrument=instrument) for number in pages]}


def seed_job(notation="staff", suffix=".pdf", pages=(1,)):
    job_id = "a" * 32
    name = "source" + suffix
    job = {"id": job_id, "source_sha256": hashlib.sha256(SOURCE).hexdigest(), "pages": list(pages),
           "notation": notation, "source_name": name, "source_url": f"/api/score-omr/{job_id}/files/{name}",
           "preview_urls": [], "results": [], "warnings": [], "status": "running"}
    omr.save(job)
    path = omr.directory(job_id) / name
    path.write_bytes(SOURCE)
    return job, path


def fake_evidence_worker(monkeypatch, document):
    calls = []
    def run(args, event, cwd, **kwargs):
        calls.append((args, event, cwd, kwargs))
        Path(args[args.index("--output") + 1]).write_text(json.dumps(document), encoding="utf-8")
        return b""
    monkeypatch.setattr(omr, "run_command", run)
    return calls


@pytest.mark.parametrize("notation,expected", [("staff", "auto"), ("drums", "drums")])
def test_source_coordinates_runs_bounded_worker_and_derives_verified_summary(client, monkeypatch, notation, expected):
    job, source = seed_job(notation=notation, pages=(1, 3))
    document = evidence((1, 3), expected)
    # Deliberately incorrect parser statistics must not become UI counts.
    document["pages"][0]["statistics"]["tab_digits"] = 999999
    calls = fake_evidence_worker(monkeypatch, document)
    event = threading.Event()
    summary = omr.source_coordinates(job, source, event)
    args, forwarded_event, cwd, kwargs = calls[0]
    assert forwarded_event is event and cwd == source.parent and kwargs["timeout"] == 45
    assert args[0] == sys.executable and args[1].endswith("pdf_score_analysis.py")
    assert args[args.index("--input") + 1] == str(source)
    assert args[args.index("--instrument") + 1] == expected
    assert args[args.index("--pages") + 1] == "1,3"
    assert summary["pages"] == [1, 3]
    assert summary["tab_staffs"] == (2 if notation == "staff" else 0)
    assert summary["digit_candidates"] == (4 if notation == "staff" else 0)
    assert summary["matched_digits"] == (2 if notation == "staff" else 0)
    assert summary["editable_musicxml"] is False and summary["rhythm_known"] is False
    payload = (source.parent / "source-coordinates.json").read_bytes()
    assert summary["sha256"] == hashlib.sha256(payload).hexdigest()
    assert source.read_bytes() == SOURCE


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_coordinate_analysis_never_runs_on_images(client, monkeypatch, suffix):
    job, source = seed_job(suffix=suffix)
    def unexpected(*args, **kwargs):
        pytest.fail("Image uploads must not invoke the PDF coordinate worker")
    monkeypatch.setattr(omr, "run_command", unexpected)
    assert omr.source_coordinates(job, source, threading.Event()) is None


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "0" * 64), ("schema_version", 2), ("schema_version", "1"),
    ("editable_musicxml", True), ("editable_musicxml", 0), ("rhythm_known", True), ("rhythm_known", 0),
    ("pages", []), ("pages", [{"page": 2, "staffs": []}]),
])
def test_mismatched_source_schema_scope_and_conversion_flags_are_rejected(client, monkeypatch, field, value):
    job, source = seed_job()
    document = evidence()
    document[field] = value
    fake_evidence_worker(monkeypatch, document)
    with pytest.raises(ValueError):
        omr.source_coordinates(job, source, threading.Event())


@pytest.mark.parametrize("case", ["schema-boolean", "review-disabled", "accepted-number", "accepted-string"])
def test_coordinate_summary_requires_strict_types_and_review_semantics(client, monkeypatch, case):
    job, source = seed_job()
    document = evidence()
    if case == "schema-boolean":
        document["schema_version"] = True
    elif case == "review-disabled":
        document["requires_review"] = False
    else:
        document["pages"][0]["staffs"][0]["digits"][0]["accepted"] = 5 if case == "accepted-number" else "yes"
    fake_evidence_worker(monkeypatch, document)
    with pytest.raises(ValueError):
        omr.source_coordinates(job, source, threading.Event())


@pytest.mark.parametrize("case", ["root-list", "root-null", "pages-object", "page-string", "page-boolean", "staffs-object",
                                 "staff-string", "staff-kind-list", "staff-kind-unknown", "digits-object", "digit-string", "digit-review-disabled"])
def test_malformed_coordinate_shapes_are_validation_errors(client, monkeypatch, case):
    job, source = seed_job()
    document = evidence()
    if case == "root-list":
        document = []
    elif case == "root-null":
        document = None
    elif case == "pages-object":
        document["pages"] = {}
    elif case == "page-string":
        document["pages"] = ["invalid"]
    elif case == "page-boolean":
        document["pages"][0]["page"] = True
    elif case == "staffs-object":
        document["pages"][0]["staffs"] = {}
    elif case == "staff-string":
        document["pages"][0]["staffs"] = ["invalid"]
    elif case in {"staff-kind-list", "staff-kind-unknown"}:
        document["pages"][0]["staffs"][0]["kind"] = [] if case == "staff-kind-list" else "unknown"
    elif case == "digits-object":
        document["pages"][0]["staffs"][0]["digits"] = {}
    elif case == "digit-string":
        document["pages"][0]["staffs"][0]["digits"] = ["invalid"]
    else:
        document["pages"][0]["staffs"][0]["digits"][0]["requires_review"] = False
    fake_evidence_worker(monkeypatch, document)
    with pytest.raises(ValueError):
        omr.source_coordinates(job, source, threading.Event())


@pytest.mark.parametrize("case", ["missing", "oversized", "symlink", "invalid-json"])
def test_coordinate_worker_output_file_is_bounded_and_not_a_link(client, monkeypatch, case):
    job, source = seed_job()
    output = source.parent / "source-coordinates.json"
    def worker(*args, **kwargs):
        if case == "oversized":
            output.write_bytes(b" " * (2 * 1024 * 1024 + 1))
        elif case == "symlink":
            output.symlink_to(source)
        elif case == "invalid-json":
            output.write_bytes(b"not JSON")
        return b""
    monkeypatch.setattr(omr, "run_command", worker)
    with pytest.raises(ValueError):
        omr.source_coordinates(job, source, threading.Event())


def test_coordinate_worker_cancellation_is_not_treated_as_optional_failure(client, monkeypatch):
    job, source = seed_job()
    def worker(*args, **kwargs):
        raise omr.Cancelled()
    monkeypatch.setattr(omr, "run_command", worker)
    with pytest.raises(omr.Cancelled):
        omr.source_coordinates(job, source, threading.Event())


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(omr, "executable", lambda: sys.executable)
    monkeypatch.setattr(omr.shutil, "which", lambda name: "/usr/bin/" + name)
    _, xml = exported(tmp_path)
    control = SimpleNamespace(bad_hash=False, fail=False, cancel=False, coordinate_started=threading.Event(),
                              engine_calls=0, coordinate_calls=0)
    def worker(args, event, cwd, **kwargs):
        if args[0].endswith("pdfinfo"):
            return b"Pages: 3\nEncrypted: no\nPage 1 MediaBox: 0 0 600 800\nPage 1 CropBox: 0 0 600 800\n"
        if args[0].endswith("pdftoppm"):
            Path(args[-1] + ".png").write_bytes(png())
        elif len(args) > 1 and args[1].endswith("pdf_score_analysis.py"):
            control.coordinate_calls += 1
            control.coordinate_started.set()
            if control.cancel:
                event.wait(3)
                raise omr.Cancelled()
            if control.fail:
                raise ValueError("optional worker unavailable")
            pages = [int(value) for value in args[args.index("--pages") + 1].split(",")]
            instrument = args[args.index("--instrument") + 1]
            document = evidence(pages, instrument)
            if control.bad_hash:
                document["source_sha256"] = "0" * 64
            Path(args[args.index("--output") + 1]).write_text(json.dumps(document), encoding="utf-8")
        elif "-batch" in args:
            control.engine_calls += 1
            (cwd / "output" / "score.musicxml").write_bytes(xml)
        return b""
    monkeypatch.setattr(omr, "run_command", worker)
    return control


def test_pdf_evidence_is_allowlisted_as_source_linked_review_not_score_result(client, pipeline):
    response = client.post("/api/score-omr", files={"file": ("source.pdf", SOURCE)}, data={"pages": "1,3"})
    assert response.status_code == 202, response.text
    job = finish(client, response.json())
    assert job["status"] == "ready", job
    summary = job["source_coordinates"]
    assert summary["digit_candidates"] == 4 and summary["matched_digits"] == 2
    assert summary["download_url"].endswith("source-coordinates.json")
    assert len(job["results"]) == 1 and job["results"][0]["filename"].endswith(".musicxml")
    download = client.get(summary["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert hashlib.sha256(download.content).hexdigest() == summary["sha256"]
    assert download.json()["source_sha256"] == job["source_sha256"]
    assert client.get(job["source_url"]).content == SOURCE
    assert client.get(f'/api/score-omr/{job["id"]}/files/source-coordinates.json.tmp').status_code == 404
    assert client.get(f'/api/score-omr/{job["id"]}/files/engine.log').status_code == 404


@pytest.mark.parametrize("failure", ["bad_hash", "fail"])
def test_optional_coordinate_failure_keeps_omr_working_and_does_not_allowlist_bad_evidence(client, pipeline, failure):
    setattr(pipeline, failure, True)
    response = client.post("/api/score-omr", files={"file": ("source.pdf", SOURCE)})
    job = finish(client, response.json())
    assert job["status"] == "ready" and job["results"] and pipeline.engine_calls == 1
    assert not job.get("source_coordinates")
    assert any("보조 분석" in warning for warning in job["warnings"])
    assert client.get(f'/api/score-omr/{job["id"]}/files/source-coordinates.json').status_code == 404


def test_image_job_skips_pdf_coordinates_but_still_runs_omr(client, pipeline):
    response = client.post("/api/score-omr", files={"file": ("source.png", png())})
    job = finish(client, response.json())
    assert job["status"] == "ready" and job["results"]
    assert pipeline.coordinate_calls == 0 and pipeline.engine_calls == 1
    assert not job.get("source_coordinates")


def test_cancel_during_coordinate_subprocess_stops_before_audiveris(client, pipeline):
    pipeline.cancel = True
    response = client.post("/api/score-omr", files={"file": ("source.pdf", SOURCE)})
    job = response.json()
    assert pipeline.coordinate_started.wait(2)
    assert client.post(f'/api/score-omr/{job["id"]}/cancel').status_code == 200
    job = finish(client, job)
    assert job["status"] == "cancelled" and not job["results"]
    assert pipeline.engine_calls == 0 and not job.get("source_coordinates")
    assert client.get(job["source_url"]).content == SOURCE


@pytest.mark.parametrize("corruption,status", [("changed", 409), ("oversized", 409), ("symlink", 404)])
def test_coordinate_download_checks_allowlist_and_immutable_digest(client, pipeline, corruption, status):
    response = client.post("/api/score-omr", files={"file": ("source.pdf", SOURCE)})
    job = finish(client, response.json())
    summary = job["source_coordinates"]
    path = omr.directory(job["id"]) / "source-coordinates.json"
    if corruption == "changed":
        path.write_bytes(path.read_bytes() + b" ")
    elif corruption == "oversized":
        path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    else:
        path.unlink()
        path.symlink_to(omr.directory(job["id"]) / "source.pdf")
    assert client.get(summary["download_url"]).status_code == status
    assert client.get(job["source_url"]).content == SOURCE
