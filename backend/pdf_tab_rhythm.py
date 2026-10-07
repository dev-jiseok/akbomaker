"""Conservative rhythm suggestions from paired vector notation and TAB.

Only a bounded monophonic, filled-notehead subset of vector engraving is
supported. Time comes from stem/beam/flag/dot geometry, NEVER horizontal note
spacing. A whole measure is withheld unless every head and TAB digit is
accounted for, observed ties explain missing continuation frets, and explicit
caller-confirmed meter matches the exact rational duration. Suggestions still
require original review; this is not a general OMR engine or auto-import.
"""
from __future__ import annotations

import math
from fractions import Fraction

MAX_OBJECTS = 50_000
MAX_MEASURES = 600
METHOD = "paired-vector-staff-rhythm"
TYPES = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1),
         "eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16)}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _box(value):
    if not isinstance(value, dict):
        return None
    box = [value.get(key) for key in ("x0", "top", "x1", "bottom")]
    if not all(_finite(item) for item in box) or box[0] > box[2] or box[1] > box[3]:
        return None
    return box


def _path(curve):
    raw = curve.get("path", [])
    if not isinstance(raw, (list, tuple)) or len(raw) > 100:
        return None
    for command in raw:
        if not isinstance(command, (list, tuple)) or not command or not isinstance(command[0], str):
            return None
        for point in command[1:]:
            if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(_finite(v) for v in point):
                return None
    return raw


def _commands(curve):
    path = _path(curve)
    return "".join(item[0] for item in path) if path is not None else ""


def _head(curve, spacing):
    """Known filled oval contour, normalized independently of PDF font size."""
    box = _box(curve)
    if box is None or curve.get("fill") is not True or _commands(curve) != "mcccc":
        return False
    width, height = box[2] - box[0], box[3] - box[1]
    if not .96 <= height / spacing <= 1.04 or not 1.14 <= width / height <= 1.22:
        return False
    path = _path(curve)
    if any(len(command) != (2 if index == 0 else 4) for index, command in enumerate(path)):
        return False
    # Endpoint topology distinguishes the slanted note oval from circles,
    # clef dots and arbitrary mcccc decorative paths of comparable size.
    ends = [command[-1] for command in path]
    expected = [(0.329, 1), (1, .332), (.671, 0), (0, .668), (.329, 1)]
    return all(abs((point[0] - box[0]) / width - target[0]) < .035
               and abs((point[1] - box[1]) / height - target[1]) < .035
               for point, target in zip(ends, expected))


def _circle(curve, spacing, augmentation=False):
    box = _box(curve)
    if box is None or curve.get("fill") is not True or _commands(curve) != "mcccc":
        return False
    width, height = box[2] - box[0], box[3] - box[1]
    low, high = (.37, .43) if augmentation else (.30, .44)
    return low <= width / spacing <= high and abs(width - height) <= spacing * .025


def _beam(curve, spacing):
    path = _path(curve)
    if path is None or curve.get("fill") is not True or _commands(curve) != "mlll" or any(len(p) != 2 for p in path):
        return None
    points = [p[1] for p in path]
    # Filled parallelogram with two vertical sides, not a slur/flag outline.
    if (abs(points[0][0] - points[3][0]) > .04 * spacing
            or abs(points[1][0] - points[2][0]) > .04 * spacing
            or abs(points[0][0] - points[1][0]) < .6 * spacing):
        return None
    thickness = abs(points[3][1] - points[0][1])
    if not .44 * spacing <= thickness <= .56 * spacing or abs(abs(points[2][1] - points[1][1]) - thickness) > .04 * spacing:
        return None
    return points


def _beam_at(points, x):
    a, b = points[0], points[1]
    return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])


def _flag(curve, spacing):
    box = _box(curve)
    # This exact contour family is the up-stem eighth flag in the inspected
    # vector scores. Different fonts/flags are deliberately not classified.
    return (box is not None and curve.get("fill") is True and _commands(curve) == "mvcccclcccccc"
            and .99 < (box[2] - box[0]) / spacing < 1.12
            and 3.20 < (box[3] - box[1]) / spacing < 3.36)


def _tie(curve, spacing):
    path = _path(curve)
    if path is None or curve.get("fill") is not True or _commands(curve) != "mcc" or [len(p) for p in path] != [2, 4, 4]:
        return None
    start, end, close = path[0][1], path[1][-1], path[2][-1]
    if (end[0] - start[0] < spacing * .5 or abs(start[1] - end[1]) > .08 * spacing
            or abs(start[0] - close[0]) > .04 * spacing or abs(start[1] - close[1]) > .04 * spacing):
        return None
    controls = [point for command in path[1:] for point in command[1:-1]]
    offsets = [point[1] - start[1] for point in controls]
    if (not all(start[0] <= p[0] <= end[0] for p in controls)
            or not (all(v > 0 for v in offsets) or all(v < 0 for v in offsets))
            or not .15 * spacing < max(abs(v) for v in offsets) < 1.6 * spacing):
        return None
    return start, end


def _header_symbol(curve, spacing):
    """Known bass-clef/key-signature/time-4 outlines, only allowed in header.

    This narrow allowlist prevents an unrecognized leading rest or second
    voice from being silently ignored merely because it precedes TAB digits.
    Other engraving fonts and header families require manual review.
    """
    box = _box(curve)
    if box is None or curve.get("fill") is not True:
        return False
    width, height = (box[2] - box[0]) / spacing, (box[3] - box[1]) / spacing
    profiles = {
        "mcccccccccccccccc": (2.144, 3.589),
        "mcccc": (.440, .440),
        "mcccccccc": (.380, .668),
        "mclccvclcclccclcclcyclccvcclcyclccvcclcclclcclcclcyclcclclch": (.997, 2.793),
        "mclccvclcclccclcclcyclccvcclcyclccvcclclcclcclcclcyclcclclch": (.997, 2.793),
        "mlcclcllccccccclcccllccclcccllcch": (1.716, 2.000),
        "mlccccclch": (1.876, 1.180),  # bracket cap, left of staff start
    }
    expected = profiles.get(_commands(curve))
    return expected is not None and abs(width - expected[0]) < .045 and abs(height - expected[1]) < .045


def _stem(head, lines, spacing):
    box = _box(head)
    found = []
    for line in lines:
        segment = _box(line)
        if segment is None or line.get("stroke") is not True or segment[2] - segment[0] > spacing * .04:
            continue
        x, top, _, bottom = segment
        if not 1.7 * spacing < bottom - top < 5.5 * spacing:
            continue
        if abs(x - box[2]) <= spacing * .12 and box[1] <= bottom <= box[3] and top < box[1] - spacing * 1.4:
            found.append({"x": x, "end": top, "direction": "up", "bbox": segment})
        elif abs(x - box[0]) <= spacing * .12 and box[1] <= top <= box[3] and bottom > box[3] + spacing * 1.4:
            found.append({"x": x, "end": bottom, "direction": "down", "bbox": segment})
    unique = {tuple(round(v, 3) for v in stem["bbox"]): stem for stem in found}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _duration(head, stem, curves, spacing):
    box = _box(head)
    layers, flags = [], []
    for index, curve in enumerate(curves):
        points = _beam(curve, spacing)
        if points:
            left, right = sorted((points[0][0], points[1][0]))
            if left - spacing * .10 <= stem["x"] <= right + spacing * .10:
                y = _beam_at(points, stem["x"])
                if (stem["direction"] == "up" and stem["end"] - .2 * spacing <= y < min(box[1] - .5 * spacing, stem["end"] + 2 * spacing)
                    or stem["direction"] == "down" and max(box[3] + .5 * spacing, stem["end"] - 2 * spacing) < y <= stem["end"] + .2 * spacing):
                    layers.append((index, y))
        elif _flag(curve, spacing) and stem["direction"] == "up":
            flag = _box(curve)
            if abs(flag[0] - stem["x"]) < spacing * .10 and abs(flag[1] - stem["end"]) < spacing * .13:
                flags.append(index)
    layers = sorted(layers, key=lambda pair: pair[1])
    # Duplicated painted contours are not additional beam levels.
    ys = []
    for _, y in layers:
        if not ys or y - ys[-1] > .12 * spacing:
            ys.append(y)
    if flags and layers or len(flags) > 1 or len(ys) > 3:
        return None
    if len(ys) > 1 and any(not .65 * spacing <= b - a <= .85 * spacing for a, b in zip(ys, ys[1:])):
        return None
    level = len(ys) or (1 if flags else 0)
    dot_candidates = []
    for index, curve in enumerate(curves):
        if not _circle(curve, spacing, augmentation=True):
            continue
        dot = _box(curve)
        cx, cy = (dot[0] + dot[2]) / 2, (dot[1] + dot[3]) / 2
        if box[2] + .12 * spacing < cx < box[2] + 1.3 * spacing and abs(cy - (box[1] + box[3]) / 2) <= .56 * spacing:
            dot_candidates.append(index)
    if len(dot_candidates) > 2:
        return None
    duration = Fraction(1, 2 ** level) * sum((Fraction(1, 2 ** index) for index in range(len(dot_candidates) + 1)), Fraction())
    return duration, {"beam_count": level if layers else 0, "flag_count": len(flags), "dots": len(dot_candidates),
                      "beam_curve_indices": [index for index, _ in layers], "flag_curve_indices": flags, "dot_curve_indices": dot_candidates}


def _representation(duration):
    for kind, value in TYPES.items():
        for dots in range(3):
            if value * sum((Fraction(1, 2 ** index) for index in range(dots + 1)), Fraction()) == duration:
                return kind, dots
    return None


def _measure(tab, staff, measure, lines, curves, chars, bar):
    spacing = staff["spacing"]
    left, _, right, _ = measure["bbox"]
    candidates = [curve for curve in curves if (box := _box(curve)) is not None
                  and box[2] > left and box[0] < right
                  and staff["bbox"][1] - 6 * spacing <= box[1] <= staff["bbox"][3] + 6 * spacing]
    heads = sorted([curve for curve in candidates if _head(curve, spacing)
                    and left < (curve["x0"] + curve["x1"]) / 2 < right], key=lambda c: c["x0"])
    if len(candidates) > 2500 or len(heads) > 128:
        return [], ["마디 기호 수가 안전한 리듬 검토 범위를 넘었습니다."]
    if not heads:
        return [], ["지원하는 채워진 음표 머리를 찾지 못했습니다. 쉼표·온음표·빈 머리 등은 직접 확인해주세요."]
    if any(b["x0"] - a["x0"] < .6 * spacing for a, b in zip(heads, heads[1:])):
        return [], ["동시음·복수 성부·꾸밈음의 구분이 필요해 이 마디 리듬을 제안하지 않습니다."]
    # Text tuplets and inline technique labels can change/qualify the rhythm.
    for char in chars:
        box = _box(char)
        if box and isinstance(char.get("text"), str) and char["text"].strip() and left < (box[0] + box[2]) / 2 < right:
            if staff["bbox"][1] - 4 * spacing <= (box[1] + box[3]) / 2 <= staff["bbox"][3] + 3 * spacing:
                # Printed bar labels sit before the first note / on a bar
                # boundary. Only numbers in the note-bearing span can be
                # tuplets; these are withheld, not interpreted as durations.
                if (char["text"].isdigit() and box[3] > staff["bbox"][1] - 2 * spacing
                        and heads[0]["x0"] <= (box[0] + box[2]) / 2 <= heads[-1]["x1"]):
                    return [], ["오선 근처 숫자가 있어 잇단음표·박자 등의 직접 확인이 필요합니다."]
    timings, cursor = [], Fraction()
    for head in heads:
        stem = _stem(head, lines, spacing)
        value = _duration(head, stem, candidates, spacing) if stem else None
        if value is None:
            return [], ["음표 기둥·빔·꼬리·점의 유일한 조합을 확인하지 못했습니다."]
        duration, evidence = value
        timings.append({"head": head, "onset": cursor, "duration": duration,
                        "evidence": {"head_bbox": _box(head), "stem_bbox": stem["bbox"],
                                     "onset": str(cursor), "duration": str(duration), **evidence}})
        cursor += duration
    if cursor != bar:
        return [], [f"벡터 음표 길이 합계 {cursor}박이 확인한 박자표 {bar}박과 다릅니다. 누락·쉼표·리듬 기호를 직접 확인해주세요."]
    used_symbols = {index for timing in timings for key in ("beam_curve_indices", "flag_curve_indices", "dot_curve_indices")
                    for index in timing["evidence"][key]}
    for index, curve in enumerate(candidates):
        if (_beam(curve, spacing) or _flag(curve, spacing) or _circle(curve, spacing, augmentation=True)) and index not in used_symbols:
            return [], ["음표와 연결되지 않은 빔·꼬리·점을 발견해 리듬을 보류합니다."]
    # Unknown outlines, including leading/trailing rests and second voices,
    # must not become implicit quarter notes or disappear from the measure.
    for curve in candidates:
        box = _box(curve)
        if _head(curve, spacing) or _circle(curve, spacing) or _beam(curve, spacing) or _flag(curve, spacing) or _tie(curve, spacing):
            continue
        if (measure["index"] == 1 and box[2] < heads[0]["x0"]
                and box[2] < staff["bbox"][0] + 13 * spacing and _header_symbol(curve, spacing)):
            continue
        if box[3] >= staff["bbox"][1] - 4 * spacing and box[1] <= staff["bbox"][3] + 3 * spacing:
            return [], ["지원하지 않는 오선 벡터 기호가 있어 해당 마디는 직접 확인해야 합니다."]
    digits = [d for d in tab["digits"] if d["id"] in measure["digit_ids"]]
    if any(not d.get("accepted") for d in digits):
        return [], ["확정되지 않은 프렛 숫자·줄 위치가 있어 이 마디를 자동 연결하지 않습니다."]
    if any(left <= (symbol["bbox"][0] + symbol["bbox"][2]) / 2 <= right for symbol in tab.get("symbols", [])):
        return [], ["TAB의 뮤트·괄호·추가 기호가 있어 타이와 음표의 직접 대조가 필요합니다."]
    matches, seen_digits = {}, set()
    for index, item in enumerate(timings):
        center = (item["head"]["x0"] + item["head"]["x1"]) / 2
        nearby = [d for d in digits if abs((d["bbox"][0] + d["bbox"][2]) / 2 - center) <= .35 * spacing]
        if len(nearby) > 1:
            return [], ["오선 음표와 TAB 숫자의 연결이 유일하지 않습니다."]
        if nearby:
            if nearby[0]["id"] in seen_digits:
                return [], ["한 TAB 숫자에 여러 오선 음표가 연결되어 확인이 필요합니다."]
            matches[index] = nearby[0]
            seen_digits.add(nearby[0]["id"])
    if len(seen_digits) != len(digits):
        return [], ["모든 TAB 숫자를 오선 음표에 연결하지 못했습니다."]
    tie_after = {}
    for curve in candidates:
        endpoints = _tie(curve, spacing)
        if not endpoints:
            continue
        start, end = endpoints
        pairs = []
        for index, (a, b) in enumerate(zip(heads, heads[1:])):
            if (abs(a["x1"] - start[0]) < .3 * spacing and abs(b["x0"] - end[0]) < .3 * spacing
                    and abs((a["top"] + a["bottom"]) - (b["top"] + b["bottom"])) < .1 * spacing
                    and abs(start[1] - (a["top"] + a["bottom"]) / 2) < .4 * spacing):
                pairs.append(index)
        if len(pairs) != 1 or pairs[0] in tie_after or pairs[0] + 1 in matches:
            return [], ["타이와 슬러 또는 타이 계속음의 TAB 표기를 유일하게 구별하지 못했습니다."]
        tie_after[pairs[0]] = endpoints
    rows, consumed = [], set()
    for index, item in enumerate(timings):
        if index in consumed:
            continue
        digit = matches.get(index)
        if digit is None:
            return [], ["TAB 숫자가 없는 음표에 연결되는 타이를 확인하지 못했습니다."]
        duration, last, evidence = item["duration"], index, [item["evidence"]]
        consumed.add(index)
        while last in tie_after:
            last += 1
            duration += timings[last]["duration"]
            evidence.append(timings[last]["evidence"])
            consumed.add(last)
        representation = _representation(duration)
        if representation is None:
            return [], [f"타이 합산 길이 {duration}박을 현재 검토 입력 형식으로 정확히 표현할 수 없습니다."]
        kind, dots = representation
        ties = [{"start": list(tie_after[i][0]), "end": list(tie_after[i][1])} for i in range(index, last)]
        rows.append({"row_id": f'{tab["id"]}:{digit["id"]}', "staff_id": tab["id"], "measure_index": measure["index"],
                     "onset": str(item["onset"]), "duration": str(duration), "type": kind, "dots": dots,
                     "string": digit["string"], "fret": digit["fret"], "requires_review": True,
                     "evidence": {"notes": evidence, "tie_count": last - index, "ties": ties}})
    return rows, []


def extract_page_rhythm(geometry: dict, *, lines: list, curves: list, chars: list, beats: int, beat_type: int) -> dict:
    if type(beats) is not int or not 1 <= beats <= 12 or type(beat_type) is not int or beat_type not in {2, 4, 8, 16}:
        raise ValueError("직접 확인한 박자표를 지정해주세요.")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("staffs"), list):
        raise ValueError("원본 보표 좌표 자료가 필요합니다.")
    if any(not isinstance(items, list) or len(items) > MAX_OBJECTS for items in (lines, curves, chars)):
        raise ValueError("페이지 벡터·문자 객체 수 제한을 초과했습니다.")
    report = {"schema_version": 1, "method": METHOD, "page": geometry.get("page"),
              "meter": {"beats": beats, "beat_type": beat_type, "source": "caller-confirmed"},
              "requires_review": True, "rhythm_known": False, "measures": [],
              "warnings": ["벡터 음표와 TAB 숫자를 연결한 검토 후보입니다. 원본의 모든 기호를 복원하거나 음악적 동일성을 보증하지 않습니다.",
                           "채워진 단선율 음표·명확한 빔·일부 꼬리·마디 안 타이만 지원합니다. 지원하지 않는 마디는 제안하지 않습니다."]}
    bar = Fraction(4 * beats, beat_type)
    for tab in geometry["staffs"]:
        if tab.get("kind") != "tab" or not tab.get("measure_boundaries_verified"):
            continue
        paired = [staff for staff in geometry["staffs"] if staff.get("kind") == "staff" and staff.get("line_count") == 5
                  and 0 < tab["bbox"][1] - staff["bbox"][3] < 7 * staff["spacing"]
                  and abs(tab["bbox"][0] - staff["bbox"][0]) < staff["spacing"]
                  and abs(tab["bbox"][2] - staff["bbox"][2]) < staff["spacing"]]
        for measure in tab.get("measures", []):
            if len(report["measures"]) >= MAX_MEASURES:
                raise ValueError("리듬 검토 마디 수 제한을 초과했습니다.")
            if len(paired) != 1:
                rows, reasons = [], ["TAB와 짝인 위쪽 5선 보표를 유일하게 확인하지 못했습니다."]
            else:
                rows, reasons = _measure(tab, paired[0], measure, lines, curves, chars, bar)
            report["measures"].append({"staff_id": tab["id"], "staff_measure_id": measure["id"],
                "measure_index": measure["index"], "paired_staff_id": paired[0]["id"] if len(paired) == 1 else None,
                "status": "unresolved" if reasons else "suggested", "rows": rows, "unresolved": reasons})
    return report
