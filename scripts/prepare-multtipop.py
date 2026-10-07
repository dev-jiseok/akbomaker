"""Prepare public MulTTiPop development MIDI/metadata for manual evaluation review.

This command never fetches audio, YouTube pages, models, or the test partition.
Downloaded references are NOT benchmark-ready: audio rights/alignment and every
MIDI part assignment still need explicit human review. Do not train on this
evaluation selection. No performance or reference-quality filter selects cases.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import ssl
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REVISION = "341cd7f31d29d092862fe7bdd49f9ccdce7825b0"
DATASET_URL = "https://huggingface.co/datasets/gclef-cmu/multtipop"
BASE = f"{DATASET_URL}/resolve/{REVISION}/"
PROJECT_URL = "https://gclef-cmu.org/multtipop/"
SELECTION_VERSION = "unique-artist-then-underrepresented-known-genre-v1"
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 512 * 1024
MAX_MIDI_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def _identifier(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("Dataset case ID is missing or unsafe")
    return value


def source_url(relative):
    """Construct only pinned dev metadata/MIDI URLs, never metadata audio links."""
    if relative != "dev.json":
        bits = relative.split("/")
        if len(bits) != 3 or bits[0] != "dev" or bits[2] not in {"meta.json", "aligned.mid"}:
            raise ValueError("Only dev.json and development metadata/MIDI may be requested")
        _identifier(bits[1])
    return BASE + relative


def validate_source_url(url):
    """Fail closed on external, unpinned, test, or audio redirects."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "huggingface.co"
            or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("Dataset request/redirect must stay on public Hugging Face HTTPS")
    prefixes = (f"/datasets/gclef-cmu/multtipop/resolve/{REVISION}/",
                f"/api/resolve-cache/datasets/gclef-cmu/multtipop/{REVISION}/")
    for prefix in prefixes:
        if parsed.path.startswith(prefix):
            # HF's cache redirect percent-encodes path separators (dev%2FID%2Fmeta.json).
            # Verify host/revision before decoding, then decode exactly once and
            # reapply the strict dev/ID/file allowlist. Residual '%' (including
            # double encoding), traversal, and test/audio paths remain invalid.
            relative = unquote(parsed.path[len(prefix):], errors="strict")
            source_url(relative)
            return url
    raise ValueError("Dataset request/redirect must remain pinned to development metadata/MIDI")


class PinnedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(relative, max_bytes):
    """Bounded public download; no tokens, audio URLs, or arbitrary redirects."""
    url = source_url(relative)
    context = ssl.create_default_context(cafile=certifi.where())
    opener = build_opener(HTTPSHandler(context=context), PinnedRedirectHandler())
    request = Request(url, headers={"User-Agent": "akbo-multtipop-evaluation/1"})
    for attempt in range(MAX_ATTEMPTS):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                validate_source_url(response.geturl())
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        length = int(length)
                    except (TypeError, ValueError) as error:
                        raise ValueError("Invalid dataset Content-Length") from error
                    if not 0 <= length <= max_bytes:
                        raise ValueError("Dataset response exceeds its size limit")
                data = response.read(max_bytes + 1)
                if not data or len(data) > max_bytes:
                    raise ValueError("Dataset response is empty or exceeds its size limit")
                return data
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == MAX_ATTEMPTS - 1:
                raise
        except (URLError, TimeoutError):
            if attempt == MAX_ATTEMPTS - 1:
                raise
        time.sleep(.25 * 2 ** attempt)
    raise RuntimeError("Download attempts exhausted")  # Defensive; loop raises above.


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Dataset JSON has duplicate object keys")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Dataset JSON contains nonfinite constant {value}")


def parse_json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid dataset JSON") from error


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def validate_clip(value, *, allow_signed_start=False):
    if (not isinstance(value, dict) or not isinstance(value.get("ytid"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{11}", value["ytid"])
            or value.get("timestampType") != "absolute"):
        raise ValueError("Dataset YouTube clip identifier/timestamp type is invalid")
    start, end = _number(value.get("start"), "clip start"), _number(value.get("end"), "clip end")
    if (not allow_signed_start and start < 0) or not 0 < end - start <= 600:
        raise ValueError("Dataset clip must be a positive excerpt of at most 600 seconds")
    return {"ytid": value["ytid"], "timestampType": "absolute", "start": start, "end": end}


def _text(value, label, *, optional=False):
    if optional and value is None:
        return ""
    if not isinstance(value, str) or len(value) > 1000 or not optional and not value.strip():
        raise ValueError(f"Dataset {label} must be a bounded string")
    return value.strip()


def validate_index(data):
    rows = parse_json(data)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("Development index must contain between 1 and 1000 cases")
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Development index rows must be objects")
        identifier = _identifier(row.get("id"))
        if identifier in seen:
            raise ValueError("Duplicate development case ID")
        seen.add(identifier)
        if row.get("split") != "dev":
            raise ValueError("Development index contains a non-development case")
        # Preserve only descriptive selection/identity fields. Scores, MIDI
        # quality, predictions, audio_link, labels, and preview are not read.
        result.append({"id": identifier, "artist": _text(row.get("artist"), "artist"),
                       "name": _text(row.get("name"), "name"), "split": "dev",
                       "section": _text(row.get("section"), "section", optional=True),
                       "genre_everynoise": _text(row.get("genre_everynoise"), "genre", optional=True),
                       # Some official index crops start before zero. Preserve
                       # their signed clock for metadata-only selection; never
                       # clamp or filter them. A selected case still undergoes
                       # strict metadata/adapter validation and is retained as
                       # failed until its audio padding/alignment is understood.
                       "youtube": validate_clip(row.get("youtube"), allow_signed_start=True)})
    return result


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def select_cases(rows, count):
    """Fixed metadata-only greedy diversity; unaffected by input ordering.

    Unique artists precede repeated artists; known, underrepresented genres
    precede unknown/repeated genres. SHA256(ID) breaks remaining ties. Each
    prefix is stable across count choices, and no reference is inspected here.
    """
    if not 1 <= count <= 50 or count > len(rows):
        raise ValueError("Count must be 1..50 and no larger than the development index")
    remaining = list(rows)
    artists, genres, selected = Counter(), Counter(), []
    while len(selected) < count:
        def rank(row):
            artist, genre = _normalized(row["artist"]), _normalized(row["genre_everynoise"])
            tie = hashlib.sha256(f"{SELECTION_VERSION}:{row['id']}".encode()).hexdigest()
            return (artists[artist] > 0, not bool(genre), genres[genre], artists[artist], tie)
        row = min(remaining, key=rank)
        remaining.remove(row)
        selected.append(row)
        artists[_normalized(row["artist"])] += 1
        genres[_normalized(row["genre_everynoise"])] += 1
    return selected


def validate_metadata(data, row):
    meta = parse_json(data)
    if not isinstance(meta, dict) or meta.get("id") != row["id"] or meta.get("split_name") != "dev":
        raise ValueError("Case metadata ID/split does not match its selected development case")
    clip = validate_clip(meta.get("youtube"))
    expected = row["youtube"]
    if (clip["ytid"] != expected["ytid"] or abs(clip["start"] - expected["start"]) > .01
            or abs(clip["end"] - expected["end"]) > .01):
        raise ValueError("Case metadata clip does not match the development index")
    duration = _number(meta.get("audio_length"), "audio_length")
    if not 0 < duration <= 600 or abs(duration - (clip["end"] - clip["start"])) > .01:
        raise ValueError("Case audio_length disagrees with its absolute clip bounds")
    checksum = meta.get("aligned_midi_checksum")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise ValueError("Case metadata requires an aligned MIDI SHA256 checksum")
    return meta


def inspect_reference(midi_path, metadata_path):
    # This adapter parses local references only; importing it loads no model.
    from backend.multtipop import inspect_reference as inspect
    return inspect(midi_path, metadata_path)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, document):
    data = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return sha256(data)


def ensure_empty_output(output):
    if output.is_symlink() or output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Use a new or empty --output directory; existing preparation files are never overwritten")


def _manifest(count):
    return {
        "schema": "akbo.multtipop.manifest", "schema_version": 1,
        "dataset": "MulTTiPop", "revision": REVISION, "partition": "dev",
        "status": "preparing", "benchmark_ready": False,
        "license": "CC-BY-4.0", "audio_rights_not_included": True,
        "attribution": {"title": "MulTTiPop: A Multitrack Transcription Dataset for Pop Music",
                        "authors": ["Nathan Pruyne", "Benjamin Stoler", "William Chen", "Chien-yu Huang",
                                    "Shinji Watanabe", "Chris Donahue"],
                        "source": DATASET_URL, "license_url": "https://creativecommons.org/licenses/by/4.0/",
                        "derived_midi_source": {"name": "Lakh MIDI Dataset", "author": "Colin Raffel",
                                                "url": "https://colinraffel.com/projects/lmd/"}},
        "source_urls": {"dataset": DATASET_URL, "project": PROJECT_URL, "index": source_url("dev.json"),
                        "dataset_card": f"{DATASET_URL}/blob/{REVISION}/README.md"},
        "selection": {"method": SELECTION_VERSION, "fields": ["id", "artist", "genre_everynoise"],
                      "requested_count": count, "selected_ids": [], "index": "dev.json"},
        "limitations": ["Public metadata and aligned MIDI only; no audio is included or requested.",
                        "CC-BY-4.0 reference licensing does not grant rights to the original audio.",
                        "Publisher recommends evaluation use, not training; this is a dev-only diagnostic subset.",
                        "Lakh-derived arrangements and GM programs are not verified six-instrument audio labels.",
                        "Every part needs manual mapping; GM suggestions are not accepted assignments.",
                        "Downloaded references do not establish audio alignment, audio rights, or benchmark readiness.",
                        "Failed selected cases are retained, never replaced using reference quality."],
        "cases": [], "failures": [], "prepared_count": 0, "failed_count": 0,
    }


def _case(row):
    identifier = row["id"]
    clip = row["youtube"]
    return {"id": identifier, "artist": row["artist"], "title": row["name"], "section": row["section"],
            "genre": row["genre_everynoise"] or None, "duration": clip["end"] - clip["start"],
            "folder": identifier, "metadata": f"{identifier}/meta.json", "midi": f"{identifier}/aligned.mid",
            "reference_audit": f"{identifier}/audit.json", "mapping": f"{identifier}/mapping.json",
            "status": "pending", "youtube": clip,
            "audio": None, "audio_sha256": None, "audio_rights_confirmed": False,
            "audio_alignment_reviewed": False,
            "source_urls": {"metadata": source_url(f"dev/{identifier}/meta.json"),
                            "midi": source_url(f"dev/{identifier}/aligned.mid")}}


def _error(error, stage, identifier=None):
    return {"case_id": identifier, "stage": stage, "type": type(error).__name__, "message": str(error)[:1000]}


def prepare(output, count=24):
    if not 1 <= count <= 50:
        raise ValueError("Count must be between 1 and 50")
    output = Path(output)
    ensure_empty_output(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(count)
    manifest_path = output / "manifest.json"
    write_json(manifest_path, manifest)
    stage = "download_index"
    try:
        index = download("dev.json", MAX_INDEX_BYTES)
        (output / "dev.json").write_bytes(index)
        manifest["selection"]["index_sha256"] = sha256(index)
        stage = "validate_select_index"
        selected = select_cases(validate_index(index), count)
        manifest["selection"]["selected_ids"] = [row["id"] for row in selected]
        manifest["cases"] = [_case(row) for row in selected]
        write_json(manifest_path, manifest)
    except Exception as error:
        manifest["status"] = "failed"
        manifest["failures"].append(_error(error, stage))
        write_json(manifest_path, manifest)
        raise
    for row, case in zip(selected, manifest["cases"]):
        identifier = case["id"]
        stage = "create_case_directory"
        try:
            folder = output / identifier
            folder.mkdir()
            stage = "download_metadata"
            metadata = download(f"dev/{identifier}/meta.json", MAX_METADATA_BYTES)
            (folder / "meta.json").write_bytes(metadata)
            case["metadata_sha256"] = sha256(metadata)
            stage = "validate_metadata"
            meta = validate_metadata(metadata, row)
            case["duration"] = float(meta["audio_length"])
            stage = "download_midi"
            midi = download(f"dev/{identifier}/aligned.mid", MAX_MIDI_BYTES)
            (folder / "aligned.mid").write_bytes(midi)
            case["midi_sha256"] = sha256(midi)
            stage = "verify_midi_checksum"
            if case["midi_sha256"] != meta["aligned_midi_checksum"].lower():
                raise ValueError("Aligned MIDI SHA256 does not match its official metadata checksum")
            stage = "inspect_reference"
            audit = inspect_reference(folder / "aligned.mid", folder / "meta.json")
            case["reference_audit_sha256"] = write_json(folder / "audit.json", audit)
            stage = "write_mapping_template"
            mapping = {"schema": "akbo.multtipop.mapping", "schema_version": 1, "case_id": identifier,
                       "midi_sha256": case["midi_sha256"], "reviewed": False, "reviewer": "",
                       "assignments": {part["part_id"]: {"instrument": None, "role": None, "reason": ""}
                                       for part in audit["parts"]}}
            case["mapping_template_sha256"] = write_json(folder / "mapping.json", mapping)
            case["status"] = "reference_prepared"
            manifest["prepared_count"] += 1
            print(f"Prepared reference {identifier}: {len(audit['parts'])} parts; manual review and audio still required", flush=True)
        except Exception as error:
            failure = _error(error, stage, identifier)
            case.update(status="failed", error=failure)
            manifest["failures"].append(failure)
            manifest["failed_count"] += 1
            print(f"Failed reference {identifier} ({stage}): {error}", file=sys.stderr, flush=True)
        write_json(manifest_path, manifest)
    manifest["status"] = "failed" if manifest["failures"] else "references_prepared"
    write_json(manifest_path, manifest)
    if manifest["failures"]:
        raise RuntimeError(f"{manifest['failed_count']} selected reference(s) failed; all selected cases retained in {manifest_path}")
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New or empty directory for references and review templates")
    parser.add_argument("--count", type=int, default=24, help="Number of fixed, metadata-selected development cases (1..50; default 24)")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 50:
        parser.error("--count must be between 1 and 50")
    return args


def main(argv=None):
    args = parse_args(argv)
    prepare(args.output, args.count)
    print(f"Reference preparation complete: {args.output / 'manifest.json'} (NOT benchmark-ready)")


if __name__ == "__main__":
    main()
