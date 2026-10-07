"""Compare musical XML before/after layout without judging OMR correctness."""
import copy
import hashlib
import json

from .score_import import safe_xml
from .score_preservation import POSITION_ATTRIBUTES


def music_fingerprint(xml: str | bytes, part_id: str) -> str:
    root = safe_xml(xml.encode("utf-8") if isinstance(xml, str) else xml)
    selected = next((p for p in root.findall("part") if p.get("id") == part_id), None)
    if selected is None:
        raise ValueError("검증할 악보 파트를 찾을 수 없어요.")
    selected = copy.deepcopy(selected)
    for measure in selected.findall("measure"):
        measure.attrib.pop("width", None)
        for item in list(measure.findall("print")):
            measure.remove(item)

    def structure(node):
        # Ignore only the position attributes removed by the layout engine.
        # Element order, semantic attributes, all note/notation text remain.
        attrs = sorted((key, value) for key, value in node.attrib.items() if key not in POSITION_ATTRIBUTES)
        return [node.tag, attrs, (node.text or "").strip(), [structure(child) for child in node]]

    definition = next((p for p in root.findall("part-list/score-part") if p.get("id") == part_id), None)
    if definition is None:
        raise ValueError("검증할 악보의 악기 정의를 찾을 수 없어요.")
    payload = json.dumps([structure(definition), structure(selected)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_layout(current: str, original: str, styled: bytes, part_id: str) -> dict:
    current_hash = music_fingerprint(current, part_id)
    styled_hash = music_fingerprint(styled, part_id)
    if current_hash != styled_hash:
        raise ValueError("스타일 변경 중 음악 내용이 달라져 출력을 중단했어요. 원본은 유지됩니다.")
    original_hash = music_fingerprint(original, part_id)
    return {"style_preserves_music": True, "music_edited": current_hash != original_hash,
            "current_music_sha256": current_hash, "original_music_sha256": original_hash,
            "styled_music_sha256": styled_hash,
            "scope": "selected-part-musicxml-not-pdf-recognition"}
