import json
from types import SimpleNamespace

import pytest

from backend.yourmt3_challenger import (
    CHECKPOINT_RELATIVE, CHECKPOINT_SHA256, REVISION, instrument_for_program,
    serialize_notes, verify_runtime,
    isolate_source_namespaces,
)


@pytest.mark.parametrize("program,is_drum,expected", [
    (0, False, "piano"), (7, False, "piano"), (8, False, "other"),
    (24, False, "guitar"), (31, False, "guitar"), (32, False, "bass"),
    (38, False, "bass"), (39, False, "bass"), (50, False, "other"),
    (80, False, "synthesizer"), (95, False, "synthesizer"), (96, False, "other"),
    (100, False, "vocal"), (101, False, "vocal"), (128, True, "drums"),
])
def test_mapping_respects_author_singing_extension(program, is_drum, expected):
    assert instrument_for_program(program, is_drum) == expected


def note(**overrides):
    return SimpleNamespace(**({"onset": .2, "offset": .8, "pitch": 60,
                              "program": 24, "velocity": 1, "is_drum": False} | overrides))


def test_serialization_preserves_true_octaves_and_raw_programs():
    result, audit = serialize_notes([note(pitch=72), note(), note(program=0)], 1.)
    assert len(result) == 3
    assert {row["pitch"] for row in result} == {60, 72}
    assert {row["program"] for row in result} == {0, 24}
    assert all(row["velocity_token"] == 1 for row in result)
    assert audit == {"invalid_or_padded_notes_rejected": 0, "offsets_clipped_to_audio": 0}


@pytest.mark.parametrize("change", [
    {"pitch": 60.5}, {"pitch": -1}, {"pitch": 128}, {"onset": -1}, {"onset": 1},
    {"onset": float("nan")}, {"offset": .1}, {"offset": float("inf")},
    {"program": -1}, {"program": 129}, {"velocity": 0},
])
def test_invalid_or_padded_notes_not_moved_into_audio(change):
    notes, audit = serialize_notes([note(**change)], 1.)
    assert notes == [] and audit["invalid_or_padded_notes_rejected"] == 1


def test_only_offset_is_clipped_not_onset_or_pitch():
    result, audit = serialize_notes([note(offset=2.)], 1.)
    assert result[0]["start"] == .2 and result[0]["end"] == 1. and result[0]["pitch"] == 60
    assert audit["offsets_clipped_to_audio"] == 1


def test_runtime_identity_rejected_before_import_or_unpickle(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"revision": "unknown"}))
    with pytest.raises(ValueError, match="identity"):
        verify_runtime(tmp_path)


@pytest.mark.parametrize("relative", ["../../secret", "/tmp/untrusted"])
def test_manifest_cannot_escape_source_root(tmp_path, relative):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "revision": REVISION, "checkpoint": CHECKPOINT_RELATIVE,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "files": [{"path": relative, "bytes": 0, "sha256": "a" * 64}]}))
    with pytest.raises(ValueError, match="pinned author snapshot"):
        verify_runtime(tmp_path)


def test_manifest_must_cover_source_and_pinned_weights(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "revision": REVISION, "checkpoint": CHECKPOINT_RELATIVE,
        "checkpoint_sha256": CHECKPOINT_SHA256, "files": []}))
    with pytest.raises(ValueError, match="pinned author snapshot"):
        verify_runtime(tmp_path)


def test_preimported_generic_namespace_is_rejected(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "utils.untrusted", SimpleNamespace())
    with pytest.raises(RuntimeError, match="fresh isolated worker"):
        isolate_source_namespaces(tmp_path)


def test_source_changed_during_inference_is_not_published(tmp_path, monkeypatch):
    """Stub model transport only; this is a provenance test, not ML quality."""
    from contextlib import nullcontext
    import sys
    import numpy as np
    import soundfile as sf
    from backend.yourmt3_challenger import transcribe_with_model
    path = tmp_path / "source.wav"
    sf.write(path, np.ones(16000) * .01, 16000, subtype="FLOAT")
    class Tensor:
        def __init__(self, value): self.value = value
        def unsqueeze(self, axis): return self
        def numpy(self): return self.value.reshape(1, -1)
        def __len__(self): return len(self.value)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(from_numpy=Tensor, inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules, "torchaudio", SimpleNamespace(functional=SimpleNamespace(resample=lambda x, *args: x)))
    monkeypatch.setitem(sys.modules, "utils.audio", SimpleNamespace(slice_padded_array=lambda *args: np.zeros((1, 32767))))
    monkeypatch.setitem(sys.modules, "utils.note2event", SimpleNamespace(mix_notes=lambda notes: []))
    monkeypatch.setitem(sys.modules, "utils.event2note", SimpleNamespace(merge_zipped_note_events_and_ties_to_notes=lambda x: ([], {})))
    def inference_file(**kwargs):
        sf.write(path, np.zeros(16000), 16000, subtype="FLOAT")
        return [np.zeros((1, 1, 1))], None
    model = SimpleNamespace(audio_cfg={"sample_rate": 16000, "input_frames": 32767},
                            inference_file=inference_file, task_manager=SimpleNamespace(
                                num_decoding_channels=1, detokenize_list_batches=lambda *args, **kwargs: ([], [], {})))
    with pytest.raises(RuntimeError, match="changed"):
        transcribe_with_model(model, path)
