"""Read-only PDF geometry evidence, not OMR or a MusicXML converter.

PDFs with actual text digits and stroked TAB lines can expose original fret
numbers without guessing from audio or running OCR. This module records those
coordinates for human review; it does NOT recover rhythm, pitches, tuning,
ties, muted notes or the correspondence between notation and TAB staves.

Run PDF parsing in the bounded worker used by the caller, never inline in an
HTTP request. The CLI writes one JSON object to stdout and never changes its
input. Limits here complement (not replace) worker time/memory isolation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import sys
from pathlib import Path

UPLOAD_LIMIT = 25 * 1024 * 1024
MAX_PAGES = 4
MAX_DOCUMENT_PAGES = 100
MAX_OBJECTS = 50_000
MAX_CHARS = 20_000
MAX_STAFFS = 120
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 24_000_000
INSTRUMENTS = {"auto", "bass", "guitar", "drums", "piano", "synthesizer", "vocal"}
BASE_WARNINGS = [
    "PDF의 선과 실제 문자 좌표를 읽은 검토 자료입니다. 원본 악보와 같은 음·TAB를 완전히 복원했다는 뜻이 아닙니다.",
    "음표 길이·박자·타이·쉼표·튜닝·실제 음정과 오선/TAB 연동은 확인하지 않습니다. MusicXML 악보나 자동 채보 결과로 사용할 수 없습니다.",
    "숫자가 아닌 뮤트·슬라이드·연주 기호, 이미지나 윤곽선으로 그려진 숫자는 복원하지 않습니다. 숫자 후보와 줄 번호를 원본에서 확인해주세요.",
]


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bbox(node):
    values = [node.get(key) for key in ("x0", "top", "x1", "bottom")]
    if not all(_finite(value) for value in values):
        return None
    x0, top, x1, bottom = map(float, values)
    if x0 >= x1 or top > bottom:
        return None
    return [x0, top, x1, bottom]


def _round_box(values):
    return [round(value, 4) for value in values]


def _check_page_dimensions(width, height):
    if (not _finite(width) or not _finite(height) or not 0 < width <= 1440 or not 0 < height <= 1440
            or width * height * (300 / 72) ** 2 > MAX_PIXELS):
        raise ValueError("PDF 페이지 크기 제한을 초과하거나 좌표가 올바르지 않아요.")


def _merge_horizontal(edges, width, height):
    """Join same-height line segments interrupted by printed TAB digits.

    No segments are added to the source. Returned spans are evidence envelopes
    with an observed coverage ratio, not a reconstructed drawable staff.
    """
    segments = []
    for edge in edges:
        box = _bbox(edge)
        if box is None:
            continue
        x0, top, x1, bottom = box
        if (abs(bottom - top) > .3 or x1 - x0 < 1 or x0 < -.5 or x1 > width + .5
                or top < 0 or bottom > height or edge.get("stroke") is False):
            continue
        thickness = edge.get("linewidth", 0)
        if _finite(thickness) and thickness > 1.5:
            continue
        segments.append((.5 * (top + bottom), x0, x1))
    segments.sort()
    rows = []
    for y, x0, x1 in segments:
        if not rows or y - rows[-1]["y"] > .2:
            rows.append({"y": y, "segments": [(x0, x1)]})
        else:
            rows[-1]["segments"].append((x0, x1))
    result = []
    for row in rows:
        pieces = sorted(set(row["segments"]))
        runs = []
        for start, end in pieces:
            if not runs or start - runs[-1]["x1"] > 12:
                runs.append({"x0": start, "x1": end, "observed": end - start, "segments": 1})
            else:
                run = runs[-1]
                run["observed"] += max(0, end - max(start, run["x1"]))
                run["x1"] = max(run["x1"], end)
                run["segments"] += 1
        for run in runs:
            span = run["x1"] - run["x0"]
            if span >= max(100, width * .35) and run["observed"] / span >= .55:
                result.append({"y": row["y"], **run, "coverage": run["observed"] / span})
    return sorted(result, key=lambda row: (row["y"], row["x0"]))


def _same_span(a, b):
    overlap = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    return overlap >= .9 * max(a["x1"] - a["x0"], b["x1"] - b["x0"])


def _staff_groups(lines, instrument):
    """Only maximal, regularly spaced runs count; never take 4 out of 5 lines."""
    groups, used = [], set()
    for i, first in enumerate(lines):
        if i in used:
            continue
        following = [(j, line) for j, line in enumerate(lines[i + 1:], i + 1)
                     if 2 <= line["y"] - first["y"] <= 16 and _same_span(first, line)]
        if not following:
            continue
        second_index, second = following[0]
        spacing = second["y"] - first["y"]
        # A line directly above means this is an interior subset of an
        # unsupported/irregular staff; do not turn it into a 4-line TAB staff.
        if any(_same_span(first, previous) and abs(first["y"] - previous["y"] - spacing) <= .35 for previous in lines[:i]):
            continue
        chosen = [(i, first), (second_index, second)]
        while len(chosen) <= 8:
            target = chosen[-1][1]["y"] + spacing
            candidates = [(j, line) for j, line in enumerate(lines) if j > chosen[-1][0]
                          and abs(line["y"] - target) <= .35 and _same_span(first, line)]
            if len(candidates) != 1:
                break
            chosen.append(candidates[0])
        count = len(chosen)
        if count < 4:
            continue
        used.update(j for j, _ in chosen)
        if count == 5:
            kind = "staff"
        elif count in {4, 6} and instrument in {"auto", "bass", "guitar"}:
            kind = "tab"
        else:
            kind = "ambiguous"
        actual = [line for _, line in chosen]
        groups.append({"kind": kind, "line_count": count, "spacing": spacing,
                       "lines": actual, "bbox": [max(l["x0"] for l in actual), first["y"],
                                                    min(l["x1"] for l in actual), actual[-1]["y"]]})
    return groups


def _digit_tokens(chars, staff):
    candidates = []
    x0, top, x1, bottom = staff["bbox"]
    spacing = staff["spacing"]
    for index, char in enumerate(chars):
        text = char.get("text", "")
        box = _bbox(char)
        if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,2}", text) or box is None:
            continue
        centre = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        if not (x0 <= centre[0] <= x1 and top - spacing * .6 <= centre[1] <= bottom + spacing * .6):
            continue
        char_height = box[3] - box[1]
        if not .4 * spacing <= char_height <= 2 * spacing:
            continue
        candidates.append({"text": text, "bbox": box, "font": str(char.get("fontname", ""))[:160],
                           "upright": char.get("upright") is not False, "indices": [index]})
    # PDF text can be painted twice (e.g. faux-bold). Exact duplicate glyphs
    # represent one observed token, not two notes. Conflicting overprints stay.
    unique = {}
    for candidate in candidates:
        key = (candidate["text"], candidate["font"], tuple(round(v, 3) for v in candidate["bbox"]))
        if key in unique:
            unique[key]["indices"] += candidate["indices"]
        else:
            unique[key] = candidate
    candidates = sorted(unique.values(), key=lambda c: ((c["bbox"][1] + c["bbox"][3]) / 2, c["bbox"][0]))
    rows = []
    for candidate in candidates:
        middle = (candidate["bbox"][1] + candidate["bbox"][3]) / 2
        if not rows or abs(middle - rows[-1]["middle"]) > .15 * spacing:
            rows.append({"middle": middle, "chars": [candidate]})
        else:
            rows[-1]["chars"].append(candidate)
    tokens = []
    for row in rows:
        current = None
        for char in sorted(row["chars"], key=lambda c: c["bbox"][0]):
            gap = char["bbox"][0] - current["bbox"][2] if current else math.inf
            same_font = current and char["font"] == current["font"]
            height = char["bbox"][3] - char["bbox"][1]
            # Small glyph-box overlap can be font kerning (notably "11").
            # Substantial overprinting is not a safe multi-digit number.
            min_gap = -.2 * (char["bbox"][2] - char["bbox"][0])
            if current and same_font and min_gap <= gap <= min(.7, height * .12):
                current["text"] += char["text"]
                current["indices"] += char["indices"]
                current["bbox"] = [min(current["bbox"][0], char["bbox"][0]), min(current["bbox"][1], char["bbox"][1]),
                                    max(current["bbox"][2], char["bbox"][2]), max(current["bbox"][3], char["bbox"][3])]
                current["upright"] = current["upright"] and char["upright"]
            else:
                current = {**char, "bbox": list(char["bbox"]), "indices": list(char["indices"])}
                tokens.append(current)
    return sorted(tokens, key=lambda c: (c["bbox"][0], c["bbox"][1]))


def _describe_digits(tokens, staff):
    results = []
    spacing = staff["spacing"]
    overlapping = set()
    for index, token in enumerate(tokens):
        box = token["bbox"]
        for j in range(index + 1, len(tokens)):
            other = tokens[j]["bbox"]
            if other[0] > box[2] - .2:
                break
            horizontal = min(box[2], other[2]) - max(box[0], other[0])
            vertical = min(box[3], other[3]) - max(box[1], other[1])
            if horizontal > .2 and vertical > .3 * min(box[3] - box[1], other[3] - other[1]):
                overlapping.update((index, j))
    for index, token in enumerate(tokens):
        box = token["bbox"]
        center = (box[1] + box[3]) / 2
        nearest = sorted((abs(line["y"] - center), j + 1) for j, line in enumerate(staff["lines"]))
        distance, string = nearest[0]
        reasons = []
        if not token["upright"]:
            reasons.append("회전된 숫자의 줄 위치는 자동 확정하지 않습니다.")
        if box[2] - box[0] > spacing * 2.5:
            reasons.append("숫자 텍스트의 폭이 일반 TAB 범위를 벗어나 원본 확인이 필요합니다.")
        if distance > spacing * .3 or nearest[1][0] - distance < spacing * .25:
            reasons.append("숫자가 한 TAB 줄에 충분히 가깝지 않습니다.")
        text = token["text"]
        valid_fret = len(text) <= 2 and not (len(text) > 1 and text.startswith("0")) and int(text) <= 36
        if not valid_fret:
            reasons.append("0~36의 명확한 한 프렛 숫자로 판단할 수 없습니다.")
        if index in overlapping:
            reasons.append("다른 숫자와 겹쳐 원본 대조가 필요합니다.")
        results.append({"id": f"d{index}", "text": text[:80], "fret": int(text) if valid_fret else None,
                        "string": string if not reasons else None, "bbox": _round_box(box),
                        "font": token["font"], "accepted": not reasons, "requires_review": True,
                        "method": "embedded-text-and-vector-lines", "line_distance": round(distance, 4),
                        "reasons": reasons})
    return results


def _tab_symbols(chars, staff):
    """Keep nearby embedded non-numeric marks as unclassified review evidence.

    This deliberately does not infer a muted note, a tie or a playing technique.
    Outlined marks are not PDF characters and are still outside this analysis.
    """
    x0, top, x1, bottom = staff["bbox"]
    spacing = staff["spacing"]
    result, seen = [], set()
    for char in chars:
        text = char.get("text", "")
        box = _bbox(char)
        if (not isinstance(text, str) or not text.strip() or re.fullmatch(r"[0-9]+", text)
                or box is None or len(text) > 32):
            continue
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if not (x0 <= cx <= x1 and top - spacing * .8 <= cy <= bottom + spacing * .8):
            continue
        if not .3 * spacing <= box[3] - box[1] <= 2 * spacing:
            continue
        font = str(char.get("fontname", ""))[:160]
        key = (text, font, tuple(round(v, 3) for v in box))
        if key in seen:
            continue
        seen.add(key)
        result.append({"text": text, "bbox": _round_box(box), "font": font,
                       "requires_review": True,
                       "reason": "숫자가 아닌 원본 문자입니다. 뮤트·괄호·연주 기호 등의 의미와 리듬은 직접 확인해주세요."})
    return sorted(result, key=lambda item: (item["bbox"][0], item["bbox"][1]))


def _tab_measures(edges, staff, staff_id, digits):
    """Split only at observed full-height vector strokes, never by spacing.

    The verification flag means the geometric boundary evidence is present;
    it does not certify a complete musical measure or recover its duration.
    Nearby strokes of a double/final bar belong to one boundary. A missing
    system edge leaves the staff unsegmented rather than inventing an edge.
    """
    x0, top, x1, bottom = staff["bbox"]
    spacing = staff["spacing"]
    tolerance = max(.35, spacing * .08)
    cluster_gap = spacing * .8
    candidates = []
    for edge in edges:
        if edge.get("object_type") not in {None, "line", "rect_edge"}:
            continue
        values = [edge.get(key) for key in ("x0", "top", "x1", "bottom")]
        if not all(_finite(value) for value in values):
            continue
        left, start, right, end = map(float, values)
        if left > right or start >= end or right - left > tolerance:
            continue
        # Stroked lines and the vertical sides of filled barline rectangles
        # are evidence; invisible/non-painted paths are not.
        if edge.get("stroke") is not True and edge.get("fill") is not True:
            continue
        width = edge.get("linewidth", 0)
        if _finite(width) and width > max(2, spacing * .4):
            continue
        if start > top + tolerance or end < bottom - tolerance:
            continue
        x = (left + right) / 2
        if x0 - cluster_gap <= x <= x1 + cluster_gap:
            candidates.append(x)
    clusters = []
    for x in sorted(candidates):
        if not clusters or x - clusters[-1][0] > cluster_gap:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    # Use the original horizontal-line endpoint when that endpoint is backed
    # by an observed stroke. This also avoids cutting off the final barline.
    left_groups = [group for group in clusters if min(abs(x - x0) for x in group) <= tolerance]
    right_groups = [group for group in clusters if min(abs(x - x1) for x in group) <= tolerance]
    if len(left_groups) != 1 or len(right_groups) != 1 or left_groups[0] is right_groups[0]:
        return [], False
    boundaries = [x0]
    for group in clusters:
        if group is left_groups[0] or group is right_groups[0]:
            continue
        centre = sum(group) / len(group)
        if x0 < centre < x1:
            boundaries.append(centre)
    boundaries.append(x1)
    boundaries.sort()
    if any(b - a <= cluster_gap for a, b in zip(boundaries, boundaries[1:])):
        return [], False
    measures = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:]), 1):
        ids = []
        for digit in digits:
            center = (digit["bbox"][0] + digit["bbox"][2]) / 2
            if left <= center < right or (index == len(boundaries) - 1 and center == right):
                ids.append(digit["id"])
        measures.append({"id": f"{staff_id}m{index}", "index": index,
                         "bbox": _round_box([left, top, right, bottom]), "digit_ids": ids})
    return measures, True


def analyze_page_geometry(*, page_number, width, height, chars, edges, instrument, rotation=0, image_count=0):
    """Pure bounded geometry analysis; exposed for regression tests."""
    _check_page_dimensions(width, height)
    if len(chars) > MAX_CHARS or len(edges) > MAX_OBJECTS:
        raise ValueError("페이지의 문자·벡터 객체가 너무 많아요. 페이지를 나누어주세요.")
    if not isinstance(instrument, str) or instrument not in INSTRUMENTS:
        raise ValueError("분석할 악기를 선택해주세요.")
    warnings = []
    if rotation not in {0, None}:
        warnings.append("회전된 페이지는 좌표 자동 배정을 지원하지 않습니다. 원본 방향을 확인해주세요.")
        lines, groups = [], []
    else:
        lines = _merge_horizontal(edges, width, height)
        groups = _staff_groups(lines, instrument)
    if len(groups) > MAX_STAFFS:
        raise ValueError("페이지의 보표 후보가 너무 많아요.")
    staffs = []
    for index, group in enumerate(groups):
        staff_id = f"p{page_number}s{index}"
        digits = _describe_digits(_digit_tokens(chars, group), group) if group["kind"] == "tab" else []
        symbols = _tab_symbols(chars, group) if group["kind"] == "tab" else []
        measures, verified = _tab_measures(edges, group, staff_id, digits) if group["kind"] == "tab" else ([], False)
        staff_warnings = []
        if group["kind"] == "tab":
            staff_warnings.append("규칙적인 4/6줄 영역을 TAB 후보로 표시했습니다. 보표 종류와 위에서부터 매긴 줄 번호를 원본에서 확인해주세요.")
            if verified:
                staff_warnings.append("마디 구간은 보표 전체 높이를 지나는 원본 세로선의 좌표입니다. 박자·음표 길이·타이와 마디의 음악적 완전성은 확인하지 않았습니다.")
            else:
                staff_warnings.append("양쪽 끝의 원본 세로선을 확인하지 못해 마디 구간을 추정하지 않았습니다. 원본에서 마디를 직접 확인해주세요.")
            if symbols:
                staff_warnings.append("숫자가 아닌 문자가 있습니다. X·괄호·연주 기호 등을 프렛 숫자로 바꾸거나 생략한 완성 악보로 취급하지 마세요.")
        if group["kind"] == "ambiguous":
            staff_warnings.append("이 선 배열의 악보 종류를 확정하지 않았습니다.")
        staffs.append({"id": staff_id, "kind": group["kind"], "line_count": group["line_count"],
                       "spacing": round(group["spacing"], 4), "bbox": _round_box(group["bbox"]),
                       "lines": [{"y": round(line["y"], 4), "x0": round(line["x0"], 4), "x1": round(line["x1"], 4),
                                  "coverage": round(line["coverage"], 4)} for line in group["lines"]],
                       "digits": digits, "symbols": symbols, "measures": measures,
                       "measure_boundaries_verified": verified, "warnings": staff_warnings})
    if not chars:
        warnings.append("추출할 실제 PDF 문자가 없습니다. 스캔 이미지·윤곽선 숫자는 이 분석으로 복원하지 않습니다.")
    if not staffs:
        warnings.append("확실한 규칙적 보표 선을 찾지 못했습니다. 선을 추정하거나 새로 그리지 않습니다.")
    if instrument == "drums":
        warnings.append("드럼 보표의 선 위치만 확인합니다. 음표·킥/하이햇 종류나 리듬을 해석하거나 생략된 음자리표를 추가하지 않습니다.")
    elif not any(staff["kind"] == "tab" for staff in staffs):
        warnings.append("4/6줄 TAB 후보를 찾지 못했습니다. 5줄은 일반 오선과 구별할 근거가 없어 TAB로 배정하지 않습니다.")
    if any(staff["kind"] == "tab" and not staff["digits"] for staff in staffs):
        warnings.append("TAB 선 후보는 있지만 읽을 수 있는 숫자 문자가 없습니다. 이미지나 윤곽선 숫자는 추정하지 않습니다.")
    return {"page": page_number, "width": float(width), "height": float(height), "rotation": rotation or 0,
            "coordinate_system": "pdf-points-top-left", "staffs": staffs, "warnings": warnings,
            "statistics": {"characters": len(chars), "image_objects": image_count, "horizontal_lines": len(lines),
                           "tab_digits": sum(len(staff["digits"]) for staff in staffs),
                           "coordinate_matched_digits": sum(d["accepted"] for s in staffs for d in s["digits"])}}


def analyze_pdf(data: bytes, pages: list[int], instrument: str = "auto") -> dict:
    if not isinstance(data, bytes) or not data.startswith(b"%PDF-") or not 0 < len(data) <= UPLOAD_LIMIT:
        raise ValueError("25MB 이하의 PDF 파일을 선택해주세요.")
    if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES
            or any(type(page) is not int or not 1 <= page <= MAX_DOCUMENT_PAGES for page in pages)
            or len(set(pages)) != len(pages)):
        raise ValueError("PDF 페이지를 중복 없이 1~100 중 최대 4개 선택해주세요.")
    if not isinstance(instrument, str) or instrument not in INSTRUMENTS:
        raise ValueError("분석할 악기를 선택해주세요.")
    try:
        import pdfplumber
        from pdfminer.pdftypes import PDFStream, resolve1
    except ImportError:
        raise ValueError("PDF 좌표 분석 패키지가 설치되지 않았어요. 운영자가 pdfplumber를 설치해야 합니다.") from None
    try:
        with pdfplumber.open(io.BytesIO(data), password="") as document:
            total_pages = len(document.pages)
            if not 1 <= total_pages <= MAX_DOCUMENT_PAGES or max(pages) > total_pages:
                raise ValueError("PDF는 100페이지 이하이며 선택한 페이지가 파일 안에 있어야 합니다.")
            if not document.doc.is_extractable or document.doc.encryption:
                raise ValueError("암호화되었거나 추출 권한이 없는 PDF는 분석하지 않습니다.")
            extracted = []
            for number in pages:
                page = document.pages[number - 1]
                _check_page_dimensions(page.width, page.height)
                content_size = 0
                for reference in page.page_obj.contents:
                    stream = resolve1(reference)
                    if not isinstance(stream, PDFStream):
                        raise ValueError("PDF 페이지 내용의 구조가 올바르지 않아요.")
                    content_size += len(stream.get_data())
                    if content_size > MAX_CONTENT_BYTES:
                        raise ValueError("PDF 페이지의 압축 해제 내용이 너무 커요.")
                objects = page.objects
                if sum(len(items) for items in objects.values()) > MAX_OBJECTS:
                    raise ValueError("PDF 페이지의 객체 수가 너무 많아요.")
                extracted.append(analyze_page_geometry(page_number=number, width=page.width, height=page.height,
                                 chars=page.chars, edges=page.edges, instrument=instrument,
                                 rotation=page.rotation, image_count=len(page.images)))
                page.close()
    except ValueError:
        raise
    except Exception:
        raise ValueError("PDF의 문자·벡터 좌표를 안전하게 읽지 못했어요. 원본 파일은 변경하지 않았습니다.") from None
    result = {"schema_version": 1, "source_sha256": hashlib.sha256(data).hexdigest(), "page_count": total_pages,
              "instrument": instrument, "pages": extracted, "warnings": BASE_WARNINGS.copy(),
              "editable_musicxml": False, "rhythm_known": False, "requires_review": True}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("PDF 좌표 분석 결과가 너무 커요. 페이지를 나누어주세요.")
    return result


def _worker_limits():
    """Apply limits only in the disposable CLI worker, not library callers."""
    limits = {}
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (40, 42))
        limits["cpu_seconds"] = 40
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
            limits["address_space_bytes"] = 1024 ** 3
        elif sys.platform == "darwin":
            # Darwin's AS limit does not reliably bound mmap. DATA bounds heap
            # allocations but is not advertised as a total process-memory cap.
            resource.setrlimit(resource.RLIMIT_DATA, (512 * 1024 ** 2, 512 * 1024 ** 2))
            limits["data_segment_bytes"] = 512 * 1024 ** 2
    except (ImportError, OSError, ValueError):
        limits["warning"] = "일부 OS 자원 제한을 적용하지 못했습니다. 외부 작업 시간 제한과 격리가 필요합니다."
    return limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Optional trusted destination for bounded UTF-8 JSON")
    parser.add_argument("--pages", default="1", help="Comma-separated 1-based page numbers (at most four)")
    parser.add_argument("--instrument", default="auto", choices=sorted(INSTRUMENTS))
    arguments = parser.parse_args()
    try:
        limits = _worker_limits()
        if arguments.output and arguments.input.resolve() == arguments.output.resolve():
            raise ValueError("원본 PDF를 분석 결과로 덮어쓸 수 없습니다.")
        if arguments.input.stat().st_size > UPLOAD_LIMIT:
            raise ValueError("25MB 이하의 PDF 파일을 선택해주세요.")
        if not re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{1,3}){0,3}", arguments.pages):
            raise ValueError("페이지 번호를 1,2처럼 최대 4개 입력해주세요.")
        result = analyze_pdf(arguments.input.read_bytes(), [int(p) for p in arguments.pages.split(",")], arguments.instrument)
        result["worker_limits"] = limits
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_OUTPUT_BYTES:
            raise ValueError("PDF 좌표 분석 결과가 2MB를 초과해요. 페이지를 나누어주세요.")
        if arguments.output:
            temporary = arguments.output.with_name(arguments.output.name + ".tmp")
            if temporary.resolve() == arguments.input.resolve():
                raise ValueError("원본 PDF를 임시 분석 파일로 덮어쓸 수 없습니다.")
            if arguments.output.is_symlink() or temporary.is_symlink():
                raise ValueError("안전하지 않은 분석 결과 저장 경로예요.")
            temporary.write_bytes(payload)
            temporary.replace(arguments.output)
            print(json.dumps({"source_sha256": result["source_sha256"], "bytes": len(payload),
                              "pages": [page["page"] for page in result["pages"]]}))
        else:
            print(payload.decode("utf-8"))
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)[:500]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
