"""Editable 16th-note documents; notation/MIDI are derived from the same notes."""
import json
import math
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .score import export_score, quantize
from .tablature import default_tab, assign_positions
from .rhythm import measure_map, normalize_meters, drum_attack_lengths
from .drum_mapping import DRUM_PITCHES


def xml_text(value):
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", value):
        raise ValueError("악보에 사용할 수 없는 제어 문자가 포함되어 있어요.")
    return value

class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    start: StrictInt = Field(ge=0)
    length: StrictInt = Field(ge=1, le=16384)
    pitch: StrictInt = Field(ge=0, le=127)
    velocity: StrictInt = Field(ge=1, le=127)
    string: StrictInt | None = Field(default=None, ge=1, le=7)
    fret: StrictInt | None = Field(default=None, ge=0, le=24)
    articulation: Literal["none", "accent", "staccato", "tenuto"] = "none"
    muted: bool = False
    bend: StrictInt = Field(default=0, ge=0, le=12)
    hand: Literal["auto", "right", "left"] = "auto"


class KeyboardSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["grand", "single"] = "grand"
    split_pitch: StrictInt = Field(default=60, ge=21, le=108)


class TabSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["staff", "both", "tab"] = "both"
    order: Literal["tab-first", "staff-first"] = "tab-first"
    capo: StrictInt = Field(default=0, ge=0, le=12)
    tuning: list[StrictInt] = Field(min_length=4, max_length=7)


class Lyric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    start: StrictInt = Field(ge=0)
    text: str = Field(min_length=1, max_length=80)
    _xml_text = field_validator("text")(xml_text)


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure: StrictInt = Field(ge=1)
    section: str = Field(default="", max_length=24)
    cue: str = Field(default="", max_length=100)
    _xml_text = field_validator("section", "cue")(xml_text)


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["practice", "standard", "large"] = "standard"
    measures_per_line: Literal[2, 4] = 4
    show_numbers: bool = True
    beam_group: Literal["beat", "half"] = "beat"
    system_breaks: list[StrictInt] = Field(default_factory=list, max_length=600)
    page_breaks: list[StrictInt] = Field(default_factory=list, max_length=600)


class Meter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure: StrictInt = Field(ge=1, le=600)
    beats: StrictInt
    beat_type: StrictInt


class ScoreEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: str = Field(max_length=80)
    title: str = Field(min_length=1, max_length=180)
    bpm: StrictInt = Field(ge=40, le=240)
    ticks: StrictInt | None = Field(default=None, ge=8, le=14400)
    meters: list[Meter] | None = Field(default=None, min_length=1, max_length=600)
    notes: list[Note] = Field(max_length=30_000)
    annotations: list[Annotation] = Field(default_factory=list, max_length=600)
    layout: Layout
    tab: TabSettings | None = None
    keyboard: KeyboardSettings | None = None
    lyrics: list[Lyric] = Field(default_factory=list, max_length=2000)
    _xml_text = field_validator("title")(xml_text)


def create_document(events, inst, title, bpm, duration, audio_offset=0, meters=None):
    effective_duration = max(.001, duration - audio_offset)
    meters = normalize_meters(meters)
    ticks = measure_map(max(1, math.ceil(effective_duration * bpm / 60 * 4 - 1e-9)), meters, exact=False)[-1]["end"]
    shifted = [(max(0, a - audio_offset), b - audio_offset, p, v) for a, b, p, v in events if b > audio_offset]
    quantized = quantize(shifted, bpm, effective_duration)
    # Drum resonance is not a rhythmic duration. Notate hats in eighths and
    # kick/snare in quarters, shortened at the next onset in that voice.
    if inst == "drums":
        starts = {voice: sorted({n[0] for n in quantized if (n[2] == 36) == voice}) for voice in (True, False)}
        quantized = [(a, min(ticks, a + (2 if p in {42, 46, 49, 51} else 4),
                            next((s for s in starts[p == 36] if s > a), ticks)), p, v) for a, _, p, v in quantized]
    # Duplicate overlapping detections of one pitch describe one sustained note.
    merged = []
    for note in sorted(quantized, key=lambda n: (n[2], n[0])):
        if merged and merged[-1][2] == note[2] and merged[-1][1] > note[0]:
            previous = merged.pop()
            merged.append((previous[0], max(previous[1], note[1]), note[2], max(previous[3], note[3])))
        else:
            merged.append(note)
    notes = [{"id": uuid4().hex, "start": a, "length": b - a, "pitch": p,
              "velocity": min(127, max(1, round(v * 100)))} for a, b, p, v in sorted(merged)]
    tab = default_tab(inst)
    if tab:
        notes = assign_positions(notes, tab["tuning"])
    return {"version": 1, "instrument": inst, "title": title, "bpm": bpm, "timing_bpm": bpm, "audio_offset": audio_offset,
            "ticks": ticks, "meters": meters, "revision": uuid4().hex, "edited": False, "notes": notes,
            "tab": tab, "keyboard": {"mode": "grand", "split_pitch": 60} if inst in {"piano", "synthesizer"} else None,
            "lyrics": [], "annotations": [], "layout": {"preset": "practice" if inst in {"drums", "bass", "guitar"} else "standard",
                                          "measures_per_line": 4, "show_numbers": True, "beam_group": "half" if inst == "drums" else "beat"}}


def persist(document, folder: Path, *, automatic=False):
    """Stage all derived files before replacing any existing score."""
    inst = document["instrument"]
    staging = Path(tempfile.mkdtemp(prefix="score-", dir=folder))
    try:
        notes = [(n["start"], n["start"] + n["length"], n["pitch"], n["velocity"] / 100) for n in document["notes"]]
        export_score([], inst, document["title"], document["bpm"], document["ticks"] / 4 * 60 / document["bpm"],
                     staging, quantized=sorted(notes), layout=document["layout"],
                     annotations=document["annotations"], edited=document["edited"],
                     tab=document.get("tab"), positions=document["notes"], lyrics=document.get("lyrics", []),
                     meters=document.get("meters"), ticks=document["ticks"], keyboard=document.get("keyboard"))
        data = json.dumps(document, ensure_ascii=False)
        (staging / f"{inst}.score.json").write_text(data)
        if automatic:
            (staging / f"{inst}.auto.json").write_text(data)
        for file in staging.iterdir():
            file.replace(folder / file.name)
    finally:
        shutil.rmtree(staging)


def generate_score(events, inst, title, bpm, duration, folder, audio_offset=0, lyric_cues=None, meters=None, *, transcription=None):
    document = create_document(events, inst, title, bpm, duration, audio_offset, meters)
    if inst == "drums":
        document["notes"] = drum_attack_lengths(document["notes"], document["ticks"], document["meters"])
    if transcription is not None:
        document["transcription"] = transcription
    if lyric_cues:
        from .lyrics import timed_lyrics
        document["lyrics"], _ = timed_lyrics(lyric_cues, document)
    persist(document, folder, automatic=True)
    return document


def notation_metadata(document):
    tab = document.get("tab")
    return {"score_meters": normalize_meters(document.get("meters")), "score_tab_mode": tab["mode"] if tab else "staff",
            "score_audio_offset": document.get("audio_offset", 0),
            "score_keyboard": document.get("keyboard"), "score_transcription": document.get("transcription"),
            "score_tab_unassigned": sum(n.get("string") is None for n in document["notes"]) if tab else 0}


def load_document(folder, inst, duration, *, original=False):
    path = folder / f"{inst}.{'auto' if original else 'score'}.json"
    if path.exists():
        document = json.loads(path.read_text())
        # Additive migration: preserve note edits, original snapshot and revision.
        if "timing_bpm" not in document:
            automatic = folder / f"{inst}.auto.json"
            document["timing_bpm"] = json.loads(automatic.read_text()).get("bpm", document["bpm"]) if automatic.exists() else document["bpm"]
        document.setdefault("lyrics", [])
        document.setdefault("audio_offset", 0)
        document.setdefault("meters", normalize_meters())
        # Preserve old output until the user explicitly changes notation. A
        # re-transcription uses the new grand-staff default.
        document.setdefault("keyboard", {"mode": "single", "split_pitch": 60} if inst in {"piano", "synthesizer"} else None)
        # Legacy files used one-beat beams. Keep their saved layout until the
        # user explicitly selects/saves two-beat grouping; new drums use half.
        document["layout"].setdefault("beam_group", "beat")
        if "tab" not in document:
            document["tab"] = default_tab(inst)
            if document["tab"]:
                document["notes"] = assign_positions(document["notes"], document["tab"]["tuning"])
        elif document["tab"]:
            # Existing saved output keeps its staff order until explicitly changed.
            document["tab"].setdefault("order", "staff-first")
            document["tab"].setdefault("capo", 0)
        return document
    # The first version wrote only XML. Reuse the bounded importer so bar
    # lengths and written/concert transposition are handled consistently.
    xml = folder / f"{inst}.musicxml"
    if not xml.is_file():
        raise FileNotFoundError
    from .score_import import import_document
    document, _, _ = import_document(xml.read_bytes(), xml.name, "P1", inst)
    document["edited"] = False
    for kind in ("score", "auto"):
        (folder / f"{inst}.{kind}.json").write_text(json.dumps(document, ensure_ascii=False))
    return document


def validate_edit(edit: ScoreEdit, current: dict):
    ticks = edit.ticks if edit.ticks is not None else current["ticks"]
    meters = normalize_meters([m.model_dump() for m in edit.meters] if edit.meters is not None else current.get("meters"))
    bars = measure_map(ticks, meters)
    seen, by_pitch = set(), {}
    for note in edit.notes:
        if note.id in seen or note.start + note.length > ticks:
            raise ValueError("음표 ID가 중복되었거나 곡의 마지막 마디를 벗어났어요.")
        if current["instrument"] == "drums" and note.pitch not in DRUM_PITCHES:
            raise ValueError("지원하지 않는 드럼 종류예요.")
        seen.add(note.id)
        by_pitch.setdefault(note.pitch, []).append(note)
    for notes in by_pitch.values():
        ordered = sorted(notes, key=lambda n: n.start)
        if any(a.start + a.length > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError("같은 음정의 음표가 겹쳐 있어요. 위치 또는 길이를 조절해주세요.")
    measures = set()
    for item in edit.annotations:
        if item.measure > len(bars) or item.measure in measures:
            raise ValueError("구간 메모의 마디 번호가 중복되었거나 범위를 벗어났어요.")
        measures.add(item.measure)
    data = edit.model_dump(exclude={"base_revision"})
    data["ticks"] = ticks
    data["meters"] = meters
    for key in ("system_breaks", "page_breaks"):
        breaks = data["layout"][key]
        if len(set(breaks)) != len(breaks) or any(b < 2 or b > len(bars) for b in breaks):
            raise ValueError("줄/페이지 시작 마디는 2마디부터 마지막 마디까지 중복 없이 지정해주세요.")
    if "tab" not in edit.model_fields_set:
        data["tab"] = current.get("tab")
    elif edit.tab and current.get("tab"):
        for key, fallback in (("order", "staff-first"), ("capo", 0)):
            if key not in edit.tab.model_fields_set:
                data["tab"][key] = current["tab"].get(key, fallback)
    if "lyrics" not in edit.model_fields_set:
        data["lyrics"] = current.get("lyrics", [])
    if "keyboard" not in edit.model_fields_set:
        data["keyboard"] = current.get("keyboard")
    if data["keyboard"] is not None and current["instrument"] not in {"piano", "synthesizer"}:
        raise ValueError("대보표는 피아노·신디사이저 악보에서 사용할 수 있어요.")
    if any(n.hand != "auto" for n in edit.notes) and current["instrument"] not in {"piano", "synthesizer"}:
        raise ValueError("손 배정은 피아노·신디사이저 악보에서 사용할 수 있어요.")
    # Older clients omit per-note hand assignments when making unrelated edits.
    old_notes = {n["id"]: n for n in current["notes"]}
    for note, model in zip(data["notes"], edit.notes):
        if "hand" not in model.model_fields_set and "hand" in old_notes.get(note["id"], {}):
            note["hand"] = old_notes[note["id"]]["hand"]
    tab = data["tab"]
    if tab is not None:
        inst = current["instrument"]
        valid_count = {"bass": {4, 5, 6}, "guitar": {6, 7}}.get(inst, set())
        tuning = tab["tuning"]
        if len(tuning) not in valid_count or any(p < 0 or p > 103 for p in tuning) or any(a < b for a, b in zip(tuning, tuning[1:])):
            raise ValueError("악기와 맞지 않는 튜닝이에요. 1번 줄부터 높은 음 순서로 입력해주세요.")
        by_string = {}
        for n in data["notes"]:
            string, fret = n["string"], n["fret"]
            if (string is None) != (fret is None):
                raise ValueError("TAB의 줄 번호와 프렛을 함께 입력해주세요.")
            if string is not None:
                if string > len(tuning) or tuning[string - 1] + tab.get("capo", 0) + fret != n["pitch"]:
                    raise ValueError("줄·프렛과 음정이 일치하지 않아요.")
                by_string.setdefault(string, []).append(n)
        for group in by_string.values():
            ordered = sorted(group, key=lambda n: n["start"])
            if any(a["start"] + a["length"] > b["start"] for a, b in zip(ordered, ordered[1:])):
                raise ValueError("한 줄에서 동시에 두 음을 연주할 수 없어요. 줄 또는 길이를 바꿔주세요.")
        data["notes"] = assign_positions(data["notes"], tuning, tab.get("capo", 0))
    elif any(n.string is not None or n.fret is not None for n in edit.notes):
        raise ValueError("이 악보는 TAB 악기가 아니에요.")
    if tab is None and any(n.muted or n.bend for n in edit.notes):
        raise ValueError("뮤트·벤딩 표기는 기타·베이스 TAB에서만 사용할 수 있어요.")
    if any(n.pitch + n.bend > 127 for n in edit.notes):
        raise ValueError("벤딩 음정이 MIDI 음역을 벗어나요.")
    lyric_ids, lyric_starts = set(), set()
    for item in data["lyrics"]:
        if item["start"] >= ticks or item["id"] in lyric_ids or item["start"] in lyric_starts or not item["text"].strip():
            raise ValueError("가사 위치가 중복되었거나 곡 범위를 벗어났어요.")
        lyric_ids.add(item["id"])
        lyric_starts.add(item["start"])
    return {**current, **data, "revision": uuid4().hex, "edited": True}
