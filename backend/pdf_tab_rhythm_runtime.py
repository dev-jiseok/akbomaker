"""Bounded disposable parser for opt-in, evidence-based PDF TAB rhythm proposals."""
import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import pdf_score_analysis as geometry


def analyze(source, coordinates, staff_id, beats, beat_type):
    """Source/coordinates are verified by the parent as well as this worker."""
    from backend.pdf_tab_rhythm import extract_page_rhythm
    import pdfplumber
    from pdfminer.pdftypes import PDFStream, resolve1

    source_hash = hashlib.sha256(source).hexdigest()
    analysis = json.loads(coordinates)
    if analysis.get("source_sha256") != source_hash:
        raise ValueError("원본 PDF와 좌표 자료가 일치하지 않아요.")
    selected = [(page, staff) for page in analysis["pages"] for staff in page["staffs"]
                if staff.get("kind") == "tab" and staff.get("id") == staff_id]
    if len(selected) != 1:
        raise ValueError("리듬 후보를 만들 TAB 보표를 하나 선택해주세요.")
    page_geometry, _ = selected[0]
    with pdfplumber.open(io.BytesIO(source), password="") as document:
        if (document.doc.encryption or not document.doc.is_extractable
                or not 1 <= len(document.pages) <= geometry.MAX_DOCUMENT_PAGES):
            raise ValueError("암호화되었거나 너무 큰 PDF는 분석하지 않습니다.")
        page = document.pages[page_geometry["page"] - 1]
        geometry._check_page_dimensions(page.width, page.height)
        content_size = 0
        for reference in page.page_obj.contents:
            stream = resolve1(reference)
            if not isinstance(stream, PDFStream):
                raise ValueError("PDF 내용 구조를 확인할 수 없어요.")
            content_size += len(stream.get_data())
            if content_size > geometry.MAX_CONTENT_BYTES:
                raise ValueError("PDF 페이지의 압축 해제 내용이 너무 커요.")
        if sum(len(items) for items in page.objects.values()) > geometry.MAX_OBJECTS:
            raise ValueError("PDF 페이지 객체가 너무 많아요.")
        result = extract_page_rhythm(page_geometry, lines=page.lines, curves=page.curves,
                                     chars=page.chars, beats=beats, beat_type=beat_type)
        result["measures"] = [measure for measure in result["measures"] if measure["staff_id"] == staff_id]
        page.close()
    return {**result, "source_sha256": source_hash,
            "coordinate_sha256": hashlib.sha256(coordinates).hexdigest(), "staff_id": staff_id,
            "page": page_geometry["page"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--coordinates", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--coordinates-sha", required=True)
    parser.add_argument("--staff", required=True)
    parser.add_argument("--beats", required=True, type=int)
    parser.add_argument("--beat-type", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    geometry._worker_limits()
    if args.output.resolve() in {args.source.resolve(), args.coordinates.resolve()}:
        raise ValueError("원본 자료에 결과를 덮어쓸 수 없습니다.")
    payloads = []
    for path, maximum, digest in ((args.source, geometry.UPLOAD_LIMIT, args.source_sha),
                                  (args.coordinates, geometry.MAX_OUTPUT_BYTES, args.coordinates_sha)):
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
            raise ValueError("리듬 검토 원본을 안전하게 읽을 수 없어요.")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("리듬 검토 원본이 변경됐어요.")
        payloads.append(content)
    result = analyze(*payloads, args.staff, args.beats, args.beat_type)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > geometry.MAX_OUTPUT_BYTES:
        raise ValueError("리듬 후보 자료가 너무 커요.")
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("기존 결과 파일을 덮어쓰지 않습니다.")
    args.output.write_bytes(encoded)


if __name__ == "__main__":
    main()
