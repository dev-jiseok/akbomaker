"""Explicit notation marks on canonical MusicXML, never inferred from audio.

Connections are edited as complete pairs. Standard/TAB counterparts are only
mirrored when the mapping is unique in both directions; uncertain links remain
locked. Other musical nodes, timing and engraving attributes are untouched.
"""
import bisect
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

from . import source_score as base

OPERATIONS = {"articulation", "connection"}
ARTICULATIONS = {"accent", "staccato", "tenuto"}
CONNECTIONS = {"tie", "slur", "slide", "hammer-on", "pull-off"}
TECHNIQUES = {"slide", "hammer-on", "pull-off"}
PATHS = {"tie": "notations/tied", "slur": "notations/slur", "slide": "notations/slide",
         "hammer-on": "notations/technical/hammer-on", "pull-off": "notations/technical/pull-off"}


def _position(row):
    return row["measure_index"], row["onset_value"]


def _identity(row):
    if "pitch" in row:
        return ("pitch", tuple(row["pitch"].items()))
    if row["kind"] == "unpitched" and row.get("drum_id"):
        note = row["node"]
        return ("drum", row["drum_id"], ET.tostring(note.find("unpitched")),
                tuple(ET.tostring(head) for head in note.findall("notehead")))
    return None


def _basic(row):
    note = row["node"]
    if (row["kind"] == "rest" or row["onset_value"] is None or row["duration_value"] is None
            or row["duration_value"] <= 0 or row["onset_value"] < 0
            or sum(len(note.findall(tag)) for tag in ("pitch", "unpitched", "rest")) != 1
            or len(note.findall("voice")) > 1 or len(note.findall("staff")) > 1
            or any(note.find(tag) is not None for tag in ("grace", "cue", "play", "listen"))
            or any(key in note.attrib for key in ("attack", "release", "time-only"))):
        raise ValueError("리듬·성부가 명확한 보통 음표에만 연주 기호를 수정할 수 있어요.")
    if row["kind"] == "pitched" and "pitch" not in row:
        raise ValueError("음정을 확인할 수 없어 연결 기호를 수정하지 않았어요.")


class Context:
    def __init__(self, part, instrument, rows):
        self.part, self.instrument, self.rows = part, instrument, rows
        self.by_id = {row["id"]: row for row in rows}
        self.staffs = {row["staff"] for row in rows}
        self.linked = instrument in {"guitar", "bass"} and len(self.staffs) > 1 and any(row["clef"] == "TAB" for row in rows)
        self.matches, self.sequences, self.sequence_index, self.spans, self.unisons = {}, {}, {}, {}, {}
        self.lengths, self.implicit = {}, set()
        length = None
        for index, measure in enumerate(part.findall("measure"), 1):
            times = measure.findall("attributes/time")
            if times:
                try:
                    beats, unit = times[-1].findtext("beats", ""), times[-1].findtext("beat-type", "")
                    if (len(times) != 1 or times[-1].get("number") is not None
                            or len(times[-1].findall("beats")) != 1 or len(times[-1].findall("beat-type")) != 1
                            or not re.fullmatch(r"[1-9][0-9]?", beats) or not re.fullmatch(r"[1-9][0-9]?", unit)):
                        raise ValueError()
                    length = Fraction(int(beats) * 4, int(unit))
                except (ValueError, ZeroDivisionError):
                    length = None
            self.lengths[index] = length
            if measure.get("implicit") == "yes":
                self.implicit.add(index)
        for row in rows:
            if row["onset_value"] is None:
                continue
            key = self.match_key(row)
            if key is not None:
                self.matches.setdefault(key, []).append(row)
            sequence = (row["staff"], row["voice"])
            self.sequences.setdefault(sequence, []).append(row)
            ident = _identity(row)
            if ident is not None:
                key = (*sequence, *_position(row), ident)
                self.unisons[key] = self.unisons.get(key, 0) + 1
            for mark, path in PATHS.items():
                for node in row["node"].findall(path):
                    group = (mark, row["staff"], row["voice"], node.get("number", "1"))
                    self.spans.setdefault(group, []).append((row, node))
        for sequence in self.sequences.values():
            sequence.sort(key=lambda row: (*_position(row), row["note_index"]))
            for index, row in enumerate(sequence):
                self.sequence_index[row["id"]] = index
        for spans in self.spans.values():
            spans.sort(key=lambda item: (*_position(item[0]), item[0]["note_index"]))
        self.span_positions, self.span_balances, self.span_indices = {}, {}, {}
        for key, spans in self.spans.items():
            self.span_positions[key] = [_position(row) for row, _ in spans]
            balance, totals = 0, [0]
            for index, (_, node) in enumerate(spans):
                balance += 1 if node.get("type") == "start" else -1 if node.get("type") == "stop" else 0
                totals.append(balance)
                self.span_indices[id(node)] = index
            self.span_balances[key] = totals
        self.partner_cache = {}

    @staticmethod
    def match_key(row):
        if "pitch" not in row or row["transpose"] is None or row["duration_value"] is None:
            return None
        return (*_position(row), row["duration_value"], base._midi(row["pitch"]) + row["transpose"])

    def partner(self, row):
        if not self.linked:
            return None
        if row["id"] in self.partner_cache:
            value = self.partner_cache[row["id"]]
            if isinstance(value, str):
                raise ValueError(value)
            return value
        try:
            if len(self.staffs) != 2 or row["clef"] not in {"TAB", "G", "F"}:
                raise ValueError("명확한 오선+TAB 두 보표의 연결만 함께 수정할 수 있어요.")
            matched = self.matches.get(self.match_key(row), [])
            own = [other for other in matched if other["staff"] == row["staff"]]
            others = [other for other in matched if other["staff"] != row["staff"]]
            if len(own) != 1 or len(others) != 1:
                raise ValueError("오선·TAB 음표 연결이 양방향으로 하나씩 대응해야 기호를 함께 수정할 수 있어요.")
            other = others[0]
            if (row["clef"] == "TAB") == (other["clef"] == "TAB") or other["clef"] not in {"TAB", "G", "F"}:
                raise ValueError("연결 오선·TAB 보표를 명확히 확인할 수 없어요.")
            tab = row if row["clef"] == "TAB" else other
            self.tab_values(tab)
            _basic(other)
            self.partner_cache[row["id"]] = other
            return other
        except ValueError as error:
            self.partner_cache[row["id"]] = str(error)
            raise

    @staticmethod
    def tab_values(row):
        if row["tuning"] is None or row["transpose"] is None or "fingering" not in row or "pitch" not in row:
            raise ValueError("명시된 튜닝·카포와 줄·프렛이 필요해요.")
        tuning, capo = row["tuning"]
        finger = row["fingering"]
        if (finger["string"] > len(tuning)
                or tuning[finger["string"] - 1] + capo + finger["fret"] != base._midi(row["pitch"]) + row["transpose"]
                or any((n.text or "normal") != "normal" for n in row["node"].findall("notehead"))):
            raise ValueError("TAB 음정·프렛 또는 특수 음표머리를 먼저 확인해주세요.")
        return finger

    def targets(self, row):
        sequence = self.sequences.get((row["staff"], row["voice"]), [])
        start = self.sequence_index.get(row["id"], len(sequence)) + 1
        return [target for target in sequence[start:start + 32]
                if target["kind"] != "rest" and _position(target) > _position(row)][:16]


def _notes_with_partner(ctx, row):
    _basic(row)
    partner = ctx.partner(row)
    return [row, *([partner] if partner is not None else [])]


def _connection_pair(ctx, row, target, mark):
    _basic(row)
    _basic(target)
    if (row is target or row["staff"] != target["staff"] or row["voice"] != target["voice"]
            or _position(row) >= _position(target)):
        raise ValueError("같은 보표·성부의 뒤쪽 음표를 연결 대상으로 선택해주세요.")
    if mark == "tie":
        if _identity(row) is None or _identity(row) != _identity(target):
            raise ValueError("붙임줄은 같은 음정 또는 같은 타악기 표기의 음표끼리 연결해주세요.")
        if any(ctx.unisons.get((endpoint["staff"], endpoint["voice"], *_position(endpoint), _identity(endpoint))) != 1
               for endpoint in (row, target)):
            raise ValueError("같은 위치·성부에 같은 음이 여러 개 있어 붙임줄 연결을 확정할 수 없어요.")
        if row["measure_index"] == target["measure_index"]:
            contiguous = row["onset_value"] + row["duration_value"] == target["onset_value"]
        else:
            contiguous = (target["measure_index"] == row["measure_index"] + 1
                          and row["measure_index"] not in ctx.implicit
                          and ctx.lengths.get(row["measure_index"]) is not None
                          and row["onset_value"] + row["duration_value"] == ctx.lengths[row["measure_index"]]
                          and target["onset_value"] == 0)
        if not contiguous:
            raise ValueError("붙임줄은 빈 시간 없이 바로 이어지는 음표만 연결할 수 있어요.")
        if "fingering" in row or "fingering" in target:
            if row.get("fingering") != target.get("fingering"):
                raise ValueError("TAB 붙임줄은 같은 줄·프렛의 음표끼리 연결해주세요.")


def _pairs(ctx, row, target, mark):
    _connection_pair(ctx, row, target, mark)
    left, right = ctx.partner(row), ctx.partner(target)
    result = [(row, target)]
    if left is not None or right is not None:
        if left is None or right is None:
            raise ValueError("연결 양끝의 오선·TAB 대응이 모두 명확해야 해요.")
        _connection_pair(ctx, left, right, mark)
        result.append((left, right))
    if mark in TECHNIQUES:
        if ctx.instrument not in {"guitar", "bass"}:
            raise ValueError("이 주법은 기타·베이스 음표에서만 사용할 수 있어요.")
        tab_pair = next(((a, b) for a, b in result if "fingering" in a and "fingering" in b), None)
        if tab_pair is None:
            raise ValueError("같은 줄의 프렛을 확인할 수 있는 TAB 음표를 선택해주세요.")
        a, b = tab_pair
        af, bf = ctx.tab_values(a), ctx.tab_values(b)
        if a["tuning"] != b["tuning"] or af["string"] != bf["string"] or af["fret"] == bf["fret"]:
            raise ValueError("주법 연결은 같은 튜닝·줄의 서로 다른 프렛 사이에서 지원해요.")
        if mark == "hammer-on" and af["fret"] >= bf["fret"] or mark == "pull-off" and af["fret"] <= bf["fret"]:
            raise ValueError("해머링은 높은 프렛으로, 풀링은 낮은 프렛으로 연결해주세요.")
    return result


def _simple_span(ctx, row, target, mark):
    starts, stops = row["node"].findall(PATHS[mark]), target["node"].findall(PATHS[mark])
    starts = [node for node in starts if node.get("type") == "start"]
    stops = [node for node in stops if node.get("type") == "stop"]
    candidates = [(a, b) for a in starts for b in stops if a.get("number", "1") == b.get("number", "1")]
    if len(candidates) != 1:
        raise ValueError("한 쌍으로 확인되는 연결 기호만 제거할 수 있어요.")
    a, b = candidates[0]
    if len(a) or len(b):
        raise ValueError("복잡한 연결 기호는 원본을 유지합니다.")
    number = a.get("number", "1")
    key = (mark, row["staff"], row["voice"], number)
    positions = ctx.span_positions.get(key, [])
    spans = ctx.spans.get(key, [])
    between = spans[bisect.bisect_left(positions, _position(row)):bisect.bisect_right(positions, _position(target))]
    if len(between) != 2 or between[0] != (row, a) or between[1] != (target, b):
        raise ValueError("겹치거나 이어지는 연결을 하나의 쌍으로 확정할 수 없어요.")
    if mark == "tie":
        for endpoint, expected in ((row, "start"), (target, "stop")):
            sounds = endpoint["node"].findall("tie")
            visuals = endpoint["node"].findall(PATHS[mark])
            if (len(sounds) != 1 or len(visuals) != 1 or sounds[0].get("type") != expected
                    or set(sounds[0].attrib) != {"type"} or len(sounds[0])):
                raise ValueError("재생용 붙임줄과 표시용 붙임줄이 일치하는 단순한 쌍만 제거할 수 있어요.")
    return a, b, number


def _addition(ctx, pairs, mark):
    for a, b in pairs:
        for endpoint in (a, b):
            if endpoint["node"].find(PATHS[mark]) is not None or mark == "tie" and endpoint["node"].find("tie") is not None:
                raise ValueError("이미 연결 기호가 있는 양끝에는 중복 연결을 추가하지 않아요.")
    if mark == "tie":
        for a, b in pairs:
            positions = ctx.span_positions.get((mark, a["staff"], a["voice"], "1"), [])
            if bisect.bisect_right(positions, _position(b)) > bisect.bisect_left(positions, _position(a)):
                raise ValueError("화음의 다른 붙임줄과 겹치는 연결은 묶음 편집이 필요해요.")
        return "1"
    for number in map(str, range(1, 7)):
        used = False
        for a, b in pairs:
            # A number is busy if a span endpoint lies inside the new span or
            # if an existing span is already open at the new start position.
            key = (mark, a["staff"], a["voice"], number)
            positions = ctx.span_positions.get(key, [])
            left = bisect.bisect_left(positions, _position(a))
            right = bisect.bisect_right(positions, _position(b))
            if right > left or ctx.span_balances.get(key, [0])[left] != 0:
                used = True
        if not used:
            return number
    raise ValueError("겹치는 연결 번호를 안전하게 배정할 수 없어요.")


def _notation(note):
    items = note.findall("notations")
    if items:
        return items[0]
    notation = ET.Element("notations")
    base._put(note, "notations", notation)
    return notation


def _container(note, mark):
    notation = _notation(note)
    if mark in {"hammer-on", "pull-off"}:
        technical = notation.find("technical")
        if technical is None:
            technical = ET.SubElement(notation, "technical")
        return technical
    return notation


def _remove(note, target):
    # Empty existing containers/attributes are retained: they may carry IDs or
    # formatting, and removing them is not part of the requested mark edit.
    for parent in note.iter():
        if target in list(parent):
            parent.remove(target)
            return


def augment(root, part, instrument, rows, bank):
    ctx = Context(part, instrument, rows)
    for row in rows:
        row["articulations"] = [mark for mark in sorted(ARTICULATIONS) if row["node"].find(f"notations/articulations/{mark}") is not None]
        row["connections"], row["connection_targets"] = [], []
        try:
            _notes_with_partner(ctx, row)
            row["editable"]["articulation"] = True
        except ValueError as error:
            row["editable"]["articulation"] = False
            row["reasons"]["articulation"] = str(error)
        if row["editable"]["articulation"]:
            for mark in sorted(CONNECTIONS):
                if mark in TECHNIQUES and instrument not in {"guitar", "bass"}:
                    continue
                for target in ctx.targets(row):
                    try:
                        pairs = _pairs(ctx, row, target, mark)
                        _addition(ctx, pairs, mark)
                        row["connection_targets"].append({"mark": mark, "target_note_id": target["id"]})
                    except ValueError:
                        pass
                for start in row["node"].findall(PATHS[mark]):
                    if start.get("type") != "start":
                        continue
                    group = ctx.spans.get((mark, row["staff"], row["voice"], start.get("number", "1")), [])
                    index = ctx.span_indices[id(start)]
                    if index + 1 >= len(group):
                        continue
                    target, _ = group[index + 1]
                    try:
                        pairs = _pairs(ctx, row, target, mark)
                        spans = [_simple_span(ctx, a, b, mark) for a, b in pairs]
                        row["connections"].append({"mark": mark, "target_note_id": target["id"], "number": spans[0][2]})
                    except ValueError:
                        pass
        row["editable"]["connection"] = bool(row["connections"] or row["connection_targets"])
        if not row["editable"]["connection"]:
            row["reasons"]["connection"] = "연결 가능한 뒤쪽 음표나 제거할 명확한 연결 쌍이 없어요. 복잡한 연결은 원본을 유지합니다."


def apply_operation(root, part, instrument, rows, bank, patch):
    operation = patch["operation"]
    expected = {"note_id", "operation", "mark", "action"} | ({"target_note_id"} if operation == "connection" else set())
    if set(patch) != expected or not isinstance(patch.get("action"), str) or patch["action"] not in {"add", "remove"}:
        raise ValueError("연주 기호 수정에 필요한 항목과 추가·제거 작업을 확인해주세요.")
    mark = patch.get("mark")
    if not isinstance(mark, str) or mark not in (ARTICULATIONS if operation == "articulation" else CONNECTIONS):
        raise ValueError("지원하는 연주 기호를 선택해주세요.")
    ctx = Context(part, instrument, rows)
    row = ctx.by_id.get(patch.get("note_id")) if isinstance(patch.get("note_id"), str) else None
    if row is None:
        raise ValueError("편집할 음표를 찾을 수 없어요.")
    if operation == "articulation":
        plans = []
        for endpoint in _notes_with_partner(ctx, row):
            marks = endpoint["node"].findall(f"notations/articulations/{mark}")
            if len(marks) > 1 or any(len(node) for node in marks):
                raise ValueError("중복되거나 복잡한 연주 기호는 원본을 유지합니다.")
            plans.append((endpoint["node"], marks))
        for note, marks in plans:
            if patch["action"] == "remove":
                for node in marks:
                    _remove(note, node)
            elif not marks:
                notation = _notation(note)
                articulation = notation.find("articulations")
                if articulation is None:
                    articulation = ET.SubElement(notation, "articulations")
                ET.SubElement(articulation, mark)
        return
    target = ctx.by_id.get(patch.get("target_note_id")) if isinstance(patch.get("target_note_id"), str) else None
    if target is None:
        raise ValueError("연결할 대상 음표를 선택해주세요.")
    pairs = _pairs(ctx, row, target, mark)
    if patch["action"] == "remove":
        plans = [(a, b, _simple_span(ctx, a, b, mark)) for a, b in pairs]
        for a, b, (start, stop, _) in plans:
            _remove(a["node"], start)
            _remove(b["node"], stop)
            if mark == "tie":
                a["node"].remove(a["node"].find("tie"))
                b["node"].remove(b["node"].find("tie"))
        return
    number = _addition(ctx, pairs, mark)
    for a, b in pairs:
        for endpoint, typ in ((a, "start"), (b, "stop")):
            if mark == "tie":
                base._put(endpoint["node"], "tie", ET.Element("tie", type=typ))
            node = ET.SubElement(_container(endpoint["node"], mark), "tied" if mark == "tie" else mark,
                                 type=typ, **({} if mark == "tie" else {"number": number}))
            if typ == "start" and mark in {"hammer-on", "pull-off"}:
                node.text = "H" if mark == "hammer-on" else "P"
