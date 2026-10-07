"""Preparation safety/selection tests use generated MIDI and mocked HTTP only."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import mido
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepare-multtipop.py"
SPEC = importlib.util.spec_from_file_location("prepare_multtipop", SCRIPT)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def encode(document):
    return json.dumps(document).encode()


def row(identifier="DEVCASE0001", *, artist="Artist A", genre="pop"):
    return {"id": identifier, "split": "dev", "artist": artist, "name": "A title",
            "section": "chorus", "genre_everynoise": genre,
            "youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": 10., "end": 11.},
            "audio_link": "https://www.youtube.com/watch?v=forbidden", "chosen_method": "not-used"}


def midi_bytes():
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000)]))
    for channel, program, pitch, ticks in [(0, 0, 60, 240), (9, 16, 36, 24)]:
        midi.tracks.append(mido.MidiTrack([
            mido.Message("program_change", channel=channel, program=program),
            mido.Message("note_on", channel=channel, note=pitch, velocity=90),
            mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=ticks),
        ]))
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def metadata(index_row, midi):
    return {"id": index_row["id"], "split_name": "dev", "audio_length": 1.,
            "youtube": index_row["youtube"], "aligned_midi_checksum": hashlib.sha256(midi).hexdigest(),
            "tempo": 151, "chosen_beat_time": 42.}  # Neither controls the MIDI clock.


def mock_dataset(monkeypatch, rows=None):
    rows = [row()] if rows is None else rows
    midi = midi_bytes()
    files = {"dev.json": encode(rows)}
    for item in rows:
        files[f"dev/{item['id']}/meta.json"] = encode(metadata(item, midi))
        files[f"dev/{item['id']}/aligned.mid"] = midi
    requests = []

    def fetch(relative, cap):
        prepare.source_url(relative)  # Exercise the same URL allowlist.
        requests.append(relative)
        result = files[relative]
        if isinstance(result, Exception):
            raise result
        assert len(result) <= cap
        return result

    monkeypatch.setattr(prepare, "download", fetch)
    return files, requests


def test_cli_dev_only_count_defaults_and_bounds(tmp_path):
    assert prepare.parse_args(["--output", str(tmp_path)]).count == 24
    for value in ("0", "51", "1.5"):
        with pytest.raises(SystemExit):
            prepare.parse_args(["--output", str(tmp_path), "--count", value])
    with pytest.raises(SystemExit):
        prepare.parse_args(["--output", str(tmp_path), "--partition", "test"])
    with pytest.raises(SystemExit):
        prepare.parse_args([])


def test_selection_deterministic_diverse_and_prefix_stable():
    rows = [row(f"DEVCASE{i:04}", artist=artist, genre=genre) for i, (artist, genre) in enumerate([
        ("A", "pop"), ("Ａ", "rock"), ("B", "pop"), ("C", "rock"), ("D", "jazz"), ("E", None)])]
    normalized = prepare.validate_index(encode(rows))
    selection = prepare.select_cases(normalized, 6)
    assert selection == prepare.select_cases(list(reversed(normalized)), 6)
    assert selection[:3] == prepare.select_cases(normalized, 3)
    assert len({prepare._normalized(item["artist"]) for item in selection[:5]}) == 5
    assert {item["genre_everynoise"] for item in selection[:3]} == {"pop", "rock", "jazz"}
    with pytest.raises(ValueError, match="Count"):
        prepare.select_cases(normalized, 7)


def test_selection_ignores_prediction_reference_quality_and_urls():
    rows = [row(f"DEVCASE{i:04}", artist=f"Artist {i}", genre="rock") for i in range(8)]
    original = prepare.select_cases(prepare.validate_index(encode(rows)), 5)
    for index, item in enumerate(rows):
        item.update(chosen_method="better", score=index, reference_quality=1 - index,
                    predicted_f1=100 - index, labels={"accuracy": 1}, preview="https://evil.test/test.wav")
    assert prepare.select_cases(prepare.validate_index(encode(rows)), 5) == original


@pytest.mark.parametrize("bad", ["../other", "a/b", "a\\b", ".", "..", "%2e%2e", "a.wav", "한글", "x" * 81])
def test_case_path_traversal_and_unsafe_ids_rejected(bad):
    with pytest.raises(ValueError):
        prepare.validate_index(encode([row(bad)]))
    with pytest.raises(ValueError):
        prepare.source_url(f"dev/{bad}/aligned.mid")


@pytest.mark.parametrize("relative", ["test.json", "metadata.json", "test/DEVCASE0001/aligned.mid",
                                     "dev/DEVCASE0001/audio.wav", "dev/DEVCASE0001/../../test.json"])
def test_no_test_audio_or_arbitrary_source_paths(relative):
    with pytest.raises(ValueError):
        prepare.source_url(relative)


def test_redirects_allow_only_same_pinned_public_reference():
    normal = prepare.source_url("dev/DEVCASE0001/aligned.mid")
    cached = normal.replace("/datasets/gclef-cmu/multtipop/resolve/", "/api/resolve-cache/datasets/gclef-cmu/multtipop/")
    assert prepare.validate_source_url(cached + "?download=true")
    handler = prepare.PinnedRedirectHandler()
    redirect = handler.redirect_request(Request(normal), None, 302, "Found", {}, cached)
    assert redirect.full_url == cached
    encoded = f"https://huggingface.co/api/resolve-cache/datasets/gclef-cmu/multtipop/{prepare.REVISION}/dev%2FQLgnqXapm-V%2Fmeta.json?download=true"
    assert prepare.validate_source_url(encoded) == encoded
    assert handler.redirect_request(Request(normal), None, 302, "Found", {}, encoded).full_url == encoded
    encoded_prefix = encoded.split("/dev%2F")[0] + "/"
    for suffix in ["test%2FQLgnqXapm-V%2Fmeta.json", "dev%252FQLgnqXapm-V%252Fmeta.json",
                   "dev%2F..%2Fmeta.json", "dev%2F%2e%2e%2Fmeta.json",
                   "dev%2FQLgnqXapm-V%2Faudio.wav", "dev%2FQLgnqXapm-V%2Fmeta.json%2Fextra"]:
        with pytest.raises(ValueError):
            handler.redirect_request(Request(normal), None, 302, "Found", {}, encoded_prefix + suffix)
    for bad in ["https://youtube.com/audio", "http://huggingface.co" + normal.split("huggingface.co")[1],
                normal.replace(prepare.REVISION, "main"), normal.replace("/dev/", "/test/"),
                normal.replace("aligned.mid", "audio.wav"), normal.replace("huggingface.co", "huggingface.co.evil.test"),
                normal.replace("huggingface.co", "user:secret@huggingface.co"), normal + "#fragment"]:
        with pytest.raises(ValueError):
            handler.redirect_request(Request(normal), None, 302, "Found", {}, bad)


class Response:
    def __init__(self, data=b"{}", *, length=None, url=None):
        self.data, self.requested = data, []
        self.headers = {} if length is None else {"Content-Length": length}
        self.url = url or prepare.source_url("dev.json")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        self.requested.append(size)
        return self.data[:size]


def mock_opener(monkeypatch, responses):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            value = responses[len(calls) - 1]
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(prepare, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(prepare.time, "sleep", lambda seconds: None)
    return calls


@pytest.mark.parametrize("response", [Response(b"123456", length="6"), Response(b"123456"),
                                     Response(length="invalid"), Response(length="-1"), Response(b"")])
def test_download_size_caps_and_invalid_lengths(monkeypatch, response):
    calls = mock_opener(monkeypatch, [response])
    with pytest.raises(ValueError):
        prepare.download("dev.json", 5)
    assert len(calls) == 1
    assert all(size <= 6 for size in response.requested)


def test_download_bounded_retry_and_final_url_check(monkeypatch):
    url = prepare.source_url("dev.json")
    calls = mock_opener(monkeypatch, [URLError("temporary"), HTTPError(url, 503, "down", {}, None), Response()])
    assert prepare.download("dev.json", 5) == b"{}"
    assert len(calls) == 3
    assert all(item == (url, prepare.TIMEOUT_SECONDS) for item in calls)
    calls = mock_opener(monkeypatch, [HTTPError(url, 404, "absent", {}, None)])
    with pytest.raises(HTTPError):
        prepare.download("dev.json", 5)
    assert len(calls) == 1
    calls = mock_opener(monkeypatch, [Response(url="https://youtube.com/forbidden")])
    with pytest.raises(ValueError):
        prepare.download("dev.json", 5)
    assert len(calls) == 1


def test_duplicate_keys_nonfinite_and_nondev_index_rejected():
    for data in [b'{"id":1,"id":2}', b"NaN", b"Infinity"]:
        with pytest.raises(ValueError):
            prepare.parse_json(data)
    with pytest.raises(ValueError, match="Duplicate"):
        prepare.validate_index(encode([row(), row()]))
    bad = row()
    bad["split"] = "test"
    with pytest.raises(ValueError, match="non-development"):
        prepare.validate_index(encode([bad]))


@pytest.mark.parametrize("change", [
    {"id": "OTHERCASE01"}, {"split_name": "test"}, {"audio_length": 2.},
    {"aligned_midi_checksum": "notsha256"},
    {"youtube": {"ytid": "abcdefghijk", "timestampType": "relative", "start": 10., "end": 11.}},
    {"youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": 11., "end": 12.}},
    {"youtube": {"ytid": "ABCDEFGHIJK", "timestampType": "absolute", "start": 10., "end": 11.}},
    {"youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": True, "end": 11.}},
])
def test_metadata_identity_and_clip_validation(change):
    item = row()
    document = metadata(item, midi_bytes())
    document.update(change)
    with pytest.raises(ValueError):
        prepare.validate_metadata(encode(document), item)


def test_complete_preparation_uses_real_local_adapter_and_never_claims_ready(tmp_path, monkeypatch):
    files, requests = mock_dataset(monkeypatch)
    manifest = prepare.prepare(tmp_path / "references", 1)
    output = tmp_path / "references"
    assert manifest == json.loads((output / "manifest.json").read_text())
    assert manifest["schema"] == "akbo.multtipop.manifest"
    assert manifest["revision"] == prepare.REVISION
    assert manifest["partition"] == "dev"
    assert manifest["status"] == "references_prepared"
    assert manifest["benchmark_ready"] is False
    assert manifest["audio_rights_not_included"] is True
    assert manifest["prepared_count"] == 1 and manifest["failed_count"] == 0
    assert requests == ["dev.json", "dev/DEVCASE0001/meta.json", "dev/DEVCASE0001/aligned.mid"]
    case = manifest["cases"][0]
    assert case["audio"] is None and case["audio_sha256"] is None
    assert case["audio_rights_confirmed"] is False and case["audio_alignment_reviewed"] is False
    for key in ("metadata", "midi", "reference_audit", "mapping"):
        assert not Path(case[key]).is_absolute()
        assert (output / case[key]).is_file()
    assert case["midi_sha256"] == hashlib.sha256(files[requests[-1]]).hexdigest()
    audit = json.loads((output / case["reference_audit"]).read_text())
    assert audit["source"]["aligned_midi_checksum_verified"] is True
    assert {part["suggested_instrument"] for part in audit["parts"]} == {"piano", "drums"}
    drum = next(part for part in audit["parts"] if part["is_drum"])
    assert drum["program"] is None and drum["programs_seen"] == [16]
    mapping = json.loads((output / case["mapping"]).read_text())
    assert mapping["schema_version"] == 1 and mapping["reviewed"] is False and mapping["reviewer"] == ""
    assert set(mapping["assignments"]) == {"t1:c0:p0", "t2:c9:drums"}
    assert all(value == {"instrument": None, "role": None, "reason": ""} for value in mapping["assignments"].values())


@pytest.mark.parametrize("failure", ["checksum", "metadata", "network", "inspection"])
def test_case_failure_preserves_selection_and_continues_other_cases(tmp_path, monkeypatch, failure):
    rows = [row("DEVCASE0001"), row("DEVCASE0002", artist="Artist B")]
    selected = prepare.select_cases(prepare.validate_index(encode(rows)), 2)
    failed_id, good_id = selected[0]["id"], selected[1]["id"]
    files, requests = mock_dataset(monkeypatch, rows)
    if failure in {"checksum", "metadata"}:
        document = json.loads(files[f"dev/{failed_id}/meta.json"])
        document["aligned_midi_checksum" if failure == "checksum" else "id"] = "0" * 64 if failure == "checksum" else "WRONGID0001"
        files[f"dev/{failed_id}/meta.json"] = encode(document)
    elif failure == "network":
        files[f"dev/{failed_id}/aligned.mid"] = URLError("not available")
    else:
        original = prepare.inspect_reference

        def inspect(midi, metadata):
            if midi.parent.name == failed_id:
                raise ValueError("Malformed MIDI: dangling notes")
            return original(midi, metadata)

        monkeypatch.setattr(prepare, "inspect_reference", inspect)
    with pytest.raises(RuntimeError, match="1 selected reference"):
        prepare.prepare(tmp_path, 2)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["selection"]["selected_ids"] == [failed_id, good_id]
    assert manifest["status"] == "failed" and manifest["benchmark_ready"] is False
    assert manifest["prepared_count"] == manifest["failed_count"] == 1
    assert [case["status"] for case in manifest["cases"]] == ["failed", "reference_prepared"]
    assert (tmp_path / failed_id / "meta.json").is_file()
    assert not (tmp_path / failed_id / "mapping.json").exists()
    assert (tmp_path / good_id / "mapping.json").is_file()
    if failure in {"checksum", "inspection"}:
        assert (tmp_path / failed_id / "aligned.mid").is_file()
    assert f"dev/{good_id}/aligned.mid" in requests
    assert all(url == "dev.json" or url.startswith("dev/") and url.endswith(("meta.json", "aligned.mid")) for url in requests)


def test_global_index_failure_checkpoints_manifest(tmp_path, monkeypatch):
    files, _ = mock_dataset(monkeypatch)
    files["dev.json"] = b"not json"
    with pytest.raises(ValueError):
        prepare.prepare(tmp_path, 1)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["cases"] == []
    assert manifest["failures"][0]["stage"] == "validate_select_index"
    assert (tmp_path / "dev.json").read_bytes() == b"not json"


def test_unselected_signed_index_clip_does_not_abort_preparation(tmp_path, monkeypatch):
    rows = [row("DEVCASE0001"), row("DEVCASE0002", artist="Artist B")]
    chosen = prepare.select_cases(prepare.validate_index(encode(rows)), 1)[0]["id"]
    unselected = next(item for item in rows if item["id"] != chosen)
    unselected["youtube"].update(start=-.5, end=.5)
    validated = prepare.validate_index(encode(rows))
    assert next(item for item in validated if item["id"] != chosen)["youtube"]["start"] == -.5
    assert prepare.select_cases(validated, 1)[0]["id"] == chosen
    _, requests = mock_dataset(monkeypatch, rows)
    manifest = prepare.prepare(tmp_path, 1)
    assert manifest["status"] == "references_prepared"
    assert manifest["selection"]["selected_ids"] == [chosen]
    assert not any(f"/{unselected['id']}/" in relative for relative in requests)


def test_selected_signed_clip_fails_without_clamping_or_replacement(tmp_path, monkeypatch):
    rows = [row("DEVCASE0001"), row("DEVCASE0002", artist="Artist B")]
    original_order = [item["id"] for item in prepare.select_cases(prepare.validate_index(encode(rows)), 2)]
    signed = next(item for item in rows if item["id"] == original_order[0])
    signed["youtube"].update(start=-.5, end=.5)
    _, requests = mock_dataset(monkeypatch, rows)
    with pytest.raises(RuntimeError, match="1 selected reference"):
        prepare.prepare(tmp_path, 2)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["selection"]["selected_ids"] == original_order
    assert manifest["status"] == "failed" and manifest["benchmark_ready"] is False
    assert [case["status"] for case in manifest["cases"]] == ["failed", "reference_prepared"]
    assert manifest["cases"][0]["youtube"]["start"] == -.5
    assert manifest["failures"][0]["stage"] == "validate_metadata"
    assert f"dev/{original_order[0]}/aligned.mid" not in requests
    assert f"dev/{original_order[1]}/aligned.mid" in requests
    assert json.loads((tmp_path / original_order[0] / "meta.json").read_text())["youtube"]["start"] == -.5


def test_nonempty_output_and_symlink_rejected_before_network(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(prepare, "download", lambda *args: calls.append(args))
    output = tmp_path / "populated"
    output.mkdir()
    (output / "keep.txt").write_text("preserve")
    linked = tmp_path / "linked"
    linked.symlink_to(output, target_is_directory=True)
    for path in (output, linked, output / "keep.txt"):
        with pytest.raises(ValueError, match="new or empty"):
            prepare.prepare(path, 1)
    assert calls == [] and (output / "keep.txt").read_text() == "preserve"
