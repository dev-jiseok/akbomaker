"""Targeted repairs in canonical MusicXML, with exact rational timelines.

W3C MusicXML 4.0 /elements/{duration,chord,rest,beam,staff-tuning,transpose}:
duration moves the cursor except in chord tones; backup/forward are retained;
TAB sounding pitch = tuning + capo + fret = written pitch + transpose.
No audio or quantized ScoreDocument code is used here.
"""
import copy
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

from . import source_score as base

OPERATIONS = {"tab_pitch", "delete", "insert", "rhythm", "chord", "lyric_add", "lyric_delete"}
TYPES = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1), "eighth": Fraction(1, 2),
         "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16)}
PITCH_TAGS = {"pitch", "unpitched", "rest", "instrument", "accidental", "notehead", "notehead-text"}


def _simple(row, *, structural=False):
    note = row["node"]
    reason = base._note_reason(note, technical={"string", "fret", "open-string"})
    if reason:
        return reason
    if (row["onset_value"] is None or row["duration_value"] is None or row["duration_value"] <= 0
            or row["divisions"] is None or row["divisions"] <= 0):
        return "음표의 리듬 위치와 길이가 명확해야 수정할 수 있어요."
    if sum(len(note.findall(tag)) for tag in ("pitch", "unpitched", "rest")) != 1:
        return "음표 종류가 명확하지 않아 수정하지 않았어요."
    if (len(note.findall("voice")) > 1 or len(note.findall("staff")) > 1
            or any(attr in note.attrib for attr in ("attack", "release", "time-only"))):
        return "별도 재생·성부 정보가 있는 음표는 연결 정보를 먼저 확인해주세요."
    if any(n.tag not in base.NOTE_ORDER for n in note):
        return "지원 범위 밖의 음표 표기를 보존하기 위해 이 수정은 잠겨 있어요."
    if note.find("notehead-text") is not None or any(n.get("parentheses", "no") != "no" for n in note.findall("notehead")):
        return "특수·고스트 음표머리를 포함한 표기는 그대로 보관합니다."
    accidentals = note.findall("accidental")
    if (len(accidentals) > 1 or any(len(n) or n.get("smufl") or n.text not in base.ACCIDENTALS.values() for n in accidentals)):
        return "특수 임시표가 있는 음표는 해당 기호를 보존하는 연결 편집이 필요해요."
    if structural and (note.find("time-modification") is not None or note.find("notations/tuplet") is not None):
        return "잇단음표의 리듬 그룹 전체를 수정해야 하므로 이 구조 수정은 잠겨 있어요."
    return None


def _chord_group(row, rows):
    siblings = list(row["measure"])
    index = siblings.index(row["node"])
    if row["node"].find("chord") is not None:
        return True
    # Directions/attributes do not advance the note cursor. A chord tone can
    # follow them while still belonging to this base note (matching _scan).
    for sibling in siblings[index + 1:]:
        if sibling.tag in {"backup", "forward"}:
            return False
        if sibling.tag == "note":
            return sibling.find("chord") is not None
    return False


def _tab_link(row, rows, *, rest=False, staffs=None):
    if row["tuning"] is None or row["transpose"] is None:
        raise ValueError("명시된 튜닝·카포·이조 정보가 있는 TAB 보표에서 선택해주세요.")
    if not rest:
        if "fingering" not in row or "pitch" not in row:
            raise ValueError("명확한 음정과 기존 줄·프렛이 있는 음표를 선택해주세요.")
        if row["fingering"]["string"] > len(row["tuning"][0]):
            raise ValueError("현재 줄 번호가 튜닝의 줄 수를 벗어나요.")
        if any((head.text or "normal") != "normal" for head in row["node"].findall("notehead")):
            raise ValueError("뮤트·하모닉 등 특수 TAB 음표는 보통 음표와 구분해 보관합니다.")
        # A user-entered new string/fret is explicit repair authority, so an
        # inconsistent OLD fret need not be trusted as pitch truth. Companion
        # matching below still uses the old encoded pitch and exact position.
    staffs = staffs if staffs is not None else {r["staff"] for r in rows}
    if len(staffs) == 1:
        return None
    if len(staffs) != 2 or row["clef"] != "TAB":
        raise ValueError("단일 TAB 또는 연결이 명확한 오선+TAB 두 보표만 함께 수정할 수 있어요.")
    matching = []
    for other in rows:
        if (other["measure"] is not row["measure"] or other["staff"] == row["staff"]
                or other["onset_value"] != row["onset_value"] or other["duration_value"] != row["duration_value"]):
            continue
        if rest and other["kind"] == "rest":
            matching.append(other)
        elif (not rest and "pitch" in other and other["transpose"] is not None
              and base._midi(other["pitch"]) + other["transpose"] == base._midi(row["pitch"]) + row["transpose"]):
            matching.append(other)
    if len(matching) != 1 or matching[0]["clef"] not in {"G", "F"}:
        raise ValueError("같은 위치·길이·음정의 연결 오선 음표를 하나로 확정할 수 없어 수정하지 않았어요.")
    other = matching[0]
    # Both directions must be unique: two TAB unisons can otherwise appear to
    # share one ordinary-staff note even though neither mapping is established.
    reverse = []
    for candidate in rows:
        if (candidate["measure"] is not row["measure"] or candidate["staff"] != row["staff"]
                or candidate["onset_value"] != row["onset_value"] or candidate["duration_value"] != row["duration_value"]):
            continue
        if rest and candidate["kind"] == "rest":
            reverse.append(candidate)
        elif (not rest and "pitch" in candidate and candidate["transpose"] is not None
              and base._midi(candidate["pitch"]) + candidate["transpose"] == base._midi(other["pitch"]) + other["transpose"]):
            reverse.append(candidate)
    if len(reverse) != 1 or reverse[0] is not row:
        raise ValueError("여러 TAB 음이 같은 오선 음과 대응할 수 있어 연결을 하나로 확정하지 못했어요.")
    reason = _simple(other)
    if reason:
        raise ValueError(reason)
    if any((head.text or "normal") != "normal" for head in other["node"].findall("notehead")):
        raise ValueError("연결 오선의 특수 음표머리를 함께 바꿀 수 없어 잠겨 있어요.")
    if "fingering" in other and not rest and other["fingering"] != row["fingering"]:
        raise ValueError("오선과 TAB의 운지 정보가 서로 달라 수정하지 않았어요.")
    return other


def _linked_tab_present(part):
    return (part.find("measure/attributes/clef[sign='TAB']") is not None
            and len({n.findtext("staff", "1") for n in part.findall("measure/note")}) > 1)


def _kinds(row, rows, bank, instrument, part, *, staffs=None, linked=None):
    if instrument == "drums":
        return ["unpitched"] if bank else []
    if instrument in {"guitar", "bass"}:
        has_tab = row["clef"] == "TAB" or "fingering" in row or (linked if linked is not None else _linked_tab_present(part))
        if has_tab:
            try:
                _tab_link(row, rows, rest=row["kind"] == "rest", staffs=staffs)
                return ["tab"]
            except ValueError:
                return []
    return ["pitched"]


def _plain_rest(row):
    note = row["node"]
    return (row["kind"] == "rest" and row["duration_value"] is not None and row["duration_value"] > 0
            and not any(note.find(tag) is not None for tag in ("lyric", "notations", "beam", "time-modification", "chord"))
            and all(n.tag in {"rest", "duration", "voice", "type", "dot", "staff"} for n in note)
            and not any(a in note.attrib for a in ("attack", "release", "time-only")))


def _following_rest(row, rows):
    siblings = list(row["measure"])
    index = siblings.index(row["node"])
    if index + 1 == len(siblings):
        return None
    following = siblings[index + 1]
    result = next((r for r in rows if r["node"] is following), None)
    if (result and _plain_rest(result) and result["voice"] == row["voice"] and result["staff"] == row["staff"]
            and result["divisions"] == row["divisions"]
            and result["onset_value"] == row["onset_value"] + row["duration_value"]):
        return result
    return None


def augment(root, part, instrument, rows, bank):
    linked = _linked_tab_present(part)
    staffs = {r["staff"] for r in rows}
    by_measure = {}
    for r in rows:
        by_measure.setdefault(id(r["measure"]), []).append(r)
    definition = next(n for n in root.findall("part-list/score-part") if n.get("id") == part.get("id"))
    instrument_ids = [n.get("id") for n in definition.findall("score-instrument")]
    ids_valid = (len(set(instrument_ids)) == len(instrument_ids) and all(instrument_ids))
    dynamic_instruments = part.find(".//instrument-change") is not None or part.find(".//midi-instrument") is not None
    for row in rows:
        note = row["node"]
        nearby = by_measure[id(row["measure"])]
        row["notation"] = {"type": note.findtext("type", ""), "dots": len(note.findall("dot"))}
        reasons = {op: "선택한 음표에서 이 수정은 지원하지 않아요." for op in OPERATIONS}
        reason = _simple(row)
        tab_reason, partner = None, None
        if instrument in {"guitar", "bass"} and row["kind"] == "pitched" and reason is None:
            try:
                partner = _tab_link(row, nearby, staffs=staffs)
                reasons["tab_pitch"] = None
                tuning, capo = row["tuning"]
                current = row["fingering"]
                if tuning[current["string"] - 1] + capo + current["fret"] != base._midi(row["pitch"]) + row["transpose"]:
                    row["fingering_mismatch"] = True
                    row["warnings"] = ["현재 TAB 숫자와 음정이 다릅니다. 새로 입력한 줄·프렛을 기준으로 음정과 연결 오선을 함께 수정합니다."]
            except ValueError as error:
                tab_reason = str(error)
                reasons["tab_pitch"] = tab_reason
        if reason:
            reasons.update({op: reason for op in {"delete", "insert", "rhythm", "chord"}})
        else:
            # In a combined guitar score, one representation must never be
            # edited structurally without the corresponding one.
            structural_linked = linked and (row["clef"] != "TAB" or row["kind"] != "rest" and tab_reason is not None)
            group = _chord_group(row, nearby)
            if row["kind"] != "rest" and not group and not structural_linked:
                reasons["delete"] = None
            elif group:
                reasons["delete"] = "화음의 개별 음 삭제는 연결 구조를 함께 수정해야 하므로 현재 잠겨 있어요."
            if row["kind"] == "rest" and not group:
                kinds = _kinds(row, nearby, bank, instrument, part, staffs=staffs, linked=linked)
                row["insert_kinds"] = kinds
                if kinds:
                    reasons["insert"] = None
            elif row["kind"] != "rest":
                kinds = _kinds(row, nearby, bank, instrument, part, staffs=staffs, linked=linked)
                row["chord_kinds"] = kinds
                if kinds and not structural_linked:
                    reasons["chord"] = None
            rhythmic = _simple(row, structural=True)
            if rhythmic:
                reasons["rhythm"] = rhythmic
            elif group:
                reasons["rhythm"] = "화음의 길이는 구성 음 전체를 함께 바꿔야 하므로 이 경로에서는 잠겨 있어요."
            elif linked:
                reasons["rhythm"] = "오선·TAB의 리듬 동시 변경은 아직 지원하지 않아요."
            elif row["notation"]["type"] in TYPES and row["notation"]["dots"] <= 2:
                expected = TYPES[row["notation"]["type"]] * sum(Fraction(1, 2 ** i) for i in range(row["notation"]["dots"] + 1))
                if expected == row["duration_value"]:
                    reasons["rhythm"] = None
                    rest = _following_rest(row, nearby)
                    row["rhythm_limit"] = str(row["duration_value"] + (rest["duration_value"] if rest else 0))
                else:
                    reasons["rhythm"] = "음표 모양과 실제 길이가 달라 리듬을 추정해서 바꾸지 않아요."
        row.setdefault("insert_kinds", [])
        row.setdefault("chord_kinds", [])
        row.setdefault("rhythm_limit", row["duration"])
        refs = note.findall("instrument")
        if ((refs and (not ids_valid or len(refs) != 1 or refs[0].get("id") not in instrument_ids)) or dynamic_instruments):
            for op in ("insert", "chord"):
                reasons[op] = "악기 전환·참조 정보를 명확히 보존할 수 없어 새 음표 추가는 잠겨 있어요."
            row["insert_kinds"], row["chord_kinds"] = [], []
        for item, lyric in zip(row["lyrics"], note.findall("lyric")):
            deletable = (item["editable"] and lyric.find("extend") is None and lyric.find("elision") is None
                         and lyric.findtext("syllabic", "single") == "single")
            item["deletable"] = deletable
            if not deletable:
                item["delete_reason"] = "이어지는 음절·멜리스마 가사는 연결을 보존하며 텍스트만 수정해주세요."
        if any(item["deletable"] for item in row["lyrics"]):
            reasons["lyric_delete"] = None
        if len(row["lyrics"]) < 8:
            reasons["lyric_add"] = None
        for operation, why in reasons.items():
            row["editable"][operation] = why is None
            if why:
                row["reasons"][operation] = why


def _text(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 500 or not value.strip()
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", value)):
        raise ValueError("가사는 유효한 문자 1~500자로 입력해주세요.")
    return value


def _pitch_value(patch):
    step = patch.get("step")
    if not isinstance(step, str) or step not in base.STEPS:
        raise ValueError("음이름을 C~B에서 선택해주세요.")
    return {"step": step, "alter": base._int(patch.get("alter"), -2, 2), "octave": base._int(patch.get("octave"), 0, 9)}


def _spell(value, old=None):
    if old is not None and base._midi(old) == value:
        return old.copy()
    names = [("C", 0), ("C", 1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
             ("F", 1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
    octave, (step, alter) = value // 12 - 1, names[value % 12]
    if not 0 <= octave <= 9:
        raise ValueError("새 음정이 MusicXML 음역 범위를 벗어나요.")
    return {"step": step, "alter": alter, "octave": octave}


def _accidental(note, alteration):
    element = note.find("accidental")
    if element is None:
        element = ET.Element("accidental")
        base._put(note, "accidental", element)
    element.text = base.ACCIDENTALS[alteration]


def _protect_following(row, rows, affected):
    # Explicit signs preserve subsequent printed pitches when the edited note
    # changes accidental state. Their pitch/duration/voice never change.
    for other in rows:
        p = other.get("pitch")
        relevant = (other is not row and other["measure"] is row["measure"] and other["staff"] == row["staff"]
                    and other["node"].find("accidental") is None
                    and (other["onset_value"] is None or other["onset_value"] >= row["onset_value"]))
        if relevant and p and (p["step"], p["octave"]) in affected:
            _accidental(other["node"], p["alter"])
        elif relevant and not p and other["node"].find("pitch") is not None:
            raw = other["node"].find("pitch")
            if raw.findtext("step") in {step for step, octave in affected}:
                raise ValueError("뒤의 미분음·불명확한 음정에 미치는 임시표 영향을 안전하게 보존할 수 없어요.")


def _write_pitch(row, pitch, rows):
    note, old = row["node"], row.get("pitch")
    if old == pitch and note.find("pitch") is not None:
        return
    affected = {(pitch["step"], pitch["octave"])}
    if old:
        affected.add((old["step"], old["octave"]))
    _protect_following(row, rows, affected)
    element = note.find("pitch")
    if element is None:
        element = ET.Element("pitch")
        for tag in ("step", "alter", "octave"):
            ET.SubElement(element, tag)
        base._put(note, "pitch", element)
    element.find("step").text, element.find("octave").text = pitch["step"], str(pitch["octave"])
    alter = element.find("alter")
    if alter is None:
        alter = ET.Element("alter")
        element.insert(1, alter)
    alter.text = str(pitch["alter"])
    _accidental(note, pitch["alter"])


def _check_string(row, rows, string, *, exclude=()):
    for other in rows:
        if other is row or any(other is item for item in exclude) or other["measure"] is not row["measure"] or other["staff"] != row["staff"] or other["kind"] == "rest":
            continue
        start, duration = other["onset_value"], other["duration_value"]
        if start is None or duration is None or (start < row["onset_value"] + row["duration_value"] and row["onset_value"] < start + duration):
            if "fingering" not in other or other["fingering"]["string"] == string:
                raise ValueError("같은 시간에 해당 줄이 사용 중이거나 다른 음의 줄을 확인할 수 없어요.")


def _tab_values(row, patch):
    tuning, capo = row["tuning"]
    string, fret = base._int(patch.get("string"), 1, len(tuning)), base._int(patch.get("fret"), 0, 36)
    return string, fret, tuning[string - 1] + capo + fret


def _set_fingering(note, string, fret):
    tech = note.find("notations/technical")
    if tech is None:
        notation = note.find("notations")
        if notation is None:
            notation = ET.Element("notations")
            base._put(note, "notations", notation)
        tech = ET.SubElement(notation, "technical")
    for tag, value in (("string", string), ("fret", fret)):
        node = tech.find(tag)
        if node is None:
            node = ET.SubElement(tech, tag)
        node.text = str(value)


def _clear_sound(note, *, erase_marks=False, preserve_instrument=False):
    for child in list(note):
        if child.tag in PITCH_TAGS and not (preserve_instrument and child.tag == "instrument"):
            note.remove(child)
    for notation in list(note.findall("notations")):
        for child in list(notation):
            if child.tag == "technical" or erase_marks and child.tag == "articulations":
                notation.remove(child)
        if not len(notation):
            note.remove(notation)


def _make_sound(row, patch, rows, bank):
    kind, note = patch["kind"], row["node"]
    if kind == "pitched":
        pitch = _pitch_value(patch)
        _clear_sound(note, preserve_instrument=True)
        _write_pitch(row, pitch, rows)
    elif kind == "unpitched":
        ident = patch.get("drum_id")
        if not isinstance(ident, str) or ident not in bank:
            raise ValueError("원본에서 표기가 확인된 타격 종류를 선택해주세요.")
        _clear_sound(note)
        for item in bank[ident]["shape"]:
            if item.tag == "technical":
                notation = note.find("notations")
                if notation is None:
                    notation = ET.Element("notations")
                    base._put(note, "notations", notation)
                notation.append(copy.deepcopy(item))
            else:
                base._put(note, item.tag, copy.deepcopy(item))
    elif kind == "tab":
        string, fret, sound = _tab_values(row, patch)
        _check_string(row, rows, string)
        _clear_sound(note, preserve_instrument=True)
        _write_pitch(row, _spell(sound - row["transpose"], row.get("pitch")), rows)
        _set_fingering(note, string, fret)
    else:
        raise ValueError("추가할 음표 종류를 확인해주세요.")


def _decimal(value):
    # MusicXML positive-divisions is decimal, not an arbitrary rational string.
    if value <= 0 or value > 999_999_999:
        raise ValueError("음표 길이가 지원 범위를 벗어나요.")
    scaled = value * 1_000_000
    if scaled.denominator != 1:
        raise ValueError("이 divisions에서 새 길이를 정확한 소수로 표현할 수 없어요.")
    whole, fraction = divmod(int(scaled), 1_000_000)
    return str(whole) if fraction == 0 else f"{whole}.{fraction:06d}".rstrip("0")


def _notation(note, typ, dots):
    element = ET.Element("type")
    element.text = typ
    base._put(note, "type", element)
    for dot in list(note.findall("dot")):
        note.remove(dot)
    index = list(note).index(element) + 1
    for offset in range(dots):
        note.insert(index + offset, ET.Element("dot"))


def _rest_pieces(length, row):
    choices = sorted([(duration * sum(Fraction(1, 2 ** i) for i in range(dots + 1)), typ, dots)
                      for typ, duration in TYPES.items() for dots in range(3)], reverse=True)
    result = []
    while length:
        choice = next((c for c in choices if c[0] <= length), None)
        if choice is None or len(result) >= 32:
            raise ValueError("남는 쉼표를 정확하게 표현할 수 없는 길이예요.")
        size, typ, dots = choice
        note = ET.Element("note")
        ET.SubElement(note, "rest")
        ET.SubElement(note, "duration").text = _decimal(size * row["divisions"])
        ET.SubElement(note, "voice").text = row["voice"]
        _notation(note, typ, dots)
        ET.SubElement(note, "staff").text = row["staff"]
        result.append(note)
        length -= size
    return result


def _new_chord(row):
    original = row["node"]
    note = ET.Element("note")
    ET.SubElement(note, "chord")
    # A chord tone shares rhythm/voice/staff, not title IDs, lyrics, articulations
    # or endpoint markings that belong only to the source note.
    for child in original:
        if child.tag in {"duration", "instrument", "voice", "type", "dot", "time-modification", "stem", "staff"}:
            clone = copy.deepcopy(child)
            for n in clone.iter():
                if n.tag != "instrument":
                    n.attrib.pop("id", None)
            note.append(clone)
    return {**row, "node": note, "kind": "rest"}


def apply_operation(root, part, instrument, rows, bank, patch):
    operation = patch["operation"]
    fields = {"tab_pitch": {"string", "fret"}, "delete": set(), "rhythm": {"type", "dots"},
              "lyric_add": {"text"}, "lyric_delete": {"lyric_index"}}
    if operation in {"insert", "chord"}:
        kind = patch.get("kind")
        if not isinstance(kind, str) or kind not in {"pitched", "unpitched", "tab"}:
            raise ValueError("추가할 음표 종류를 선택해주세요.")
        fields[operation] = {"kind"} | {"pitched": {"step", "alter", "octave"}, "unpitched": {"drum_id"}, "tab": {"string", "fret"}}[kind]
    if set(patch) != fields[operation] | {"operation", "note_id"}:
        raise ValueError("편집 작업의 필수 값과 허용 항목을 확인해주세요.")
    ident = patch.get("note_id")
    if not isinstance(ident, str) or not re.fullmatch(r"m\d{1,3}n\d{1,5}", ident):
        raise ValueError("음표 위치를 확인해주세요.")
    row = next((r for r in rows if r["id"] == ident), None)
    if row is None:
        raise ValueError("편집할 음표를 찾을 수 없어요.")
    if not row["editable"].get(operation):
        raise ValueError(row["reasons"][operation])
    note = row["node"]
    if operation == "lyric_add":
        text = _text(patch["text"])
        numbers = {n.get("number", "1") for n in note.findall("lyric")}
        number = next(n for n in range(1, 10) if str(n) not in numbers)
        lyric = ET.Element("lyric", number=str(number))
        ET.SubElement(lyric, "syllabic").text = "single"
        ET.SubElement(lyric, "text").text = text
        index = next((i for i, child in enumerate(note) if child.tag in {"play", "listen"}), len(note))
        note.insert(index, lyric)
    elif operation == "lyric_delete":
        index = base._int(patch["lyric_index"], 0, max(0, len(row["lyrics"]) - 1))
        if index >= len(row["lyrics"]) or not row["lyrics"][index]["deletable"]:
            raise ValueError("음절·멜리스마로 연결되지 않은 가사 항목을 선택해주세요.")
        note.remove(note.findall("lyric")[index])
    elif operation == "tab_pitch":
        partner = _tab_link(row, rows)
        string, fret, sound = _tab_values(row, patch)
        _check_string(row, rows, string)
        pitch = _spell(sound - row["transpose"], row["pitch"])
        if partner:
            other_pitch = _spell(sound - partner["transpose"], partner["pitch"])
        _write_pitch(row, pitch, rows)
        _set_fingering(note, string, fret)
        if partner:
            _write_pitch(partner, other_pitch, rows)
            if "fingering" in partner:
                _set_fingering(partner["node"], string, fret)
    elif operation == "delete":
        partner = _tab_link(row, rows) if _linked_tab_present(part) else None
        if partner and _chord_group(partner, rows):
            raise ValueError("연결 오선의 화음을 개별 쉼표로 바꿀 수 없어요.")
        if instrument == "drums" and bank:
            from .source_score_palette import store as store_palette
            store_palette(root, part.get("id"), bank)
        for item in [row, *([partner] if partner else [])]:
            if "pitch" in item:
                _protect_following(item, rows, {(item["pitch"]["step"], item["pitch"]["octave"])})
            _clear_sound(item["node"], erase_marks=True, preserve_instrument=True)
            base._put(item["node"], "rest", ET.Element("rest"))
    elif operation in {"insert", "chord"}:
        if patch["kind"] not in row[f"{operation}_kinds"]:
            raise ValueError("해당 보표에서 안전하게 추가할 수 있는 음표 종류를 선택해주세요.")
        partner = _tab_link(row, rows, rest=operation == "insert") if patch["kind"] == "tab" else None
        target = _new_chord(row) if operation == "chord" else row
        if operation == "chord" and len(rows) >= 29_998:
            raise ValueError("음표 수가 30,000개를 넘지 않도록 파트를 나누어주세요.")
        if operation == "chord":
            if patch["kind"] == "pitched":
                proposed = _pitch_value(patch)
                if any(r["measure"] is row["measure"] and r["staff"] == row["staff"] and r["onset_value"] == row["onset_value"] and r.get("pitch") == proposed for r in rows):
                    raise ValueError("같은 위치에 이미 있는 음정이에요.")
            elif patch["kind"] == "unpitched" and any(r["measure"] is row["measure"] and r["onset_value"] == row["onset_value"] and r.get("drum_id") == patch.get("drum_id") for r in rows):
                raise ValueError("같은 위치에 이미 있는 타격 종류예요.")
        _make_sound(target, patch, rows, bank)
        if operation == "chord":
            measure = row["measure"]
            index = list(measure).index(note) + 1
            # Insert directly after the selected note, before any zero-time
            # attributes that may change divisions for later chord members.
            measure.insert(index, target["node"])
        if partner:
            string, fret, sound = _tab_values(row, patch)
            other = _new_chord(partner) if operation == "chord" else partner
            _clear_sound(other["node"], preserve_instrument=True)
            _write_pitch(other, _spell(sound - partner["transpose"], partner.get("pitch")), rows)
            if "fingering" in partner:
                _set_fingering(other["node"], string, fret)
            if operation == "chord":
                measure = partner["measure"]
                index = list(measure).index(partner["node"]) + 1
                measure.insert(index, other["node"])
    else:
        typ, dots = patch.get("type"), base._int(patch.get("dots"), 0, 2)
        if not isinstance(typ, str) or typ not in TYPES:
            raise ValueError("지원하는 음표 길이를 선택해주세요.")
        length = TYPES[typ] * sum(Fraction(1, 2 ** i) for i in range(dots + 1))
        rest = _following_rest(row, rows)
        available = row["duration_value"] + (rest["duration_value"] if rest else 0)
        if length > available:
            raise ValueError("뒤의 인접 쉼표보다 길게 늘리면 다른 음의 위치가 변하므로 수정하지 않았어요.")
        if note.find("beam") is not None and typ != row["notation"]["type"]:
            raise ValueError("연결된 빔의 단계가 바뀌는 길이는 그룹 전체 편집이 필요해요. 같은 음표의 점 길이는 수정할 수 있습니다.")
        if length == row["duration_value"]:
            return
        remnants = _rest_pieces(available - length, row)
        note.find("duration").text = _decimal(length * row["divisions"])
        _notation(note, typ, dots)
        if note.find("rest") is not None:
            note.find("rest").attrib.pop("measure", None)
        measure = row["measure"]
        index = list(measure).index(note) + 1
        if rest:
            measure.remove(rest["node"])
        for offset, item in enumerate(remnants):
            measure.insert(index + offset, item)
