"""Offline, review-gated MulTTiPop evaluation. No inference or network access.

Reference metadata is not a guarantee of musical correctness. Explicit human
part assignment and audio/version/alignment review are required before scoring.
Imported predictions are evidence, not proof that the declared engine ran.
"""
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import soundfile as sf

from .evaluation import compare_events
from .multtipop import build_reference, inspect_reference

REVISION = "341cd7f31d29d092862fe7bdd49f9ccdce7825b0"
INSTRUMENTS = ("vocal", "bass", "drums", "synthesizer", "guitar", "piano")
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 512 * 1024 * 1024
MAX_EVENTS = 30_000
DURATION_TOLERANCE = .05


class AuditError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _fail(code, message):
    raise AuditError(code, message)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _json(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        _fail("invalid_json_file", "JSON is missing, empty, or larger than 8 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("duplicate_json_key", f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def constant(value):
        _fail("nonfinite_json", f"Nonfinite JSON number: {value}")
    with path.open("rb") as stream:
        payload = stream.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        _fail("invalid_json_file", "JSON grew beyond 8 MiB during reading")
    try:
        result = json.loads(payload, object_pairs_hook=unique, parse_constant=constant)
    except (RecursionError, UnicodeError) as error:
        raise AuditError("invalid_json_file", "JSON encoding or nesting is invalid") from error
    if not isinstance(result, dict):
        _fail("invalid_document", "Expected a JSON object")
    return result


def _local_path(root, value, *, external_audio=False):
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        _fail("invalid_path", "A nonempty local file path is required")
    if "://" in value or "\\" in value:
        _fail("invalid_path", "URLs and backslash paths are not accepted")
    raw = Path(value)
    if ".." in raw.parts or (raw.is_absolute() and not external_audio):
        _fail("unsafe_path", "Reference/artifact paths must stay within their manifest directory")
    path = (raw if raw.is_absolute() else root / raw).resolve()
    if not external_audio or not raw.is_absolute():
        if not path.is_relative_to(root.resolve()):
            _fail("unsafe_path", "Path or symlink escapes its manifest directory")
    if not path.is_file():
        _fail("missing_file", "Referenced local file does not exist")
    return path


def _audio(path, duration, expected_hash=None):
    if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        _fail("audio_size", "Audio must be a nonempty file at most 512 MiB")
    before = file_sha256(path)
    if expected_hash is not None and (not _hash(expected_hash) or before != expected_hash):
        _fail("audio_hash", "Audio SHA-256 does not match the reviewed file")
    with sf.SoundFile(path) as audio:
        if not 1 <= audio.channels <= 2 or not 8000 <= audio.samplerate <= 192000:
            _fail("audio_format", "Audio must have 1–2 channels and sample rate 8–192 kHz")
        actual_duration = len(audio) / audio.samplerate
        if not 0 < actual_duration <= 600 or abs(actual_duration - duration) > DURATION_TOLERANCE:
            _fail("audio_duration", "Audio must be the already-cropped reference clip (within 50 ms)")
        for block in audio.blocks(blocksize=65536, dtype="float32", always_2d=True):
            if not np.isfinite(block).all():
                _fail("audio_nonfinite", "Audio contains NaN or infinity")
        result = {"sha256": before, "duration": actual_duration,
                  "sample_rate": audio.samplerate, "channels": audio.channels}
    if file_sha256(path) != before:
        _fail("input_changed", "Audio changed during validation")
    return result


def _manifest(path):
    path = Path(path).resolve()
    before = file_sha256(path)
    doc = _json(path)
    if file_sha256(path) != before:
        _fail("input_changed", "Manifest changed during reading")
    if (doc.get("schema") != "akbo.multtipop.manifest" or type(doc.get("schema_version")) is not int
            or doc["schema_version"] != 1 or doc.get("dataset") != "MulTTiPop"
            or doc.get("revision") != REVISION or doc.get("partition") != "dev"):
        _fail("manifest_scope", "Only the pinned MulTTiPop dev manifest version 1 is accepted; test stays sealed")
    cases = doc.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 50:
        _fail("case_count", "Manifest requires 1–50 cases")
    seen = set()
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", case_id) or case_id in seen:
            _fail("case_id", "Every case must have a unique safe ID")
        seen.add(case_id)
    return path, doc, before


def _issue(error):
    return {"code": getattr(error, "code", "invalid_input"), "message": str(error)}


def _audit_case(root, case):
    report = {"id": case["id"], "status": "blocked", "issues": [],
              "preparation_status": case.get("status"), "preparation_error": case.get("error")}
    reference = None
    paths = {}
    for field in ("midi", "metadata", "mapping"):
        try:
            paths[field] = _local_path(root, case.get(field))
        except (ValueError, OSError) as error:
            report["issues"].append({**_issue(error), "field": field})
    inspection = None
    if "midi" in paths and "metadata" in paths:
        try:
            metadata = _json(paths["metadata"])
            if metadata.get("id") != case["id"] or metadata.get("split_name") != "dev":
                _fail("reference_identity", "Reference ID/split does not match this dev case")
            inspection = inspect_reference(paths["midi"], paths["metadata"])
            for field in ("midi_sha256", "metadata_sha256"):
                if field in case and case[field] != inspection["source"][field]:
                    _fail("reference_hash", "Reference bytes differ from the pinned preparation manifest")
            report.update(duration=inspection["duration"], part_count=len(inspection["parts"]),
                          reference_source=inspection["source"], reference_audit=inspection["audit"])
        except (ValueError, OSError, EOFError, KeyError, TypeError) as error:
            report["issues"].append({**_issue(error), "field": "reference"})
    if "mapping" in paths:
        try:
            mapping_hash = file_sha256(paths["mapping"])
            mapping = _json(paths["mapping"])
            if (mapping.get("schema") != "akbo.multtipop.mapping" or mapping.get("case_id") != case["id"]
                    or type(mapping.get("schema_version")) is not int or mapping["schema_version"] != 1):
                _fail("mapping_identity", "Mapping schema/case ID mismatch")
            if mapping.get("reviewed") is not True or not isinstance(mapping.get("reviewer"), str) or not mapping["reviewer"].strip():
                _fail("mapping_unreviewed", "Human review and reviewer identity are required for every part assignment")
            if inspection is not None:
                if mapping.get("midi_sha256") != inspection["source"]["midi_sha256"]:
                    _fail("mapping_hash", "Mapping was reviewed against different MIDI bytes")
                reference = build_reference(paths["midi"], paths["metadata"], mapping.get("assignments"))
                if reference["source"] != inspection["source"] or file_sha256(paths["mapping"]) != mapping_hash:
                    _fail("input_changed", "Reference or mapping changed during validation")
                report.update(reference_notes={inst: len(reference["events"][inst]) for inst in INSTRUMENTS},
                              ignored_parts=reference["ignored_parts"],
                              mapping_sha256=mapping_hash,
                              reference_scope="declared-parts" if reference["ignored_parts"] else "all-reference-parts",
                              vocal_scope="all-assigned-lead-and-backing-notes-pooled")
        except (ValueError, OSError, KeyError, TypeError) as error:
            report["issues"].append({**_issue(error), "field": "mapping"})
    if case.get("audio_rights_confirmed") is not True:
        report["issues"].append({"code": "audio_rights_unconfirmed", "message": "Local audio usage rights have not been confirmed"})
    if case.get("audio_alignment_reviewed") is not True:
        report["issues"].append({"code": "audio_alignment_unreviewed", "message": "Same recording/version and clip alignment require human review"})
    try:
        path = _local_path(root, case.get("audio"), external_audio=True)
        if not _hash(case.get("audio_sha256")):
            _fail("audio_hash", "A reviewed audio SHA-256 is required")
        if inspection is not None:
            report["audio"] = _audio(path, inspection["duration"], case["audio_sha256"])
    except (ValueError, OSError, RuntimeError) as error:
        report["issues"].append({**_issue(error), "field": "audio"})
    if not report["issues"] and reference is not None:
        report["status"] = "ready"
    return report, reference


def _base_report(doc, manifest_hash):
    return {"schema": "akbo.multtipop.audit", "schema_version": 1, "dataset": "MulTTiPop",
            "revision": REVISION, "partition": "dev", "manifest_sha256": manifest_hash,
            "selection": doc.get("selection"), "cases": [],
            "limitations": ["Evaluation references, not infallible note-level ground truth.",
                            "Local checks verify byte consistency, not cryptographic proof of official dataset origin; retain the pinned preparation provenance.",
                            "No automatic audio download, alignment fitting, model inference, or training.",
                            "No test-set access. This development subset cannot establish generalization.",
                            "No TAB fingering, notation layout, lyrics, or source separation SDR score."]}


def _summarize(report, *, evaluated=False):
    success = "complete" if evaluated else "ready"
    total = len(report["cases"])
    ready = sum(case["status"] == success for case in report["cases"])
    report["summary"] = {"total_cases": total, "ready_cases": ready, "blocked_cases": total - ready}
    report["status"] = success if ready == total else "blocked"


def audit_manifest(manifest_path):
    path, doc, manifest_hash = _manifest(manifest_path)
    report = _base_report(doc, manifest_hash)
    report["cases"] = [_audit_case(path.parent, case)[0] for case in doc["cases"]]
    _summarize(report)
    if file_sha256(path) != manifest_hash:
        _fail("input_changed", "Manifest changed during audit")
    return report


def _prediction_events(root, entry, inst, duration):
    if not isinstance(entry, dict):
        _fail("prediction_entry", "Each instrument requires an explicit notes artifact and stem audio")
    notes_path = _local_path(root, entry.get("notes"))
    stem_path = _local_path(root, entry.get("audio"))
    notes_hash = file_sha256(notes_path)
    doc = _json(notes_path)
    if (doc.get("schema") != "akbo.pre-quantization-notes" or type(doc.get("schema_version")) is not int
            or doc["schema_version"] != 1 or doc.get("instrument") != inst):
        _fail("prediction_schema", "Expected this instrument's pre-quantization notes v1")
    if not _finite(doc.get("duration")) or abs(doc["duration"] - duration) > DURATION_TOLERANCE:
        _fail("prediction_duration", "Prediction duration differs from the reference clip")
    semantics = doc.get("semantics", {})
    if (not isinstance(semantics, dict) or semantics.get("stage") != "input-to-notation-after-instrument-postprocessing"
            or semantics.get("time_unit") != "seconds"
            or semantics.get("time_origin") != "source-audio-start-before-score-offset"
            or semantics.get("ground_truth") is not False or semantics.get("reflects_manual_score_edits") is not False):
        _fail("prediction_semantics", "Only unedited, clip-relative pre-quantization predictions are accepted")
    source = doc.get("source", {})
    if (not isinstance(source, dict) or source.get("kind") != "stem" or source.get("instrument") != inst
            or source.get("file") != f"{inst}.wav" or not _hash(source.get("sha256"))):
        _fail("prediction_source", "Prediction source must identify this instrument's stem and SHA-256")
    audio = _audio(stem_path, duration, source["sha256"])
    if abs(doc["duration"] - audio["duration"]) > 1 / audio["sample_rate"] + 1e-9:
        _fail("prediction_duration", "Notes duration must match its exact source audio")
    provenance = doc.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("engine"), str) or not provenance["engine"].strip():
        _fail("prediction_provenance", "Transcription engine provenance is required")
    events = doc.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS or type(doc.get("event_count")) is not int or doc["event_count"] != len(events):
        _fail("prediction_events", "Event count must match a list of at most 30,000 events")
    if doc.get("event_fields") != ["start", "end", "pitch", "amplitude"]:
        _fail("prediction_fields", "Unexpected event field order")
    for event in events:
        if (not isinstance(event, list) or len(event) != 4 or not all(_finite(value) for value in event)):
            _fail("prediction_events", "Events require four finite numeric values, not booleans")
        start, end, pitch, amp = event
        if not 0 <= start < end <= doc["duration"] or int(pitch) != pitch or not 0 <= pitch <= 127 or not 0 < amp <= 1:
            _fail("prediction_events", "Event exceeds time, pitch, or amplitude bounds")
    if file_sha256(notes_path) != notes_hash:
        _fail("input_changed", "Prediction file changed during validation")
    return events, {"notes_sha256": notes_hash, "audio": audio, "provenance": provenance}


def _aggregate(cases):
    result = {}
    for inst in INSTRUMENTS:
        metrics = [case["metrics"][inst] for case in cases]
        ref = sum(item["reference_notes"] for item in metrics)
        est = sum(item["estimated_notes"] for item in metrics)
        hits = sum(item["matched_notes"] for item in metrics)
        result[inst] = {"reference_notes": ref, "estimated_notes": est, "matched_notes": hits,
                        "missing_notes": ref - hits, "extra_notes": est - hits,
                        "precision": hits / est if est else 0., "recall": hits / ref if ref else 0.,
                        "f1": 2 * hits / (ref + est) if ref + est else 0.,
                        "reference_present": ref > 0}
        if inst != "drums":
            offsets = sum(item["duration_diagnostics"]["matched_with_valid_offset"] for item in metrics)
            result[inst]["duration_diagnostics"] = {"method": "existing-onset-pairs-offset-check-v1",
                                                    "matched_with_valid_offset": offsets,
                                                    "f1": 2 * offsets / (ref + est) if ref + est else 0.}
    return {"case_count": len(cases), "by_instrument": result,
            "scope": "declared-reference-parts; inspect ignored_parts and vocal_scope per case"}


def evaluate_predictions(manifest_path, predictions_path):
    path, doc, manifest_hash = _manifest(manifest_path)
    prediction_path = Path(predictions_path).resolve()
    predictions_hash = file_sha256(prediction_path)
    predictions = _json(prediction_path)
    if file_sha256(prediction_path) != predictions_hash:
        _fail("input_changed", "Predictions manifest changed during reading")
    if (predictions.get("schema") != "akbo.multtipop.predictions" or type(predictions.get("schema_version")) is not int
            or predictions["schema_version"] != 1):
        _fail("prediction_manifest", "Expected akbo.multtipop.predictions version 1")
    entries = predictions.get("cases")
    if not isinstance(entries, list) or len(entries) > 50:
        _fail("prediction_cases", "Predictions require a bounded cases list")
    by_id = {}
    for entry in entries:
        case_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(case_id, str) or case_id in by_id:
            _fail("prediction_cases", "Predictions require unique case IDs")
        by_id[case_id] = entry
    if set(by_id) != {case["id"] for case in doc["cases"]}:
        _fail("prediction_coverage", "Prediction IDs must exactly match every selected case; no favorable subset scoring")
    report = _base_report(doc, manifest_hash)
    report.update(schema="akbo.multtipop.evaluation", predictions_sha256=predictions_hash, aggregate=None,
                  metric={"matching": "chronological-same-MIDI-pitch-onset-v1", "onset_tolerance_seconds": .05,
                          "drums": "raw-GM-pitch-no-kit-alias-collapse", "pitched_offset_ratio": .2},
                  pipeline_verification="user-declared SAM provenance plus byte hashes; execution chain not independently verified")
    for case in doc["cases"]:
        item, reference = _audit_case(path.parent, case)
        if item["status"] == "ready":
            try:
                entry = by_id[case["id"]]
                if entry.get("mix_sha256") != item["audio"]["sha256"]:
                    _fail("prediction_mix", "Predictions refer to a different original mix")
                provenance = entry.get("separation_provenance", {})
                if (entry.get("pipeline") != "sam-separated-stems" or entry.get("separation_reviewed") is not True
                        or not isinstance(provenance, dict) or provenance.get("engine") != "sam-audio"):
                    _fail("separation_provenance", "Explicit SAM separation provenance and review are required")
                instruments = entry.get("instruments")
                if not isinstance(instruments, dict) or set(instruments) != set(INSTRUMENTS):
                    _fail("prediction_instruments", "All six instrument artifacts are required, including explicit empty results")
                metrics, evidence = {}, {}
                for inst in INSTRUMENTS:
                    events, evidence[inst] = _prediction_events(prediction_path.parent, instruments[inst], inst, reference["duration"])
                    metrics[inst] = compare_events(reference["events"][inst], events, include_errors=True,
                                                   duration_tolerance_ratio=None if inst == "drums" else .2)
                item.update(status="complete", metrics=metrics, evidence=evidence, separation_provenance=provenance)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                item["status"] = "blocked"
                item["issues"].append(_issue(error))
        report["cases"].append(item)
    _summarize(report, evaluated=True)
    if report["status"] == "complete":
        report["aggregate"] = _aggregate(report["cases"])
    if file_sha256(path) != manifest_hash or file_sha256(prediction_path) != predictions_hash:
        _fail("input_changed", "Manifest or predictions changed during evaluation")
    return report
