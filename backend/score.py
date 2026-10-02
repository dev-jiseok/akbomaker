"""Transcription and rhythm-quantized MusicXML/MIDI export. No invented notes."""
import math
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

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


def export_score(events: list[tuple], inst: str, title: str, bpm: int, duration: float, folder: Path):
    notes = quantize(events, bpm, duration)
    if len(notes) > 30_000:
        raise ValueError("채보 결과가 너무 복잡해요. 짧은 구간으로 나누어 다시 시도해주세요.")
    root = ET.Element("score-partwise", version="4.0")
    work = child(root, "work")
    child(work, "work-title", title)
    identification = child(root, "identification")
    child(identification, "creator", "Akbo Maker · automatic transcription draft", type="composer")
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
    measures = max(1, math.ceil(duration * bpm / 60 / 4))
    types = {16: ("whole", False), 12: ("half", True), 8: ("half", False), 6: ("quarter", True), 4: ("quarter", False), 3: ("eighth", True), 2: ("eighth", False), 1: ("16th", False)}
    pitch_names = [("C", 0), ("C", 1), ("D", 0), ("E", -1), ("E", 0), ("F", 0), ("F", 1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
    for number in range(measures):
        measure = child(part, "measure", number=str(number + 1))
        lo, hi = number * 16, (number + 1) * 16
        if number == 0:
            attrs = child(measure, "attributes")
            child(attrs, "divisions", 4)
            child(child(attrs, "key"), "fifths", 0)
            time = child(attrs, "time")
            child(time, "beats", 4)
            child(time, "beat-type", 4)
            clef = child(attrs, "clef")
            child(clef, "sign", "percussion" if inst == "drums" else "F" if inst == "bass" else "G")
            if inst != "drums":
                child(clef, "line", 4 if inst == "bass" else 2)
            direction = child(measure, "direction", placement="above")
            metronome = child(child(direction, "direction-type"), "metronome")
            child(metronome, "beat-unit", "quarter")
            child(metronome, "per-minute", bpm)
            child(direction, "sound", tempo=str(bpm))
        in_bar = [n for n in notes if n[0] < hi and n[1] > lo]
        bounds = sorted({lo, hi, *[max(lo, n[0]) for n in in_bar], *[min(hi, n[1]) for n in in_bar]})
        for start, end in zip(bounds, bounds[1:]):
            active = {n[2]: n for n in in_bar if n[0] <= start and n[1] > start}
            position = start
            while position < end:
                size = next(value for value in types if value <= end - position)
                for index, (pitch, event) in enumerate(sorted(active.items()) if active else [(None, None)]):
                    note = child(measure, "note")
                    if index:
                        child(note, "chord")
                    if pitch is None:
                        child(note, "rest")
                    elif inst == "drums":
                        step, octave = {36: ("F", 4), 38: ("C", 5), 42: ("G", 5)}.get(pitch, ("C", 5))
                        unpitched = child(note, "unpitched")
                        child(unpitched, "display-step", step)
                        child(unpitched, "display-octave", octave)
                    else:
                        step, alter = pitch_names[pitch % 12]
                        node = child(note, "pitch")
                        child(node, "step", step)
                        if alter:
                            child(node, "alter", alter)
                        child(node, "octave", pitch // 12 - 1)
                    child(note, "duration", size)
                    ties = []
                    if event and inst != "drums":
                        if event[0] < position:
                            ties.append("stop")
                        if event[1] > position + size:
                            ties.append("start")
                        for tie in ties:
                            child(note, "tie", type=tie)
                    if pitch is not None and inst == "drums":
                        child(note, "instrument", id=f"I{pitch}")
                    type_name, dot = types[size]
                    child(note, "type", type_name)
                    if dot:
                        child(note, "dot")
                    if pitch == 42 and inst == "drums":
                        child(note, "notehead", "x")
                    if ties:
                        notation = child(note, "notations")
                        for tie in ties:
                            child(notation, "tied", type=tie)
                position += size
        if number == measures - 1:
            child(child(measure, "barline", location="right"), "bar-style", "light-heavy")
    ET.indent(root)
    ET.ElementTree(root).write(folder / f"{inst}.musicxml", encoding="utf-8", xml_declaration=True)
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    channel = 9 if inst == "drums" else 0
    track.append(mido.Message("program_change", program=PROGRAMS[inst] - 1 if inst != "drums" else 0, channel=channel))
    midi_events = []
    for start, end, pitch, amplitude in notes:
        midi_events.append((start * 120, 1, pitch, min(127, max(1, round(amplitude * 100)))))
        midi_events.append((end * 120, 0, pitch, 0))
    previous = 0
    for tick, on, pitch, velocity in sorted(midi_events):
        track.append(mido.Message("note_on" if on else "note_off", note=pitch, velocity=velocity, time=tick - previous, channel=channel))
        previous = tick
    mid.save(folder / f"{inst}.mid")
    return len(notes)
