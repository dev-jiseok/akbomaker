import copy
import hashlib
import json

import mido
import pytest

from backend import multtipop
from backend.config import INSTRUMENTS


def pair(tmp_path, tracks, *, duration=4., start=75., midi_type=1, ticks_per_beat=480, metadata_changes=None):
    midi = mido.MidiFile(type=midi_type, ticks_per_beat=ticks_per_beat)
    midi.tracks.extend(mido.MidiTrack(messages) for messages in tracks)
    midi_path, metadata_path = tmp_path / "aligned.mid", tmp_path / "meta.json"
    midi.save(midi_path)
    metadata = {"id": "clip_001", "split_name": "dev", "tempo": 173,
                "youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": start, "end": start + duration},
                "audio_length": duration, "aligned_midi_checksum": hashlib.sha256(midi_path.read_bytes()).hexdigest()}
    metadata.update(metadata_changes or {})
    metadata_path.write_text(json.dumps(metadata))
    return midi_path, metadata_path


def note(pitch=60, channel=0, start=0, length=480, velocity=90):
    return [mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=start),
            mido.Message("note_off", channel=channel, note=pitch, time=length)]


def mapping(audit, **overrides):
    return {part["part_id"]: {"instrument": overrides.get(part["part_id"], part["suggested_instrument"] or "ignore"),
                              "role": None, "reason": "Reviewed source part"} for part in audit["parts"]}


def test_uses_global_midi_tempo_not_song_bpm_or_absolute_youtube_crop(tmp_path):
    paths = pair(tmp_path, [[mido.MetaMessage("set_tempo", tempo=1_000_000)], note(start=480)], start=125.)
    inspected = multtipop.inspect_reference(*paths)
    result = multtipop.build_reference(*paths, mapping(inspected))
    assert result["duration"] == 4
    assert result["events"]["piano"] == [[1., 2., 60, 90 / 127]]
    assert result["source"]["youtube"]["start"] == 125
    assert result["source"]["aligned_midi_checksum_verified"]
    assert result["source"]["dataset_revision"] == multtipop.DATASET_REVISION
    assert result["source"]["midi_sha256"] == hashlib.sha256(paths[0].read_bytes()).hexdigest()
    assert result["source"]["metadata_sha256"] == hashlib.sha256(paths[1].read_bytes()).hexdigest()
    assert set(result["events"]) == set(INSTRUMENTS)


def test_multiple_tempo_changes_in_another_track_apply_to_note_duration(tmp_path):
    paths = pair(tmp_path, [[mido.MetaMessage("set_tempo", tempo=500_000),
                            mido.MetaMessage("set_tempo", tempo=1_000_000, time=480),
                            mido.MetaMessage("set_tempo", tempo=250_000, time=480)], note(length=1440)])
    audit = multtipop.inspect_reference(*paths)
    assert multtipop.build_reference(*paths, mapping(audit))["events"]["piano"][0][:2] == [0., 1.75]
    assert [item["seconds"] for item in audit["audit"]["tempo_map"]] == [0., 0., .5, 1.5]


def test_type_zero_default_tempo_and_velocity_zero_noteoff(tmp_path):
    paths = pair(tmp_path, [[mido.Message("note_on", note=60, velocity=80),
                            mido.Message("note_on", note=60, velocity=0, time=480)]], midi_type=0)
    audit = multtipop.inspect_reference(*paths)
    assert audit["audit"]["midi_type"] == 0
    assert multtipop.build_reference(*paths, mapping(audit))["events"]["piano"] == [[0., .5, 60, 80 / 127]]


def test_percussion_keeps_raw_gm_beyond_kit_and_program_changes_do_not_split_drum_part(tmp_path):
    tracks = [[mido.Message("program_change", channel=9, program=24), *note(33, 9),
               mido.Message("program_change", channel=9, program=80), *note(54, 9), *note(70, 9)]]
    paths = pair(tmp_path, tracks)
    audit = multtipop.inspect_reference(*paths)
    assert len(audit["parts"]) == 1
    part = audit["parts"][0]
    assert part["part_id"] == "t0:c9:drums" and part["program"] is None
    assert part["programs_seen"] == [24, 80]
    assert part["pitch_range"] == [33, 70] and part["suggested_instrument"] == "drums"
    assert [event[2] for event in multtipop.build_reference(*paths, mapping(audit))["events"]["drums"]] == [33, 54, 70]


def test_program_changes_make_stable_parts_and_noteoff_retains_starting_program(tmp_path):
    tracks = [[mido.Message("program_change", channel=0, program=24),
               mido.Message("note_on", channel=0, note=64, velocity=90),
               mido.Message("program_change", channel=0, program=32, time=240),
               mido.Message("note_off", channel=0, note=64, time=240), *note(40)]]
    paths = pair(tmp_path, tracks)
    audit = multtipop.inspect_reference(*paths)
    assert [p["part_id"] for p in audit["parts"]] == ["t0:c0:p24", "t0:c0:p32"]
    assert [p["suggested_instrument"] for p in audit["parts"]] == ["guitar", "bass"]
    result = multtipop.build_reference(*paths, mapping(audit))
    assert result["events"]["guitar"][0][:3] == [0., .5, 64]
    assert result["events"]["bass"][0][:3] == [.5, 1., 40]


def test_channel_program_state_is_global_but_same_pitch_track_voices_remain_distinct(tmp_path):
    paths = pair(tmp_path, [[mido.Message("program_change", channel=0, program=24)], note(64), note(64), note(76)])
    audit = multtipop.inspect_reference(*paths)
    assert [p["part_id"] for p in audit["parts"]] == ["t1:c0:p24", "t2:c0:p24", "t3:c0:p24"]
    result = multtipop.build_reference(*paths, mapping(audit))
    assert [e[2] for e in result["events"]["guitar"]] == [64, 64, 76]
    assert len(result["part_events"]) == 3


def test_same_pitch_overlap_is_fifo_paired_and_audited_without_deduplication(tmp_path):
    tracks = [[mido.Message("note_on", note=60, velocity=80),
               mido.Message("note_on", note=60, velocity=60, time=120),
               mido.Message("note_off", note=60, time=120), mido.Message("note_off", note=60, time=120)]]
    paths = pair(tmp_path, tracks)
    audit = multtipop.inspect_reference(*paths)
    assert audit["audit"]["overlapping_note_on_count"] == 1 and audit["audit"]["warnings"]
    result = multtipop.build_reference(*paths, mapping(audit))
    assert [e[:2] for e in result["events"]["piano"]] == [[0., .25], [.125, .375]]


@pytest.mark.parametrize("program,suggestion", [(0, "piano"), (7, "piano"), (24, "guitar"), (31, "guitar"),
                                                (32, "bass"), (39, "bass"), (80, "synthesizer"), (103, "synthesizer"),
                                                (53, None), (79, None), (104, None)])
def test_suggestions_never_auto_assign_choir_as_vocal(tmp_path, program, suggestion):
    paths = pair(tmp_path, [[mido.Message("program_change", program=program), *note()]])
    assert multtipop.inspect_reference(*paths)["parts"][0]["suggested_instrument"] == suggestion


def test_all_parts_require_explicit_review_including_ignored_and_vocal_roles(tmp_path):
    paths = pair(tmp_path, [[mido.Message("program_change", program=53), *note()]])
    audit = multtipop.inspect_reference(*paths)
    part_id = audit["parts"][0]["part_id"]
    for assignments in ({}, {"unknown": {"instrument": "ignore", "reason": "reviewed"}},
                        {part_id: {"instrument": None, "reason": ""}},
                        {part_id: {"instrument": "ignore", "reason": " "}},
                        {part_id: {"instrument": "vocal", "reason": "reviewed"}}):
        with pytest.raises(ValueError):
            multtipop.build_reference(*paths, assignments)
    result = multtipop.build_reference(*paths, {part_id: {"instrument": "vocal", "role": "lead", "reason": "Reviewed melody track"}})
    assert len(result["events"]["vocal"]) == 1
    ignored = multtipop.build_reference(*paths, {part_id: {"instrument": "ignore", "reason": "Unsupported choir sound"}})
    assert ignored["ignored_parts"][0]["part_id"] == part_id and not any(ignored["events"].values())


@pytest.mark.parametrize("channel,instrument", [(9, "piano"), (0, "drums")])
def test_never_maps_percussion_to_pitched_or_pitched_to_drums(tmp_path, channel, instrument):
    paths = pair(tmp_path, [note(channel=channel)])
    audit = multtipop.inspect_reference(*paths)
    assignments = mapping(audit)
    next(iter(assignments.values()))["instrument"] = instrument
    with pytest.raises(ValueError, match="Percussion"):
        multtipop.build_reference(*paths, assignments)


def test_mapping_hash_is_order_independent_but_changes_with_reviewed_mapping(tmp_path):
    paths = pair(tmp_path, [note(), note(67, channel=1)])
    audit = multtipop.inspect_reference(*paths)
    assignments = mapping(audit)
    before = copy.deepcopy(assignments)
    a = multtipop.build_reference(*paths, assignments)
    b = multtipop.build_reference(*paths, dict(reversed(list(assignments.items()))))
    assert a["mapping_sha256"] == b["mapping_sha256"] and assignments == before
    next(iter(assignments.values()))["reason"] = "Another reviewed reason"
    assert multtipop.build_reference(*paths, assignments)["mapping_sha256"] != a["mapping_sha256"]


@pytest.mark.parametrize("messages,error", [
    ([mido.Message("note_on", note=60, velocity=80)], "dangling"),
    ([mido.Message("note_off", note=60)], "unpaired"),
    (note(length=0), "positive duration"),
    (note(start=4000), "onset is outside"),
    (note(length=5000), "extends outside"),
])
def test_invalid_or_truncated_notes_are_rejected_not_invented(tmp_path, messages, error):
    with pytest.raises(ValueError, match=error):
        multtipop.inspect_reference(*pair(tmp_path, [messages]))


def test_tiny_clip_end_rounding_is_explicitly_audited(tmp_path):
    paths = pair(tmp_path, [note(length=960)], duration=.99)
    audit = multtipop.inspect_reference(*paths)
    result = multtipop.build_reference(*paths, mapping(audit))
    assert result["events"]["piano"][0][1] == .99
    assert audit["audit"]["clipped_note_count"] == 1 and audit["audit"]["warnings"]


@pytest.mark.parametrize("change", [
    {"audio_length": float("nan")}, {"audio_length": float("inf")}, {"audio_length": 0}, {"audio_length": 601},
    {"audio_length": 10 ** 1000},
    {"audio_length": True}, {"audio_length": 3}, {"aligned_midi_checksum": "0" * 64},
    {"aligned_midi_checksum": "../aligned.mid"}, {"id": "../../secret"}, {"split_name": "../train"},
    {"youtube": {"ytid": "abcdefghijk", "timestampType": "relative", "start": 75, "end": 79}},
    {"youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": float("nan"), "end": 79}},
    {"youtube": {"ytid": "abcdefghijk", "timestampType": "absolute", "start": 79, "end": 75}},
    {"youtube": {"ytid": "../../secret", "timestampType": "absolute", "start": 75, "end": 79}},
])
def test_metadata_finite_bounds_hash_and_crop_identity_are_verified(tmp_path, change):
    paths = pair(tmp_path, [note()], metadata_changes=change)
    with pytest.raises(ValueError):
        multtipop.inspect_reference(*paths)


@pytest.mark.parametrize("midi_type,tpb", [(2, 480), (1, -24)])
def test_rejects_asynchronous_and_smpte_clocks(tmp_path, midi_type, tpb):
    with pytest.raises(ValueError):
        multtipop.inspect_reference(*pair(tmp_path, [note()], midi_type=midi_type, ticks_per_beat=tpb))


def test_resource_limits_and_paths_are_bounded(tmp_path, monkeypatch):
    paths = pair(tmp_path, [note()])
    linked = tmp_path / "linked.mid"
    linked.symlink_to(paths[0])
    with pytest.raises(ValueError, match="symbolic"):
        multtipop.inspect_reference(linked, paths[1])
    with pytest.raises(ValueError):
        multtipop.inspect_reference(tmp_path, paths[1])
    monkeypatch.setattr(multtipop, "MAX_MESSAGES", 1)
    with pytest.raises(ValueError, match="message count"):
        multtipop.inspect_reference(*paths)
    monkeypatch.setattr(multtipop, "MAX_MESSAGES", 100_000)
    monkeypatch.setattr(multtipop, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="2 MB"):
        multtipop.inspect_reference(*paths)


def test_metadata_embedded_filenames_are_never_read_or_exposed(tmp_path):
    paths = pair(tmp_path, [note()], metadata_changes={"chosen_lmd_midi_filename": "/private/secrets", "tempo": 1})
    output = multtipop.inspect_reference(*paths)
    text = json.dumps(output)
    assert "/private/secrets" not in text and str(tmp_path) not in text


def test_invalid_midi_bytes_fail_even_with_matching_checksum(tmp_path):
    midi_path, meta = pair(tmp_path, [note()])
    midi_path.write_bytes(b"not a MIDI file")
    document = json.loads(meta.read_text())
    document["aligned_midi_checksum"] = hashlib.sha256(midi_path.read_bytes()).hexdigest()
    meta.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="parse"):
        multtipop.inspect_reference(midi_path, meta)


@pytest.mark.parametrize("track", [b"\x81" * 5 + b"\x00\xff\x2f\x00", b"\x00\xff\x01" + b"\x81" * 5 + b"\x00",
                                    b"\x00\x90\xff\x40", b"\x00\xff\x01\x7f\x00"])
def test_preflight_rejects_oversized_vlq_invalid_pitch_and_payload_escape(tmp_path, track):
    paths = pair(tmp_path, [note()])
    data = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0MTrk" + len(track).to_bytes(4, "big") + track
    paths[0].write_bytes(data)
    metadata = json.loads(paths[1].read_text())
    metadata["aligned_midi_checksum"] = hashlib.sha256(data).hexdigest()
    paths[1].write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        multtipop.inspect_reference(*paths)


def test_invalid_assignment_types_raise_valueerror_not_internal_typeerror(tmp_path):
    paths = pair(tmp_path, [note()])
    audit = multtipop.inspect_reference(*paths)
    assignments = mapping(audit)
    next(iter(assignments.values()))["role"] = []
    with pytest.raises(ValueError):
        multtipop.build_reference(*paths, assignments)


@pytest.mark.parametrize("old,replacement", [
    ('"audio_length": 4.0', '"audio_length": 9999, "audio_length": 4.0'),
    ('"start": 75.0', '"start": 0, "start": 75.0'),
])
def test_rejects_duplicate_metadata_keys_at_every_level(tmp_path, old, replacement):
    paths = pair(tmp_path, [note()])
    text = paths[1].read_text()
    assert old in text
    paths[1].write_text(text.replace(old, replacement))
    with pytest.raises(ValueError, match="metadata JSON"):
        multtipop.inspect_reference(*paths)


def test_shared_channel_program_collisions_have_an_explicit_audit_warning(tmp_path):
    paths = pair(tmp_path, [[mido.Message("program_change", channel=0, program=24), *note()],
                           [mido.Message("program_change", channel=0, program=32), *note(40)]])
    audit = multtipop.inspect_reference(*paths)
    assert audit["audit"]["shared_program_channels"] == [0]
    assert any("global MIDI program state" in message for message in audit["audit"]["warnings"])


def test_pitch_boundaries_remain_raw_and_note_track_limits_are_enforced(tmp_path, monkeypatch):
    paths = pair(tmp_path, [note(0), note(127, channel=1)])
    audit = multtipop.inspect_reference(*paths)
    result = multtipop.build_reference(*paths, mapping(audit))
    assert [event[2] for event in result["events"]["piano"]] == [0, 127]
    monkeypatch.setattr(multtipop, "MAX_NOTES", 1)
    with pytest.raises(ValueError, match="note count"):
        multtipop.inspect_reference(*paths)
    monkeypatch.setattr(multtipop, "MAX_NOTES", 100_000)
    monkeypatch.setattr(multtipop, "MAX_TRACKS", 1)
    with pytest.raises(ValueError, match="track count"):
        multtipop.inspect_reference(*paths)
