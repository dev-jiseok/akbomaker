"""Transcription and rhythm-quantized MusicXML/MIDI export. No invented notes."""
import math
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import mido
import numpy as np
import soundfile as sf
from .rhythm import measure_map, normalize_meters, grouping_ticks

PROGRAMS = {"vocal": 53, "bass": 33, "drums": 0, "synthesizer": 89, "guitar": 25, "piano": 1}
NAMES = {"vocal": "Vocal", "bass": "Bass", "drums": "Drums", "synthesizer": "Synthesizer", "guitar": "Guitar", "piano": "Piano"}


def drum_events(path: Path) -> list[tuple]:
    """Experimental drum onsets with coarse kick/snare/hat spectral classification."""
    samples, sr = sf.read(path, dtype="float32")
    hop, window = 480, 2048
    if len(samples) < window:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(samples, window)[::hop]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(window), axis=1))
    flux = np.maximum(np.diff(spectrum, axis=0), 0).sum(axis=1)
    threshold = max(float(np.median(flux) + 1.8 * np.std(flux)), 0.01)
    peaks = np.where((flux[1:-1] > flux[:-2]) & (flux[1:-1] >= flux[2:]) & (flux[1:-1] > threshold))[0] + 1
    frequencies = np.fft.rfftfreq(window, 1 / sr)
    events, last = [], -1.0
    for peak in peaks:
        time = float((peak + 1) * hop / sr)
        if time - last < 0.08:
            continue
        energies = spectrum[peak + 1] ** 2
        low = energies[frequencies < 200].sum()
        high = energies[frequencies > 5000].sum()
        middle = energies[(frequencies >= 200) & (frequencies <= 5000)].sum()
        pitch = 36 if low > middle else 42 if high > middle else 38
        events.append((time, time + 0.1, pitch, 0.7))
        last = time
    return events


def transcribe(path: Path, inst: str) -> list[tuple]:
    samples, _ = sf.read(path, dtype="float32")
    if not len(samples) or np.sqrt(np.mean(samples.astype(np.float64) ** 2)) < 1e-5:
        return []
    if inst == "drums":
        return drum_events(path)
    from basic_pitch.inference import predict
    _, _, events = predict(str(path), model_or_model_path=transcription_model(), onset_threshold=0.5, frame_threshold=0.3, minimum_note_length=100)
    return [(float(start), float(end), int(pitch), float(amplitude)) for start, end, pitch, amplitude, *_ in events]


@lru_cache(maxsize=1)
def transcription_model():
    from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
    from basic_pitch.inference import Model
    # Use the bundled ONNX model and the CPU onnxruntime wheel. Keep SAM's GPU
    # memory separate from a TensorFlow/CoreML transcription runtime.
    return Model(build_icassp_2022_model_path(FilenameSuffix.onnx))


def child(parent, tag: str, text=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if text is not None:
        node.text = str(text)
    return node


def quantize(events: list[tuple], bpm: int, duration: float) -> list[tuple]:
    units = bpm / 60 * 4  # sixteenth-note grid
    end_limit = max(1, round(duration * units))
    notes = []
    for start, end, pitch, amp in events:
        if not all(math.isfinite(float(x)) for x in (start, end, pitch, amp)) or end <= start:
            continue
        a = min(end_limit - 1, max(0, round(start * units)))
        b = min(end_limit, max(a + 1, round(end * units)))
        notes.append((a, b, min(127, max(0, int(pitch))), float(amp)))
    return sorted(set(notes))


DRUM_NOTATION = {
    36: ("F", 4, False), 38: ("C", 5, False), 42: ("G", 5, True),
    46: ("G", 5, True), 49: ("A", 5, True), 51: ("F", 5, True),
    45: ("A", 4, False), 47: ("D", 5, False), 50: ("E", 5, False),
}


def export_score(events: list[tuple], inst: str, title: str, bpm: int, duration: float, folder: Path,
                 *, quantized=None, layout=None, annotations=None, edited=False,
                 tab=None, positions=None, lyrics=None, meters=None, ticks=None):
    notes = quantize(events, bpm, duration) if quantized is None else quantized
    layout = layout or {"preset": "practice" if inst == "drums" else "standard", "measures_per_line": 4}
    annotations = {item["measure"]: item for item in (annotations or [])}
    lyric_map = {item["start"]: item["text"] for item in (lyrics or [])}
    tab_mode = tab["mode"] if tab else "staff"
    tab_first = bool(tab and tab.get("order", "staff-first") == "tab-first")
    tab_staff = 1 if tab_mode == "tab" or tab_first else 2
    standard_staff = 2 if tab_mode == "both" and tab_first else 1
    tab_positions = {(n["start"], n["start"] + n["length"], n["pitch"]): (n.get("string"), n.get("fret")) for n in (positions or [])}
    note_marks = {(n["start"], n["start"] + n["length"], n["pitch"]): n for n in (positions or [])}
    if len(notes) > 30_000:
        raise ValueError("채보 결과가 너무 복잡해요. 짧은 구간으로 나누어 다시 시도해주세요.")
    root = ET.Element("score-partwise", version="4.0")
    work = child(root, "work")
    child(work, "work-title", title)
    identification = child(root, "identification")
    child(identification, "creator", "Akbo Maker · " + ("edited score" if edited else "automatic transcription draft"), type="composer")
    defaults = child(root, "defaults")
    scaling = child(defaults, "scaling")
    child(scaling, "millimeters", 7 if layout["preset"] != "large" else 8.5)
    child(scaling, "tenths", 40)
    page = child(defaults, "page-layout")
    child(page, "page-height", 1697)
    child(page, "page-width", 1200)
    margins = child(page, "page-margins", type="both")
    for name in ("left", "right", "top", "bottom"):
        child(margins, f"{name}-margin", 65)
    system = child(defaults, "system-layout")
    child(system, "system-distance", 140 if layout["preset"] != "standard" else 95)
    part_list = child(root, "part-list")
    part_info = child(part_list, "score-part", id="P1")
    child(part_info, "part-name", NAMES[inst])
    pitches = sorted({n[2] for n in notes}) if inst == "drums" else [None]
    for pitch in pitches or [36]:
        instrument_id = f"I{pitch}" if inst == "drums" else "I1"
        child(child(part_info, "score-instrument", id=instrument_id), "instrument-name", NAMES[inst])
        midi = child(part_info, "midi-instrument", id=instrument_id)
        child(midi, "midi-channel", 10 if inst == "drums" else 1)
        child(midi, "midi-program", PROGRAMS[inst])
        if inst == "drums":
            child(midi, "midi-unpitched", pitch + 1)
    part = child(root, "part", id="P1")
    meters = normalize_meters(meters)
    bars = measure_map(ticks if ticks is not None else max(1, math.ceil(duration * bpm / 60 * 4 - 1e-9)), meters, exact=ticks is not None)
    measures = len(bars)
    types = {16: ("whole", False), 12: ("half", True), 8: ("half", False), 6: ("quarter", True), 4: ("quarter", False), 3: ("eighth", True), 2: ("eighth", False), 1: ("16th", False)}
    pitch_names = [("C", 0), ("C", 1), ("D", 0), ("E", -1), ("E", 0), ("F", 0), ("F", 1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
    for number, bar in enumerate(bars):
        measure = child(part, "measure", number=str(number + 1))
        lo, hi = bar["start"], bar["end"]
        beat_ticks = grouping_ticks(bar)
        page_break = number + 1 in layout.get("page_breaks", [])
        if number == 0 or number % layout.get("measures_per_line", 4) == 0 or number + 1 in layout.get("system_breaks", []) or page_break:
            printing = child(measure, "print", **({"new-page": "yes"} if page_break else {"new-system": "yes"} if number else {}))
            child(printing, "measure-numbering", "measure" if layout.get("show_numbers", True) else "none")
        annotation = annotations.get(number + 1, {})
        for name, tag, placement in [("section", "rehearsal", "above"), ("cue", "words", "below")]:
            if annotation.get(name):
                direction = child(measure, "direction", placement=placement)
                child(child(direction, "direction-type"), tag, annotation[name])
        if number == 0:
            attrs = child(measure, "attributes")
            child(attrs, "divisions", 4)
            child(child(attrs, "key"), "fifths", 0)
            time = child(attrs, "time")
            child(time, "beats", bar["beats"])
            child(time, "beat-type", bar["beat_type"])
            if tab_mode == "both":
                child(attrs, "staves", 2)
            if tab_mode != "tab":
                clef = child(attrs, "clef", **({"number": str(standard_staff)} if tab_mode == "both" else {}))
                child(clef, "sign", "percussion" if inst == "drums" else "F" if inst == "bass" else "G")
                if inst != "drums":
                    child(clef, "line", 4 if inst == "bass" else 2)
                else:
                    child(child(attrs, "staff-details"), "staff-lines", 5)
            if tab_mode != "staff":
                staff = tab_staff
                clef = child(attrs, "clef", number=str(staff))
                child(clef, "sign", "TAB")
                child(clef, "line", 5 if len(tab["tuning"]) > 4 else 3)
                details = child(attrs, "staff-details", number=str(staff))
                child(details, "staff-lines", len(tab["tuning"]))
                for line, pitch in enumerate(reversed(tab["tuning"]), 1):
                    tuning = child(details, "staff-tuning", line=str(line))
                    step, alter = pitch_names[pitch % 12]
                    child(tuning, "tuning-step", step)
                    if alter:
                        child(tuning, "tuning-alter", alter)
                    child(tuning, "tuning-octave", pitch // 12 - 1)
                if tab.get("capo", 0):
                    child(details, "capo", tab["capo"])
            if tab_mode == "both":
                # Readers (including OSMD) may initialize clefs in element
                # order. Keep the XML staff sequence consistent with numbers.
                clefs = attrs.findall("clef")
                first_clef = min(list(attrs).index(c) for c in clefs)
                for clef in clefs:
                    attrs.remove(clef)
                for offset, clef in enumerate(sorted(clefs, key=lambda c: int(c.get("number", "1")))):
                    attrs.insert(first_clef + offset, clef)
            if tab and tab_mode != "tab":
                # Guitar/bass standard notation is written one octave above
                # concert pitch. String/fret values and exported MIDI stay concert.
                transpose = child(attrs, "transpose", number=str(standard_staff))
                child(transpose, "diatonic", 0)
                child(transpose, "chromatic", 0)
                child(transpose, "octave-change", -1)
            direction = child(measure, "direction", placement="above")
            metronome = child(child(direction, "direction-type"), "metronome")
            child(metronome, "beat-unit", "quarter")
            child(metronome, "per-minute", bpm)
            child(direction, "sound", tempo=str(bpm))
            if tab and tab.get("capo", 0):
                capo_direction = child(measure, "direction", placement="above")
                child(child(capo_direction, "direction-type"), "words", f"Capo {tab['capo']} · frets relative to capo")
                if tab_mode == "both":
                    child(capo_direction, "staff", 1)
        elif any(m["measure"] == number + 1 for m in meters):
            time = child(child(measure, "attributes"), "time")
            child(time, "beats", bar["beats"])
            child(time, "beat-type", bar["beat_type"])
        in_bar = [n for n in notes if n[0] < hi and n[1] > lo]
        playable = [n for n in in_bar if tab_positions.get(n[:3], (None, None))[0] is not None]
        if tab_mode == "both":
            voices = [(playable, tab_staff, True), (in_bar, standard_staff, False)] if tab_first else [(in_bar, standard_staff, False), (playable, tab_staff, True)]
        elif tab_mode == "tab":
            voices = [(playable, 1, True)]
        elif inst == "drums":
            voices = [([n for n in in_bar if n[2] != 36], 1, False), ([n for n in in_bar if n[2] == 36], 1, False)]
        else:
            voices = [(in_bar, 1, False)]
        lyric_voice = 1 if tab_mode == "both" else 0
        for voice_index, (voice_notes, staff, is_tab) in enumerate(voices):
            if voice_index:
                child(child(measure, "backup"), "duration", hi - lo)
            bounds = sorted({lo, hi, *[max(lo, n[0]) for n in voice_notes], *[min(hi, n[1]) for n in voice_notes],
                             *[tick for tick in lyric_map if lo <= tick < hi and voice_index == lyric_voice]})
            for start, end in zip(bounds, bounds[1:]):
                active = {n[2]: n for n in voice_notes if n[0] <= start and n[1] > start}
                position = start
                while position < end:
                    # Offbeat notes/rests must not hide beat boundaries.
                    local = position - lo
                    remaining = min(end - position, beat_ticks - local % beat_ticks) if local % beat_ticks else end - position
                    # Compound meter reads as dotted-quarter beats; do not
                    # obscure the 3+3 / 3+3+3 eighth-note subdivisions.
                    if bar["beat_type"] == 8:
                        remaining = min(remaining, beat_ticks - local % beat_ticks)
                    size = next(value for value in types if value <= remaining)
                    for index, (pitch, event) in enumerate(sorted(active.items()) if active else [(None, None)]):
                        note = write_note(measure, pitch, event, index, inst, position, size, types, pitch_names,
                                          voice_index, staff=staff if tab_mode != "staff" else None,
                                          is_tab=is_tab, tab_position=tab_positions.get(event[:3]) if event else None,
                                          marks=note_marks.get(event[:3], {}) if event and position == event[0] else {},
                                          octave_shift=12 if tab and not is_tab else 0)
                        if index == 0 and voice_index == lyric_voice and position in lyric_map:
                            lyric = child(note, "lyric", number="1", placement="below")
                            child(lyric, "syllabic", "single")
                            child(lyric, "text", lyric_map[position], **{"font-size": "11"})
                    position += size
            if not is_tab:
                group = beat_ticks if bar["beat_type"] == 8 else 8 if layout.get("beam_group", "half" if inst == "drums" else "beat") == "half" else 4
                beam_voice(measure, str(voice_index + 1), group, 2 if bar["beat_type"] == 8 else 4)
        if number == measures - 1:
            child(child(measure, "barline", location="right"), "bar-style", "light-heavy")
    ET.indent(root)
    ET.ElementTree(root).write(folder / f"{inst}.musicxml", encoding="utf-8", xml_declaration=True)
    write_midi(notes, inst, bpm, folder, bars=bars, meters=meters)
    return len(notes)


def write_note(measure, pitch, event, index, inst, position, size, types, pitch_names, voice_index,
               *, staff=None, is_tab=False, tab_position=None, octave_shift=0, marks=None):
    marks = marks or {}
    note = child(measure, "note")
    if index:
        child(note, "chord")
    if pitch is None:
        child(note, "rest")
    elif inst == "drums":
        step, octave, _ = DRUM_NOTATION.get(pitch, ("C", 5, False))
        unpitched = child(note, "unpitched")
        child(unpitched, "display-step", step)
        child(unpitched, "display-octave", octave)
    else:
        step, alter = pitch_names[pitch % 12]
        node = child(note, "pitch")
        child(node, "step", step)
        if alter:
            child(node, "alter", alter)
        child(node, "octave", (pitch + octave_shift) // 12 - 1)
    child(note, "duration", size)
    ties = []
    if event:
        if event[0] < position:
            ties.append("stop")
        if event[1] > position + size:
            ties.append("start")
        for tie in ties:
            child(note, "tie", type=tie)
    if pitch is not None and inst == "drums":
        child(note, "instrument", id=f"I{pitch}")
    child(note, "voice", voice_index + 1)
    type_name, dot = types[size]
    child(note, "type", type_name)
    if dot:
        child(note, "dot")
    if is_tab:
        child(note, "stem", "none")
    elif inst == "drums":
        child(note, "stem", "down" if voice_index else "up")
    if inst == "drums" and DRUM_NOTATION.get(pitch, (None, None, False))[2]:
        child(note, "notehead", "x")
    elif marks.get("muted"):
        child(note, "notehead", "x")
    if staff:
        child(note, "staff", staff)
    if ties or (inst == "drums" and pitch == 46) or (is_tab and tab_position) or marks.get("articulation", "none") != "none" or marks.get("bend"):
        notation = child(note, "notations")
        for tie in ties:
            child(notation, "tied", type=tie)
        if pitch == 46 and inst == "drums":
            child(child(notation, "technical"), "open-string")
        if is_tab and tab_position and tab_position[0] is not None:
            technical = child(notation, "technical")
            child(technical, "string", tab_position[0])
            child(technical, "fret", tab_position[1])
        if marks.get("bend"):
            technical = notation.find("technical")
            if technical is None:
                technical = child(notation, "technical")
            child(child(technical, "bend"), "bend-alter", marks["bend"])
        if marks.get("articulation", "none") != "none":
            child(child(notation, "articulations"), marks["articulation"])
    return note


def beam_voice(measure, voice, group_ticks=4, secondary_ticks=4):
    """Explicit beat-grouped beams survive export and percussion stem directions."""
    groups, current, position, beat = [], [], 0, -1
    for note in measure.findall("note"):
        if note.findtext("voice") != voice or note.find("chord") is not None:
            continue
        size = int(note.findtext("duration"))
        next_beat = position // group_ticks
        eligible = note.findtext("type") in {"eighth", "16th"} and note.find("rest") is None
        if next_beat != beat or not eligible:
            if current:
                groups.append(current)
            current = []
        if eligible:
            current.append((note, position))
        beat, position = next_beat, position + size
    if current:
        groups.append(current)
    for group in groups:
        if len(group) < 2:
            continue
        for index, (note, position) in enumerate(group):
            beam = ET.Element("beam", number="1")
            beam.text = "begin" if index == 0 else "end" if index == len(group) - 1 else "continue"
            anchor = note.find("notations")
            if anchor is None:
                anchor = note.find("lyric")
            note.insert(list(note).index(anchor) if anchor is not None else len(note), beam)
            if note.findtext("type") == "16th":
                # The primary beam spans two beats in the rehearsal preset;
                # secondary beams retain readable quarter-note subdivisions.
                before = index > 0 and group[index - 1][0].findtext("type") == "16th" and group[index - 1][1] // secondary_ticks == position // secondary_ticks
                after = index + 1 < len(group) and group[index + 1][0].findtext("type") == "16th" and group[index + 1][1] // secondary_ticks == position // secondary_ticks
                secondary = ET.Element("beam", number="2")
                secondary.text = "continue" if before and after else "end" if before else "begin" if after else "forward hook" if index == 0 else "backward hook"
                anchor = note.find("notations")
                if anchor is None:
                    anchor = note.find("lyric")
                note.insert(list(note).index(anchor) if anchor is not None else len(note), secondary)


def write_midi(notes, inst, bpm, folder, *, bars=None, meters=None):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    meters = normalize_meters(meters)
    initial = meters[0]
    track.append(mido.MetaMessage("time_signature", numerator=initial["beats"], denominator=initial["beat_type"], clocks_per_click=36 if initial["beat_type"] == 8 else 24))
    channel = 9 if inst == "drums" else 0
    track.append(mido.Message("program_change", program=PROGRAMS[inst] - 1 if inst != "drums" else 0, channel=channel))
    midi_events = []
    for start, end, pitch, amplitude in notes:
        midi_events.append((start * 120, 2, pitch, mido.Message("note_on", note=pitch, velocity=min(127, max(1, round(amplitude * 100))), channel=channel)))
        midi_events.append((end * 120, 1, pitch, mido.Message("note_off", note=pitch, velocity=0, channel=channel)))
    if bars:
        for meter in meters[1:]:
            midi_events.append((bars[meter["measure"] - 1]["start"] * 120, 0, -1,
                                mido.MetaMessage("time_signature", numerator=meter["beats"], denominator=meter["beat_type"], clocks_per_click=36 if meter["beat_type"] == 8 else 24)))
    previous = 0
    for tick, priority, pitch, message in sorted(midi_events, key=lambda e: e[:3]):
        track.append(message.copy(time=tick - previous))
        previous = tick
    if bars:
        track.append(mido.MetaMessage("end_of_track", time=max(0, bars[-1]["end"] * 120 - previous)))
    mid.save(folder / f"{inst}.mid")
