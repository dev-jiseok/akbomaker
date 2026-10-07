import hashlib
import io
import sys
import threading
import time
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from backend import score_omr as omr
from backend.tests.test_audio import client
from backend.tests.test_score_import import exported


def png():
    stream = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setattr(omr, "executable", lambda: sys.executable)
    monkeypatch.setattr(omr.shutil, "which", lambda name: "/usr/bin/" + name)
    _, xml = exported(tmp_path)

    def command(args, event, cwd, **kwargs):
        if "-batch" in args:
            assert args[-2] == "--"
            assert "org.audiveris.omr.sheet.ProcessingSwitches.drumNotation=true" in args
            output = cwd / "output"
            (output / "first.musicxml").write_bytes(xml)
            (output / "second.musicxml").write_bytes(xml)
        return b""

    monkeypatch.setattr(omr, "run_command", command)
    return xml


def finish(client, job):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get('/api/score-omr/' + job["id"]).json()
        if job["status"] not in {"running", "queued"}:
            return job
        time.sleep(.01)
    pytest.fail("OMR did not finish")


@pytest.mark.parametrize("text,expected", [("1", [1]), ("1-3,5", [1, 2, 3, 5]), ("4,2,2", [2, 4])])
def test_pages(text, expected):
    assert omr.parse_pages(text) == expected


@pytest.mark.parametrize("text", ["", "0", "-1", "1-9999", "4-2", "1,2,3,4,5", "1;touch x", "1,", "@args", "1--2"])
def test_bad_pages(text):
    with pytest.raises(ValueError):
        omr.parse_pages(text)


def test_upload_content_validation():
    assert omr.inspect_upload(png(), "score.png") == ".png"
    assert omr.inspect_upload(b"%PDF-1.4", "score.pdf") == ".pdf"
    for data, filename in [(b"", "a.png"), (png(), "a.jpg"), (b"hello", "a.pdf"), (b"gif", "a.gif"), (b"bad", "a.png")]:
        with pytest.raises(ValueError):
            omr.inspect_upload(data, filename)


def test_pixel_limit(monkeypatch):
    monkeypatch.setattr(omr, "MAX_PIXELS", 100)
    with pytest.raises(ValueError, match="픽셀"):
        omr.inspect_upload(png(), "score.png")


@pytest.mark.parametrize("box", ["0 0 595.28 841.89", "0 0 1190 841.89"])
def test_pdf_box_limit_accepts_normal_pages(box):
    omr.validate_pdf_boxes(f"Page  1 MediaBox: {box}\nPage  1 CropBox: {box}\n")


@pytest.mark.parametrize("box", ["0 0 100000 100000", "0 0 nan 800", "0 0 inf 800", "0 0 -4 800", "0 0 1440 1440", "bad"])
def test_pdf_box_limit_rejects_large_or_malformed_pages(box):
    with pytest.raises(ValueError):
        omr.validate_pdf_boxes(f"MediaBox: {box}\nCropBox: {box}\n")


def test_unconfigured_engine_is_explicit(client, monkeypatch):
    monkeypatch.delenv("AKBO_OMR_EXECUTABLE", raising=False)
    assert client.get("/api/score-omr/status").json()["available"] is False
    assert client.post("/api/score-omr", files={"file": ("a.png", png())}).status_code == 503


def test_multiple_outputs_provenance_and_original_retention(client, engine):
    response = client.post("/api/score-omr", files={"file": ("@malicious name.png", png())}, data={"notation": "drums"})
    assert response.status_code == 202
    job = finish(client, response.json())
    assert job["status"] == "ready", job
    assert len(job["results"]) == 2 and len(job["preview_urls"]) == 1
    assert client.get(job["source_url"]).content == png()
    assert "attachment" in client.get(job["source_url"]).headers["content-disposition"]
    assert client.get(job["preview_urls"][0]).headers["content-type"] == "image/png"
    result = job["results"][1]
    assert client.get(result["download_url"]).content == engine
    assert client.get('/api/score-omr/' + job["id"] + '/files/state.json').status_code == 404
    response = client.post("/api/score-import", files={"file": (result["filename"], engine)}, data={"part_id": "P1", "instrument": "bass", "omr_id": job["id"], "omr_result_id": result["id"]})
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["source_type"] == "musicxml" and project["original_url"] is None
    assert project["score_omr"]["source_url"] == job["source_url"]
    assert project["score_omr"]["result_sha256"] == hashlib.sha256(engine).hexdigest()
    assert "자동 인식" in next(s for s in project["stems"] if s["id"] == "bass")["score_warning"]
    bad = client.post("/api/score-import", files={"file": (result["filename"], engine + b" ")}, data={"part_id": "P1", "instrument": "bass", "omr_id": job["id"], "omr_result_id": result["id"]})
    assert bad.status_code == 409


def test_invalid_job_and_partial_provenance(client, engine):
    assert client.get("/api/score-omr/not-an-id").status_code == 404
    response = client.post("/api/score-import", files={"file": ("score.xml", engine)}, data={"part_id": "P1", "instrument": "bass", "omr_id": "a" * 32})
    assert response.status_code == 422


def test_image_page_selection_fails_without_false_success(client, engine):
    response = client.post("/api/score-omr", files={"file": ("score.png", png())}, data={"notation": "drums", "pages": "2"})
    job = finish(client, response.json())
    assert job["status"] == "error" and not job["results"]
    assert "1페이지" in job["error"]


def test_selected_pdf_pages_are_validated_and_forwarded(client, engine, monkeypatch):
    engine_command = omr.run_command
    calls = []

    def command(args, event, cwd, **kwargs):
        calls.append(args)
        if args[0].endswith("pdfinfo"):
            return b"Pages: 4\nEncrypted: no\nPage  1 MediaBox: 0 0 595 842\nPage  1 CropBox: 0 0 595 842\n"
        if args[0].endswith("pdftoppm"):
            from pathlib import Path
            Path(args[-1] + ".png").write_bytes(png())
            return b""
        return engine_command(args, event, cwd, **kwargs)

    monkeypatch.setattr(omr, "run_command", command)
    response = client.post("/api/score-omr", files={"file": ("score.pdf", b"%PDF-1.4")}, data={"notation": "drums", "pages": "1,3"})
    job = finish(client, response.json())
    assert job["status"] == "ready", job
    assert len(job["preview_urls"]) == 2
    assert job["preview_urls"][1].endswith("preview-3.png")
    command = next(args for args in calls if "-batch" in args)
    assert command[command.index("-sheets") + 1:command.index("-sheets") + 3] == ["1", "3"]
    assert "org.audiveris.omr.image.ImageLoading.pdfResolution=300" in command


@pytest.mark.parametrize("metadata", [b"Pages: 1\nEncrypted: yes", b"Pages: 101\nEncrypted: no", b"Pages: 1\nEncrypted: no", b"not a pdf"])
def test_bad_pdf_metadata_fails_before_recognition(client, engine, monkeypatch, metadata):
    def command(args, *a, **kw):
        assert args[0].endswith("pdfinfo")
        return metadata

    monkeypatch.setattr(omr, "run_command", command)
    response = client.post("/api/score-omr", files={"file": ("score.pdf", b"%PDF-1.4")}, data={"pages": "2"})
    job = finish(client, response.json())
    assert job["status"] == "error" and not job["results"]


def test_cancel_is_terminal_and_preserves_original(client, engine, monkeypatch):
    started = threading.Event()

    def command(args, event, *a, **kw):
        started.set()
        event.wait(3)
        raise omr.Cancelled()

    monkeypatch.setattr(omr, "run_command", command)
    response = client.post("/api/score-omr", files={"file": ("score.png", png())}, data={"notation": "drums"})
    job = response.json()
    assert started.wait(2)
    assert client.post('/api/score-omr/' + job["id"] + '/cancel').status_code == 200
    job = finish(client, job)
    assert job["status"] == "cancelled" and not job["results"]
    assert client.get(job["source_url"]).status_code == 200


def test_empty_and_unsafe_engine_output_rejected(tmp_path):
    with pytest.raises(ValueError, match="찾지 못했"):
        omr.collect_results("a" * 32, tmp_path)
    (tmp_path / "escape.xml").symlink_to(tmp_path.parent / "secret.xml")
    with pytest.raises(ValueError, match="경로"):
        omr.collect_results("a" * 32, tmp_path)


def test_disk_budget_limits_files_and_bytes(tmp_path, monkeypatch):
    (tmp_path / "result.bin").write_bytes(b"12345")
    monkeypatch.setattr(omr, "MAX_JOB_BYTES", 4)
    with pytest.raises(ValueError, match="용량"):
        omr.check_disk_budget(tmp_path)
    monkeypatch.setattr(omr, "MAX_JOB_BYTES", 100)
    monkeypatch.setattr(omr, "MAX_JOB_ENTRIES", 0)
    with pytest.raises(ValueError, match="파일 수"):
        omr.check_disk_budget(tmp_path)


def test_restart_marks_unfinished_not_success(client):
    job_id = "b" * 32
    omr.save({"id": job_id, "status": "running"})
    job = client.get('/api/score-omr/' + job_id).json()
    assert job["status"] == "error" and "재시작" in job["error"]


def test_body_size_limit_before_parser(client):
    response = client.post("/api/score-omr", content=b"x", headers={"content-length": str(27 * 1024 * 1024)})
    assert response.status_code == 413


def test_upload_reservation_released_and_capacity_enforced(client, engine, monkeypatch):
    response = client.post("/api/score-omr", files={"file": ("a.png", b"invalid")})
    assert response.status_code == 422 and omr.UPLOADING == 0
    monkeypatch.setattr(omr, "UPLOADING", 3)
    response = client.post("/api/score-omr", files={"file": ("a.png", png())})
    assert response.status_code == 429 and omr.UPLOADING == 3


def test_runner_bounds_output_and_timeout_and_cancel(tmp_path):
    event = threading.Event()
    data = omr.run_command([sys.executable, "-c", "print('a' * 200000)"], event, tmp_path, capture=True)
    assert len(data) == 65536
    with pytest.raises(ValueError, match="제한 시간"):
        omr.run_command([sys.executable, "-c", "import time; time.sleep(5)"], event, tmp_path, timeout=.1)
    event.set()
    with pytest.raises(omr.Cancelled):
        omr.run_command([sys.executable, "-c", "import time; time.sleep(5)"], event, tmp_path)


def test_preserve_preview_keeps_unsupported_notes(client, tmp_path):
    _, xml = exported(tmp_path)
    xml = xml.replace(b"</note>", b"<notations><slur type='start'/></notations></note>", 1)
    response = client.post("/api/score-import/preserve-preview", files={"file": ("score.xml", xml)}, data={"part_id": "P1", "preset": "large", "measures_per_line": "2"})
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "layout-only"
    assert "slur" in response.json()["xml"]


def test_rhythm_diagnostics_not_mistaken_for_success():
    log = b"INFO Measure{#7} Voice{#3 excess:1/16} too long\nINFO S5 MeasureStack#18 no correct rhythm\n"
    warning, = omr.recognition_warnings(log)
    assert "7, 18" in warning and "검수" in warning
    assert omr.recognition_warnings(b"export completed") == []


def test_ocr_language_selection_validation(monkeypatch):
    monkeypatch.setenv("AKBO_OMR_LANGUAGES", "eng+kor")
    assert omr.ocr_languages() == "eng+kor"
    monkeypatch.setenv("AKBO_OMR_LANGUAGES", "@somewhere")
    with pytest.raises(ValueError, match="언어"):
        omr.ocr_languages()


def audiveris_drum_export(tmp_path):
    """A valid editable score with the actual 5.11.0 zero-based bank format."""
    _, xml = exported(tmp_path, inst="drums")
    root = ET.fromstring(xml)
    identification = root.find("identification")
    if identification is None:
        identification = ET.SubElement(root, "identification")
    encoding = ET.SubElement(identification, "encoding")
    ET.SubElement(encoding, "software").text = "Audiveris 5.11.0"
    definition = root.find("part-list/score-part")
    for child in list(definition):
        if child.tag in {"score-instrument", "midi-instrument"}:
            definition.remove(child)
    instrument = ET.SubElement(definition, "score-instrument", id="P1-I46")
    ET.SubElement(instrument, "instrument-name").text = "Open_Hi_Hat"
    midi = ET.SubElement(definition, "midi-instrument", id="P1-I46")
    ET.SubElement(midi, "midi-channel").text = "10"
    ET.SubElement(midi, "midi-unpitched").text = "46"
    for note in root.findall(".//note"):
        if note.find("instrument") is not None:
            note.find("instrument").set("id", "P1-I46")
    return ET.tostring(root)


def test_raw_audiveris_manual_import_cannot_turn_open_hihat_into_tom(client, tmp_path):
    from backend.audiveris_compat import normalize_export
    raw = audiveris_drum_export(tmp_path)
    form = {"part_id": "P1", "instrument": "drums"}
    denied = client.post("/api/score-import", files={"file": ("raw.musicxml", raw)}, data=form)
    assert denied.status_code == 422 and "MIDI 번호" in denied.json()["detail"]
    normalized, _ = normalize_export(raw, "raw.musicxml")
    accepted = client.post("/api/score-import", files={"file": ("normalized.musicxml", normalized)}, data=form)
    assert accepted.status_code == 201, accepted.text
    doc = client.get(f'/api/jobs/{accepted.json()["id"]}/scores/drums').json()
    assert all(note["pitch"] == 46 for note in doc["notes"])


def test_collect_retains_raw_and_normalized_exports(tmp_path, monkeypatch):
    from backend import store
    from backend.tests.test_audiveris_compat import exported as engine_export
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    folder = omr.directory("c" * 32)
    output = folder / "output"
    output.mkdir(parents=True)
    raw = engine_export()
    (output / "score.musicxml").write_bytes(raw)
    result, = omr.collect_results("c" * 32, output, notation="drums")
    assert result["download_url"] != result["raw_download_url"]
    assert (folder / result["raw_download_url"].rsplit("/", 1)[-1]).read_bytes() == raw
    assert result["normalizations"] and result["sha256"] != result["raw_sha256"]


def test_drum_pitched_mixture_warns_instead_of_claiming_correct_mapping(client, engine):
    response = client.post("/api/score-omr", files={"file": ("a.png", png())}, data={"notation": "drums"})
    job = finish(client, response.json())
    assert job["status"] == "ready"
    assert any("일반 음정" in warning for warning in job["warnings"])


@pytest.fixture
def normalized_omr_job(client, engine, monkeypatch, tmp_path):
    from backend.tests.test_audiveris_compat import packed
    raw = packed(audiveris_drum_export(tmp_path))
    diagnostic = b"INFO PRIVATE_OPERATOR_DIAGNOSTIC_PATH\n"

    def command(args, event, cwd, **kwargs):
        assert "-batch" in args
        (cwd / "output" / "drums.mxl").write_bytes(raw)
        return diagnostic

    monkeypatch.setattr(omr, "run_command", command)
    response = client.post("/api/score-omr", files={"file": ("score.png", png())}, data={"notation": "drums"})
    assert response.status_code == 202
    job = finish(client, response.json())
    assert job["status"] == "ready", job
    result, = job["results"]
    return job, result, raw, diagnostic


def test_raw_and_normalized_assets_download_with_independent_hashes(client, normalized_omr_job):
    job, result, raw, _ = normalized_omr_job
    original = client.get(result["raw_download_url"])
    normalized = client.get(result["download_url"])
    assert original.status_code == normalized.status_code == 200
    assert original.content == raw and normalized.content != raw
    assert result["raw_download_url"].endswith(".mxl")
    assert result["download_url"].endswith(".normalized.musicxml")
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["sha256"] == hashlib.sha256(normalized.content).hexdigest()
    assert result["raw_sha256"] != result["sha256"]
    assert job["source_sha256"] == hashlib.sha256(png()).hexdigest()
    assert len({job["source_sha256"], result["raw_sha256"], result["sha256"]}) == 3
    for response in (original, normalized):
        assert response.headers["content-type"] == "application/octet-stream"
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
    root = ET.fromstring(normalized.content)
    assert root.findtext("part-list/score-part/midi-instrument/midi-unpitched") == "47"
    assert result["normalizations"] and all(warning in job["warnings"] for warning in result["normalizations"])


def test_normalized_project_provenance_preserves_raw_and_rejects_mismatched_bytes(client, normalized_omr_job):
    job, result, raw, _ = normalized_omr_job
    normalized = client.get(result["download_url"]).content
    form = {"part_id": "P1", "instrument": "drums", "omr_id": job["id"], "omr_result_id": result["id"]}
    response = client.post("/api/score-import", files={"file": (result["filename"], normalized)}, data=form)
    assert response.status_code == 201, response.text
    project = response.json()
    provenance = project["score_omr"]
    assert provenance["source_sha256"] == job["source_sha256"]
    assert provenance["result_sha256"] == hashlib.sha256(normalized).hexdigest()
    assert provenance["raw_result_sha256"] == hashlib.sha256(raw).hexdigest()
    assert provenance["result_url"] == result["download_url"]
    assert provenance["raw_result_url"] == result["raw_download_url"]
    assert provenance["normalizations"] == result["normalizations"]
    assert client.get(provenance["raw_result_url"]).content == raw
    assert client.get(provenance["source_url"]).content == png()
    stem = next(stem for stem in project["stems"] if stem["id"] == "drums")
    assert client.get(stem["score_source_url"]).content == normalized
    doc = client.get(f'/api/jobs/{project["id"]}/scores/drums').json()
    assert doc["notes"] and all(note["pitch"] == 46 for note in doc["notes"])

    # Raw MXL is a valid retained artifact, but it is not the selected normalized
    # score. Neither raw bytes nor a one-byte modification may claim its hash.
    for filename, data in (("drums.mxl", raw), (result["filename"], normalized + b" ")):
        denied = client.post("/api/score-import", files={"file": (filename, data)}, data=form)
        assert denied.status_code == 409
    wrong_result = {**form, "omr_result_id": "f" * 32}
    denied = client.post("/api/score-import", files={"file": (result["filename"], normalized)}, data=wrong_result)
    assert denied.status_code == 409


def test_private_engine_log_and_non_public_job_files_are_not_downloadable(client, normalized_omr_job):
    job, _, _, diagnostic = normalized_omr_job
    folder = omr.directory(job["id"])
    assert (folder / "engine.log").read_bytes() == diagnostic
    assert (folder / "input.png").is_file()
    public_state = client.get('/api/score-omr/' + job["id"])
    assert "PRIVATE_OPERATOR_DIAGNOSTIC_PATH" not in public_state.text
    for filename in ("engine.log", "state.json", "state.tmp", "input.png", "drums.mxl", "unlisted.musicxml"):
        response = client.get(f'/api/score-omr/{job["id"]}/files/{filename}')
        assert response.status_code == 404
        assert "PRIVATE_OPERATOR_DIAGNOSTIC_PATH" not in response.text
    assert client.get(f'/api/score-omr/{job["id"]}/files/output/drums.mxl').status_code == 404


@pytest.mark.parametrize("asset_key", ["raw_download_url", "download_url"])
def test_even_allowlisted_raw_or_normalized_asset_cannot_be_a_symlink(client, normalized_omr_job, tmp_path, asset_key):
    job, result, _, _ = normalized_omr_job
    path = omr.directory(job["id"]) / result[asset_key].rsplit("/", 1)[-1]
    original = path.read_bytes()
    outside = tmp_path / "private-operator-file"
    outside.write_bytes(b"PRIVATE_OPERATOR_FILE")
    path.unlink()
    try:
        path.symlink_to(outside)
        response = client.get(result[asset_key])
        assert response.status_code == 404
        assert b"PRIVATE_OPERATOR_FILE" not in response.content
    finally:
        path.unlink()
        path.write_bytes(original)
