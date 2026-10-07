"""Narrow, evidence-based compatibility for Audiveris 5.11.0 drum exports.

MusicXML midi-unpitched is 1-based, unlike the GM percussion note number:
https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/midi-unpitched/

Audiveris 5.11.0 exports DrumSound.getMidi() without adding one. Its instrument
ID and instrument-name independently identify that same GM note. Verified in:
https://github.com/Audiveris/audiveris/blob/5.11.0/app/src/main/java/org/audiveris/omr/score/PartwiseBuilder.java
https://github.com/Audiveris/audiveris/blob/5.11.0/app/src/main/java/org/audiveris/omr/score/DrumSet.java

This adapter does NOT infer percussion from staff positions, noteheads, or part
names, and does NOT repair OMR recognition. The caller must keep the original
export separately. Only an unambiguously identified, consistently zero-based
bank from this exact engine version is changed. Already-correct banks are a
no-op. The values below are GM instrument identifiers, not recognition rules.
"""
import xml.etree.ElementTree as ET

from .score_import import UPLOAD_LIMIT, inspect, integer, unpack


SOFTWARE = "Audiveris 5.11.0"
MAX_DEPTH = 128
GM_NAMES = {
    35: "Acoustic_Bass_Drum", 36: "Bass_Drum_1", 37: "Side_Stick",
    38: "Acoustic_Snare", 39: "Hand_Clap", 40: "Electric_Snare",
    41: "Low_Floor_Tom", 42: "Closed_Hi_Hat", 43: "High_Floor_Tom",
    44: "Pedal_Hi_Hat", 45: "Low_Tom", 46: "Open_Hi_Hat",
    47: "Low_Mid_Tom", 48: "Hi_Mid_Tom", 49: "Crash_Cymbal_1",
    50: "High_Tom", 51: "Ride_Cymbal_1", 52: "Chinese_Cymbal",
    53: "Ride_Bell", 54: "Tambourine", 55: "Splash_Cymbal",
    56: "Cowbell", 57: "Crash_Cymbal_2", 58: "Vibraslap",
    59: "Ride_Cymbal_2", 60: "Hi_Bongo", 61: "Low_Bongo",
    62: "Mute_Hi_Conga", 63: "Open_Hi_Conga", 64: "Low_Conga",
    65: "High_Timbale", 66: "Low_Timbale", 67: "High_Agogo",
    68: "Low_Agogo", 69: "Cabasa", 70: "Maracas", 71: "Short_Whistle",
    72: "Long_Whistle", 73: "Short_Guiro", 74: "Long_Guiro",
    75: "Claves", 76: "Hi_Wood_Block", 77: "Low_Wood_Block",
    78: "Mute_Cuica", 79: "Open_Cuica", 80: "Mute_Triangle",
    81: "Open_Triangle",
}
NAME_GM = {name: pitch for pitch, name in GM_NAMES.items()}


def _unsafe(detail):
    return ValueError("Audiveris 드럼 내보내기 정보를 안전하게 확인할 수 없어요: "
                      + detail + ". 추측하여 악기를 바꾸지 않았습니다. 원본 결과를 확인해주세요.")


def _one_text(parent, tag):
    children = parent.findall(tag)
    if (len(children) != 1 or len(children[0]) or not children[0].text
            or not children[0].text.strip()):
        raise _unsafe(f"{tag} 값이 없거나 중복됨")
    return children[0].text.strip()


def _unique_ids(elements, label):
    result = {}
    for element in elements:
        ident = element.get("id")
        if not ident or ident in result:
            raise _unsafe(f"{label} ID가 없거나 중복됨")
        result[ident] = element
    return result


def normalize_export(data: bytes, filename: str) -> tuple[bytes | None, list[str]]:
    """Return corrected plain MusicXML bytes, or None when nothing changes.

    ValueError means invalid/unsafe input, ambiguous or inconsistent drum-bank
    metadata, an unverified Audiveris version, or an oversized normalized file.
    This pure function does not save or overwrite the caller's raw export.
    """
    if not isinstance(data, bytes) or not isinstance(filename, str) or not filename:
        raise ValueError("Audiveris MusicXML/MXL 파일을 확인해주세요.")
    source, root = unpack(data, filename)
    listing = inspect(data, filename)
    pending = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise _unsafe("XML 구조가 너무 깊음")
        pending.extend((child, depth + 1) for child in element)

    software = [node.text.strip() for node in root.findall("identification/encoding/software")
                if node.text and node.text.strip().casefold().startswith("audiveris")]
    if not software:
        return None, []
    has_drums = (root.find(".//midi-unpitched") is not None
                 or root.find("part/measure/note/unpitched") is not None)
    if not has_drums:
        return None, []
    if software != [SOFTWARE]:
        raise _unsafe("검증된 Audiveris 5.11.0 단일 버전 정보와 일치하지 않음")

    lists = root.findall("part-list")
    if len(lists) != 1:
        raise _unsafe("파트 목록이 없거나 중복됨")
    definitions = _unique_ids(lists[0].findall("score-part"), "파트")
    parts = {part.get("id"): part for part in root.findall("part")}
    if set(definitions) != {part["id"] for part in listing["parts"]}:
        raise _unsafe("파트 목록과 실제 파트가 일치하지 않음")

    modes, changes, global_instrument_ids, checked_values = set(), {}, set(), set()
    for part_id, definition in definitions.items():
        instruments = _unique_ids(definition.findall("score-instrument"), "score-instrument")
        midi_instruments = _unique_ids(definition.findall("midi-instrument"), "midi-instrument")
        if global_instrument_ids.intersection(instruments):
            raise _unsafe("서로 다른 파트에 같은 score-instrument ID가 있음")
        global_instrument_ids.update(instruments)
        part = parts[part_id]
        unpitched_notes = part.findall("measure/note[unpitched]")
        bank = {ident: node for ident, node in midi_instruments.items()
                if node.find("midi-unpitched") is not None}
        if not bank and not unpitched_notes:
            continue
        # Audiveris declares one score-instrument and one MIDI entry for every
        # bank member. Incomplete/mixed banks must not be partially corrected.
        if not bank or set(bank) != set(instruments) or set(bank) != set(midi_instruments):
            raise _unsafe("드럼 악기 정의와 MIDI 은행의 ID가 일치하지 않음")
        for ident, midi in bank.items():
            name = _one_text(instruments[ident], "instrument-name")
            gm = NAME_GM.get(name)
            if gm is None or ident != f"{part_id}-I{gm}":
                raise _unsafe("드럼 이름과 명시적 GM 악기 ID가 일치하지 않음")
            if integer(_one_text(midi, "midi-channel")) != 10:
                raise _unsafe("드럼 MIDI 채널이 10이 아님")
            value = integer(_one_text(midi, "midi-unpitched"))
            if value not in {gm, gm + 1}:
                raise _unsafe("드럼 이름·ID와 midi-unpitched 값이 일치하지 않음")
            checked_values.add(midi.find("midi-unpitched"))
            modes.add("zero" if value == gm else "one")
            changes[(part_id, ident)] = str(gm + 1)
        # Missing instrument references are an OMR failure, not evidence from
        # which it is safe to guess a hi-hat/snare/kick.
        for note in unpitched_notes:
            references = note.findall("instrument")
            if (not references or any(ref.get("id") not in bank for ref in references)
                    or len({ref.get("id") for ref in references}) != len(references)):
                raise _unsafe("타악기 음표의 악기 참조가 없거나 유효하지 않음")
    # Per-measure sound/MIDI overrides are valid MusicXML but not part of this
    # verified export convention. Leaving them zero-based after changing the
    # initial bank would change playback halfway through the score.
    if set(root.findall(".//midi-unpitched")) != checked_values:
        raise _unsafe("검증한 초기 드럼 은행 밖에 MIDI 타악기 번호가 있음")
    if len(modes) != 1:
        raise _unsafe("0 기반과 1 기반 MIDI 값이 섞여 있거나 은행을 확인할 수 없음")
    if modes == {"one"}:
        return None, []

    # safe_xml() deliberately simplifies namespace tags for inspection. Parse
    # the validated source again so serialization retains original namespaces,
    # comments, metadata, voices, ties, beams and every other musical element.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    output_root = ET.fromstring(source, parser=parser)
    for definition in output_root.findall("{*}part-list/{*}score-part"):
        for midi in definition.findall("{*}midi-instrument"):
            replacement = changes.get((definition.get("id"), midi.get("id")))
            if replacement is not None:
                midi.find("{*}midi-unpitched").text = replacement
    output = ET.tostring(output_root, encoding="utf-8", xml_declaration=True)
    if len(output) > UPLOAD_LIMIT:
        raise ValueError("호환성을 정리한 MusicXML이 2MB를 초과해요. 파트를 나누어주세요.")
    warnings = [f"Audiveris 5.11.0의 드럼 MIDI 내보내기 번호 {len(changes)}개를 "
                "MusicXML 표준의 1 기반 번호로 정리했습니다. 음표·악기 인식을 고친 것이 아니라 "
                "검증된 내보내기 호환성 수정입니다. 원본 인식 결과는 별도로 보관됩니다."]
    return output, warnings
