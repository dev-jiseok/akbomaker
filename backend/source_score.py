"""MusicXML-native, narrowly scoped editing; never regenerate through ScoreDocument.

The source upload is retained by the caller. This module retains every part and
musical node except the fields explicitly edited, and removes external resources
before returning browser-facing XML. Layout is a separate derived operation.

MusicXML 4.0 references (checked against W3C): pitch/alter encode sound, while
accidental encodes its printed sign; staff-tuning is non-capo and numbered bottom
to top, string is numbered from the highest string; transpose is ADDED to written
pitch. See https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/
{pitch,accidental,staff-tuning,string,capo,transpose,note}/ .
"""
import copy
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

from .audiveris_compat import normalize_export
from .score_import import XML_LIMIT, STEPS, inspect, integer, number, safe_xml, unpack
from .score_preservation import _bounded_depth, _remove_resources, POSITION_ATTRIBUTES

INSTRUMENTS = {"vocal", "bass", "drums", "synthesizer", "guitar", "piano"}
ACCIDENTALS = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
BASE_WARNING = "원본 MusicXML의 모든 파트·리듬·성부·주법을 유지합니다. 표시 가능한 항목만 직접 수정하며, PDF 인식 오류를 자동으로 고치지는 않습니다."
NOTE_ORDER = {name: i for i, name in enumerate((
    "grace", "cue", "chord", "pitch", "unpitched", "rest", "duration", "tie",
    "instrument", "footnote", "level", "voice", "type", "dot", "accidental",
    "time-modification", "stem", "notehead", "notehead-text", "staff", "beam",
    "notations", "lyric", "play", "listen"))}


def _serialize(root):
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(data) > XML_LIMIT:
        raise ValueError("편집 악보는 압축 해제 후 8MB 이하로 나누어주세요.")
    return data.decode("utf-8")


def _validate(root, part_id, instrument):
    _bounded_depth(root)
    if not isinstance(instrument, str) or instrument not in INSTRUMENTS:
        raise ValueError("지원하는 악기를 선택해주세요.")
    if root.tag != "score-partwise":
        raise ValueError("score-partwise MusicXML 악보를 선택해주세요.")
    parts, lists = root.findall("part"), root.findall("part-list")
    ids = [p.get("id") for p in parts]
    if (not 1 <= len(parts) <= 64 or len(set(ids)) != len(ids)
            or any(not v or len(v) > 80 for v in ids) or len(lists) != 1):
        raise ValueError("MusicXML 파트 구성이 올바르지 않아요.")
    declared = [p.get("id") for p in lists[0].findall("score-part")]
    if len(set(declared)) != len(declared) or set(declared) != set(ids):
        raise ValueError("MusicXML 파트 정의와 실제 파트가 일치하지 않아요.")
    if not isinstance(part_id, str) or part_id not in ids:
        raise ValueError("편집할 파트를 선택해주세요.")
    part = next(p for p in parts if p.get("id") == part_id)
    if not 1 <= len(part.findall("measure")) <= 600:
        raise ValueError("선택한 파트는 1~600마디로 나누어주세요.")
    if len(part.findall("measure/note")) > 30_000:
        raise ValueError("선택한 파트의 음표는 30,000개 이하로 나누어주세요.")
    return part


def _load(xml, part_id, instrument):
    if not isinstance(xml, str) or not xml:
        raise ValueError("편집할 MusicXML을 확인해주세요.")
    try:
        root = safe_xml(xml.encode("utf-8"))
    except UnicodeError:
        raise ValueError("올바른 UTF-8 MusicXML을 선택해주세요.") from None
    part = _validate(root, part_id, instrument)
    # Normally already removed during prepare; reject dirty canonical input so
    # a patch cannot silently alter unrelated content during sanitization.
    if _remove_resources(copy.deepcopy(root)):
        raise ValueError("안전하게 준비한 악보에서 편집을 시작해주세요.")
    return root, part


def prepare(data, filename, part_id, instrument):
    _, root = unpack(data, filename)
    listing = inspect(data, filename)
    _validate(root, part_id, instrument)
    normalized, warnings = normalize_export(data, filename)
    if normalized is not None:
        root = safe_xml(normalized)
    removed = _remove_resources(root)
    if removed:
        warnings.append(f"외부 이미지·링크·활성 콘텐츠 참조 {removed}개를 안전하게 제외했습니다. 업로드 원본은 별도로 보관됩니다.")
    return {"xml": _serialize(root), "title": listing["title"], "part_id": part_id,
            "instrument": instrument, "warnings": [BASE_WARNING, *warnings]}


def _one(parent, tag, default=None):
    nodes = parent.findall(tag)
    if not nodes and default is not None:
        return default
    if len(nodes) != 1 or len(nodes[0]) or nodes[0].text is None:
        raise ValueError("누락되거나 중복된 표기")
    return nodes[0].text.strip()


def _pitch(note):
    nodes = note.findall("pitch")
    if len(nodes) != 1 or any(n.tag not in {"step", "alter", "octave"} for n in nodes[0]):
        raise ValueError("음정 표기가 불명확함")
    pitch = nodes[0]
    step, alter, octave = _one(pitch, "step"), number(_one(pitch, "alter", "0")), integer(_one(pitch, "octave"))
    if step not in STEPS or alter.denominator != 1 or int(alter) not in ACCIDENTALS or not 0 <= octave <= 9:
        raise ValueError("미분음 또는 편집 범위 밖 음정")
    return {"step": step, "alter": int(alter), "octave": octave}


def _midi(pitch):
    return (pitch["octave"] + 1) * 12 + STEPS[pitch["step"]] + pitch["alter"]


def _fingering(note):
    strings, frets = note.findall("notations/technical/string"), note.findall("notations/technical/fret")
    if len(strings) != 1 or len(frets) != 1 or len(strings[0]) or len(frets[0]):
        raise ValueError("명확한 줄·프렛이 없음")
    string, fret = integer(strings[0].text), integer(frets[0].text)
    if not 1 <= string <= 7 or not 0 <= fret <= 36:
        raise ValueError("줄·프렛 편집 범위 밖")
    return {"string": string, "fret": fret}


def _note_reason(note, *, technical=()):
    if note.find("tie") is not None or note.find("notations/tied") is not None:
        return "타이로 연결된 음표는 연결 전체를 함께 수정해야 하므로 현재 잠겨 있어요."
    if note.find("grace") is not None or note.find("cue") is not None:
        return "꾸밈음·큐 음표의 연결 편집은 아직 지원하지 않아요."
    if note.find("play") is not None or note.find("listen") is not None:
        return "별도 재생 정보가 있는 음표는 자동으로 바꾸지 않아요."
    for notation in note.findall("notations"):
        for mark in notation:
            if mark.tag in {"articulations", "tuplet"}:
                continue
            if mark.tag == "technical" and all(n.tag in technical for n in mark):
                continue
            return "연결된 슬러·벤딩·주법 등 복잡한 연주 표기를 함께 수정할 수 없어 잠겨 있어요."
    return None


def _tuning(details):
    count = integer(_one(details, "staff-lines"))
    tunings = details.findall("staff-tuning")
    if not 4 <= count <= 7 or len(tunings) != count:
        raise ValueError("모든 줄의 명시적인 튜닝이 필요해요.")
    lines = {}
    for item in tunings:
        line = integer(item.get("line"))
        step = _one(item, "tuning-step")
        alter, octave = integer(_one(item, "tuning-alter", "0")), integer(_one(item, "tuning-octave"))
        if line in lines or step not in STEPS or not 0 <= octave <= 9 or not -2 <= alter <= 2:
            raise ValueError("튜닝 정보가 불명확해요.")
        lines[line] = (octave + 1) * 12 + STEPS[step] + alter
    if set(lines) != set(range(1, count + 1)):
        raise ValueError("튜닝 줄 번호가 불명확해요.")
    tuning = [lines[n] for n in range(count, 0, -1)]
    capo = integer(_one(details, "capo", "0"))
    if not 0 <= capo <= 24 or any(a < b for a, b in zip(tuning, tuning[1:])):
        raise ValueError("고음 줄부터 순서가 명확한 튜닝·카포가 필요해요.")
    return tuning, capo


def _scan(part):
    """Exact rational positions; uncertain timing remains '?' rather than guessed."""
    rows = []
    divisions, tuning, transpose, clefs = None, {}, {}, {}
    for mi, measure in enumerate(part.findall("measure")):
        position, previous, ni = Fraction(0), None, 0
        for node in measure:
            if node.tag == "attributes":
                for clef in node.findall("clef"):
                    clefs[clef.get("number", "1")] = clef.findtext("sign")
                if node.find("divisions") is not None:
                    try:
                        divisions = number(_one(node, "divisions"))
                        if divisions <= 0:
                            divisions = None
                    except ValueError:
                        divisions = None
                for details in node.findall("staff-details"):
                    staff = details.get("number", "1")
                    try:
                        tuning[staff] = _tuning(details)
                    except (ValueError, TypeError):
                        tuning[staff] = None
                for trans in node.findall("transpose"):
                    staff = trans.get("number", "all")
                    try:
                        if trans.find("double") is not None:
                            raise ValueError
                        transpose[staff] = integer(_one(trans, "chromatic")) + 12 * integer(_one(trans, "octave-change", "0"))
                    except ValueError:
                        transpose[staff] = None
            elif node.tag in {"backup", "forward"}:
                try:
                    delta = number(_one(node, "duration")) / divisions
                    if delta <= 0 or position is None:
                        raise ValueError
                    position += delta * (-1 if node.tag == "backup" else 1)
                    if position < 0:
                        raise ValueError
                except (ValueError, TypeError, ZeroDivisionError):
                    position = None
                previous = None
            elif node.tag == "note":
                duration = None
                try:
                    duration = Fraction(0) if node.find("grace") is not None else number(_one(node, "duration")) / divisions
                    if duration < 0 or duration == 0 and node.find("grace") is None:
                        duration = None
                except (ValueError, TypeError, ZeroDivisionError):
                    pass
                onset = previous if node.find("chord") is not None else position
                if node.find("chord") is None:
                    previous = position
                    position = position + duration if position is not None and duration is not None else None
                staff = node.findtext("staff", "1")
                rows.append({"node": node, "measure": measure, "id": f"m{mi}n{ni}", "measure_index": mi + 1,
                             "measure_number": measure.get("number", str(mi + 1)), "note_index": ni + 1,
                             "staff": staff, "voice": node.findtext("voice", "1"),
                             "onset_value": onset, "duration_value": duration,
                             "onset": str(onset) if onset is not None else "?",
                             "duration": str(duration) if duration is not None else "?",
                             "tuning": tuning.get(staff), "transpose": transpose.get(staff, transpose.get("all", 0)),
                             "divisions": divisions, "clef": clefs.get(staff)})
                ni += 1
    return rows


def _drum_shape(note):
    if _note_reason(note, technical={"open-string"}):
        return None
    refs, unpitched, heads = note.findall("instrument"), note.findall("unpitched"), note.findall("notehead")
    if (len(refs) != 1 or len(unpitched) != 1 or len(heads) > 1
            or note.find("pitch") is not None or note.find("rest") is not None
            or note.find("notehead-text") is not None):
        return None
    try:
        if (_one(unpitched[0], "display-step") not in STEPS
                or not 0 <= integer(_one(unpitched[0], "display-octave")) <= 9
                or any(n.tag not in {"display-step", "display-octave"} for n in unpitched[0])):
            return None
    except ValueError:
        return None
    if heads and (len(heads[0]) or heads[0].get("parentheses", "no") != "no"):
        return None
    technical = note.findall("notations/technical")
    if len(technical) > 1 or technical and len(technical[0].findall("open-string")) > 1:
        return None
    pieces = [copy.deepcopy(n) for n in [unpitched[0], refs[0], *heads, *technical]]
    # Source positions belong to another note, not to its notation style.
    for piece in pieces:
        for n in piece.iter():
            n.tail = None
            for key in POSITION_ATTRIBUTES | {"id"}:
                if key != "id" or n.tag != "instrument":
                    n.attrib.pop(key, None)
    return pieces


def _drum_bank(root, part, rows):
    # A measure-local MIDI override can change the same instrument ID's sound
    # during the piece. We cannot safely reuse an initial-bank exemplar there.
    if part.find(".//midi-instrument") is not None or part.find(".//instrument-change") is not None:
        return {}
    info = next(p for p in root.findall("part-list/score-part") if p.get("id") == part.get("id"))
    definitions, midi = info.findall("score-instrument"), info.findall("midi-instrument")
    ids, mids = [n.get("id") for n in definitions], [n.get("id") for n in midi]
    global_ids = [n.get("id") for n in root.findall("part-list/score-part/score-instrument")]
    if (None in ids or len(set(ids)) != len(ids) or len(set(mids)) != len(mids)
            or any(m not in ids for m in mids) or len(set(global_ids)) != len(global_ids)):
        return {}
    names = {n.get("id"): n.findtext("instrument-name", n.get("id")) for n in definitions}
    bank = {}
    for item in midi:
        try:
            value = integer(_one(item, "midi-unpitched"))
            if not 1 <= value <= 128:
                continue
        except ValueError:
            continue
        bank[item.get("id")] = {"id": item.get("id"), "name": names[item.get("id")], "shapes": {}}
    for row in rows:
        shape = _drum_shape(row["node"])
        if shape is None:
            continue
        ident = row["node"].find("instrument").get("id")
        if ident in bank:
            fingerprint = b"".join(ET.tostring(n) for n in shape)
            bank[ident]["shapes"][fingerprint] = shape
    from .source_score_palette import read as read_palette
    for ident, shape in read_palette(root, info, bank, _drum_shape).items():
        fingerprint = b"".join(ET.tostring(n) for n in shape)
        bank[ident]["shapes"][fingerprint] = shape
    # A single ID represented with multiple note positions/techniques is not a
    # safe replacement template. Never infer a new style from GM or note height.
    return {ident: {"id": ident, "name": entry["name"], "shape": next(iter(entry["shapes"].values()))}
            for ident, entry in bank.items() if len(entry["shapes"]) == 1}


def _analysis(root, part, instrument):
    rows = _scan(part)
    bank = _drum_bank(root, part, rows)
    tab = any(n.text == "TAB" for n in part.findall("measure/attributes/clef/sign"))
    multi_staff = (len({r["staff"] for r in rows}) > 1 or
                   any(n.text not in {None, "1"} for n in part.findall("measure/attributes/staves")))
    for row in rows:
        note = row["node"]
        kinds = [k for k in ("pitch", "unpitched", "rest") if note.find(k) is not None]
        row["kind"] = {"pitch": "pitched", "unpitched": "unpitched", "rest": "rest"}.get(kinds[0] if kinds else "", "rest")
        reasons = {"pitch": "음정이 있는 음표에서 사용할 수 있어요.", "fingering": "기존의 명확한 TAB 줄·프렛이 필요해요.", "drum": "기존의 명확한 타악기 표기가 필요해요."}
        common = _note_reason(note)
        malformed = (len(kinds) != 1 or any(len(note.findall(k)) != 1 for k in kinds)
                     or len(note.findall("staff")) > 1 or len(note.findall("voice")) > 1
                     or row["duration_value"] is None or row["onset_value"] is None)
        if malformed:
            common = "음표 종류·성부·리듬 위치를 명확히 확인할 수 없어 편집이 잠겨 있어요."
        try:
            row["pitch"] = _pitch(note)
            reasons["pitch"] = common
            heads = note.findall("notehead")
            if heads and (len(heads) != 1 or (heads[0].text or "normal") != "normal"):
                reasons["pitch"] = "특수 음표머리의 음정 변경은 아직 지원하지 않아요."
            accidental = note.findall("accidental")
            if len(accidental) > 1 or any((n.text or "") not in ACCIDENTALS.values() or len(n) or n.get("smufl") for n in accidental):
                reasons["pitch"] = "특수 임시표를 유지하며 음정을 변경할 수 없어 잠겨 있어요."
            if tab or note.find("notations/technical/string") is not None or note.find("notations/technical/fret") is not None:
                reasons["pitch"] = "TAB와 음정의 동시 변경은 아직 지원하지 않아요. 같은 음의 운지 변경을 이용해주세요."
            if instrument == "drums":
                reasons["pitch"] = "드럼 파트의 음정 표기는 인식 오류일 수 있어요. 타격 종류를 추정하지 않습니다."
        except ValueError:
            pass
        try:
            row["fingering"] = _fingering(note)
            reason = _note_reason(note, technical={"string", "fret"})
            if malformed:
                reason = common
            if instrument not in {"guitar", "bass"}:
                reason = "기타·베이스의 TAB 운지 편집만 지원해요."
            elif multi_staff:
                reason = "오선·TAB 또는 여러 보표의 연동 운지는 아직 함께 수정할 수 없어요."
            elif row["tuning"] is None or row["transpose"] is None or "pitch" not in row:
                reason = "명시된 튜닝·카포·음정 정보가 모두 있어야 운지를 안전하게 바꿀 수 있어요."
            elif any((head.text or "normal") != "normal" for head in note.findall("notehead")):
                reason = "뮤트·특수 음표머리의 운지를 임의로 바꾸지 않아요."
            else:
                tuning, capo = row["tuning"]
                fingering = row["fingering"]
                if (fingering["string"] > len(tuning) or
                        tuning[fingering["string"] - 1] + capo + fingering["fret"] != _midi(row["pitch"]) + row["transpose"]):
                    reason = "현재 줄·프렛과 실제 음정이 일치하지 않아 추측해서 수정하지 않아요."
            reasons["fingering"] = reason
        except ValueError:
            pass
        shape = _drum_shape(note)
        refs = note.findall("instrument")
        if len(refs) == 1:
            row["drum_id"] = refs[0].get("id", "")
        if instrument == "drums" and shape is not None and row.get("drum_id") in bank:
            reasons["drum"] = common if malformed else None
        row["reasons"] = {k: v for k, v in reasons.items() if v}
        row["editable"] = {k: v is None for k, v in reasons.items()}
        lyrics = []
        for index, lyric in enumerate(note.findall("lyric")):
            texts = lyric.findall("text")
            editable = len(texts) == 1 and not len(texts[0])
            lyrics.append({"index": index, "text": " · ".join(n.text or "" for n in texts), "editable": editable,
                           **({} if editable else {"reason": "한 가사 항목에 단일 text가 있는 경우만 수정할 수 있어요."})})
        row["lyrics"] = lyrics
        if row["kind"] == "pitched" and "pitch" in row:
            p = row["pitch"]
            row["description"] = p["step"] + {-2: "♭♭", -1: "♭", 0: "", 1: "♯", 2: "𝄪"}[p["alter"]] + str(p["octave"])
        elif row["kind"] == "unpitched":
            row["description"] = bank.get(row.get("drum_id"), {}).get("name", "타격 종류 확인 필요")
        else:
            row["description"] = "쉼표" if note.find("rest") is not None else "원본 음정 확인 필요"
    from .source_score_structure import augment
    augment(root, part, instrument, rows, bank)
    from .source_score_marks import augment as augment_marks
    augment_marks(root, part, instrument, rows, bank)
    return rows, bank


def describe(xml, part_id, instrument):
    root, part = _load(xml, part_id, instrument)
    rows, bank = _analysis(root, part, instrument)
    private = {"node", "measure", "onset_value", "duration_value", "tuning", "transpose", "divisions", "clef"}
    warnings = ["음정은 원본에 적힌 음높이입니다. 직접 선택한 수정만 적용하며 리듬 수정은 인접 쉼표를 이용해 마디 길이를 유지합니다.",
                "TAB 음정 수정은 확인된 연결 오선과 함께 반영합니다. 뒤 음표의 음높이가 달라 보이지 않도록 필요한 임시표만 명시할 수 있습니다."]
    if any(not any(row["editable"].values()) for row in rows):
        warnings.append("연결·주법·튜닝이 불명확한 음표는 해당 수정 기능을 잠갔습니다. 표기를 삭제하거나 단순화하지 않습니다.")
    return {"notes": [{k: v for k, v in row.items() if k not in private} for row in rows],
            "drum_options": [{"id": value["id"], "name": value["name"]} for value in bank.values()] if instrument == "drums" else [],
            "warnings": warnings}


def _put(note, tag, replacement):
    existing = note.findall(tag)
    if existing:
        index = list(note).index(existing[0])
        for old in existing:
            note.remove(old)
    else:
        index = next((i for i, n in enumerate(note) if NOTE_ORDER.get(n.tag, 999) > NOTE_ORDER[tag]), len(note))
    if replacement is not None:
        note.insert(index, replacement)


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("편집 값의 정수 범위를 확인해주세요.")
    return value


def _check_accidental_neighbors(row, rows, target):
    old = row["pitch"]
    if target == old:
        return
    affected = {(old["step"], old["octave"]), (target["step"], target["octave"])}
    for other in rows:
        if other is row or other["measure"] is not row["measure"] or other["staff"] != row["staff"]:
            continue
        pitch = other.get("pitch")
        if pitch and (pitch["step"], pitch["octave"]) in affected:
            # A later (or simultaneous other-voice) implicit accidental can be
            # visually changed by this edit, despite its unchanged sound pitch.
            if (other["onset_value"] is None or other["onset_value"] >= row["onset_value"]) and other["node"].find("accidental") is None:
                raise ValueError("같은 마디의 뒤 음표에 임시표가 이어질 수 있어 이 음정 변경은 잠겨 있어요. 다른 음표를 함께 바꾸지 않았습니다.")


def apply_edit(xml, part_id, instrument, patch):
    root, part = _load(xml, part_id, instrument)
    rows, bank = _analysis(root, part, instrument)
    from .source_score_marks import OPERATIONS as MARK_OPERATIONS, apply_operation as apply_mark
    if isinstance(patch, dict) and isinstance(patch.get("operation"), str) and patch["operation"] in MARK_OPERATIONS:
        apply_mark(root, part, instrument, rows, bank, patch)
        _validate(root, part_id, instrument)
        return _serialize(root)
    from .source_score_structure import OPERATIONS, apply_operation
    if isinstance(patch, dict) and isinstance(patch.get("operation"), str) and patch["operation"] in OPERATIONS:
        apply_operation(root, part, instrument, rows, bank, patch)
        _validate(root, part_id, instrument)
        return _serialize(root)
    fields = {"pitch": {"step", "alter", "octave"}, "fingering": {"string", "fret"},
              "drum": {"drum_id"}, "lyric": {"lyric_index", "text"}}
    if not isinstance(patch, dict) or not isinstance(patch.get("operation"), str) or patch["operation"] not in fields:
        raise ValueError("지원하는 음표 편집 작업을 선택해주세요.")
    operation = patch["operation"]
    if set(patch) != fields[operation] | {"operation", "note_id"}:
        raise ValueError("편집 작업의 필수 값과 허용 항목을 확인해주세요.")
    if not isinstance(patch["note_id"], str) or not re.fullmatch(r"m\d{1,3}n\d{1,5}", patch["note_id"]):
        raise ValueError("음표 위치를 확인해주세요.")
    row = next((r for r in rows if r["id"] == patch["note_id"]), None)
    if row is None:
        raise ValueError("편집할 음표를 찾을 수 없어요.")
    note = row["node"]
    if operation != "lyric" and not row["editable"][operation]:
        raise ValueError(row["reasons"][operation])
    if operation == "pitch":
        step = patch["step"]
        if not isinstance(step, str) or step not in STEPS:
            raise ValueError("음이름은 C~B로 선택해주세요.")
        pitch = {"step": step, "alter": _int(patch["alter"], -2, 2), "octave": _int(patch["octave"], 0, 9)}
        _check_accidental_neighbors(row, rows, pitch)
        if pitch == row["pitch"]:
            return xml
        element = note.find("pitch")
        element.find("step").text, element.find("octave").text = step, str(pitch["octave"])
        alter = element.find("alter")
        if alter is None:
            alter = ET.Element("alter")
            element.insert(1, alter)
        alter.text = str(pitch["alter"])
        accidental = note.find("accidental")
        accidental = copy.deepcopy(accidental) if accidental is not None else ET.Element("accidental")
        accidental.text = ACCIDENTALS[pitch["alter"]]
        _put(note, "accidental", accidental)
    elif operation == "fingering":
        tuning, capo = row["tuning"]
        string, fret = _int(patch["string"], 1, len(tuning)), _int(patch["fret"], 0, 36)
        if tuning[string - 1] + capo + fret != _midi(row["pitch"]) + row["transpose"]:
            raise ValueError("이번 운지 편집은 원래 음을 유지하는 줄·프렛 이동만 가능해요.")
        if {"string": string, "fret": fret} == row["fingering"]:
            return xml
        for other in rows:
            if other is row or other["measure"] is not row["measure"] or other["staff"] != row["staff"]:
                continue
            if other["kind"] == "rest":
                continue
            start, length = other["onset_value"], other["duration_value"]
            overlaps = (start is None or length is None or
                        start < row["onset_value"] + row["duration_value"] and row["onset_value"] < start + length)
            if overlaps:
                if "fingering" not in other:
                    raise ValueError("동시에 연주하는 다른 음의 줄을 확인할 수 없어 운지를 이동하지 않았어요.")
                if other["fingering"]["string"] == string:
                    raise ValueError("선택한 줄에서 다른 음이 동시에 연주되고 있어요. 다른 운지를 선택해주세요.")
        note.find("notations/technical/string").text = str(string)
        note.find("notations/technical/fret").text = str(fret)
    elif operation == "drum":
        ident = patch["drum_id"]
        if not isinstance(ident, str) or ident not in bank:
            raise ValueError("원본 악보에서 표기가 명확히 확인된 타격 종류를 선택해주세요.")
        if ident == row.get("drum_id"):
            return xml
        shape = {n.tag: copy.deepcopy(n) for n in bank[ident]["shape"]}
        for tag in ("unpitched", "instrument", "notehead"):
            _put(note, tag, shape.get(tag))
        for notation in note.findall("notations"):
            for technical in list(notation.findall("technical")):
                notation.remove(technical)
        if "technical" in shape:
            notation = next(iter(note.findall("notations")), None)
            if notation is None:
                notation = ET.Element("notations")
                _put(note, "notations", notation)
            notation.append(shape["technical"])
    else:
        index = _int(patch["lyric_index"], 0, max(0, len(row["lyrics"]) - 1))
        if index >= len(row["lyrics"]) or not row["lyrics"][index]["editable"]:
            raise ValueError("단일 텍스트가 있는 기존 가사 항목을 선택해주세요.")
        value = patch["text"]
        if (not isinstance(value, str) or not 1 <= len(value) <= 500 or not value.strip()
                or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", value)):
            raise ValueError("가사는 유효한 문자 1~500자로 입력해주세요.")
        note.findall("lyric")[index].find("text").text = value
    return _serialize(root)
