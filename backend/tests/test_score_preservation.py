import io
import zipfile
import xml.etree.ElementTree as ET

import pytest

from backend.score_import import UPLOAD_LIMIT, XML_LIMIT, safe_xml
from backend.score_preservation import restyle_musicxml, restyle_working_xml


def score(*, parts=1, measures=6):
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(root, "work"), "work-title").text = "연습 & 원본"
    identity = ET.SubElement(root, "identification")
    ET.SubElement(identity, "creator", type="composer").text = "Original Composer"
    ET.SubElement(identity, "rights").text = "© 원본 저작자"
    defaults = ET.SubElement(root, "defaults")
    ET.SubElement(defaults, "concert-score")
    ET.SubElement(defaults, "music-font", **{"font-family": "Bravura"})
    credit = ET.SubElement(root, "credit", page="1")
    ET.SubElement(credit, "credit-words", **{"default-x": "100", "default-y": "1200"}).text = "© 별도 표기"
    listing = ET.SubElement(root, "part-list")
    ET.SubElement(listing, "part-group", type="start", number="1")
    for i in range(parts):
        part = ET.SubElement(listing, "score-part", id=f"P{i + 1}")
        ET.SubElement(part, "part-name").text = f"Part {i + 1}"
        ET.SubElement(ET.SubElement(part, "score-instrument", id=f"I{i + 1}"), "instrument-name").text = "Instrument"
        ET.SubElement(ET.SubElement(part, "midi-instrument", id=f"I{i + 1}"), "midi-program").text = "1"
    ET.SubElement(listing, "part-group", type="stop", number="1")
    for i in range(parts):
        part = ET.SubElement(root, "part", id=f"P{i + 1}")
        for j in range(measures):
            measure = ET.SubElement(part, "measure", number=str(j), implicit="yes" if j == 0 else "no", width="300")
            ET.SubElement(measure, "print", **{"new-page": "yes"})
            if j == 0:
                attributes = ET.SubElement(measure, "attributes")
                ET.SubElement(attributes, "divisions").text = "12"
                ET.SubElement(ET.SubElement(attributes, "key"), "fifths").text = "-3"
                time = ET.SubElement(attributes, "time")
                ET.SubElement(time, "beats").text = "7"
                ET.SubElement(time, "beat-type").text = "8"
                ET.SubElement(ET.SubElement(attributes, "clef"), "sign").text = "G"
            direction = ET.SubElement(measure, "direction", placement="below")
            ET.SubElement(ET.SubElement(ET.SubElement(direction, "direction-type"), "dynamics"), "pp")
            ET.SubElement(direction, "sound", tempo="93.5", dynamics="25")
            note = ET.SubElement(measure, "note", **{"default-x": "90"})
            pitch = ET.SubElement(note, "pitch")
            for tag, text in [("step", "E"), ("alter", "-1"), ("octave", "4")]:
                ET.SubElement(pitch, tag).text = text
            for tag, text in [("duration", "4"), ("voice", "2"), ("type", "eighth")]:
                ET.SubElement(note, tag).text = text
            timing = ET.SubElement(note, "time-modification")
            ET.SubElement(timing, "actual-notes").text = "3"
            ET.SubElement(timing, "normal-notes").text = "2"
            ET.SubElement(note, "stem").text = "down"
            ET.SubElement(note, "beam", number="1").text = "begin"
            notation = ET.SubElement(note, "notations")
            ET.SubElement(notation, "tuplet", type="start", number="1")
            ET.SubElement(notation, "slur", type="start", number="1")
            ET.SubElement(ET.SubElement(notation, "articulations"), "accent")
            ET.SubElement(ET.SubElement(notation, "technical"), "hammer-on", type="start").text = "H"
            lyric = ET.SubElement(note, "lyric", number="2")
            ET.SubElement(lyric, "syllabic").text = "begin"
            ET.SubElement(lyric, "text").text = "노래"
            ET.SubElement(lyric, "extend", type="start")
            barline = ET.SubElement(measure, "barline", location="right")
            ET.SubElement(barline, "ending", type="stop", number="1")
            ET.SubElement(barline, "repeat", direction="backward", times="2")
    return ET.tostring(root, encoding="utf-8")


def semantic_part(root):
    for node in root.iter():
        for name in ("default-x", "default-y", "relative-x", "relative-y"):
            node.attrib.pop(name, None)
    for measure in root.findall("part/measure"):
        measure.attrib.pop("width", None)
        for printing in list(measure.findall("print")):
            measure.remove(printing)
    return ET.tostring(root.find("part"))


@pytest.mark.parametrize("preset", ["practice", "standard", "large"])
@pytest.mark.parametrize("count", [2, 4])
def test_layout_changes_preserve_all_musical_content_and_credits(preset, count):
    source = score()
    output, warnings = restyle_musicxml(source, "source.musicxml", None, preset, count)
    original, rendered = safe_xml(source), safe_xml(output)
    assert semantic_part(original) == semantic_part(rendered)
    assert rendered.findtext("work/work-title") == "연습 & 원본"
    assert ET.tostring(rendered.find("identification")) == ET.tostring(original.find("identification"))
    assert rendered.findtext("credit/credit-words") == "© 별도 표기"
    assert rendered.find("defaults/music-font").get("font-family") == "Bravura"
    assert [n.tag for n in rendered.find("defaults")][:3] == ["scaling", "concert-score", "page-layout"]
    fresh = safe_xml(output)
    assert [i for i, measure in enumerate(fresh.findall("part/measure")) if measure.find("print") is not None] == list(range(0, 6, count))
    assert fresh.findtext("defaults/scaling/millimeters") == ("8.5" if preset == "large" else "7")
    assert float(fresh.findtext("defaults/page-layout/page-width")) * float(fresh.findtext("defaults/scaling/millimeters")) / 40 == pytest.approx(210, abs=.001)
    assert any("음표 수정 모드가 아니" in warning for warning in warnings)


def test_part_selection_keeps_matching_instrument_metadata_and_no_dangling_groups():
    output, warnings = restyle_musicxml(score(parts=2), "source.xml", "P2")
    root = safe_xml(output)
    assert [part.get("id") for part in root.findall("part")] == ["P2"]
    assert [part.get("id") for part in root.findall("part-list/score-part")] == ["P2"]
    assert root.find("part-list/score-part/midi-instrument").get("id") == "I2"
    assert root.find("part-list/part-group") is None
    assert any("한 파트" in warning for warning in warnings)
    with pytest.raises(ValueError, match="여러 파트"):
        restyle_musicxml(score(parts=2), "source.xml", None)
    with pytest.raises(ValueError, match="찾을 수"):
        restyle_musicxml(score(parts=2), "source.xml", "missing")


@pytest.mark.parametrize("case", ["missing-list", "duplicate-list", "missing-definition", "duplicate-definition", "missing-name"])
def test_invalid_part_lists_fail_closed(case):
    root = safe_xml(score())
    listing = root.find("part-list")
    if case == "missing-list":
        root.remove(listing)
    elif case == "duplicate-list":
        root.append(ET.fromstring(ET.tostring(listing)))
    elif case == "missing-definition":
        listing.remove(listing.find("score-part"))
    elif case == "duplicate-definition":
        listing.append(ET.fromstring(ET.tostring(listing.find("score-part"))))
    else:
        part = listing.find("score-part")
        part.remove(part.find("part-name"))
    with pytest.raises(ValueError, match="파트"):
        restyle_musicxml(ET.tostring(root), "source.xml", None)


def test_external_images_links_and_active_content_removed_but_text_attribution_preserved():
    root = safe_xml(score())
    credit = root.find("credit")
    ET.SubElement(credit, "credit-image", source="https://private.example/track", type="image/png")
    image_credit = ET.SubElement(root, "credit", page="1")
    ET.SubElement(image_credit, "credit-image", source="file:///private/secret", type="image/png")
    direction = root.find("part/measure/direction/direction-type")
    ET.SubElement(direction, "image", source="data:text/html,invalid")
    ET.SubElement(root.find("part/measure"), "link", **{"{http://www.w3.org/1999/xlink}href": "https://private.example"})
    ET.SubElement(root.find("part-list/score-part"), "part-link", **{"{http://www.w3.org/1999/xlink}href": "https://private.example/part.xml"})
    ET.SubElement(root, "script").text = "alert('not music')"
    root.find("credit/credit-words").set("onclick", "bad()")
    root.find("part/measure/note").set("{http://www.w3.org/1999/xlink}href", "javascript:bad()")
    output, warnings = restyle_musicxml(ET.tostring(root), "source.xml", None)
    rendered = safe_xml(output)
    for tag in ("credit-image", "image", "link", "part-link", "script"):
        assert rendered.find(f".//{tag}") is None
    assert rendered.findtext("credit/credit-words") == "© 별도 표기"
    assert len(rendered.findall("credit")) == 1
    assert b"private.example" not in output and b"javascript:" not in output and b"onclick" not in output
    assert any("이미지·링크" in warning for warning in warnings)


@pytest.mark.parametrize("preset", ["practice", "standard", "large"])
def test_multiple_credits_retain_distinct_positions_and_symbol_only_credit(preset):
    root = safe_xml(score())
    for number, (text, x, y) in enumerate([("원래 제목", "600", "1500"),
                                          ("Composer", "1000", "1420"),
                                          ("Copyright Owner", "600", "40")]):
        credit = ET.Element("credit", page="1")
        ET.SubElement(credit, "credit-words", **{"default-x": x, "default-y": y,
            "relative-x": "3", "relative-y": "-5", "halign": "center"}).text = text
        root.insert(4 + number, credit)
    credit = ET.Element("credit", page="1")
    ET.SubElement(credit, "credit-symbol", **{"default-x": "100", "default-y": "80"}).text = "coda"
    root.insert(4, credit)
    before = [ET.tostring(credit) for credit in root.findall("credit")]
    output, _ = restyle_musicxml(ET.tostring(root), "source.xml", None, preset)
    assert [ET.tostring(credit) for credit in safe_xml(output).findall("credit")] == before
    assert "default-x" not in safe_xml(output).find("part/measure/note").attrib


def test_reapplying_same_layout_is_idempotent():
    output, _ = restyle_musicxml(score(), "source.xml", None, "large", 2)
    repeated, _ = restyle_musicxml(output, "restyled.musicxml", None, "large", 2)
    assert repeated == output


def packed(data, path="score.xml"):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("META-INF/container.xml", f'<container><rootfiles><rootfile full-path="{path}" /></rootfiles></container>')
        archive.writestr(path, data)
    return target.getvalue()


def test_namespace_and_mxl_are_supported_without_note_editor_conversion():
    data = score().replace(b'<score-partwise version="4.0">', b'<score-partwise xmlns="http://www.musicxml.org/ns/musicxml" version="4.0">')
    output, _ = restyle_musicxml(packed(data), "source.mxl", "P1")
    assert semantic_part(safe_xml(output)) == semantic_part(safe_xml(data))


@pytest.mark.parametrize("data,filename", [
    (b"%PDF-1.4", "source.pdf"),
    (b"", "source.xml"),
    (b"not xml", "source.xml"),
    (b"<score-timewise/>", "source.xml"),
    (b'<!DOCTYPE score-partwise [<!ENTITY secret SYSTEM "file:///etc/passwd">]><score-partwise/>', "source.xml"),
    (b"x" * (2 * 1024 * 1024 + 1), "source.xml"),
])
def test_hostile_or_invalid_documents_rejected(data, filename):
    with pytest.raises(ValueError):
        restyle_musicxml(data, filename, None)


def test_zip_traversal_depth_and_measure_limits_rejected():
    with pytest.raises(ValueError, match="경로"):
        restyle_musicxml(packed(score(), "../score.xml"), "source.mxl", None)
    with pytest.raises(ValueError, match="깊어요"):
        restyle_musicxml(b"<a>" * 130 + b"</a>" * 130, "source.xml", None)
    for count in (0, 601):
        with pytest.raises(ValueError, match="600마디"):
            restyle_musicxml(score(measures=count), "source.xml", None)
    output, _ = restyle_musicxml(score(measures=600), "source.xml", None)
    assert len(safe_xml(output).findall("part/measure")) == 600


@pytest.mark.parametrize("preset,count,part", [("bad", 4, None), (None, 4, None), ([], 4, None),
    ("practice", True, None), ("practice", 3, None), ("practice", 2.0, None),
    ("practice", 4, ""), ("practice", 4, [])])
def test_invalid_layout_arguments_rejected(preset, count, part):
    with pytest.raises(ValueError, match="설정"):
        restyle_musicxml(score(), "source.xml", part, preset, count)


def test_defaults_created_in_schema_order_and_duplicate_defaults_rejected():
    root = safe_xml(score())
    root.remove(root.find("defaults"))
    output, _ = restyle_musicxml(ET.tostring(root), "source.xml", None)
    tags = [node.tag for node in safe_xml(output)]
    assert tags.index("identification") < tags.index("defaults") < tags.index("credit") < tags.index("part-list")
    root = safe_xml(score())
    root.insert(1, ET.Element("defaults"))
    with pytest.raises(ValueError, match="중복"):
        restyle_musicxml(ET.tostring(root), "source.xml", None)


def test_working_xml_accepts_expanded_documents_larger_than_upload_limit():
    root = safe_xml(score(measures=1))
    root.find("identification/rights").text = "r" * (UPLOAD_LIMIT + 1024)
    data = ET.tostring(root)
    assert UPLOAD_LIMIT < len(data) < XML_LIMIT
    output, warnings = restyle_working_xml(data, "P1", "large", 2)
    assert warnings and UPLOAD_LIMIT < len(output) < XML_LIMIT
    assert semantic_part(safe_xml(output)) == semantic_part(safe_xml(data))
    assert safe_xml(output).findtext("identification/rights") == root.findtext("identification/rights")
    assert restyle_working_xml(output, "P1", "large", 2)[0] == output
    # The separate uncompressed upload endpoint retains its smaller limit.
    with pytest.raises(ValueError, match="2MB"):
        restyle_musicxml(data, "source.xml", "P1")


def test_working_xml_retains_expanded_size_limit_before_parsing():
    with pytest.raises(ValueError, match="8MB"):
        restyle_working_xml(b" " * (XML_LIMIT + 1), "P1")


@pytest.mark.parametrize("data", [
    b"", b"not XML", b"<score-timewise/>", b"<score-partwise/>",
    b'<!DOCTYPE score-partwise [<!ENTITY x "bad">]><score-partwise/>',
])
def test_working_xml_rejects_invalid_or_unsafe_roots(data):
    with pytest.raises(ValueError):
        restyle_working_xml(data, "P1")


@pytest.mark.parametrize("case", ["duplicate-part", "duplicate-definition", "missing-definition", "missing-list", "duplicate-list", "empty-id", "long-id", "missing-name"])
def test_working_xml_revalidates_canonical_part_structure(case):
    root = safe_xml(score(measures=1))
    listing = root.find("part-list")
    if case == "duplicate-part":
        root.append(ET.fromstring(ET.tostring(root.find("part"))))
    elif case == "duplicate-definition":
        listing.append(ET.fromstring(ET.tostring(listing.find("score-part"))))
    elif case == "missing-definition":
        listing.remove(listing.find("score-part"))
    elif case == "missing-list":
        root.remove(listing)
    elif case == "duplicate-list":
        root.append(ET.fromstring(ET.tostring(listing)))
    elif case in {"empty-id", "long-id"}:
        root.find("part").set("id", "" if case == "empty-id" else "P" * 81)
    else:
        definition = listing.find("score-part")
        definition.remove(definition.find("part-name"))
    with pytest.raises(ValueError, match="파트"):
        restyle_working_xml(ET.tostring(root), "P1")


def test_working_xml_depth_and_measure_limits_are_enforced():
    root = safe_xml(score(measures=1))
    node = root.find("part/measure")
    for _ in range(130):
        node = ET.SubElement(node, "nested")
    with pytest.raises(ValueError, match="깊어요"):
        restyle_working_xml(ET.tostring(root), "P1")
    for count in [0, 601]:
        with pytest.raises(ValueError, match="600마디"):
            restyle_working_xml(score(measures=count), "P1")


@pytest.mark.parametrize("preset,count,part", [
    ("bad", 4, "P1"), (None, 4, "P1"), ([], 4, "P1"), ({}, 4, "P1"),
    ("practice", True, "P1"), ("practice", 2.0, "P1"), ("practice", 3, "P1"),
    ("practice", 4, None), ("practice", 4, ""), ("practice", 4, []),
])
def test_working_xml_invalid_layout_arguments_raise_validation_error(preset, count, part):
    with pytest.raises(ValueError, match="설정"):
        restyle_working_xml(score(measures=1), part, preset, count)


def test_working_xml_selection_matches_upload_style_and_preserves_resources_policy():
    root = safe_xml(score(parts=2, measures=1))
    ET.SubElement(root.find("part[@id='P2']/measure"), "link", href="file:///private/source")
    data = ET.tostring(root)
    internal, warnings = restyle_working_xml(data, "P2", "standard", 2)
    external, _ = restyle_musicxml(data, "source.xml", "P2", "standard", 2)
    assert internal == external and b"file:///" not in internal
    assert any("외부 이미지" in warning for warning in warnings)
