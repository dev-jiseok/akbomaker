import io
import zipfile
import xml.etree.ElementTree as ET

import pytest

from backend.audiveris_compat import GM_NAMES, normalize_export


def exported(*, pitches=(36, 38, 42, 44, 46, 49), offset=0,
             software="Audiveris 5.11.0", namespace=False):
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(root, "work"), "work-title").text = "Title & rights"
    identification = ET.SubElement(root, "identification")
    ET.SubElement(identification, "creator", type="composer").text = "Original composer"
    ET.SubElement(identification, "rights").text = "Original rights"
    encoding = ET.SubElement(identification, "encoding")
    ET.SubElement(encoding, "software").text = software
    ET.SubElement(encoding, "software").text = "ProxyMusic 4.0.3"
    definition = ET.SubElement(ET.SubElement(root, "part-list"), "score-part", id="P1")
    # Actual Audiveris output can say Voice even though it has a drum bank.
    ET.SubElement(definition, "part-name").text = "Voice"
    for pitch in pitches:
        ident = f"P1-I{pitch}"
        instrument = ET.SubElement(definition, "score-instrument", id=ident)
        ET.SubElement(instrument, "instrument-name").text = GM_NAMES[pitch]
        midi = ET.SubElement(definition, "midi-instrument", id=ident)
        for name, value in (("midi-channel", 10), ("midi-program", 1),
                            ("midi-unpitched", pitch + offset), ("volume", 78)):
            ET.SubElement(midi, name).text = str(value)
    part = ET.SubElement(root, "part", id="P1")
    measure = ET.SubElement(part, "measure", number="1", width="320")
    attributes = ET.SubElement(measure, "attributes")
    ET.SubElement(attributes, "divisions").text = "12"
    time = ET.SubElement(attributes, "time")
    ET.SubElement(time, "beats").text = "4"
    ET.SubElement(time, "beat-type").text = "4"
    ET.SubElement(ET.SubElement(attributes, "clef"), "sign").text = "percussion"
    note = ET.SubElement(measure, "note", {"default-x": "100"})
    unpitched = ET.SubElement(note, "unpitched")
    ET.SubElement(unpitched, "display-step").text = "G"
    ET.SubElement(unpitched, "display-octave").text = "5"
    ET.SubElement(note, "duration").text = "4"
    ET.SubElement(note, "tie", type="start")
    ET.SubElement(note, "instrument", id=f"P1-I{pitches[0]}")
    ET.SubElement(note, "voice").text = "2"
    ET.SubElement(note, "type").text = "eighth"
    ET.SubElement(note, "beam", number="1").text = "begin"
    modification = ET.SubElement(note, "time-modification")
    ET.SubElement(modification, "actual-notes").text = "3"
    ET.SubElement(modification, "normal-notes").text = "2"
    notation = ET.SubElement(note, "notations")
    ET.SubElement(notation, "tied", type="start")
    ET.SubElement(ET.SubElement(notation, "articulations"), "accent")
    ET.SubElement(ET.SubElement(notation, "technical"), "open-string")
    lyric = ET.SubElement(note, "lyric", number="1")
    ET.SubElement(lyric, "syllabic").text = "begin"
    ET.SubElement(lyric, "text").text = "가사"
    barline = ET.SubElement(measure, "barline", location="right")
    ET.SubElement(barline, "repeat", direction="backward")
    if namespace:
        for node in root.iter():
            node.tag = "{http://www.musicxml.org/ns/musicxml}" + node.tag
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def packed(data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr("score.xml", data)
    return buffer.getvalue()


def fingerprint(data):
    root = ET.fromstring(data)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "midi-unpitched":
            node.text = "EXCLUDED_NUMBER"
    return ET.tostring(root)


@pytest.mark.parametrize("mxl,namespace", [(False, False), (True, False), (False, True)])
def test_exact_audiveris_bank_corrected_without_musical_changes(mxl, namespace):
    data = exported(pitches=tuple(GM_NAMES), namespace=namespace)
    output, warnings = normalize_export(packed(data) if mxl else data, "score.mxl" if mxl else "score.xml")
    assert output is not None and len(output) < 2 * 1024 * 1024
    assert fingerprint(output) == fingerprint(data)
    root = ET.fromstring(output)
    for midi in root.findall("{*}part-list/{*}score-part/{*}midi-instrument"):
        gm = int(midi.get("id").split("-I")[1])
        assert int(midi.findtext("{*}midi-unpitched")) == gm + 1
    assert "47개" in warnings[0] and "인식을 고친 것이 아니라" in warnings[0]
    assert normalize_export(output, "score.musicxml") == (None, [])


def test_minimal_explicit_bank_does_not_guess_from_display_position():
    data = exported(pitches=(46,))
    root = ET.fromstring(data)
    root.find(".//display-step").text = "F"
    root.find(".//display-octave").text = "4"
    output, _ = normalize_export(ET.tostring(root), "score.xml")
    assert ET.fromstring(output).findtext(".//midi-unpitched") == "47"
    assert ET.fromstring(output).findtext(".//display-step") == "F"


def test_already_standard_bank_is_a_noop():
    assert normalize_export(exported(offset=1), "score.xml") == (None, [])


def test_other_software_is_not_reinterpreted():
    assert normalize_export(exported(software="Other exporter 5.11.0"), "score.xml") == (None, [])


@pytest.mark.parametrize("software", ["Audiveris 5.10.0", "Audiveris 5.11.1", "Audiveris 5.11.0-dev", "Audiveris"])
def test_unverified_audiveris_version_with_drums_is_rejected(software):
    with pytest.raises(ValueError, match="단일 버전"):
        normalize_export(exported(software=software), "score.xml")


def test_pitched_only_export_needs_no_adapter():
    root = ET.fromstring(exported(software="Audiveris 5.12.0"))
    for midi in root.findall("part-list/score-part/midi-instrument"):
        midi.remove(midi.find("midi-unpitched"))
    note = root.find("part/measure/note")
    note.remove(note.find("unpitched"))
    assert normalize_export(ET.tostring(root), "score.xml") == (None, [])


@pytest.mark.parametrize("case", [
    "mixed", "unknown_name", "space_name", "wrong_id", "wrong_value", "wrong_channel",
    "duplicate_score_id", "duplicate_midi_id", "duplicate_midi_value", "duplicate_name",
    "missing_score_id", "missing_midi_id", "missing_note_ref", "unknown_note_ref", "duplicate_note_ref",
    "duplicate_version", "duplicate_part_definition", "missing_part_definition",
    "nested_midi_value", "runtime_midi_override",
])
def test_ambiguous_or_inconsistent_banks_fail_closed(case):
    root = ET.fromstring(exported())
    definition = root.find("part-list/score-part")
    instrument = definition.find("score-instrument")
    midi = definition.find("midi-instrument")
    note = root.find("part/measure/note")
    if case == "mixed":
        midi.find("midi-unpitched").text = "37"
    elif case in {"unknown_name", "space_name"}:
        instrument.find("instrument-name").text = "Custom drum" if case == "unknown_name" else "Bass Drum 1"
    elif case == "wrong_id":
        instrument.set("id", "P1-I99")
        midi.set("id", "P1-I99")
    elif case == "wrong_value":
        midi.find("midi-unpitched").text = "42"
    elif case == "wrong_channel":
        midi.find("midi-channel").text = "1"
    elif case == "duplicate_score_id":
        definition.append(ET.fromstring(ET.tostring(instrument)))
    elif case == "duplicate_midi_id":
        definition.append(ET.fromstring(ET.tostring(midi)))
    elif case == "duplicate_midi_value":
        ET.SubElement(midi, "midi-unpitched").text = "36"
    elif case == "duplicate_name":
        ET.SubElement(instrument, "instrument-name").text = "Bass_Drum_1"
    elif case == "missing_score_id":
        definition.remove(instrument)
    elif case == "missing_midi_id":
        definition.remove(midi)
    elif case == "missing_note_ref":
        note.remove(note.find("instrument"))
    elif case == "unknown_note_ref":
        note.find("instrument").set("id", "P1-I99")
    elif case == "duplicate_note_ref":
        ET.SubElement(note, "instrument", id=note.find("instrument").get("id"))
    elif case == "duplicate_version":
        ET.SubElement(root.find("identification/encoding"), "software").text = "Audiveris 5.11.0"
    elif case == "duplicate_part_definition":
        root.find("part-list").append(ET.fromstring(ET.tostring(definition)))
    elif case == "nested_midi_value":
        ET.SubElement(midi.find("midi-unpitched"), "extra").text = "42"
    elif case == "runtime_midi_override":
        sound = ET.SubElement(root.find("part/measure"), "sound")
        override = ET.SubElement(sound, "midi-instrument", id="P1-I36")
        ET.SubElement(override, "midi-unpitched").text = "36"
    else:
        root.find("part-list").remove(definition)
    with pytest.raises(ValueError):
        normalize_export(ET.tostring(root), "score.xml")


def test_mixed_banks_across_parts_are_rejected():
    first = ET.fromstring(exported())
    second = ET.fromstring(exported(offset=1))
    for node in second.iter():
        if node.get("id"):
            node.set("id", node.get("id").replace("P1", "P2"))
    first.find("part-list").append(second.find("part-list/score-part"))
    first.append(second.find("part"))
    with pytest.raises(ValueError, match="섞여"):
        normalize_export(ET.tostring(first), "score.xml")


def test_pitched_part_in_mixed_score_is_not_changed():
    root = ET.fromstring(exported())
    definition = ET.SubElement(root.find("part-list"), "score-part", id="P2")
    ET.SubElement(definition, "part-name").text = "Piano"
    instrument = ET.SubElement(definition, "score-instrument", id="P2-I1")
    ET.SubElement(instrument, "instrument-name").text = "Piano"
    midi = ET.SubElement(definition, "midi-instrument", id="P2-I1")
    ET.SubElement(midi, "midi-program").text = "1"
    note = ET.SubElement(ET.SubElement(ET.SubElement(root, "part", id="P2"), "measure", number="1"), "note")
    pitch = ET.SubElement(note, "pitch")
    ET.SubElement(pitch, "step").text = "C"
    ET.SubElement(pitch, "octave").text = "4"
    source = ET.tostring(root)
    output, _ = normalize_export(source, "score.xml")
    assert fingerprint(output) == fingerprint(source)


def test_comments_and_processing_instructions_remain():
    data = exported().replace(b"<part-list>", b"<!-- source comment --><?source audit?><part-list>")
    output, _ = normalize_export(data, "score.xml")
    assert b"<!-- source comment -->" in output and b"<?source audit?>" in output


@pytest.mark.parametrize("data,filename", [
    (b"not xml", "score.xml"), (b"", "score.xml"), (b"%PDF-1.4", "score.pdf"),
    (b'<!DOCTYPE score-partwise [<!ENTITY x "test">]><score-partwise>&x;</score-partwise>', "score.xml"),
    (b"<score-timewise/>", "score.xml"),
])
def test_unsafe_inputs_are_rejected(data, filename):
    with pytest.raises(ValueError):
        normalize_export(data, filename)


def test_deep_xml_is_rejected_before_serialization():
    root = ET.fromstring(exported())
    element = root
    for _ in range(130):
        element = ET.SubElement(element, "extra")
    with pytest.raises(ValueError, match="깊음"):
        normalize_export(ET.tostring(root), "score.xml")


def test_compressed_result_cannot_exceed_plain_xml_upload_limit():
    root = ET.fromstring(exported())
    ET.SubElement(root, "movement-title").text = "a" * (2 * 1024 * 1024)
    with pytest.raises(ValueError, match="2MB"):
        normalize_export(packed(ET.tostring(root)), "score.mxl")
