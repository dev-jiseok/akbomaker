"""HTTP regression tests for faithful, canonical MusicXML score projects."""
import hashlib
import io
import json
import copy
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app as api, source_projects, store
from backend.tests.test_audio import client
from backend.tests.test_score_omr import engine, finish as finish_omr, png
from backend.tests.test_score_preservation import score as expressive_score


INSTRUMENTS = ("drums", "bass", "guitar", "piano", "synthesizer", "vocal")


def fixture_score(instrument="vocal"):
    """Small original notation with independent voices, TAB and kit metadata."""
    dual = instrument in {"guitar", "bass", "piano", "synthesizer"}
    strings = instrument in {"guitar", "bass"}
    kit = instrument == "drums"
    octave = "2" if instrument == "bass" else "4"
    step = "G" if instrument == "bass" else "E" if instrument == "guitar" else "C"
    pitch = f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
    if kit:
        pitch = "<unpitched><display-step>G</display-step><display-octave>5</display-octave></unpitched>"
    kit_definition = ("<score-instrument id='HH'><instrument-name>Closed Hi-Hat</instrument-name></score-instrument>"
                      "<midi-instrument id='HH'><midi-channel>10</midi-channel><midi-unpitched>43</midi-unpitched></midi-instrument>") if kit else ""
    clef = "<sign>percussion</sign>" if kit else "<sign>F</sign><line>4</line>" if instrument == "bass" else "<sign>G</sign><line>2</line>"
    second_clef = "<clef number='2'><sign>TAB</sign><line>5</line></clef>" if strings else "<clef number='2'><sign>F</sign><line>4</line></clef>"
    staff_details = ""
    if strings:
        staff_details = (f"<staff-details number='2'><staff-lines>{4 if instrument == 'bass' else 6}</staff-lines>"
                         f"<staff-tuning line='{4 if instrument == 'bass' else 6}'><tuning-step>{step}</tuning-step>"
                         f"<tuning-octave>{octave}</tuning-octave></staff-tuning></staff-details>")
    first_note = (f"<note default-x='90'>{pitch}<duration>12</duration>"
                  + ("<instrument id='HH'/>" if kit else "")
                  + "<voice>1</voice><type>quarter</type><stem>up</stem>"
                  + ("<notehead>x</notehead>" if kit else "")
                  + ("<staff>1</staff>" if dual else "")
                  + "<notations><articulations><accent/></articulations></notations>"
                  "<lyric number='1'><syllabic>single</syllabic><text>원본 가사</text></lyric></note>")
    rest = "<note><rest/><duration>36</duration><voice>1</voice><type>half</type><dot/>" + ("<staff>1</staff>" if dual else "") + "</note>"
    lower_voice = ""
    if dual:
        lower_pitch = pitch if strings else "<pitch><step>C</step><octave>3</octave></pitch>"
        lower_voice = ("<backup><duration>48</duration></backup>"
                       f"<note>{lower_pitch}<duration>48</duration><voice>2</voice><type>whole</type><staff>2</staff>"
                       + ("<notations><technical><string>1</string><fret>0</fret></technical></notations>" if strings else "") + "</note>")
    return (f"<?xml version='1.0' encoding='UTF-8'?><score-partwise version='4.0'>"
            "<work><work-title>원본 보존 테스트</work-title></work>"
            "<identification><creator type='composer'>Original Author</creator><rights>Original Rights</rights></identification>"
            f"<part-list><score-part id='P1'><part-name>{instrument}</part-name>{kit_definition}</score-part></part-list>"
            "<part id='P1'><measure number='1' width='300'><print new-page='yes'/>"
            "<attributes><divisions>12</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time>"
            + ("<staves>2</staves>" if dual else "") + f"<clef number='1'>{clef}</clef>" + (second_clef if dual else "") + staff_details
            + "</attributes><direction placement='below'><direction-type><dynamics><p/></dynamics></direction-type></direction>"
            + first_note + rest + lower_voice
            + "<barline location='right'><bar-style>light-heavy</bar-style></barline></measure></part></score-partwise>").encode()


def music_structure(xml):
    """Ignore only presentation locations; note and instrument data must match."""
    root = ET.fromstring(xml)
    for node in root.iter():
        for attribute in ("default-x", "default-y", "relative-x", "relative-y"):
            node.attrib.pop(attribute, None)
    for measure in root.findall("part/measure"):
        measure.attrib.pop("width", None)
        for printing in list(measure.findall("print")):
            measure.remove(printing)
    return (ET.tostring(root.find("part-list")), ET.tostring(root.find("part")),
            ET.tostring(root.find("identification")))


def create(client, instrument="vocal", data=None, filename="original.musicxml", **fields):
    source = data if data is not None else fixture_score(instrument)
    response = client.post("/api/source-scores", files={"file": (filename, source)},
                           data={"part_id": "P1", "instrument": instrument, **fields})
    assert response.status_code == 201, response.text
    job = response.json()
    response = client.get("/api/source-scores/" + job["id"])
    assert response.status_code == 200, response.text
    return job, response.json(), source


def edit(client, job_id, document, **patch):
    return client.post(f"/api/source-scores/{job_id}/edit", json={
        "base_revision": document["revision"], "patch": patch})


@pytest.mark.parametrize("instrument", INSTRUMENTS)
def test_all_instruments_restyle_without_reinterpreting_original_notation(client, instrument):
    job, document, source = create(client, instrument)
    assert job["source_type"] == "musicxml"
    assert job["score_preserved"]["instrument"] == instrument
    assert document["instrument"] == instrument and document["part_id"] == "P1"
    ready = [stem for stem in job["stems"] if stem["score_status"] == "ready"]
    assert len(ready) == 1 and ready[0]["id"] == instrument
    assert document["history"] == {"undo": False, "redo": False}
    assert client.get(document["source_url"]).content == source
    assert document["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert music_structure(document["xml"]) == music_structure(source)
    assert music_structure(client.get(document["original_url"]).content) == music_structure(source)
    for preset, measures in [("large", 2), ("standard", 4), ("practice", 2)]:
        result = client.put(f'/api/source-scores/{job["id"]}/layout', json={
            "base_revision": document["revision"], "preset": preset, "measures_per_line": measures})
        assert result.status_code == 200, result.text
        document = result.json()["document"]
        assert document["layout"] == {"preset": preset, "measures_per_line": measures, "show_numbers": True}
        assert music_structure(document["xml"]) == music_structure(source)
        assert client.get(document["current_url"]).content.decode() == document["xml"]
        assert client.get(document["source_url"]).content == source


def test_pitch_edit_changes_only_target_and_retains_lyrics_articulations_duration(client):
    job, before, source = create(client)
    result = edit(client, job["id"], before, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4)
    assert result.status_code == 200, result.text
    after = result.json()["document"]
    assert after["revision"] != before["revision"]
    expected = ET.fromstring(before["xml"])
    actual = ET.fromstring(after["xml"])
    pitch = actual.find("part/measure/note/pitch")
    assert pitch.findtext("step") == "D" and pitch.findtext("octave") == "4"
    original_pitch = expected.find("part/measure/note/pitch")
    actual_note = actual.find("part/measure/note")
    index = list(actual_note).index(pitch)
    actual_note.remove(pitch)
    actual_note.insert(index, original_pitch)
    # The edited pitch may receive an explicit natural sign so key-signature
    # context cannot make its new sounding pitch ambiguous.
    accidental = actual_note.find("accidental")
    if accidental is not None:
        assert accidental.text == "natural"
        actual_note.remove(accidental)
    assert music_structure(ET.tostring(actual)) == music_structure(ET.tostring(expected))
    assert client.get(after["source_url"]).content == source
    assert music_structure(client.get(after["original_url"]).content) == music_structure(source)
    assert client.get(f'/api/jobs/{job["id"]}').json()["score_preserved"]["revision"] == after["revision"]


def test_tuplets_repeats_and_performance_symbols_are_never_quantized_away(client):
    source = expressive_score()
    _, document, _ = create(client, "guitar", source)
    assert music_structure(document["xml"])[1:] == music_structure(source)[1:]
    root = ET.fromstring(document["xml"])
    assert len(root.findall(".//time-modification")) == 6
    assert len(root.findall(".//repeat")) == 6
    assert len(root.findall(".//hammer-on")) == 6
    assert len(root.findall(".//lyric/extend")) == 6


def test_layout_edit_undo_redo_are_durable_and_branching_clears_redo(client):
    job, initial, _ = create(client)
    first = edit(client, job["id"], initial, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4).json()["document"]
    response = client.put(f'/api/source-scores/{job["id"]}/layout', json={
        "base_revision": first["revision"], "preset": "large", "measures_per_line": 2})
    assert response.status_code == 200, response.text
    second = response.json()["document"]
    assert second["history"] == {"undo": True, "redo": False}
    undo = client.post(f'/api/source-scores/{job["id"]}/history', json={"base_revision": second["revision"], "action": "undo"})
    assert undo.status_code == 200, undo.text
    undone = undo.json()["document"]
    assert undone["layout"] == first["layout"] and undone["xml"] == first["xml"]
    assert undone["revision"] not in {initial["revision"], first["revision"], second["revision"]}
    assert undone["history"]["redo"]
    # Re-read instead of relying on the mutation response or in-memory state.
    assert client.get(f'/api/source-scores/{job["id"]}').json() == undone
    redo = client.post(f'/api/source-scores/{job["id"]}/history', json={"base_revision": undone["revision"], "action": "redo"})
    assert redo.status_code == 200, redo.text
    redone = redo.json()["document"]
    assert redone["xml"] == second["xml"] and redone["layout"] == second["layout"]
    undone = client.post(f'/api/source-scores/{job["id"]}/history', json={"base_revision": redone["revision"], "action": "undo"}).json()["document"]
    branched = edit(client, job["id"], undone, note_id="m0n0", operation="pitch", step="E", alter=0, octave=4)
    assert branched.status_code == 200, branched.text
    assert branched.json()["document"]["history"] == {"undo": True, "redo": False}


@pytest.mark.parametrize("action", ["edit", "layout", "history"])
def test_stale_requests_cannot_overwrite_a_newer_revision(client, action):
    job, initial, _ = create(client)
    current = edit(client, job["id"], initial, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4).json()["document"]
    path = f'/api/source-scores/{job["id"]}/{action}'
    body = {"base_revision": initial["revision"]}
    if action == "edit":
        body["patch"] = {"note_id": "m0n0", "operation": "pitch", "step": "E", "alter": 0, "octave": 4}
    elif action == "layout":
        body.update(preset="large", measures_per_line=2)
    else:
        body["action"] = "undo"
    result = client.request("PUT" if action == "layout" else "POST", path, json=body)
    assert result.status_code == 409, result.text
    assert client.get(f'/api/source-scores/{job["id"]}').json() == current


def test_simultaneous_edits_of_same_revision_have_one_winner(client):
    job, initial, _ = create(client)
    def attempt(step):
        return edit(client, job["id"], initial, note_id="m0n0", operation="pitch", step=step, alter=0, octave=4)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, ["D", "E"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["document"] for response in responses if response.status_code == 200)
    assert client.get(f'/api/source-scores/{job["id"]}').json() == winner


@pytest.mark.parametrize("patch", [
    {"note_id": "m999n999", "operation": "pitch", "step": "D", "alter": 0, "octave": 4},
    {"note_id": "m0n0", "operation": "pitch", "step": "H", "alter": 0, "octave": 4},
    {"note_id": "m0n0", "operation": "replace_xml", "xml": "<note/>"},
])
def test_rejected_edit_has_no_disk_or_project_side_effect(client, patch):
    job, document, _ = create(client)
    folder = store.directory(job["id"])
    before = {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    result = edit(client, job["id"], document, **patch)
    assert result.status_code == 422, result.text
    assert client.get(f'/api/source-scores/{job["id"]}').json() == document
    after = {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    assert after == before


def test_preserved_project_is_not_exposed_to_legacy_quantized_editor(client):
    job, document, _ = create(client)
    result = client.get(f'/api/jobs/{job["id"]}/scores/vocal')
    assert result.status_code == 409, result.text
    result = client.post(f'/api/jobs/{job["id"]}/scores/vocal/copy-lyrics', json={"base_revision": document["revision"]})
    assert result.status_code == 409, result.text
    assert client.get(f'/api/source-scores/{job["id"]}').json() == document


@pytest.mark.parametrize("name", ["job.json", "state.json", "working.musicxml", "../job.json", "%2e%2e%2fjob.json"])
def test_private_files_and_paths_are_not_downloadable(client, name):
    job, _, _ = create(client)
    assert client.get(f'/api/source-scores/{job["id"]}/files/{name}').status_code == 404


def test_invalid_source_score_ids_fail_without_creating_projects(client):
    for job_id in ["not-an-id", "a" * 32]:
        assert client.get("/api/source-scores/" + job_id).status_code == 404
    source = fixture_score()
    for fields in [{"part_id": "missing", "instrument": "vocal"}, {"part_id": "P1", "instrument": "unknown"}]:
        result = client.post("/api/source-scores", files={"file": ("score.xml", source)}, data=fields)
        assert result.status_code == 422, result.text
    assert not list(store.DATA_DIR.glob("*/job.json"))


def test_unsafe_source_is_rejected_without_creating_project(client):
    unsafe = b'<!DOCTYPE score-partwise [<!ENTITY e SYSTEM "file:///etc/passwd">]><score-partwise>&e;</score-partwise>'
    result = client.post("/api/source-scores", files={"file": ("score.xml", unsafe)}, data={"part_id": "P1", "instrument": "vocal"})
    assert result.status_code == 422, result.text
    assert not list(store.DATA_DIR.glob("*/job.json"))


def test_compressed_source_download_retains_exact_uploaded_bytes(client):
    stream = io.BytesIO()
    xml = fixture_score("bass")
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>')
        archive.writestr("score.musicxml", xml)
    source = stream.getvalue()
    _, document, _ = create(client, "bass", source, "score.mxl")
    assert client.get(document["source_url"]).content == source
    assert document["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert music_structure(document["xml"]) == music_structure(xml)


def test_omr_provenance_is_verified_and_warnings_survive_into_editor(client, engine):
    response = client.post("/api/score-omr", files={"file": ("score.png", png())}, data={"notation": "drums"})
    assert response.status_code == 202, response.text
    recognized = finish_omr(client, response.json())
    assert recognized["status"] == "ready", recognized
    result = recognized["results"][0]
    job, document, _ = create(client, "bass", engine, result["filename"], omr_id=recognized["id"], omr_result_id=result["id"])
    assert job["score_omr"]["source_url"] == recognized["source_url"]
    assert job["score_omr"]["result_sha256"] == hashlib.sha256(engine).hexdigest()
    assert any("인식" in warning for warning in document["warnings"])
    original_warnings = set(document["warnings"])
    changed = client.put(f'/api/source-scores/{job["id"]}/layout', json={
        "base_revision": document["revision"], "preset": "large", "measures_per_line": 2})
    assert changed.status_code == 200, changed.text
    assert original_warnings <= set(changed.json()["document"]["warnings"])
    response = client.post("/api/source-scores", files={"file": (result["filename"], engine + b" ")}, data={
        "part_id": "P1", "instrument": "bass", "omr_id": recognized["id"], "omr_result_id": result["id"]})
    assert response.status_code == 409, response.text
    response = client.post("/api/source-scores", files={"file": (result["filename"], engine)}, data={
        "part_id": "P1", "instrument": "bass", "omr_id": recognized["id"]})
    assert response.status_code == 422, response.text


def test_source_project_creation_never_modifies_existing_audio_project(client):
    existing = store.create("Existing recording", "file")
    folder = store.directory(existing["id"])
    (folder / "original.wav").write_bytes(b"original recording")
    before = (folder / "job.json").read_bytes()
    job, document, _ = create(client, "drums")
    assert job["id"] != existing["id"]
    assert (folder / "job.json").read_bytes() == before
    assert (folder / "original.wav").read_bytes() == b"original recording"
    assert not document.get("audio_url")


def test_archive_contains_current_edit_original_upload_and_unselected_parts(client):
    root = ET.fromstring(fixture_score())
    definition = copy.deepcopy(root.find("part-list/score-part"))
    definition.set("id", "P2")
    root.find("part-list").append(definition)
    other = copy.deepcopy(root.find("part"))
    other.set("id", "P2")
    other.find("measure/note/pitch/step").text = "G"
    root.append(other)
    source = ET.tostring(root)
    job, document, _ = create(client, data=source)
    edited = edit(client, job["id"], document, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4).json()["document"]
    response = client.get(f'/api/jobs/{job["id"]}/archive')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "source-score.json" not in archive.namelist() and "job.json" not in archive.namelist()
        assert archive.read("source.musicxml") == source
        assert archive.read("current.musicxml").decode() == edited["xml"]
        current = ET.fromstring(archive.read("current.musicxml"))
        original = ET.fromstring(archive.read("original.musicxml"))
        working = ET.fromstring(archive.read("working-all-parts.musicxml"))
        assert current.findtext("part/measure/note/pitch/step") == "D"
        assert original.findtext("part/measure/note/pitch/step") == "C"
        assert [node.get("id") for node in current.findall("part")] == ["P1"]
        assert [node.get("id") for node in working.findall("part")] == ["P1", "P2"]
        assert ET.tostring(working.find("part[@id='P2']")) == ET.tostring(other)
        assert b"Original-notation" in archive.read("README.txt")


def test_history_limits_are_enforced_and_empty_undo_is_nonmutating(client, monkeypatch):
    monkeypatch.setattr(source_projects, "HISTORY_LIMIT", 2)
    job, document, _ = create(client)
    result = client.post(f'/api/source-scores/{job["id"]}/history', json={"base_revision": document["revision"], "action": "undo"})
    assert result.status_code == 409, result.text
    assert client.get(f'/api/source-scores/{job["id"]}').json() == document
    for step in ["D", "E", "F"]:
        result = edit(client, job["id"], document, note_id="m0n0", operation="pitch", step=step, alter=0, octave=4)
        assert result.status_code == 200, result.text
        document = result.json()["document"]
    state = json.loads((store.directory(job["id"]) / "source-score.json").read_text())
    assert len(state["undo"]) == 2
    for _ in range(2):
        result = client.post(f'/api/source-scores/{job["id"]}/history', json={"base_revision": document["revision"], "action": "undo"})
        assert result.status_code == 200, result.text
        document = result.json()["document"]
    assert document["history"] == {"undo": False, "redo": True}
    assert ET.fromstring(document["xml"]).findtext("part/measure/note/pitch/step") == "D"


def test_canonical_disk_state_is_reloaded_and_corruption_is_not_overwritten(client):
    job, document, _ = create(client)
    state_path = store.directory(job["id"]) / "source-score.json"
    state = json.loads(state_path.read_text())
    state["xml"] += "<!-- corruption without checksum update -->"
    corrupt = json.dumps(state).encode()
    state_path.write_bytes(corrupt)
    assert client.get(f'/api/source-scores/{job["id"]}').status_code == 409
    assert client.get(f'/api/jobs/{job["id"]}').status_code == 409
    assert edit(client, job["id"], document, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4).status_code == 409
    assert state_path.read_bytes() == corrupt


def test_original_file_checksum_is_verified_for_download_and_archive(client):
    job, document, _ = create(client)
    original = store.directory(job["id"]) / "source.musicxml"
    original.write_bytes(original.read_bytes() + b"corruption")
    assert client.get(document["source_url"]).status_code == 409
    assert client.get(f'/api/jobs/{job["id"]}/archive').status_code == 409
    # Losing an externally modified upload does not replace valid working XML.
    assert client.get(f'/api/source-scores/{job["id"]}').json() == document


def test_downloads_are_attachments_and_external_resources_are_removed_from_working_copy(client):
    root = ET.fromstring(fixture_score())
    ET.SubElement(root.find("part/measure/direction/direction-type"), "image", source="https://example.invalid/private.png")
    root.find("part/measure/note").set("onclick", "bad()")
    source = ET.tostring(root)
    _, document, _ = create(client, data=source, filename="../../source.musicxml")
    assert "example.invalid" not in document["xml"] and "onclick" not in document["xml"]
    assert any("외부" in warning for warning in document["warnings"])
    for key in ["source_url", "current_url", "original_url"]:
        response = client.get(document[key])
        assert response.status_code == 200, response.text
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(document["source_url"]).content == source


def test_large_multipart_source_is_rejected_before_processing(client):
    result = client.post("/api/source-scores", content=b"", headers={"Content-Length": str(4 * 1024 * 1024)})
    assert result.status_code == 413
    assert not list(store.DATA_DIR.glob("*/job.json"))


@pytest.mark.parametrize("operation", ["edit", "layout", "history"])
@pytest.mark.parametrize("failure", ["temporary-write", "atomic-replace"])
def test_atomic_persistence_failure_keeps_previous_state_job_and_source(client, monkeypatch, operation, failure):
    job, document, _ = create(client)
    if operation == "history":
        result = edit(client, job["id"], document, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4)
        assert result.status_code == 200, result.text
        document = result.json()["document"]
    folder = store.directory(job["id"])
    immutable_names = ["source-score.json", "job.json", "source.musicxml"]
    before = {name: (folder / name).read_bytes() for name in immutable_names}
    body = {"base_revision": document["revision"]}
    if operation == "edit":
        body["patch"] = {"note_id": "m0n0", "operation": "pitch", "step": "E", "alter": 0, "octave": 4}
    elif operation == "layout":
        body.update(preset="large", measures_per_line=2)
    else:
        body["action"] = "undo"
    path = f'/api/source-scores/{job["id"]}/{operation}'
    method = "PUT" if operation == "layout" else "POST"
    actual_write, actual_replace = Path.write_bytes, Path.replace

    def fail_write(path, data):
        if path == folder / "source-score.tmp":
            # A disk-full write can leave a partial temporary file. It must
            # never become the authoritative state, even on the next GET.
            actual_write(path, data[:32])
            raise OSError("simulated disk full")
        return actual_write(path, data)

    def fail_replace(path, target):
        if path == folder / "source-score.tmp":
            raise OSError("simulated rename failure")
        return actual_replace(path, target)

    with monkeypatch.context() as failures:
        failures.setattr(Path, "write_bytes" if failure == "temporary-write" else "replace",
                         fail_write if failure == "temporary-write" else fail_replace)
        # The existing fixture owns app startup; this client only makes the
        # uncaught I/O failure observable as an HTTP response, not a pytest raise.
        quiet = TestClient(api.app, raise_server_exceptions=False)
        try:
            result = quiet.request(method, path, json=body)
        finally:
            quiet.close()
        assert result.status_code == 500, result.text
        assert {name: (folder / name).read_bytes() for name in immutable_names} == before
        assert client.get(f'/api/source-scores/{job["id"]}').json() == document
        current_job = client.get(f'/api/jobs/{job["id"]}').json()
        assert current_job["score_preserved"]["revision"] == document["revision"]
    # Once storage works again, a retry from the unchanged revision succeeds;
    # a leftover partial temporary file must not strand the project.
    result = client.request(method, path, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["document"]["revision"] != document["revision"]
    assert not (folder / "source-score.tmp").exists()


def test_compressed_large_canonical_score_can_restyle_edit_and_undo(client):
    root = ET.fromstring(fixture_score())
    root.find("identification/rights").text = "r" * (2 * 1024 * 1024 + 100)
    xml = ET.tostring(root)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr("score.xml", xml)
    source = stream.getvalue()
    assert len(source) < 2 * 1024 * 1024 < len(xml) < 8 * 1024 * 1024
    job, document, _ = create(client, data=source, filename="score.mxl")
    assert len(document["xml"].encode()) > 2 * 1024 * 1024
    styled = client.put(f'/api/source-scores/{job["id"]}/layout', json={
        "base_revision": document["revision"], "preset": "large", "measures_per_line": 2})
    assert styled.status_code == 200, styled.text[:300]
    document = styled.json()["document"]
    changed = edit(client, job["id"], document, note_id="m0n0", operation="pitch", step="D", alter=0, octave=4)
    assert changed.status_code == 200, changed.text[:300]
    edited = changed.json()["document"]
    assert ET.fromstring(edited["xml"]).findtext("part/measure/note/pitch/step") == "D"
    reverted = client.post(f'/api/source-scores/{job["id"]}/history', json={"base_revision": edited["revision"], "action": "undo"})
    assert reverted.status_code == 200, reverted.text[:300]
    assert reverted.json()["document"]["xml"] == document["xml"]
    assert client.get(edited["source_url"]).content == source
