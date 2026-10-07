"""Read-only, bounded MusicXML consistency checks, never PDF/OMR accuracy.

W3C MusicXML 4.0: duration is in divisions, time-modification describes the
cumulative sounding/written ratio (including nested tuplets); tie is sound and
tied is notation. Implicit/non-controlling measures and free meter can be short.
https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/
Only source facts participate. No note, lyric, rhythm or staff is inferred.
"""
from collections import Counter, defaultdict
from fractions import Fraction
import re

from . import source_score as source

SCOPE = "musicxml-structure-not-pdf-accuracy"
ISSUE_LIMIT = 200
NOTE_LENGTHS = {"maxima": Fraction(32), "long": Fraction(16), "breve": Fraction(8),
                "whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1),
                **{name: Fraction(4, denominator) for name, denominator in
                   (("eighth", 8), ("16th", 16), ("32nd", 32), ("64th", 64),
                    ("128th", 128), ("256th", 256), ("512th", 512), ("1024th", 1024))}}


class _Report:
    def __init__(self):
        self.issues, self.total = [], 0

    def add(self, code, message, location, severity="warning"):
        self.total += 1
        if len(self.issues) < ISSUE_LIMIT:
            item = {"code": code, "severity": severity, "measure_index": location["measure_index"],
                    "measure_number": str(location["measure_number"])[:120], "message": message}
            if "id" in location:
                item["note_id"] = location["id"]
            self.issues.append(item)


def _pitch(note):
    """Exact sounding semitones; enharmonic and microtonal ties remain valid."""
    pitch = note.findall("pitch")
    if len(pitch) != 1:
        raise ValueError
    step, octave = source._one(pitch[0], "step"), source.integer(source._one(pitch[0], "octave"))
    alteration = source.number(source._one(pitch[0], "alter", "0"))
    if step not in source.STEPS or not 0 <= octave <= 9 or abs(alteration) > 12:
        raise ValueError
    return Fraction((octave + 1) * 12 + source.STEPS[step]) + alteration


def _meter(node):
    if node.find("senza-misura") is not None:
        return None
    pairs = [child for child in node if child.tag in {"beats", "beat-type"}]
    if not pairs or len(pairs) % 2:
        raise ValueError
    result = Fraction(0)
    for index in range(0, len(pairs), 2):
        beats, unit = pairs[index:index + 2]
        if beats.tag != "beats" or unit.tag != "beat-type" or not re.fullmatch(r"\d{1,4}(?:\s*\+\s*\d{1,4}){0,15}", (beats.text or "").strip()):
            raise ValueError
        counts = [int(value) for value in beats.text.split("+")]
        denominator = source.integer(unit.text)
        if any(not 1 <= value <= 128 for value in counts) or not 1 <= denominator <= 1024:
            raise ValueError
        result += Fraction(4 * sum(counts), denominator)
    return result


def _written_length(row, report):
    note = row["node"]
    if note.find("grace") is not None or any(rest.get("measure") == "yes" for rest in note.findall("rest")):
        return
    types = note.findall("type")
    if not types:  # MusicXML permits an omitted visual note type.
        return
    if len(types) != 1 or types[0].text not in NOTE_LENGTHS or len(note.findall("dot")) > 8:
        report.add("rhythm-notation-review", "특수하거나 불명확한 음표 모양의 길이는 자동 비교하지 않았어요.", row, "info")
        return
    dots = len(note.findall("dot"))
    expected = NOTE_LENGTHS[types[0].text] * (2 - Fraction(1, 2 ** dots))
    modifications = note.findall("time-modification")
    if modifications:
        try:
            if len(modifications) != 1:
                raise ValueError
            actual = source.integer(source._one(modifications[0], "actual-notes"))
            normal = source.integer(source._one(modifications[0], "normal-notes"))
            if actual <= 0 or normal <= 0:
                raise ValueError
            # normal-type/normal-dot describe the unit for displaying tuplets;
            # the cumulative normal/actual ratio already includes nested groups.
            expected *= Fraction(normal, actual)
        except ValueError:
            report.add("rhythm-ratio-invalid", "잇단음표·트레몰로의 실제/기준 음표 수가 불명확해 길이를 비교하지 못했어요.", row)
            return
    elif note.find("notations/tuplet") is not None or any(n.get("type") in {"start", "stop"} for n in note.findall("notations/ornaments/tremolo")):
        report.add("rhythm-ratio-missing", "잇단음표·양음 트레몰로 표시는 있지만 시간 비율이 없어 길이를 자동 비교하지 않았어요.", row, "info")
        return
    if row["duration_value"] is not None and expected != row["duration_value"]:
        report.add("note-duration-mismatch", f"음표 모양의 길이({expected}박)와 MusicXML 재생 길이({row['duration_value']}박)가 달라요. 1박은 4분음표 기준입니다.", row)


def _timelines(part, rows, report):
    """One cursor pass and O(N) grouped extents; never sum simultaneous voices."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["measure_index"]].append(row)
    divisions, meters, absolute, segment = None, {}, Fraction(0), 0
    locations, timelines = {}, {}
    for index, measure in enumerate(part.findall("measure"), 1):
        location = {"measure_index": index, "measure_number": measure.get("number", str(index))}
        locations[index] = location
        current_rows = groups[index]
        by_node = {id(row["node"]): row for row in current_rows}
        position, extent, trustworthy, meter_changed_inside = Fraction(0), Fraction(0), True, False
        note_meters = set()
        overfull = False
        for node in measure:
            if node.tag == "attributes":
                if node.find("divisions") is not None:
                    try:
                        divisions = source.number(source._one(node, "divisions"))
                        if divisions <= 0:
                            raise ValueError
                    except ValueError:
                        divisions = None
                        report.add("divisions-invalid", "음표 시간 단위(divisions)가 없거나 양수가 아니어서 리듬 위치를 확인할 수 없어요.", location)
                declared = set()
                times = node.findall("time")
                # An unnumbered signature applies to all staves, replacing old
                # staff-specific signatures; concurrent numbered ones override it.
                if any(time.get("number") is None for time in times):
                    meters = {}
                for time in sorted(times, key=lambda time: time.get("number") is not None):
                    staff = time.get("number", "all")
                    if position not in {Fraction(0), None}:
                        meter_changed_inside = True
                    try:
                        if staff in declared:
                            raise ValueError
                        meters[staff] = _meter(time)
                    except ValueError:
                        meters[staff] = None
                        report.add("meter-review", "박자표가 복합적이거나 불명확해 마디 길이를 자동 판정하지 않았어요.", location, "info")
                    declared.add(staff)
            elif node.tag in {"backup", "forward"}:
                try:
                    delta = source.number(source._one(node, "duration")) / divisions
                    if position is None or delta <= 0:
                        raise ValueError
                    position += delta * (-1 if node.tag == "backup" else 1)
                    if position < 0:
                        raise ValueError
                    extent = max(extent, position)
                except (ValueError, TypeError, ZeroDivisionError):
                    report.add("timeline-movement-invalid", "성부 이동(backup/forward)의 길이가 잘못되었거나 마디 시작보다 앞서요.", location)
                    trustworthy, position = False, None
            elif node.tag == "note":
                row = by_node[id(node)]
                length, onset = row["duration_value"], row["onset_value"]
                meter = meters.get(row["staff"], meters.get("all"))
                note_meters.add(meter)
                if length is None or onset is None or onset < 0:
                    report.add("note-timing-invalid", "음표 길이·시작 위치가 불명확해요. duration, divisions와 화음·성부 이동을 확인해주세요.", row)
                    trustworthy = False
                else:
                    extent = max(extent, onset + length)
                    if meter is not None and onset + length > meter:
                        overfull = True
                if node.find("chord") is None:
                    position = onset + length if onset is not None and length is not None else None
        # A later <forward> may extend beyond the last sounding note, while a
        # final <backup> must not shorten the time occupied by another voice.
        common_meter = next(iter(note_meters)) if len(note_meters) == 1 else None
        if common_meter is None and not current_rows:
            common_meter = meters.get("all", meters.get("1"))
        special = measure.get("implicit") == "yes" or measure.get("non-controlling") == "yes"
        if meter_changed_inside:
            report.add("meter-change-review", "마디 안에서 박자표가 바뀌어 마디 합계와 마디 경계 타이를 자동 판정하지 않았어요.", location, "info")
        elif trustworthy:
            if overfull or common_meter is not None and extent > common_meter:
                report.add("measure-overfull", "음표·성부 이동의 끝이 기재된 박자표의 마디 길이를 넘어요. 원본의 마디 경계를 확인해주세요.", location)
            elif current_rows and common_meter is not None and extent < common_meter and not special:
                report.add("measure-underfull", f"기록된 시간 범위가 {extent}박으로 박자표의 {common_meter}박보다 짧아요. 의도적인 부분 마디인지 원본에서 확인해주세요.", location, "info")
        reliable = trustworthy and not meter_changed_inside and common_meter is not None
        if reliable:
            timelines[index] = (segment, absolute)
            absolute += extent if special else max(common_meter, extent)
        else:
            # Unknown/free-meter boundaries cannot establish exact cross-bar
            # adjacency. Isolate them without disabling subsequent clear bars.
            segment += 1
            timelines[index] = None
            absolute = Fraction(0)
    return locations, timelines


def _ties(rows, part, timelines, report):
    starts, stops = defaultdict(list), defaultdict(list)
    loose_starts, loose_stops = defaultdict(set), defaultdict(set)
    repeated = part.find(".//repeat") is not None or part.find(".//ending") is not None or any(
        any(key in node.attrib for key in ("dacapo", "dalsegno", "tocoda", "fine")) for node in part.iter("sound"))
    review_measures = set()
    def manual(row):
        if row["measure_index"] not in review_measures:
            review_measures.add(row["measure_index"])
            report.add("tie-manual-review", "반복·특수 번호·성부/보표 이동 등 복잡한 타이 연결은 원본과 직접 확인해주세요. 자동 끊김 판정은 생략했어요.", row, "info")
    candidates, ambiguous_groups = [], set()
    for row in rows:
        note = row["node"]
        sound, visual = note.findall("tie"), note.findall("notations/tied")
        if not sound and not visual:
            continue
        if not sound and all(mark.get("type") == "let-ring" for mark in visual):
            continue
        sound_types, visual_types = [n.get("type") for n in sound], [n.get("type") for n in visual]
        timeline = timelines[row["measure_index"]]
        try:
            if row["transpose"] is None or timeline is None or row["onset_value"] is None or row["duration_value"] is None or row["duration_value"] <= 0:
                raise ValueError
            pitch = _pitch(note) + row["transpose"]
        except ValueError:
            manual(row)
            continue
        group = (timeline[0], row["staff"], row["voice"], pitch)
        complex_tie = (repeated or any(n.get("time-only") is not None for n in sound) or
                       any(n.get("number", "1") != "1" for n in visual) or
                       any(value not in {"start", "stop"} for value in sound_types + visual_types) or
                       len(sound_types) != len(set(sound_types)) or len(visual_types) != len(set(visual_types)) or
                       visual_types == ["start", "stop"] or len(note.findall("voice")) > 1 or len(note.findall("staff")) > 1)
        if complex_tie:
            ambiguous_groups.add(group)
            manual(row)
        candidates.append((row, group, timeline[1] + row["onset_value"], sound_types, visual_types))
    for row, group, onset, sound, visual in candidates:
        types = set(sound) | set(visual)
        if "start" in types:
            loose_starts[(group[0], group[3], onset + row["duration_value"])].add(group[1:3])
        if "stop" in types:
            loose_stops[(group[0], group[3], onset)].add(group[1:3])
        if group in ambiguous_groups:
            continue
        if set(sound) != set(visual):
            report.add("tie-sound-notation-mismatch", "재생 타이(tie)와 그려진 붙임줄(tied)의 시작·끝 표기가 달라요. 의도적인 표기 생략인지 확인해주세요.", row)
        # Audit the union: a notated tie with absent playback must still have a
        # geometric counterpart, but each endpoint can contribute only once.
        if "start" in types:
            time = onset + row["duration_value"]
            starts[(*group, time)].append(row)
            loose_starts[(group[0], group[3], time)].add(group[1:3])
        if "stop" in types:
            stops[(*group, onset)].append(row)
            loose_stops[(group[0], group[3], onset)].add(group[1:3])
    for endpoints, opposite, loose, code, label in (
        (starts, stops, loose_stops, "tie-start-unmatched", "뒤에 이어지는"),
        (stops, starts, loose_starts, "tie-stop-unmatched", "앞에서 이어지는"),
    ):
        for key, values in endpoints.items():
            partners = opposite.get(key, [])
            if len(values) == 1 and len(partners) == 1:
                continue
            if len(values) > 1 or len(partners) > 1 or loose.get((key[0], key[3], key[4])):
                for row in values:
                    manual(row)
                continue
            neighbor = values[0]["measure_index"] + (1 if endpoints is starts else -1)
            if neighbor in timelines and timelines[neighbor] is None:
                manual(values[0])
                continue
            report.add(code, f"선택한 악보 범위에서 같은 음정·성부·보표로 {label} 타이를 찾지 못했어요. 다른 페이지나 성부로 연결되는지 확인해주세요.", values[0])


def _instrument_checks(root, part, rows, instrument, report):
    definition = next(info for info in root.findall("part-list/score-part") if info.get("id") == part.get("id"))
    declared = Counter(n.get("id") for n in definition.findall("score-instrument"))
    midi_counts = Counter(n.get("id") for n in definition.findall("midi-instrument"))
    mapped = set()
    for item in definition.findall("midi-instrument"):
        try:
            if item.get("id") and declared[item.get("id")] == 1 and midi_counts[item.get("id")] == 1 and 1 <= source.integer(source._one(item, "midi-unpitched")) <= 128:
                mapped.add(item.get("id"))
        except ValueError:
            pass
    dynamic = part.find(".//midi-instrument") is not None or part.find(".//instrument-change") is not None
    pitched_staffs = set()
    informational = set()
    def info_once(code, message, row):
        key = (code, row["measure_index"], row["staff"])
        if key not in informational:
            informational.add(key)
            report.add(code, message, row, "info")
    for row in rows:
        note = row["node"]
        if sum(len(note.findall(kind)) for kind in ("pitch", "unpitched", "rest")) != 1:
            report.add("note-kind-ambiguous", "음표의 음정·타악기·쉼표 종류가 누락되었거나 중복되어 있어요.", row)
        if note.find("pitch") is not None:
            pitched_staffs.add(row["staff"])
            if instrument == "drums":
                report.add("drum-pitched-note", "드럼 파트의 음표가 타격 종류(unpitched)가 아닌 음정(pitch)으로 기록되어 있어요. 원본 타격을 확인해주세요.", row)
        if instrument == "drums" and note.find("unpitched") is not None:
            refs = note.findall("instrument")
            if dynamic:
                info_once("drum-instrument-change-review", "곡 안의 타악기 변경 정보가 있어 타격 종류를 고정 악기표로 자동 검증하지 않았어요.", row)
            elif not refs:
                report.add("drum-instrument-missing", "타악기 음표에 명시적인 악기 참조가 없어 킥·하이햇 등 타격 종류를 확정하지 못했어요.", row)
            elif len(refs) != 1 or refs[0].get("id") not in mapped:
                report.add("drum-instrument-unmapped", "타악기 음표의 악기 참조와 명확한 MIDI 타격 정의를 연결하지 못했어요.", row)
        has_tab = row["clef"] == "TAB" or note.find("notations/technical/string") is not None or note.find("notations/technical/fret") is not None
        if not has_tab or note.find("rest") is not None:
            continue
        if any(note.find(path) is not None for path in ("notations/technical/harmonic", "notations/technical/bend")) or any((head.text or "normal") != "normal" for head in note.findall("notehead")):
            info_once("tab-technique-review", "하모닉·벤딩·뮤트 등 특수 TAB 주법의 음정은 일반 줄·프렛 공식으로 판정하지 않았어요.", row)
            continue
        try:
            fingering = source._fingering(note)
            if row["tuning"] is None or row["transpose"] is None:
                raise ValueError
            tuning, capo = row["tuning"]
            if fingering["string"] > len(tuning):
                report.add("tab-string-out-of-range", "TAB 줄 번호가 명시된 튜닝의 줄 수를 벗어나요.", row)
                continue
            if tuning[fingering["string"] - 1] + capo + fingering["fret"] != _pitch(note) + row["transpose"]:
                report.add("tab-pitch-mismatch", "명시된 튜닝·카포·이조를 적용한 TAB 줄·프렛의 음정과 기록된 음정이 달라요.", row)
        except ValueError:
            info_once("tab-pitch-unverified", "명확한 튜닝·줄·프렛·음정 정보가 부족해 TAB 음정 일치를 자동 확인하지 못했어요.", row)
    if instrument in {"piano", "synthesizer"} and len(pitched_staffs) == 1:
        first = next(row for row in rows if row["node"].find("pitch") is not None)
        report.add("keyboard-single-staff", "현재 음정 음표는 한 보표에만 기록되어 있어요. 원본이 양손 큰보표인지 확인해주세요. 한 보표 악보 자체는 오류가 아닙니다.", first, "info")


def audit(xml, part_id, instrument):
    """Return bounded issue evidence; input bytes and all score nodes stay intact."""
    root, part = source._load(xml, part_id, instrument)
    rows = source._scan(part)
    report = _Report()
    _, timelines = _timelines(part, rows, report)
    for row in rows:
        _written_length(row, report)
    _ties(rows, part, timelines, report)
    _instrument_checks(root, part, rows, instrument, report)
    return {"scope": SCOPE, "issues": report.issues, "total_issues": report.total,
            "truncated": report.total > len(report.issues), "checked_measures": len(part.findall("measure")),
            "checked_notes": len(rows)}
