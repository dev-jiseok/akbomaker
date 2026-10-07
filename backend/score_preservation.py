"""Bounded layout-only MusicXML transformation, independent of the note editor.

The editor's sixteenth-note model must not be used here: tuplets, voices and
performance notation stay in MusicXML. The caller must retain the original
upload/OMR export separately. This is not an OMR correction or an XML validator.
"""
import xml.etree.ElementTree as ET

from .score_import import XML_LIMIT, inspect, safe_xml, unpack


PRESETS = {"practice", "standard", "large"}
MAX_DEPTH = 128
POSITION_ATTRIBUTES = {"default-x", "default-y", "relative-x", "relative-y"}
RESOURCE_ELEMENTS = {"image", "credit-image", "link", "part-link", "opus"}
ACTIVE_ELEMENTS = {"script", "style", "foreignObject", "iframe", "object", "embed", "audio", "video"}
RESOURCE_ATTRIBUTES = {"href", "src", "source", "data", "base", "style"}
LAYOUT_WARNING = ("음악 구조를 유지하는 레이아웃 전용 결과입니다. 음표 수정 모드가 아니며 "
                  "인식 오류를 자동으로 고치지 않습니다. 원본과 비교해 확인해주세요.")


def _bounded_depth(root):
    pending = [(root, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise ValueError("MusicXML 구조가 너무 깊어요. 단순한 파트 파일로 나누어주세요.")
        pending.extend((child, depth + 1) for child in node)


def _remove_resources(root):
    """Never carry fetchable image/link payloads into the browser preview."""
    removed = 0
    pending = [root]
    while pending:
        node = pending.pop()
        for child in list(node):
            if child.tag in RESOURCE_ELEMENTS | ACTIVE_ELEMENTS:
                node.remove(child)
                removed += 1
            else:
                pending.append(child)
        for key in list(node.attrib):
            name = key.rsplit("}", 1)[-1]
            if name in RESOURCE_ATTRIBUTES or name.lower().startswith("on"):
                del node.attrib[key]
                removed += 1
    # An image-only credit has no valid printable content after sanitization.
    # Text/symbol credits (including title, composer and copyright) remain.
    for credit in root.findall("credit"):
        if not any(child.tag in {"credit-words", "credit-symbol"} for child in credit):
            root.remove(credit)
    return removed


def _value(value):
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _element(parent, tag, value=None, **attributes):
    child = ET.SubElement(parent, tag, attributes)
    if value is not None:
        child.text = str(value)
    return child


def _set_defaults(root, preset):
    existing = root.findall("defaults")
    if len(existing) > 1:
        raise ValueError("MusicXML 기본 레이아웃 정의가 중복되어 있어요.")
    if existing:
        defaults = existing[0]
    else:
        defaults = ET.Element("defaults")
        # MusicXML root order: work/movement/identification, defaults, credits,
        # part-list, parts. Retain every metadata element and all text credits.
        index = next((i for i, node in enumerate(root)
                      if node.tag in {"credit", "part-list", "part"}), len(root))
        root.insert(index, defaults)
    for child in list(defaults):
        if child.tag in {"scaling", "page-layout", "system-layout", "staff-layout"}:
            defaults.remove(child)
    millimeters = 8.5 if preset == "large" else 7
    scaling = ET.Element("scaling")
    _element(scaling, "millimeters", millimeters)
    _element(scaling, "tenths", 40)
    defaults.insert(0, scaling)
    page = ET.Element("page-layout")
    # A4 stays physically A4 even when the printed staff is enlarged.
    _element(page, "page-height", _value(297 * 40 / millimeters))
    _element(page, "page-width", _value(210 * 40 / millimeters))
    margins = _element(page, "page-margins", type="both")
    for side in ("left", "right", "top", "bottom"):
        _element(margins, f"{side}-margin", _value(15 * 40 / millimeters))
    system = ET.Element("system-layout")
    _element(system, "system-distance", {"standard": 95, "practice": 140, "large": 160}[preset])
    staff = ET.Element("staff-layout")
    _element(staff, "staff-distance", {"standard": 70, "practice": 90, "large": 110}[preset])
    # concert-score, if present, must follow scaling and precede page-layout.
    insertion = 2 if len(defaults) > 1 and defaults[1].tag == "concert-score" else 1
    for offset, child in enumerate((page, system, staff)):
        defaults.insert(insertion + offset, child)


def restyle_musicxml(data: bytes, filename: str, part_id: str | None,
                     preset: str = "practice", measures_per_line: int = 4) -> tuple[bytes, list[str]]:
    """Return one part as layout-only UTF-8 MusicXML and explicit warnings.

    ``part_id=None`` is allowed only for a single-part score. ValueError means
    invalid input/layout, unsafe/oversized XML, or an ambiguous part selection.
    This pure operation never writes files or quantizes musical information.
    """
    if (not isinstance(data, bytes) or not isinstance(filename, str) or not filename
            or not isinstance(preset, str) or preset not in PRESETS
            or type(measures_per_line) is not int or measures_per_line not in {2, 4}
            or part_id is not None and (not isinstance(part_id, str) or not part_id)):
        raise ValueError("악보 파일·파트·출력 스타일과 한 줄 2/4마디 설정을 확인해주세요.")
    _, root = unpack(data, filename)
    _bounded_depth(root)
    listing = inspect(data, filename)
    ids = [part["id"] for part in listing["parts"]]
    return _restyle_tree(root, ids, part_id, preset, measures_per_line)


def restyle_working_xml(data: bytes, part_id: str, preset: str = "practice",
                        measures_per_line: int = 4) -> tuple[bytes, list[str]]:
    """Style stored, already unpacked XML without applying the upload ZIP limit.

    The original upload remains bounded at 2MB, expanded XML at 8MB. Edits do
    not shrink the expanded limit back to 2MB on each preview or save.
    """
    if (not isinstance(data, bytes) or not isinstance(part_id, str) or not part_id
            or not isinstance(preset, str) or preset not in PRESETS or type(measures_per_line) is not int
            or measures_per_line not in {2, 4}):
        raise ValueError("악보 파트·스타일·한 줄 마디 설정을 확인해주세요.")
    root = safe_xml(data)
    if root.tag != "score-partwise":
        raise ValueError("파트별 score-partwise MusicXML을 선택해주세요.")
    ids = [part.get("id", "") for part in root.findall("part")]
    if (not ids or len(ids) > 64 or len(set(ids)) != len(ids)
            or any(not value or len(value) > 80 for value in ids)):
        raise ValueError("악보의 파트 구성을 읽을 수 없어요.")
    return _restyle_tree(root, ids, part_id, preset, measures_per_line)


def _restyle_tree(root, ids, part_id, preset, measures_per_line):
    _bounded_depth(root)
    if part_id is None:
        if len(ids) != 1:
            raise ValueError("여러 파트가 있는 악보입니다. 보존하여 표시할 파트 하나를 선택해주세요.")
        part_id = ids[0]
    if part_id not in ids:
        raise ValueError("선택한 악보 파트를 찾을 수 없어요.")
    lists = root.findall("part-list")
    if len(lists) != 1:
        raise ValueError("MusicXML 파트 목록을 정확히 하나 지정해주세요.")
    part_list = lists[0]
    definitions = part_list.findall("score-part")
    declared = [node.get("id") for node in definitions]
    if (len(declared) != len(set(declared)) or set(declared) != set(ids)
            or any(len(node.findall("part-name")) != 1 for node in definitions)):
        raise ValueError("MusicXML 파트 목록과 실제 파트가 일치하지 않아요.")
    selected = next(part for part in root.findall("part") if part.get("id") == part_id)
    measures = selected.findall("measure")
    if not 1 <= len(measures) <= 600:
        raise ValueError("선택한 파트는 1~600마디 범위로 나누어주세요.")
    for part in list(root.findall("part")):
        if part is not selected:
            root.remove(part)
    # A part-group across removed parts would have dangling boundaries. The
    # selected part's own staff/brace/instrument definitions remain untouched.
    for definition in list(part_list):
        if definition.tag != "score-part" or definition.get("id") != part_id:
            part_list.remove(definition)
    removed = _remove_resources(root)
    _set_defaults(root, preset)
    # Credits are free-positioned page text, not note engraving positions.
    # Removing all their coordinates collapses composer/title/rights onto a
    # renderer-defined origin. Preserve their original layout as the minimal
    # non-destructive strategy; the caller also exposes attribution separately.
    credit_nodes = {node for credit in root.findall("credit") for node in credit.iter()}
    for node in root.iter():
        if node in credit_nodes:
            continue
        for attribute in POSITION_ATTRIBUTES:
            node.attrib.pop(attribute, None)
    for index, measure in enumerate(measures):
        measure.attrib.pop("width", None)
        for printing in list(measure.findall("print")):
            measure.remove(printing)
        if index % measures_per_line == 0:
            printing = ET.Element("print", {"new-system": "yes"} if index else {})
            _element(printing, "measure-numbering", "measure")
            measure.insert(0, printing)
    output = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(output) > XML_LIMIT:
        raise ValueError("재배치한 MusicXML이 8MB를 초과해요. 파트를 나누어주세요.")
    warnings = [LAYOUT_WARNING,
                "원본 줄·페이지 배치와 음표 좌표를 다시 정리했습니다. 텍스트·기호 크레딧의 원래 좌표는 유지하며, 표시 엔진에 따라 기호와 출처 배치가 다르게 보일 수 있어요."]
    if len(ids) > 1:
        warnings.append("선택한 한 파트만 포함합니다. 나머지 파트는 원본 파일에 그대로 보관해주세요.")
    if removed:
        warnings.append(f"미리보기의 외부 이미지·링크·활성 콘텐츠 참조 {removed}개를 제외했습니다. 텍스트 제목·저작자·저작권 표기는 유지합니다.")
    return output, warnings
