"""Bounded, non-network MusicXML/MXL import into the current editing model.

Unsupported musical structures are rejected, never silently quantized away.
The uploaded source is retained separately; rendering is regenerated in our style.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import PurePosixPath
from uuid import uuid4

from .editing import ScoreEdit, create_document, validate_edit
from .rhythm import normalize_meters, measure_map

UPLOAD_LIMIT = 2 * 1024 * 1024
XML_LIMIT = 8 * 1024 * 1024
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def number(value):
    # Bound decimal parsing before Fraction: XML numeric values must not use
    # huge exponents or integer strings to trigger unbounded big-int work.
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?\d{1,9}(?:\.\d{1,6})?", value.strip()):
        raise ValueError("MusicXML의 숫자 값이 올바르지 않거나 너무 커요.")
    return Fraction(value.strip())


def integer(value):
    parsed = number(value)
    if parsed.denominator != 1:
        raise ValueError("정수로 보존할 수 없는 MusicXML 표기가 있어요.")
    return int(parsed)


def safe_xml(data):
    if len(data) > XML_LIMIT:
        raise ValueError("압축을 푼 악보는 8MB 이하로 선택해주세요.")
    try:
        text = data.decode("utf-8-sig")
        # External MusicXML DOCTYPE declarations are common and ElementTree
        # does not fetch them. Internal DTDs/entities are never accepted.
        if re.search(r"<!ENTITY|<!DOCTYPE[^>]*\[", text, re.I):
            raise ValueError("내부 DTD·엔티티가 있는 XML은 가져올 수 없어요.")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError):
        raise ValueError("올바른 UTF-8 MusicXML 파일을 선택해주세요.") from None
    if sum(1 for _ in root.iter()) > 100_000:
        raise ValueError("악보 구조가 너무 커요. 파트나 곡을 나누어주세요.")
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


def unpack(data, filename):
    if not data or len(data) > UPLOAD_LIMIT:
        raise ValueError("MusicXML/MXL 파일은 비어 있지 않은 2MB 이하 파일을 선택해주세요.")
    extension = PurePosixPath(filename.lower()).suffix
    if extension not in {".xml", ".musicxml", ".mxl"}:
        raise ValueError(".musicxml, .xml, .mxl을 지원합니다. PDF·이미지 인식은 아직 지원하지 않아요.")
    if extension != ".mxl":
        return data, safe_xml(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            if len(files) > 50 or sum(f.file_size for f in files) > XML_LIMIT:
                raise ValueError("압축 악보의 파일 수나 크기가 제한을 초과해요.")
            container = safe_xml(archive.read("META-INF/container.xml"))
            name = container.find("rootfiles/rootfile")
            path = name.get("full-path", "") if name is not None else ""
            if not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path:
                raise ValueError("압축 악보의 루트 파일 경로가 올바르지 않아요.")
            source = archive.read(path)
            return source, safe_xml(source)
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError):
        raise ValueError("올바른 비밀번호 없는 MXL 파일을 선택해주세요.") from None


def inspect(data, filename):
    _, root = unpack(data, filename)
    if root.tag != "score-partwise":
        raise ValueError("파트별 score-partwise MusicXML을 선택해주세요. score-timewise·opus는 아직 지원하지 않아요.")
    names = {p.get("id"): p.findtext("part-name", p.get("id", "파트")) for p in root.findall("part-list/score-part")}
    parts = [{"id": p.get("id", ""), "name": names.get(p.get("id"), "파트"), "measures": len(p.findall("measure"))} for p in root.findall("part")]
    if not parts or len(parts) > 64 or len({p["id"] for p in parts}) != len(parts) or any(not p["id"] or len(p["id"]) > 80 for p in parts):
        raise ValueError("악보의 파트 구성을 읽을 수 없어요.")
    return {"parts": parts, "title": root.findtext("work/work-title") or root.findtext("movement-title") or "가져온 악보"}


def import_document(data, filename, part_id, inst, *, current=None):
    source, root = unpack(data, filename)
    listing = inspect(data, filename)
    if part_id not in {p["id"] for p in listing["parts"]}:
        raise ValueError("가져올 파트를 선택해주세요.")
    part = next(p for p in root.findall("part") if p.get("id") == part_id)
    unsupported = {
        "time-modification": "셋잇단음표 등 잇단음표", "grace": "꾸밈음", "repeat": "도돌이표",
        "ending": "반복 괄호", "harmony": "코드 기호", "wedge": "크레셴도", "dynamics": "다이내믹",
        "slur": "슬러", "slide": "슬라이드", "glissando": "글리산도", "hammer-on": "해머링", "pull-off": "풀링",
        "pedal": "페달", "ornaments": "장식음", "segno": "세뇨", "coda": "코다",
    }
    found = [name for tag, name in unsupported.items() if part.find(f".//{tag}") is not None]
    if part.find(".//cue") is not None or part.find(".//swing") is not None:
        found.append("큐 음표/스윙")
    if any(any(key in sound.attrib for key in ("dacapo", "dalsegno", "tocoda", "fine")) for sound in part.findall(".//sound")):
        found.append("다카포/달세뇨 재생 순서")
    if any(n.tag not in {"tied", "technical", "articulations"} for notation in part.findall(".//notations") for n in notation):
        found.append("추가 음표 연주 기호")
    if any(n.tag not in {"string", "fret", "bend", "open-string"} for technical in part.findall(".//technical") for n in technical):
        found.append("추가 주법/운지 기호")
    if any(n.tag not in {"words", "rehearsal", "metronome"} for direction in part.findall(".//direction-type") for n in direction):
        found.append("추가 구간 지시 기호")
    if found:
        raise ValueError("이 편집기에서 아직 보존하지 못하는 표기가 있어 가져오지 않았어요: " + ", ".join(found) + ". 원본은 변경되지 않았습니다.")
    measures = part.findall("measure")
    if not measures or len(measures) > 600 or any(m.get("implicit") == "yes" for m in measures):
        raise ValueError("못갖춘마디 없는 악보를 최대 600마디까지 가져올 수 있어요.")
    meters, active_meter, total = [], (4, 4), 0
    for index, measure in enumerate(measures):
        times = measure.findall("attributes/time")
        values = []
        for time in times:
            if len(time.findall("beats")) != 1 or len(time.findall("beat-type")) != 1 or time.find("senza-misura") is not None:
                raise ValueError("복합 박자표·자유 박자는 아직 지원하지 않아요.")
            values.append((integer(time.findtext("beats")), integer(time.findtext("beat-type"))))
        if len(set(values)) > 1:
            raise ValueError("서로 다른 보표의 동시 박자는 아직 지원하지 않아요.")
        if values:
            active_meter = values[0]
        if not meters or active_meter != (meters[-1]["beats"], meters[-1]["beat_type"]):
            meters.append({"measure": index + 1, "beats": active_meter[0], "beat_type": active_meter[1]})
            normalize_meters(meters)
        total += active_meter[0] * 16 // active_meter[1]
    bars = measure_map(total, meters)
    tempos = []
    for sound in part.findall(".//sound[@tempo]"):
        tempos.append(number(sound.get("tempo")))
    if not tempos:
        for metro in part.findall(".//metronome"):
            factor = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1), "eighth": Fraction(1, 2), "16th": Fraction(1, 4)}.get(metro.findtext("beat-unit"))
            dots = len(metro.findall("beat-unit-dot"))
            if factor is None or dots > 2 or len(metro.findall("beat-unit")) != 1:
                raise ValueError("박 단위가 명확한 고정 메트로놈 템포만 가져올 수 있어요.")
            tempos.append(number(metro.findtext("per-minute", "120")) * factor * sum(Fraction(1, 2 ** i) for i in range(dots + 1)))
    bpm = tempos[0] if tempos else Fraction(120)
    if bpm.denominator != 1 or not 40 <= bpm <= 240 or any(t != bpm for t in tempos):
        raise ValueError("현재는 BPM 40~240의 정수 고정 템포만 가져올 수 있어요.")
    doc = create_document([], inst, listing["title"][:180].strip() or "가져온 악보", int(bpm), total * 15 / float(bpm), meters=meters)
    if current:
        doc.update(revision=current["revision"], timing_bpm=current.get("timing_bpm", current["bpm"]),
                   audio_offset=current.get("audio_offset", 0), layout=current["layout"].copy())
        # Imported music can have fewer bars than the previous output.
        for key in ("system_breaks", "page_breaks"):
            doc["layout"][key] = [b for b in doc["layout"].get(key, []) if b <= len(measures)]
    doc.update(ticks=total, meters=meters)
    divisions, transpose, clefs = Fraction(1), {}, {}
    keyboard_staves = set()
    notes, ties, lyrics, annotations = [], {}, {}, []
    info = next((p for p in root.findall("part-list/score-part") if p.get("id") == part_id), None)
    drum_map = {p.get("id"): integer(p.findtext("midi-unpitched")) - 1 for p in info.findall("midi-instrument") if p.findtext("midi-unpitched")} if info is not None else {}
    warnings = ["박자표·변박과 음표 시간을 보존하고 16분음표 편집 격자로 다시 배치합니다. 원본 레이아웃·보이스 분리·조표/이명동음 표기는 보존하지 않습니다. 선택한 원본 파일도 보관해주세요."]
    for index, measure in enumerate(measures):
        position, previous_start, previous_rest = Fraction(0), Fraction(0), True
        bar = bars[index]
        bar_length = bar["end"] - bar["start"]
        extent, seen_note = Fraction(0), False
        section, cue = "", ""
        for node in measure:
            if node.tag == "attributes":
                if inst in {"piano", "synthesizer"} and node.find("staves") is not None:
                    count = integer(node.findtext("staves"))
                    if count not in {1, 2}:
                        raise ValueError("건반 악보는 오른손·왼손 두 보표까지 가져올 수 있어요.")
                    keyboard_staves.update(str(s) for s in range(1, count + 1))
                if node.find("divisions") is not None:
                    divisions = number(node.findtext("divisions"))
                    if divisions <= 0:
                        raise ValueError("MusicXML divisions 값이 올바르지 않아요.")
                if (seen_note or position != 0) and node.find("time") is not None:
                    raise ValueError("박자 변경은 마디 시작에 지정해주세요.")
                for trans in node.findall("transpose"):
                    transpose[trans.get("number", "all")] = integer(trans.findtext("chromatic", "0")) + 12 * integer(trans.findtext("octave-change", "0"))
                for clef in node.findall("clef"):
                    clefs[clef.get("number", "1")] = clef.findtext("sign")
                tab_details = next((d for d in node.findall("staff-details") if d.find("staff-tuning") is not None), None)
                if doc["tab"] and tab_details is not None:
                    tuning_nodes = sorted(tab_details.findall("staff-tuning"), key=lambda t: integer(t.get("line")), reverse=True)
                    tuning = [(integer(t.findtext("tuning-octave")) + 1) * 12 + STEPS[t.findtext("tuning-step")] + integer(t.findtext("tuning-alter", "0")) for t in tuning_nodes]
                    capo = integer(tab_details.findtext("capo", "0"))
                    if index and (tuning != doc["tab"]["tuning"] or capo != doc["tab"]["capo"]):
                        raise ValueError("곡 중간의 튜닝·카포 변경은 아직 지원하지 않아요.")
                    doc["tab"].update(tuning=tuning, capo=capo)
            elif node.tag in {"backup", "forward"}:
                size = number(node.findtext("duration", "0")) * 4 / divisions
                if size <= 0:
                    raise ValueError("MusicXML 성부 이동 길이가 올바르지 않아요.")
                position += size * (-1 if node.tag == "backup" else 1)
                if not 0 <= position <= bar_length:
                    raise ValueError("MusicXML 성부 위치가 마디를 벗어나요.")
                extent = max(extent, position)
                previous_rest = True
            elif node.tag == "direction":
                section = node.findtext("direction-type/rehearsal", section)
                words = node.findtext("direction-type/words", "")
                if words and not words.startswith("Capo "):
                    cue = (cue + " " + words).strip()
                if node.get("placement") and node.findtext("offset", "0") != "0" and (section or cue):
                    raise ValueError("마디 중간의 구간·메모 표기는 아직 지원하지 않아요.")
            elif node.tag == "note":
                seen_note = True
                size = number(node.findtext("duration", "0")) * 4 / divisions
                chord = node.find("chord") is not None
                if chord and previous_rest:
                    raise ValueError("화음의 기준 음표가 올바르지 않아요.")
                start = previous_start if chord else position
                if size <= 0 or size.denominator != 1 or start.denominator != 1 or start < 0 or start + size > bar_length:
                    raise ValueError("16분음표 격자로 정확히 보존할 수 없는 음표·쉼표가 있어요. 반올림하지 않고 가져오기를 중단했습니다.")
                if not chord:
                    previous_start, position = start, start + size
                extent = max(extent, start + size)
                absolute = bar["start"] + int(start)
                texts = [l.findtext("text", "").strip() for l in node.findall("lyric")]
                if len(texts) > 1 or any(l.find("extend") is not None or l.find("elision") is not None or l.findtext("syllabic", "single") != "single" for l in node.findall("lyric")):
                    raise ValueError("여러 절 가사·음절 하이픈·멜리스마 표기는 아직 정확하게 보존할 수 없어요.")
                if texts and texts[0]:
                    if absolute in lyrics and lyrics[absolute] != texts[0]:
                        raise ValueError("같은 위치에 서로 다른 파트 가사가 겹쳐요.")
                    lyrics[absolute] = texts[0]
                staff = node.findtext("staff", "1")
                if inst in {"piano", "synthesizer"}:
                    if staff not in {"1", "2"}:
                        raise ValueError("건반 악보는 오른손·왼손 두 보표까지 가져올 수 있어요.")
                    keyboard_staves.add(staff)
                previous_rest = node.find("rest") is not None
                if previous_rest:
                    continue
                if node.find("unpitched") is not None:
                    instrument = node.find("instrument")
                    pitch = drum_map.get(instrument.get("id")) if instrument is not None else None
                    if inst != "drums" or pitch is None:
                        raise ValueError("드럼 악기와 MIDI 타악기 정보가 있는 MusicXML을 선택해주세요.")
                else:
                    if inst == "drums":
                        raise ValueError("음정 악보를 드럼으로 가져올 수 없어요. 타악기 파트를 선택해주세요.")
                    pitch = (integer(node.findtext("pitch/octave")) + 1) * 12 + STEPS[node.findtext("pitch/step")] + number(node.findtext("pitch/alter", "0")) + transpose.get(staff, transpose.get("all", 0))
                    if Fraction(pitch).denominator != 1:
                        raise ValueError("미분음은 아직 지원하지 않아요.")
                    pitch = int(pitch)
                technical = node.find("notations/technical")
                note = {"id": uuid4().hex, "start": absolute, "length": int(size), "pitch": pitch, "velocity": 80}
                if inst in {"piano", "synthesizer"}:
                    note["hand"] = "left" if staff == "2" else "right"
                if technical is not None and technical.find("string") is not None and doc["tab"]:
                    note.update(string=integer(technical.findtext("string")), fret=integer(technical.findtext("fret")))
                if technical is not None and technical.find("bend") is not None:
                    bend = technical.find("bend")
                    if bend.find("release") is not None or bend.find("pre-bend") is not None:
                        raise ValueError("프리벤드·릴리스는 아직 지원하지 않아요.")
                    amount = number(bend.findtext("bend-alter"))
                    if amount.denominator != 1:
                        raise ValueError("반음 미만 벤딩은 아직 지원하지 않아요.")
                    note["bend"] = int(amount)
                marks = node.findall("notations/articulations/*")
                if len(marks) > 1 or any(m.tag not in {"accent", "staccato", "tenuto"} for m in marks):
                    raise ValueError("이 연주 표시는 아직 보존할 수 없어요.")
                if marks:
                    note["articulation"] = marks[0].tag
                if inst in {"bass", "guitar"} and node.findtext("notehead") == "x":
                    note["muted"] = True
                tie_types = {t.get("type") for t in node.findall("tie")}
                key = (staff, node.findtext("voice", "1"), pitch)
                if "stop" in tie_types:
                    if key not in ties or notes[ties[key]]["start"] + notes[ties[key]]["length"] != absolute:
                        raise ValueError("타이로 연결된 음표 위치가 올바르지 않아요.")
                    note_index = ties[key]
                    notes[note_index]["length"] += int(size)
                else:
                    note_index = len(notes)
                    notes.append(note)
                if "start" in tie_types:
                    ties[key] = note_index
                else:
                    ties.pop(key, None)
        if seen_note and extent != bar_length:
            raise ValueError("마디 길이가 박자표와 맞지 않아요. 못갖춘마디·생략된 쉼표를 확인해주세요.")
        if section or cue:
            annotations.append({"measure": index + 1, "section": section, "cue": cue})
    if ties:
        raise ValueError("끝이 없는 타이가 있어 가져오지 않았어요.")
    combined = {}
    for note in notes:
        key = (note["start"], note["length"], note["pitch"])
        if key in combined:
            combined[key].update({k: v for k, v in note.items() if k not in {"id", "start", "length", "pitch"}})
        else:
            combined[key] = note
    doc.update(notes=list(combined.values()), lyrics=[{"id": uuid4().hex, "start": s, "text": t} for s, t in sorted(lyrics.items())], annotations=annotations)
    if inst in {"piano", "synthesizer"}:
        if any(s not in {"1", "2"} for s in clefs) or any(c not in {"G", "F"} for c in clefs.values()):
            raise ValueError("높은음자리·낮은음자리의 두 보표 건반 악보를 선택해주세요.")
        grand = "2" in clefs or "2" in keyboard_staves
        doc["keyboard"] = {"mode": "grand" if grand else "single", "split_pitch": 60}
        warnings.append("건반의 위·아래 보표 배정을 보존합니다. 보표 안의 여러 성부와 음자리표 변경은 다시 조판합니다.")
    elif inst not in {"bass", "guitar"} and len(clefs) > 1:
        raise ValueError("피아노 양손 등 여러 오선 파트는 아직 보존하지 못해요. 단일 보표 파트를 선택해주세요.")
    edit = ScoreEdit(base_revision=doc["revision"], **{k: doc[k] for k in ("title", "bpm", "ticks", "meters", "notes", "lyrics", "annotations", "tab", "keyboard", "layout")})
    result = validate_edit(edit, doc)
    if current:
        result["revision"] = current["revision"]
    return result, source, warnings
