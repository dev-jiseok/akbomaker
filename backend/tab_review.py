"""Compile explicitly reviewed PDF TAB evidence, never infer missing music.

The HTTP caller must verify both immutable hashes and explicit confirmation.
Drafts may be incomplete; build() requires a decision for every selected glyph
and explicit musical settings. PDF reading, OCR, audio and file writes are not
part of this module.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

Name = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NoteType = Literal["whole", "half", "quarter", "eighth", "16th", "32nd", "64th"]
LENGTHS = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1),
           "eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
           "64th": Fraction(1, 16), "128th": Fraction(1, 32), "256th": Fraction(1, 64)}
WARNINGS = [
    "PDF 숫자 후보와 직접 확인한 줄·프렛·박자를 바탕으로 만든 TAB 악보입니다. 원본 PDF와 완전히 동일하다는 자동 검증은 아닙니다.",
    "튜닝·카포·박자·템포·음표 길이와 누락된 숫자의 추가 여부는 사용자가 확인한 값입니다. 입력하지 않은 가사·슬라이드·벤딩·다이내믹·반복 등은 복원되지 않습니다.",
    "악보는 실제 튜닝+카포+프렛 음높이를 기록합니다. 기타·베이스 관례의 숨은 옥타브 이조를 추가하지 않습니다.",
    "첫 입력 음표가 있는 마디부터 원본 마디 번호를 유지합니다. 그 앞의 빈 마디는 만들지 않으며, 입력한 마디 번호 사이의 빈 마디는 시간을 유지하는 쉼으로 남깁니다. 이 쉼은 PDF에서 자동 인식한 표기가 아닙니다.",
]


def _text(value):
    if any(ord(ch) < 32 and ch not in "\t\n\r" or 0xD800 <= ord(ch) <= 0xDFFF or ord(ch) in {0xFFFE, 0xFFFF} for ch in value):
        raise ValueError("사용할 수 없는 문자가 있어요.")
    return value


class StrictModel(BaseModel):
    model_config = {"extra": "forbid", "strict": True}


class ReviewRow(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=170, pattern=r"^[A-Za-z0-9_:-]+$")]
    staff_id: Name
    decision: Literal["note", "exclude"]
    reason: Annotated[str, Field(max_length=200)] = ""
    measure: Annotated[int, Field(ge=1, le=600)] | None = None
    onset: Annotated[str, Field(max_length=24)] | None = None
    type: NoteType | None = None
    dots: Annotated[int, Field(ge=0, le=2)] = 0
    string: Annotated[int, Field(ge=1, le=6)] | None = None
    fret: Annotated[int, Field(ge=0, le=36)] | None = None
    muted: bool = False

    @field_validator("reason")
    @classmethod
    def printable_reason(cls, value):
        return _text(value)


class ReviewDraft(StrictModel):
    source_sha256: Digest
    coordinate_sha256: Digest
    instrument: Literal["bass", "guitar"]
    title: Annotated[str, Field(max_length=160)]
    tuning: Annotated[list[Annotated[int, Field(ge=12, le=127)]], Field(min_length=4, max_length=6)]
    capo: Annotated[int, Field(ge=0, le=12)]
    beats: Annotated[int, Field(ge=1, le=12)]
    beat_type: Literal[2, 4, 8, 16]
    tempo: Annotated[int, Field(ge=20, le=300)]
    staff_ids: Annotated[list[Name], Field(min_length=1, max_length=120)]
    rows: Annotated[list[ReviewRow], Field(max_length=2000)]

    @field_validator("beat_type", mode="before")
    @classmethod
    def strict_beat_type(cls, value):
        if type(value) is not int:
            raise ValueError("박자 분모는 정수로 입력해주세요.")
        return value

    @field_validator("title")
    @classmethod
    def printable_title(cls, value):
        return _text(value)


def _onset(raw, bar):
    if not isinstance(raw, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,3})(?:/[1-9][0-9]{0,3})?", raw):
        raise ValueError("음표 시작 위치는 0 또는 1/2 같은 음수가 아닌 분수로 입력해주세요.")
    value = Fraction(raw)
    if 64 % value.denominator or not 0 <= value < bar:
        raise ValueError("음표 시작 위치는 마디 안의 1/64박 단위(4분음표=1)로 입력해주세요.")
    return value


def _evidence(analysis, draft):
    if not isinstance(analysis, dict) or analysis.get("schema_version") != 1 or analysis.get("source_sha256") != draft.source_sha256:
        raise ValueError("PDF 원본과 검토 자료가 일치하지 않아요.")
    pages = analysis.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 4:
        raise ValueError("PDF 검토 페이지를 확인해주세요.")
    staffs = {}
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("staffs"), list):
            raise ValueError("PDF 검토 자료의 줄 구성이 올바르지 않아요.")
        for staff in page["staffs"]:
            if not isinstance(staff, dict) or not isinstance(staff.get("id"), str) or staff["id"] in staffs:
                raise ValueError("PDF 검토 자료의 줄 식별자가 올바르지 않아요.")
            staffs[staff["id"]] = staff
    if len(staffs) > 120 or len(set(draft.staff_ids)) != len(draft.staff_ids):
        raise ValueError("선택한 TAB 줄이 중복되거나 너무 많아요.")
    evidence = {}
    for staff_id in draft.staff_ids:
        staff = staffs.get(staff_id)
        if (staff is None or staff.get("kind") != "tab" or type(staff.get("line_count")) is not int
                or staff["line_count"] != len(draft.tuning)):
            raise ValueError("선택한 TAB 줄 수와 악기 튜닝이 일치해야 해요.")
        digits = staff.get("digits")
        if not isinstance(digits, list) or len(digits) > 2000:
            raise ValueError("TAB 숫자 후보가 올바르지 않거나 너무 많아요.")
        for digit in digits:
            if not isinstance(digit, dict) or not isinstance(digit.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", digit["id"]):
                raise ValueError("TAB 숫자 후보의 식별자를 확인해주세요.")
            ident = f"{staff_id}:{digit['id']}"
            if ident in evidence:
                raise ValueError("TAB 숫자 후보가 중복되었어요.")
            evidence[ident] = {"staff_id": staff_id, "raw": digit}
    if len(evidence) > 2000:
        raise ValueError("TAB 숫자는 2,000개 이하로 나누어주세요.")
    return evidence


def _events(analysis, draft):
    count = 4 if draft.instrument == "bass" else 6
    if len(draft.tuning) != count or any(a < b for a, b in zip(draft.tuning, draft.tuning[1:])):
        raise ValueError("악기에 맞는 모든 줄의 튜닝을 고음 줄부터 입력해주세요.")
    evidence = _evidence(analysis, draft)
    seen, events, exclusions = set(), [], []
    bar = Fraction(4 * draft.beats, draft.beat_type)
    for row in draft.rows:
        if row.id in seen or row.staff_id not in draft.staff_ids:
            raise ValueError("검토 항목이 중복되었거나 선택하지 않은 TAB 줄에 속해요.")
        seen.add(row.id)
        original = evidence.get(row.id)
        manual = bool(re.fullmatch(r"manual-[A-Za-z0-9_-]{1,80}", row.id))
        if (original is None and not manual) or (original and original["staff_id"] != row.staff_id):
            raise ValueError("PDF에 없는 숫자는 manual-로 시작하는 고유 식별자로 추가해주세요.")
        if row.decision == "exclude":
            if not row.reason.strip():
                raise ValueError("제외한 숫자마다 원본을 확인한 이유를 적어주세요.")
            exclusions.append(row.id)
            continue
        if any(value is None for value in (row.measure, row.onset, row.type, row.string, row.fret)):
            raise ValueError("모든 음표의 마디·시작 위치·길이·줄·프렛을 직접 확인해주세요.")
        if row.string > count:
            raise ValueError("악기의 줄 수 범위 안에서 줄 번호를 선택해주세요.")
        start = (row.measure - 1) * bar + _onset(row.onset, bar)
        duration = LENGTHS[row.type] * sum((Fraction(1, 2 ** index) for index in range(row.dots + 1)), Fraction())
        end = start + duration
        midi = draft.tuning[row.string - 1] + draft.capo + row.fret
        if not 12 <= midi <= 127:
            raise ValueError("튜닝·카포·프렛 조합이 지원하는 MIDI 음높이 범위를 벗어났어요.")
        if end > 600 * bar:
            raise ValueError("음표가 600마디 범위를 넘어요. 악보를 나누어주세요.")
        events.append({"row": row, "start": start, "end": end, "midi": midi, "duration": duration})
    if set(evidence) - seen:
        raise ValueError("선택한 TAB의 모든 숫자 후보를 음표로 확인하거나 이유를 적어 제외해주세요.")
    if not events:
        raise ValueError("확인한 음표가 하나 이상 필요해요.")
    events.sort(key=lambda event: (event["row"].string, event["start"], event["row"].id))
    previous = {}
    for event in events:
        string = event["row"].string
        if string in previous and previous[string] > event["start"]:
            raise ValueError("같은 줄의 음표가 시간상 겹쳐요. 줄 번호·위치·길이를 확인해주세요.")
        previous[string] = event["end"]
    return events, bar, exclusions, evidence


def _add(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if text is not None:
        node.text = str(text)
    return node


def _pitch(midi):
    names = (("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
             ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0))
    step, alter = names[midi % 12]
    return step, alter, midi // 12 - 1


def _pieces(length):
    options = sorted(((value, name, dots) for name, base in LENGTHS.items() for dots in range(3)
                      if (value := base * sum((Fraction(1, 2 ** i) for i in range(dots + 1)), Fraction())).denominator <= 64), reverse=True)
    while length:
        for value, name, dots in options:
            if value <= length:
                yield value, name, dots
                length -= value
                break
        else:
            raise ValueError("표현할 수 없는 음표 길이예요.")


def _rest(measure, length, voice):
    # Filler silence belongs to a single string voice, not an inferred PDF rest.
    for duration, kind, dots in _pieces(length):
        note = _add(measure, "note", **{"print-object": "no", "print-spacing": "no"})
        _add(note, "rest")
        _add(note, "duration", int(duration * 64))
        _add(note, "voice", voice)
        _add(note, "type", kind)
        for _ in range(dots):
            _add(note, "dot")
        _add(note, "staff", 1)


def _note(measure, event, duration, kind, dots, voice, start, end):
    row = event["row"]
    note = _add(measure, "note")
    step, alter, octave = _pitch(event["midi"])
    pitch = _add(note, "pitch")
    _add(pitch, "step", step)
    if alter:
        _add(pitch, "alter", alter)
    _add(pitch, "octave", octave)
    _add(note, "duration", int(duration * 64))
    ties = (["stop"] if start > event["start"] else []) + (["start"] if end < event["end"] else [])
    for value in ties:
        _add(note, "tie", type=value)
    _add(note, "voice", voice)
    _add(note, "type", kind)
    for _ in range(dots):
        _add(note, "dot")
    _add(note, "stem", "up")
    if row.muted:
        _add(note, "notehead", "x")
    _add(note, "staff", 1)
    notation = _add(note, "notations")
    for value in ties:
        _add(notation, "tied", type=value)
    technical = _add(notation, "technical")
    _add(technical, "string", row.string)
    _add(technical, "fret", row.fret)
    return note


def _beams(notes, beat):
    """Beam only explicit contiguous short notes within a simple beat group."""
    groups, group = [], []
    for note, onset, duration in notes:
        base = LENGTHS[note.findtext("type")]
        eligible = base <= Fraction(1, 2) and note.find("tie") is None
        bucket = onset // beat
        if (not eligible or group and (group[-1][1] + group[-1][2] != onset or group[0][1] // beat != bucket)
                or eligible and (onset + duration > (bucket + 1) * beat)):
            if len(group) > 1:
                groups.append(group)
            group = []
        if eligible and onset + duration <= (bucket + 1) * beat:
            group.append((note, onset, duration))
    if len(group) > 1:
        groups.append(group)
    for group in groups:
        levels = [max(0, LENGTHS[n.findtext("type")].denominator.bit_length() - 1) for n, _, _ in group]
        for index, (note, _, _) in enumerate(group):
            # Insert beam before notations according to the MusicXML schema.
            insertion = list(note).index(note.find("notations"))
            for level in range(1, levels[index] + 1):
                previous = index > 0 and levels[index - 1] >= level
                following = index + 1 < len(group) and levels[index + 1] >= level
                value = "continue" if previous and following else "end" if previous else "begin" if following else "backward hook" if index else "forward hook"
                beam = ET.Element("beam", number=str(level))
                beam.text = value
                note.insert(insertion, beam)
                insertion += 1


def build(analysis: dict, payload: dict | ReviewDraft):
    """Return editable TAB MusicXML + an auditable, non-inferred review record."""
    draft = payload if isinstance(payload, ReviewDraft) else ReviewDraft.model_validate(payload)
    events, bar, exclusions, evidence = _events(analysis, draft)
    first_index = min(event["row"].measure - 1 for event in events)
    last_measure = max(int((event["end"] - Fraction(1, 64)) // bar) + 1 for event in events)
    measures = last_measure - first_index
    root = ET.Element("score-partwise", version="4.0")
    _add(_add(root, "work"), "work-title", draft.title.strip() or "검토한 TAB 악보")
    identification = _add(root, "identification")
    _add(_add(identification, "encoding"), "software", "AkboMaker reviewed PDF TAB")
    miscellaneous = _add(identification, "miscellaneous")
    _add(miscellaneous, "miscellaneous-field", draft.source_sha256, name="source-pdf-sha256")
    _add(miscellaneous, "miscellaneous-field", "user-reviewed-not-exact-pdf-transcription", name="recognition-status")
    definition = _add(_add(root, "part-list"), "score-part", id="P1")
    _add(definition, "part-name", "Bass TAB" if draft.instrument == "bass" else "Guitar TAB")
    _add(_add(definition, "score-instrument", id="I1"), "instrument-name", draft.instrument)
    midi = _add(definition, "midi-instrument", id="I1")
    _add(midi, "midi-channel", 1)
    _add(midi, "midi-program", 34 if draft.instrument == "bass" else 25)
    part = _add(root, "part", id="P1")
    voices = sorted({event["row"].string for event in events})
    by_voice = {voice: [event for event in events if event["row"].string == voice] for voice in voices}
    beat = Fraction(4, draft.beat_type) * (3 if draft.beat_type == 8 and draft.beats in {6, 9, 12} else 1)
    silent_measures = []
    for index in range(first_index, last_measure):
        measure = _add(part, "measure", number=str(index + 1))
        if index == first_index:
            attrs = _add(measure, "attributes")
            _add(attrs, "divisions", 64)
            time = _add(attrs, "time")
            _add(time, "beats", draft.beats)
            _add(time, "beat-type", draft.beat_type)
            _add(_add(attrs, "clef"), "sign", "TAB")
            details = _add(attrs, "staff-details")
            _add(details, "staff-lines", len(draft.tuning))
            for line, value in enumerate(reversed(draft.tuning), 1):
                step, alter, octave = _pitch(value)
                tuning = _add(details, "staff-tuning", line=str(line))
                _add(tuning, "tuning-step", step)
                if alter:
                    _add(tuning, "tuning-alter", alter)
                _add(tuning, "tuning-octave", octave)
            _add(details, "capo", draft.capo)
            direction = _add(measure, "direction")
            metronome = _add(_add(direction, "direction-type"), "metronome")
            _add(metronome, "beat-unit", "quarter")
            _add(metronome, "per-minute", draft.tempo)
            _add(direction, "sound", tempo=str(draft.tempo))
        bar_start, bar_end = index * bar, (index + 1) * bar
        if not any(event["end"] > bar_start and event["start"] < bar_end for event in events):
            silent_measures.append(index + 1)
        for voice_index, voice in enumerate(voices):
            if voice_index:
                _add(_add(measure, "backup"), "duration", int(bar * 64))
            cursor, visible = bar_start, []
            for event in by_voice[voice]:
                if event["end"] <= bar_start or event["start"] >= bar_end:
                    continue
                start, end = max(event["start"], bar_start), min(event["end"], bar_end)
                _rest(measure, start - cursor, voice)
                cursor = start
                for duration, kind, dots in _pieces(end - start):
                    note = _note(measure, event, duration, kind, dots, voice, cursor, cursor + duration)
                    visible.append((note, cursor - bar_start, duration))
                    cursor += duration
            _rest(measure, bar_end - cursor, voice)
            _beams(visible, beat)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(xml) > 8 * 1024 * 1024 or len(part.findall("measure/note")) > 30_000:
        raise ValueError("생성된 TAB 악보가 너무 커요. 선택한 줄·마디를 나누어주세요.")
    summary = {"schema_version": 1, "kind": "reviewed-pdf-tab", "part_id": "P1",
               "source_sha256": draft.source_sha256, "coordinate_sha256": draft.coordinate_sha256,
               "review": draft.model_dump(), "evidence_count": len(evidence), "note_count": len(events),
               "excluded_count": len(exclusions), "manual_count": sum(event["row"].id.startswith("manual-") for event in events),
               "measure_count": measures, "first_measure": first_index + 1, "last_measure": last_measure,
               "omitted_leading_measures": first_index, "silent_gap_measures": silent_measures,
               "exact_pdf_transcription": False,
               "timing_origin": "user-confirmed", "warnings": list(WARNINGS)}
    if any(event["row"].muted for event in events):
        summary["warnings"].append("뮤트 음은 x 음표로 표시하며, 프렛의 음높이는 위치 정보입니다. 실제 무음·노이즈 소리는 재생으로 복원하지 않습니다.")
    return xml, summary
