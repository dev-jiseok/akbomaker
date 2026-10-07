"""Offline, source-verified MulTTiPop aligned-MIDI reference adapter.

The aligned MIDI clock is already relative to the audio excerpt. Metadata song
tempo and the absolute YouTube crop start must never replace that clock. This
module does not download audio or guess a reviewed instrument/lead assignment.
"""
from collections import defaultdict, deque
import hashlib
import io
import json
import math
from pathlib import Path
import re

import mido

from .config import INSTRUMENTS

DATASET_REVISION = "341cd7f31d29d092862fe7bdd49f9ccdce7825b0"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_MESSAGES = 100_000
MAX_NOTES = 100_000
MAX_TRACKS = 128
MAX_DURATION = 600.
ROUNDING_TOLERANCE = .02


def _read_file(path, suffixes):
    """Read only the two explicit regular files; no paths from metadata."""
    try:
        path = Path(path)
        if path.suffix.lower() not in suffixes or path.is_symlink() or not path.is_file():
            raise ValueError("Reference inputs must be regular MIDI/JSON files, not symbolic links")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Reference file exceeds the 2 MB limit")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("Reference file is empty or exceeds the 2 MB limit")
        return data
    except (OSError, TypeError) as error:
        raise ValueError("Cannot read the explicit local reference file") from error


def _number(value, label, *, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Metadata {label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Metadata {label} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"Metadata {label} must be a finite number")
    if number < minimum or maximum is not None and number > maximum:
        raise ValueError(f"Metadata {label} is outside the supported range")
    return number


def _metadata(data, midi_bytes):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate reference metadata JSON keys are ambiguous")
            result[key] = value
        return result
    try:
        document = json.loads(data, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid reference metadata JSON") from error
    if not isinstance(document, dict):
        raise ValueError("Reference metadata must be an object")
    identifier = document.get("id")
    if type(identifier) is int and identifier >= 0:
        identifier = str(identifier)
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", identifier):
        raise ValueError("Metadata id is missing or unsafe")
    split = document.get("split_name")
    if not isinstance(split, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", split):
        raise ValueError("Metadata split_name is missing or unsafe")
    checksum = document.get("aligned_midi_checksum")
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise ValueError("Metadata aligned_midi_checksum must be a SHA256 digest")
    actual_hash = hashlib.sha256(midi_bytes).hexdigest()
    if checksum.lower() != actual_hash:
        raise ValueError("Aligned MIDI SHA256 does not match its metadata checksum")
    youtube = document.get("youtube")
    if not isinstance(youtube, dict) or not isinstance(youtube.get("ytid"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", youtube["ytid"]):
        raise ValueError("Metadata YouTube video id is missing or invalid")
    if youtube.get("timestampType") != "absolute":
        raise ValueError("Metadata YouTube timestampType must be absolute")
    start = _number(youtube.get("start"), "youtube.start")
    end = _number(youtube.get("end"), "youtube.end")
    duration = _number(document.get("audio_length"), "audio_length", maximum=MAX_DURATION)
    if not start < end or duration <= 0 or abs((end - start) - duration) > .01:
        raise ValueError("Metadata crop start/end and audio_length do not describe the same positive excerpt")
    return {
        "duration": duration,
        "source": {"dataset": "MulTTiPop", "dataset_revision": DATASET_REVISION,
                   "id": identifier, "split_name": split,
                   "youtube": {"ytid": youtube["ytid"], "timestampType": "absolute", "start": start, "end": end},
                   "midi_sha256": actual_hash, "metadata_sha256": hashlib.sha256(data).hexdigest(),
                   "aligned_midi_checksum_verified": True},
    }


def _suggest(program, is_drum):
    if is_drum:
        return "drums"
    if 0 <= program <= 7:
        return "piano"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 39:
        return "bass"
    if 80 <= program <= 103:
        return "synthesizer"
    return None  # Choir/voice GM presets are not proof of a lead vocal part.


def _preflight_midi(data):
    """Bound SMF chunks/VLQs before Mido allocates messages or huge integers."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("Cannot parse the aligned MIDI reference header")
    header_length = int.from_bytes(data[4:8], "big")
    if not 6 <= header_length <= len(data) - 8:
        raise ValueError("Invalid MIDI header length")
    track_count = int.from_bytes(data[10:12], "big")
    if not 1 <= track_count <= MAX_TRACKS:
        raise ValueError("Reference MIDI track count exceeds the supported limit")
    position, messages = 8 + header_length, 0

    def variable(position, end):
        value = 0
        for _ in range(4):
            if position >= end:
                raise ValueError("Truncated MIDI variable-length value")
            byte = data[position]
            position += 1
            value = (value << 7) | (byte & 0x7f)
            if byte < 0x80:
                return value, position
        raise ValueError("MIDI variable-length values cannot exceed four bytes")

    for _ in range(track_count):
        if position + 8 > len(data) or data[position:position + 4] != b"MTrk":
            raise ValueError("Invalid MIDI track chunk")
        size = int.from_bytes(data[position + 4:position + 8], "big")
        position += 8
        end, running = position + size, None
        if end > len(data):
            raise ValueError("MIDI track chunk exceeds the file boundary")
        while position < end:
            _, position = variable(position, end)
            if position >= end:
                raise ValueError("Truncated MIDI message")
            status = data[position]
            if status >= 0x80:
                position += 1
                if status != 0xff:
                    running = status
            else:
                if running is None or running >= 0xf0:
                    raise ValueError("Invalid MIDI running status")
                status = running
            if status in (0xff, 0xf0, 0xf7):
                if status == 0xff:
                    if position >= end:
                        raise ValueError("Truncated MIDI meta message")
                    position += 1  # Meta type, followed by bounded payload size.
                length, position = variable(position, end)
                position += length
            else:
                if 0x80 <= status <= 0xef:
                    length = 1 if status & 0xf0 in (0xc0, 0xd0) else 2
                else:
                    length = {0xf1: 1, 0xf2: 2, 0xf3: 1, 0xf6: 0, 0xf8: 0, 0xfa: 0,
                              0xfb: 0, 0xfc: 0, 0xfe: 0}.get(status)
                    if length is None:
                        raise ValueError("Unsupported MIDI status byte")
                if any(byte >= 0x80 for byte in data[position:position + length]):
                    raise ValueError("MIDI note/controller data is outside 0–127")
                position += length
            if position > end:
                raise ValueError("MIDI message exceeds its track boundary")
            messages += 1
            if messages > MAX_MESSAGES:
                raise ValueError("Reference MIDI message count exceeds the supported limit")
    if position != len(data):
        raise ValueError("Unexpected trailing data after MIDI tracks")


def _parse(midi_path, metadata_path):
    midi_bytes = _read_file(midi_path, {".mid", ".midi"})
    metadata = _metadata(_read_file(metadata_path, {".json"}), midi_bytes)
    duration = metadata["duration"]
    _preflight_midi(midi_bytes)
    try:
        midi = mido.MidiFile(file=io.BytesIO(midi_bytes), clip=False)
    except (ValueError, OSError, EOFError, KeyError, IndexError) as error:
        raise ValueError("Cannot parse the aligned MIDI reference") from error
    if midi.type not in (0, 1):
        raise ValueError("Only synchronous MIDI type 0/1 references are supported")
    if not 1 <= midi.ticks_per_beat <= 32767:
        raise ValueError("MIDI must use a positive ticks-per-beat clock, not SMPTE")
    if not 1 <= len(midi.tracks) <= MAX_TRACKS:
        raise ValueError("Reference MIDI track count exceeds the supported limit")
    timeline, names = [], {}
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        names[track_index] = f"Track {track_index}"
        for message_index, message in enumerate(track):
            if not isinstance(message.time, int) or message.time < 0:
                raise ValueError("MIDI delta times must be nonnegative integer ticks")
            tick += message.time
            if message.type == "track_name":
                names[track_index] = " ".join(message.name.split())[:160] or names[track_index]
            timeline.append((tick, track_index, message_index, message))
            if len(timeline) > MAX_MESSAGES:
                raise ValueError("Reference MIDI message count exceeds the supported limit")
    timeline.sort(key=lambda event: event[:3])
    programs = [0] * 16
    program_values, channel_tracks = defaultdict(set), defaultdict(set)
    active = defaultdict(deque)
    parts, part_events = {}, defaultdict(list)
    tempo, last_tick, seconds = 500_000, 0, 0.
    tempo_map = [{"tick": 0, "seconds": 0., "microseconds_per_beat": tempo}]
    clipped, repeated, note_count = 0, 0, 0
    for tick, track_index, _, message in timeline:
        seconds += mido.tick2second(tick - last_tick, midi.ticks_per_beat, tempo)
        last_tick = tick
        if not math.isfinite(seconds) or seconds > MAX_DURATION + ROUNDING_TOLERANCE:
            raise ValueError("Reference MIDI clock exceeds the 600 second limit")
        if message.type == "set_tempo":
            if not 1 <= message.tempo <= 0xFFFFFF:
                raise ValueError("Reference MIDI contains an invalid tempo")
            tempo = message.tempo
            tempo_map.append({"tick": tick, "seconds": seconds, "microseconds_per_beat": tempo})
            continue
        if message.type == "program_change":
            programs[message.channel] = message.program
            program_values[message.channel].add(message.program)
            continue
        if message.type not in {"note_on", "note_off"}:
            continue
        key = (track_index, message.channel, message.note)
        if message.type == "note_on" and message.velocity > 0:
            if seconds >= duration:
                raise ValueError("Reference note onset is outside the audio excerpt")
            drum = message.channel == 9
            channel_tracks[message.channel].add(track_index)
            program_values[message.channel].add(programs[message.channel])
            program = None if drum else programs[message.channel]
            part_id = f"t{track_index}:c{message.channel}:" + ("drums" if drum else f"p{program}")
            if part_id not in parts:
                parts[part_id] = {"part_id": part_id, "name": names[track_index], "track_index": track_index,
                                  "channel": message.channel, "program": program, "is_drum": drum,
                                  "programs_seen": [], "suggested_instrument": _suggest(program, drum)}
            if programs[message.channel] not in parts[part_id]["programs_seen"]:
                parts[part_id]["programs_seen"].append(programs[message.channel])
            if active[key]:
                repeated += 1
            active[key].append((seconds, part_id, message.velocity / 127))
        else:
            if not active[key]:
                raise ValueError("Reference contains an unpaired note-off; zero-length, reordered, or clipped notes require manual review")
            start, part_id, amplitude = active[key].popleft()
            end = seconds
            if end > duration:
                if end - duration > ROUNDING_TOLERANCE:
                    raise ValueError("Reference note extends outside the audio excerpt")
                end = duration
                clipped += 1
            if end <= start:
                raise ValueError("Reference notes must have positive duration")
            part_events[part_id].append([start, end, message.note, amplitude])
            note_count += 1
            if note_count > MAX_NOTES:
                raise ValueError("Reference note count exceeds the supported limit")
    if any(queue for queue in active.values()):
        raise ValueError("Reference contains dangling notes; tail durations are not invented")
    if not note_count:
        raise ValueError("Reference MIDI does not contain any complete notes")
    for part_id, part in parts.items():
        events = sorted(part_events[part_id])
        part_events[part_id] = events
        pitches = [event[2] for event in events]
        part.update(note_count=len(events), pitch_range=[min(pitches), max(pitches)])
    warnings = []
    if clipped:
        warnings.append(f"Clipped {clipped} note ends within {ROUNDING_TOLERANCE:.3f}s of the excerpt end")
    if repeated:
        warnings.append(f"Paired {repeated} overlapping same-pitch note-ons FIFO within the same track/channel")
    shared_program_channels = sorted(channel for channel, tracks in channel_tracks.items()
                                     if channel != 9 and len(tracks) > 1 and len(program_values[channel]) > 1)
    if shared_program_channels:
        warnings.append("Multiple tracks share channels with changing programs; global MIDI program state applies: "
                        + ", ".join(map(str, shared_program_channels)))
    audit = {**metadata, "parts": list(parts.values()),
             "audit": {"midi_type": midi.type, "ticks_per_beat": midi.ticks_per_beat,
                       "message_count": len(timeline), "note_count": note_count,
                       "clipped_note_count": clipped, "overlapping_note_on_count": repeated,
                       "orphan_note_off_count": 0, "dangling_note_count": 0,
                       "shared_program_channels": shared_program_channels,
                       "warnings": warnings, "tempo_map": tempo_map,
                       "clock": "MIDI global tempo map; clip-relative seconds; YouTube start is not subtracted",
                       "drum_pitches": "Raw MIDI/GM numbers, including nonstandard percussion; no ADT or kit normalization"}}
    return audit, part_events


def inspect_reference(midi_path, metadata_path):
    """Verify a local reference pair and expose parts without accepting a map."""
    audit, _ = _parse(midi_path, metadata_path)
    return audit


def build_reference(midi_path, metadata_path, assignments):
    """Require an explicit complete per-part map, preserving independent voices."""
    inspected, part_events = _parse(midi_path, metadata_path)
    if not isinstance(assignments, dict) or set(assignments) != set(part_events):
        raise ValueError("Assignments must cover every MIDI part exactly once, including ignored parts")
    reviewed, ignored, events = [], [], {instrument: [] for instrument in INSTRUMENTS}
    canonical = {}
    for part in inspected["parts"]:
        part_id = part["part_id"]
        choice = assignments[part_id]
        if not isinstance(choice, dict) or not set(choice).issubset({"instrument", "role", "reason"}):
            raise ValueError("Each assignment needs instrument, optional role, and a review reason")
        instrument, role, reason = choice.get("instrument"), choice.get("role"), choice.get("reason")
        if not isinstance(instrument, str) or instrument not in {*INSTRUMENTS, "ignore"}:
            raise ValueError("Every part must be assigned to a supported instrument or explicitly ignored")
        if role not in (None, "lead", "backing") or instrument == "vocal" and role is None:
            raise ValueError("Vocal parts need an explicit lead/backing role; other roles must be lead/backing or null")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("Every assignment, including ignore, needs a nonempty reason of at most 500 characters")
        if instrument != "ignore" and (part["is_drum"] != (instrument == "drums")):
            raise ValueError("Percussion parts cannot become pitched instruments or vice versa")
        accepted = {"instrument": instrument, "role": role, "reason": reason.strip()}
        canonical[part_id] = accepted
        reviewed.append({**part, "assignment": accepted})
        if instrument == "ignore":
            ignored.append({"part_id": part_id, **accepted, "note_count": part["note_count"]})
        else:
            # Do not merge duplicate pitches or simultaneous octave voices from
            # separate parts. Part-origin events remain available below.
            events[instrument].extend(part_events[part_id])
    mapping_bytes = json.dumps(canonical, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    return {"schema": "akbo.multtipop-reference", "schema_version": 1,
            "duration": inspected["duration"], "events": {inst: sorted(values) for inst, values in events.items()},
            "parts": reviewed, "part_events": part_events, "ignored_parts": ignored,
            "source": inspected["source"], "mapping_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
            "audit": inspected["audit"],
            "limitations": ["Reviewed MIDI alignment is a reference, not a claim that the performance matches every annotation.",
                            "GM programs are suggestions only; lead/backing and instrument assignments require human review.",
                            "Note lengths are MIDI key-down durations; sustain-pedal rendering is not expanded."]}
