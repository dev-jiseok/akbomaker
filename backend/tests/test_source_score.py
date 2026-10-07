"""Original-score editing never round-trips through the quantized audio model."""
import copy
import io
import json
import zipfile
import xml.etree.ElementTree as ET

import pytest

from backend.source_score import apply_edit, describe, prepare


def add(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if text is not None:
        node.text = str(text)
    return node


def note(parent, *, step="C", octave=4, staff="1", string=None, fret=None, drum=None, open_hat=False):
    item = add(parent, "note", **{"default-x": "150"})
    if drum:
        pitch = add(item, "unpitched")
        add(pitch, "display-step", "G" if drum.startswith("hat") else "F")
        add(pitch, "display-octave", 5 if drum.startswith("hat") else 4)
    else:
        pitch = add(item, "pitch")
        add(pitch, "step", step)
        add(pitch, "octave", octave)
    add(item, "duration", 4)
    if drum:
        add(item, "instrument", id=drum)
    add(item, "voice", "2" if staff == "2" else "1")
    add(item, "type", "eighth")
    timing = add(item, "time-modification")
    add(timing, "actual-notes", 3)
    add(timing, "normal-notes", 2)
    add(item, "stem", "down")
    if drum:
        add(item, "notehead", "x" if drum.startswith("hat") else "normal")
    add(item, "staff", staff)
    add(item, "beam", "begin", number="1")
    notation = add(item, "notations")
    add(notation, "tuplet", type="start", number="1")
    add(add(notation, "articulations"), "accent")
    if string is not None:
        tech = add(notation, "technical")
        add(tech, "string", string)
        add(tech, "fret", fret)
    if open_hat:
        add(add(notation, "technical"), "open-string")
    lyric = add(item, "lyric", number="2", **{"default-y": "-70"})
    add(lyric, "syllabic", "begin")
    add(lyric, "text", "원본")
    add(lyric, "extend", type="start")
    return item


def fixture(inst="vocal", *, grand=False):
    root = ET.Element("score-partwise", version="4.0")
    add(add(root, "work"), "work-title", "원본 그대로")
    identity = add(root, "identification")
    add(identity, "creator", "원래 작곡자", type="composer")
    add(identity, "rights", "© 원작자")
    defaults = add(root, "defaults")
    add(add(defaults, "scaling"), "millimeters", "7")
    add(add(root, "credit", page="1"), "credit-words", "출처 유지", **{"default-x": "20", "default-y": "800"})
    listing = add(root, "part-list")
    add(listing, "part-group", number="1", type="start")
    for ident, name in [("P1", inst), ("P2", "Other")]:
        definition = add(listing, "score-part", id=ident)
        add(definition, "part-name", name)
        if ident == "P1" and inst == "drums":
            for drum, gm, label in [("hat", 42, "Closed hi-hat"), ("hat-open", 46, "Open hi-hat"), ("kick", 36, "Kick")]:
                add(add(definition, "score-instrument", id=drum), "instrument-name", label)
                midi = add(definition, "midi-instrument", id=drum)
                add(midi, "midi-channel", 10)
                add(midi, "midi-unpitched", gm + 1)
    add(listing, "part-group", number="1", type="stop")
    part = add(root, "part", id="P1")
    measure = add(part, "measure", number="0", implicit="yes", width="200")
    add(measure, "print", **{"new-system": "yes"})
    attrs = add(measure, "attributes")
    add(attrs, "divisions", 12)
    add(add(attrs, "key"), "fifths", -2)
    time = add(attrs, "time")
    add(time, "beats", 4)
    add(time, "beat-type", 4)
    if grand:
        add(attrs, "staves", 2)
        add(add(attrs, "clef", number="1"), "sign", "G")
        add(add(attrs, "clef", number="2"), "sign", "F")
    elif inst in {"guitar", "bass"}:
        add(add(attrs, "clef"), "sign", "TAB")
        tuning = [64, 59, 55, 50, 45, 40] if inst == "guitar" else [43, 38, 33, 28]
        details = add(attrs, "staff-details")
        add(details, "staff-lines", len(tuning))
        names = {0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B"}
        for index, pitch in enumerate(reversed(tuning), 1):
            line = add(details, "staff-tuning", line=str(index))
            add(line, "tuning-step", names[pitch % 12])
            add(line, "tuning-octave", pitch // 12 - 1)
        add(details, "capo", 0)
    else:
        add(add(attrs, "clef"), "sign", "percussion" if inst == "drums" else "G")
    direction = add(measure, "direction")
    add(add(direction, "direction-type"), "pedal", type="start")
    add(direction, "sound", tempo="93.5", dynamics="23")
    if inst == "drums":
        note(measure, drum="hat")
        note(measure, drum="hat-open", open_hat=True)
        note(measure, drum="kick")
    elif inst in {"guitar", "bass"}:
        note(measure, step="E" if inst == "guitar" else "G", octave=4 if inst == "guitar" else 2, string=1, fret=0)
        note(measure, step="A", octave=4 if inst == "guitar" else 2, string=1, fret=5 if inst == "guitar" else 2)
    else:
        note(measure)
        if grand:
            add(add(measure, "backup"), "duration", 4)
            note(measure, staff="2")
        else:
            note(measure, step="E")
    ending = add(measure, "barline", location="right")
    add(ending, "ending", type="stop", number="1")
    add(ending, "repeat", direction="backward")
    second = add(part, "measure", number="0")
    note(second, step="B")
    other = add(add(root, "part", id="P2"), "measure", number="X")
    add(add(other, "attributes"), "divisions", 1)
    note(other, step="F", octave=3)
    return root


def encode(root):
    return ET.tostring(root, encoding="utf-8")


def ready(root, inst="vocal"):
    return prepare(encode(root), "source.musicxml", "P1", inst)["xml"]


def patch(operation="pitch", **values):
    return {"note_id": "m0n0", "operation": operation, **values}


@pytest.mark.parametrize("inst", ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"])
def test_prepare_keeps_all_parts_and_musical_structure_for_every_instrument(inst):
    source = fixture(inst, grand=inst in {"piano", "synthesizer"})
    result = prepare(encode(source), "original.xml", "P1", inst)
    assert encode(ET.fromstring(result["xml"])) == encode(source)
    assert result["title"] == "원본 그대로"
    assert result["part_id"] == "P1" and result["instrument"] == inst
    description = describe(result["xml"], "P1", inst)
    assert len(description["notes"]) == len(source.find("part").findall("measure/note"))
    assert description["notes"][0]["onset"] == "0"
    assert description["notes"][0]["duration"] == "1/3"
    assert description["notes"][0]["measure_number"] == "0"
    assert description["notes"][-1]["id"] == "m1n0"


@pytest.mark.parametrize("inst", ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"])
def test_lyric_edit_retains_all_other_nodes_syllabic_extend_and_multiple_parts(inst):
    xml = ready(fixture(inst, grand=inst in {"piano", "synthesizer"}), inst)
    edited = ET.fromstring(apply_edit(xml, "P1", inst, patch("lyric", lyric_index=0, text="수정 가사")))
    first = edited.find("part/measure/note/lyric")
    assert first.findtext("text") == "수정 가사"
    assert first.get("number") == "2" and first.findtext("syllabic") == "begin"
    assert first.find("extend").get("type") == "start"
    first.find("text").text = "원본"
    assert encode(edited) == encode(ET.fromstring(xml))


@pytest.mark.parametrize("inst", ["vocal", "piano", "synthesizer"])
def test_pitch_edit_preserves_rhythm_tuplet_beam_voice_staff_and_other_parts(inst):
    source = fixture(inst, grand=inst != "vocal")
    xml = ready(source, inst)
    updated = ET.fromstring(apply_edit(xml, "P1", inst, patch(step="D", alter=1, octave=5)))
    first = updated.find("part/measure/note")
    assert first.findtext("pitch/step") == "D" and first.findtext("pitch/alter") == "1"
    assert first.findtext("pitch/octave") == "5" and first.findtext("accidental") == "sharp"
    original = source.find("part/measure/note")
    first.remove(first.find("pitch"))
    first.insert(0, copy.deepcopy(original.find("pitch")))
    first.remove(first.find("accidental"))
    assert encode(updated) == encode(source)


def test_piano_unisons_are_not_merged_and_second_hand_edit_keeps_first_hand():
    xml = ready(fixture("piano", grand=True), "piano")
    rows = describe(xml, "P1", "piano")["notes"]
    assert [r["staff"] for r in rows[:2]] == ["1", "2"]
    assert [r["onset"] for r in rows[:2]] == ["0", "0"]
    result = ET.fromstring(apply_edit(xml, "P1", "piano", {**patch(step="F", alter=0, octave=3), "note_id": "m0n1"}))
    first, second = result.find("part/measure").findall("note")
    assert first.findtext("pitch/step") == "C" and second.findtext("pitch/step") == "F"
    assert first.findtext("staff") == "1" and second.findtext("staff") == "2"


@pytest.mark.parametrize("inst,new_fret", [("guitar", 5), ("bass", 5)])
def test_fingering_move_preserves_pitch_and_all_other_elements(inst, new_fret):
    xml = ready(fixture(inst), inst)
    assert describe(xml, "P1", inst)["notes"][0]["editable"]["fingering"]
    updated = ET.fromstring(apply_edit(xml, "P1", inst, patch("fingering", string=2, fret=new_fret)))
    technical = updated.find("part/measure/note/notations/technical")
    assert technical.findtext("string") == "2" and technical.findtext("fret") == "5"
    technical.find("string").text, technical.find("fret").text = "1", "0"
    assert encode(updated) == encode(ET.fromstring(xml))
    with pytest.raises(ValueError, match="원래 음"):
        apply_edit(xml, "P1", inst, patch("fingering", string=2, fret=6))


def test_capo_and_transpose_are_applied_to_fingering_consistency():
    root = fixture("guitar")
    attrs = root.find("part/measure/attributes")
    attrs.find("staff-details/capo").text = "2"
    trans = add(attrs, "transpose")
    add(trans, "chromatic", 0)
    add(trans, "octave-change", -1)
    pitch = root.find("part/measure/note/pitch")
    pitch.find("step").text, pitch.find("octave").text = "F", "5"
    pitch.insert(1, ET.Element("alter"))
    pitch.find("alter").text = "1"
    xml = ready(root, "guitar")
    row = describe(xml, "P1", "guitar")["notes"][0]
    assert row["editable"]["fingering"]
    result = ET.fromstring(apply_edit(xml, "P1", "guitar", patch("fingering", string=2, fret=5)))
    assert result.findtext("part/measure/note/pitch/step") == "F"
    assert result.findtext("part/measure/note/notations/technical/fret") == "5"


def test_drum_patch_copies_explicit_source_style_and_open_mark_not_rhythm():
    root = fixture("drums")
    xml = ready(root, "drums")
    listing = describe(xml, "P1", "drums")
    assert {item["id"] for item in listing["drum_options"]} == {"hat", "hat-open", "kick"}
    assert listing["notes"][0]["editable"]["drum"]
    out = ET.fromstring(apply_edit(xml, "P1", "drums", patch("drum", drum_id="hat-open")))
    first = out.find("part/measure/note")
    assert first.find("instrument").get("id") == "hat-open"
    assert first.find("notations/technical/open-string") is not None
    assert first.findtext("duration") == "4" and first.findtext("voice") == "1"
    assert first.findtext("beam") == "begin" and first.find("notations/articulations/accent") is not None
    first.find("instrument").set("id", "hat")
    first.find("notations").remove(first.find("notations/technical"))
    assert encode(out) == encode(root)


def test_drum_closed_patch_removes_only_open_technique_preserves_accent():
    xml = ready(fixture("drums"), "drums")
    out = ET.fromstring(apply_edit(xml, "P1", "drums", {**patch("drum", drum_id="hat"), "note_id": "m0n1"}))
    second = out.find("part/measure").findall("note")[1]
    assert second.find("notations/technical") is None
    assert second.find("notations/articulations/accent") is not None


def test_unknown_or_ambiguous_drum_mapping_never_guessed():
    root = fixture("drums")
    extra = copy.deepcopy(root.find("part/measure/note"))
    extra.find("unpitched/display-step").text = "A"
    root.find("part/measure").append(extra)
    xml = ready(root, "drums")
    desc = describe(xml, "P1", "drums")
    assert "hat" not in {n["id"] for n in desc["drum_options"]}
    assert not desc["notes"][0]["editable"]["drum"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "drums", patch("drum", drum_id="not-defined"))


@pytest.mark.parametrize("case", ["sound-override", "instrument-change", "duplicate-bank", "duplicate-global-id", "no-midi"])
def test_ambiguous_or_dynamic_drum_banks_are_read_only(case):
    root = fixture("drums")
    definition = root.find("part-list/score-part")
    if case == "sound-override":
        override = add(root.find("part/measure/direction/sound"), "midi-instrument", id="hat")
        add(override, "midi-unpitched", 37)
    elif case == "instrument-change":
        add(root.find("part/measure/direction/sound"), "instrument-change", id="hat")
    elif case == "duplicate-bank":
        definition.append(copy.deepcopy(definition.find("midi-instrument")))
    elif case == "duplicate-global-id":
        add(root.findall("part-list/score-part")[1], "score-instrument", id="hat")
    else:
        for item in list(definition.findall("midi-instrument")):
            definition.remove(item)
    xml = ready(root, "drums")
    desc = describe(xml, "P1", "drums")
    assert not desc["drum_options"]
    assert not desc["notes"][0]["editable"]["drum"]
    assert desc["notes"][0]["lyrics"][0]["editable"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "drums", patch("drum", drum_id="kick"))


@pytest.mark.parametrize("case", ["tie", "slur", "bend", "grace", "cue", "microtone", "special-head", "malformed-duration"])
def test_pitch_unsupported_connections_lock_only_pitch_lyric_remains_editable(case):
    root = fixture()
    target = root.find("part/measure/note")
    if case == "tie":
        add(target, "tie", type="start")
    elif case == "slur":
        add(target.find("notations"), "slur", type="start")
    elif case == "bend":
        add(add(target.find("notations"), "technical"), "bend")
    elif case in {"grace", "cue"}:
        add(target, case)
    elif case == "microtone":
        add(target.find("pitch"), "alter", "0.5")
    elif case == "special-head":
        add(target, "notehead", "x")
    else:
        target.find("duration").text = "not-a-number"
    xml = ready(root)
    row = describe(xml, "P1", "vocal")["notes"][0]
    assert not row["editable"]["pitch"] and row["reasons"]["pitch"]
    assert row["lyrics"][0]["editable"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "vocal", patch(step="D", alter=0, octave=4))
    assert "새 가사" in apply_edit(xml, "P1", "vocal", patch("lyric", lyric_index=0, text="새 가사"))


@pytest.mark.parametrize("case", ["missing-tuning", "multiple-staves", "mismatched-pitch", "tie", "muted"])
def test_unsafe_fingering_locks_without_losing_notation(case):
    root = fixture("guitar")
    target, attrs = root.find("part/measure/note"), root.find("part/measure/attributes")
    if case == "missing-tuning":
        attrs.remove(attrs.find("staff-details"))
    elif case == "multiple-staves":
        add(attrs, "staves", 2)
    elif case == "mismatched-pitch":
        target.find("pitch/step").text = "D"
    elif case == "muted":
        add(target, "notehead", "x")
    else:
        add(target, "tie", type="start")
    xml = ready(root, "guitar")
    assert not describe(xml, "P1", "guitar")["notes"][0]["editable"]["fingering"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "guitar", patch("fingering", string=2, fret=5))
    assert encode(ET.fromstring(xml)) == encode(root)


def test_simultaneously_occupied_tab_string_is_not_overwritten():
    root = fixture("guitar")
    first, second = root.find("part/measure").findall("note")
    second.insert(0, ET.Element("chord"))
    second.find("notations/technical/string").text = "2"
    xml = ready(root, "guitar")
    with pytest.raises(ValueError, match="동시에"):
        apply_edit(xml, "P1", "guitar", patch("fingering", string=2, fret=5))


def test_simultaneous_note_with_missing_fingering_prevents_string_conflict_guess():
    root = fixture("guitar")
    second = root.find("part/measure").findall("note")[1]
    second.insert(0, ET.Element("chord"))
    second.find("notations").remove(second.find("notations/technical"))
    xml = ready(root, "guitar")
    with pytest.raises(ValueError, match="다른 음의 줄"):
        apply_edit(xml, "P1", "guitar", patch("fingering", string=2, fret=5))


def test_accidental_notation_matches_pitch_and_protects_following_implicit_notes():
    root = fixture()
    first, second = root.find("part/measure").findall("note")
    second.find("pitch/step").text = "C"
    xml = ready(root)
    with pytest.raises(ValueError, match="임시표"):
        apply_edit(xml, "P1", "vocal", patch(step="C", alter=1, octave=4))
    add(second, "accidental", "natural")
    out = ET.fromstring(apply_edit(ready(root), "P1", "vocal", patch(step="C", alter=1, octave=4)))
    assert out.findtext("part/measure/note/accidental") == "sharp"
    assert out.findtext("part/measure/note/pitch/alter") == "1"


@pytest.mark.parametrize("value", ["", " ", "\x00", "x" * 501, True, None])
def test_invalid_lyric_patch_fails_closed(value):
    xml = ready(fixture())
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "vocal", patch("lyric", lyric_index=0, text=value))


def test_multiple_lyric_text_nodes_and_absent_lyrics_are_not_replaced():
    root = fixture()
    add(root.find("part/measure/note/lyric"), "text", "두 번째")
    xml = ready(root)
    assert not describe(xml, "P1", "vocal")["notes"][0]["lyrics"][0]["editable"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "vocal", patch("lyric", lyric_index=0, text="교체"))


@pytest.mark.parametrize("change", [
    {"note_id": "m99n0"}, {"note_id": "../source"}, {"operation": "duration"},
    {"alter": True}, {"octave": 10}, {"step": "H"}, {"unexpected": "value"},
])
def test_invalid_patch_fields_locators_and_types_are_rejected(change):
    xml = ready(fixture())
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "vocal", {**patch(step="D", alter=0, octave=4), **change})


def test_external_resources_removed_without_removing_text_credits():
    root = fixture()
    add(root, "script", "unsafe")
    add(root.find("credit"), "credit-image", source="https://example.invalid/image")
    root.find("part/measure/note").set("onclick", "unsafe")
    result = prepare(encode(root), "score.xml", "P1", "vocal")
    assert "script" not in result["xml"] and "onclick" not in result["xml"] and "https://" not in result["xml"]
    assert "출처 유지" in result["xml"]
    assert any("외부" in warning for warning in result["warnings"])
    with pytest.raises(ValueError, match="안전하게"):
        describe(encode(root).decode(), "P1", "vocal")


def test_mxl_and_namespaced_source_are_supported():
    root = fixture()
    root.set("xmlns", "http://www.musicxml.org/ns/musicxml")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        zipped.writestr("score.xml", encode(root))
    result = prepare(archive.getvalue(), "score.mxl", "P1", "vocal")
    assert len(describe(result["xml"], "P1", "vocal")["notes"]) == 3


@pytest.mark.parametrize("case", ["unknown-part", "unknown-instrument", "duplicate-part", "dtd", "depth"])
def test_unsafe_or_ambiguous_source_rejected(case):
    root = fixture()
    part, inst = "P1", "vocal"
    if case == "unknown-part":
        part = "missing"
    elif case == "unknown-instrument":
        inst = "flute"
    elif case == "duplicate-part":
        root.append(copy.deepcopy(root.find("part")))
    elif case == "depth":
        node = root.find("part")
        for _ in range(130):
            node = add(node, "nested")
    data = encode(root)
    if case == "dtd":
        data = b'<!DOCTYPE foo [<!ENTITY x "bad">]>' + data
    with pytest.raises(ValueError):
        prepare(data, "score.xml", part, inst)


def straight(inst="vocal"):
    root = fixture(inst)
    measure = root.find("part/measure")
    for item in measure.findall("note"):
        item.find("duration").text = "12"
        item.find("type").text = "quarter"
        for tag in ("time-modification", "beam"):
            for child in list(item.findall(tag)):
                item.remove(child)
        for notation in item.findall("notations"):
            for child in list(notation.findall("tuplet")):
                notation.remove(child)
    rest = ET.Element("note")
    add(rest, "rest")
    add(rest, "duration", 12 if inst == "drums" else 24)
    add(rest, "voice", "1")
    add(rest, "type", "quarter" if inst == "drums" else "half")
    add(rest, "staff", "1")
    measure.insert(list(measure).index(measure.find("barline")), rest)
    return root


def combined_tab(inst="guitar"):
    root = straight(inst)
    measure = root.find("part/measure")
    attrs = measure.find("attributes")
    attrs.find("clef").set("number", "1")
    attrs.find("clef/sign").text = "G" if inst == "guitar" else "F"
    add(add(attrs, "clef", number="2"), "sign", "TAB")
    add(attrs, "staves", 2)
    attrs.find("staff-details").set("number", "2")
    transpose = add(attrs, "transpose", number="1")
    add(transpose, "chromatic", 0)
    add(transpose, "octave-change", -1)
    standard = list(measure.findall("note"))
    tabs = [copy.deepcopy(n) for n in standard]
    for n in standard:
        pitch = n.find("pitch")
        if pitch is not None:
            pitch.find("octave").text = str(int(pitch.findtext("octave")) + 1)
        for notation in n.findall("notations"):
            for tech in list(notation.findall("technical")):
                notation.remove(tech)
    index = list(measure).index(measure.find("barline"))
    backup = ET.Element("backup")
    add(backup, "duration", 48)
    measure.insert(index, backup)
    for offset, n in enumerate(tabs, 1):
        n.find("staff").text = "2"
        measure.insert(index + offset, n)
    return root


def row_for(xml, inst, **match):
    return next(r for r in describe(xml, "P1", inst)["notes"] if all(r.get(k) == v for k, v in match.items()))


@pytest.mark.parametrize("inst", ["guitar", "bass"])
def test_tab_pitch_atomic_standard_counterpart_transposition_and_other_parts(inst):
    source = combined_tab(inst)
    xml = ready(source, inst)
    row = row_for(xml, inst, staff="2", note_index=4)
    assert row["editable"]["tab_pitch"]
    output = apply_edit(xml, "P1", inst, {"note_id": row["id"], "operation": "tab_pitch", "string": 2, "fret": 7})
    result = ET.fromstring(output)
    notes = result.find("part/measure").findall("note")
    assert notes[0].findtext("pitch/step") == ("F" if inst == "guitar" else "A")
    assert int(notes[0].findtext("pitch/octave")) == int(notes[3].findtext("pitch/octave")) + 1
    assert notes[3].findtext("notations/technical/string") == "2"
    assert notes[3].findtext("notations/technical/fret") == "7"
    assert result.findtext("part/measure/backup/duration") == "48"
    assert encode(result.findall("part")[1]) == encode(source.findall("part")[1])
    assert [n.findtext("duration") for n in notes] == [n.findtext("duration") for n in source.find("part/measure").findall("note")]


def test_tab_pitch_capo_and_neighbor_accidental_compensation_retains_sound():
    root = straight("guitar")
    first, second = root.find("part/measure").findall("note")[:2]
    # Repeated E: changing the first to E-flat must leave the second sounding E.
    second.find("pitch/step").text = "E"
    second.find("notations/technical/fret").text = "0"
    xml = ready(root, "guitar")
    output = ET.fromstring(apply_edit(xml, "P1", "guitar", patch("tab_pitch", string=2, fret=4)))
    first, second = output.find("part/measure").findall("note")[:2]
    assert first.findtext("pitch/alter") == "-1"
    assert second.findtext("pitch/step") == "E" and second.find("pitch/alter") is None
    assert second.findtext("accidental") == "natural"
    assert first.findtext("duration") == second.findtext("duration") == "12"


@pytest.mark.parametrize("case", ["duplicate", "different-duration", "tie"])
def test_ambiguous_tab_pair_change_is_rejected_without_partial_application(case):
    root = combined_tab()
    notes = root.find("part/measure").findall("note")
    if case == "duplicate":
        clone = copy.deepcopy(notes[0])
        clone.insert(0, ET.Element("chord"))
        root.find("part/measure").insert(list(root.find("part/measure")).index(notes[0]) + 1, clone)
    elif case == "different-duration":
        notes[0].find("duration").text = "6"
    elif case == "tie":
        add(notes[0], "tie", type="start")
    xml = ready(root, "guitar")
    row = row_for(xml, "guitar", staff="2", kind="pitched")
    assert not row["editable"]["tab_pitch"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "guitar", {"note_id": row["id"], "operation": "tab_pitch", "string": 2, "fret": 7})
    assert encode(ET.fromstring(xml)) == encode(root)


def test_explicit_tab_pitch_repairs_wrong_old_fret_but_same_pitch_move_stays_locked():
    root = combined_tab()
    notes = root.find("part/measure").findall("note")
    notes[3].find("notations/technical/fret").text = "3"
    xml = ready(root, "guitar")
    row = row_for(xml, "guitar", staff="2", kind="pitched")
    assert row["editable"]["tab_pitch"] and not row["editable"]["fingering"]
    assert row["warnings"]
    repaired = ET.fromstring(apply_edit(xml, "P1", "guitar", {"note_id": row["id"], "operation": "tab_pitch", "string": 2, "fret": 7}))
    notes = repaired.find("part/measure").findall("note")
    assert notes[0].findtext("pitch/step") == notes[3].findtext("pitch/step") == "F"
    assert notes[3].findtext("notations/technical/fret") == "7"


def test_tab_partner_requires_reciprocal_uniqueness_for_unisons():
    root = combined_tab()
    measure = root.find("part/measure")
    target = measure.findall("note")[3]
    another = copy.deepcopy(target)
    another.insert(0, ET.Element("chord"))
    another.find("notations/technical/string").text = "2"
    another.find("notations/technical/fret").text = "5"
    measure.insert(list(measure).index(target) + 1, another)
    xml = ready(root, "guitar")
    rows = [r for r in describe(xml, "P1", "guitar")["notes"] if r["staff"] == "2" and r["onset"] == "0"]
    assert len(rows) == 2 and all(not r["editable"]["tab_pitch"] for r in rows)
    with pytest.raises(ValueError, match="여러 TAB"):
        apply_edit(xml, "P1", "guitar", {"note_id": rows[0]["id"], "operation": "tab_pitch", "string": 3, "fret": 11})


@pytest.mark.parametrize("inst", ["vocal", "piano", "synthesizer", "guitar", "bass", "drums"])
def test_delete_to_rest_and_reinsert_for_all_instruments_keeps_timeline(inst):
    source = straight(inst)
    xml = ready(source, inst)
    before = describe(xml, "P1", inst)["notes"]
    assert before[0]["editable"]["delete"]
    erased = apply_edit(xml, "P1", inst, patch("delete"))
    rest_row = describe(erased, "P1", inst)["notes"][0]
    assert rest_row["kind"] == "rest" and rest_row["editable"]["insert"]
    if inst == "drums":
        operation = patch("insert", kind="unpitched", drum_id="kick")
    elif inst in {"guitar", "bass"}:
        operation = patch("insert", kind="tab", string=2, fret=7)
    else:
        operation = patch("insert", kind="pitched", step="D", alter=1, octave=4)
    filled = apply_edit(erased, "P1", inst, operation)
    after = describe(filled, "P1", inst)["notes"]
    assert after[0]["kind"] == ("unpitched" if inst == "drums" else "pitched")
    assert [(r["onset"], r["duration"], r["staff"], r["voice"]) for r in before] == [(r["onset"], r["duration"], r["staff"], r["voice"]) for r in after]
    assert encode(ET.fromstring(filled).findall("part")[1]) == encode(source.findall("part")[1])


def test_pitched_drum_omr_mistake_can_be_replaced_by_explicit_known_kit_hit():
    root = straight("drums")
    first = root.find("part/measure/note")
    first.remove(first.find("unpitched"))
    first.remove(first.find("instrument"))
    pitch = ET.Element("pitch")
    add(pitch, "step", "G")
    add(pitch, "octave", 5)
    first.insert(0, pitch)
    xml = ready(root, "drums")
    assert not describe(xml, "P1", "drums")["notes"][0]["editable"]["pitch"]
    rest = apply_edit(xml, "P1", "drums", patch("delete"))
    fixed = ET.fromstring(apply_edit(rest, "P1", "drums", patch("insert", kind="unpitched", drum_id="hat-open")))
    first = fixed.find("part/measure/note")
    assert first.find("pitch") is None and first.find("unpitched") is not None
    assert first.find("instrument").get("id") == "hat-open"
    assert first.find("notations/technical/open-string") is not None


def test_combined_tab_delete_and_insert_repairs_both_representations_atomically():
    xml = ready(combined_tab(), "guitar")
    target = row_for(xml, "guitar", staff="2", note_index=4)["id"]
    erased = apply_edit(xml, "P1", "guitar", {"note_id": target, "operation": "delete"})
    rows = describe(erased, "P1", "guitar")["notes"]
    assert rows[0]["kind"] == rows[3]["kind"] == "rest"
    filled = ET.fromstring(apply_edit(erased, "P1", "guitar", {"note_id": target, "operation": "insert", "kind": "tab", "string": 2, "fret": 7}))
    notes = filled.find("part/measure").findall("note")
    assert notes[0].findtext("pitch/step") == notes[3].findtext("pitch/step") == "F"
    assert notes[0].findtext("pitch/octave") == "5" and notes[3].findtext("pitch/octave") == "4"


@pytest.mark.parametrize("inst", ["vocal", "piano", "synthesizer", "guitar", "bass", "drums"])
def test_add_chord_tone_never_shifts_next_notes(inst):
    xml = ready(straight(inst), inst)
    if inst == "drums":
        operation = patch("chord", kind="unpitched", drum_id="kick")
    elif inst in {"guitar", "bass"}:
        operation = patch("chord", kind="tab", string=2, fret=0)
    else:
        operation = patch("chord", kind="pitched", step="G", alter=0, octave=4)
    before = describe(xml, "P1", inst)["notes"]
    output = apply_edit(xml, "P1", inst, operation)
    after = describe(output, "P1", inst)["notes"]
    assert len(after) == len(before) + 1
    assert after[0]["onset"] == after[1]["onset"] == "0"
    assert after[2]["onset"] == before[1]["onset"]
    assert ET.fromstring(output).find("part/measure").findall("note")[1].find("chord") is not None


def test_combined_tab_chord_adds_matching_transposed_standard_tone():
    xml = ready(combined_tab(), "guitar")
    target = row_for(xml, "guitar", staff="2", note_index=4)["id"]
    output = apply_edit(xml, "P1", "guitar", {"note_id": target, "operation": "chord", "kind": "tab", "string": 2, "fret": 0})
    rows = describe(output, "P1", "guitar")["notes"]
    chord = [n for n in ET.fromstring(output).find("part/measure").findall("note") if n.find("chord") is not None]
    assert len(chord) == 2
    assert [n.findtext("pitch/step") for n in chord] == ["B", "B"]
    assert [n.findtext("pitch/octave") for n in chord] == ["4", "3"]
    assert [r["onset"] for r in rows[:3]] == ["0", "0", "1"]


@pytest.mark.parametrize("inst", ["vocal", "piano", "synthesizer", "guitar", "bass", "drums"])
def test_rhythm_extension_consumes_following_rest_without_moving_other_voices(inst):
    xml = ready(straight(inst), inst)
    before = describe(xml, "P1", inst)["notes"]
    selected = before[2 if inst == "drums" else 1]
    assert selected["editable"]["rhythm"]
    output = apply_edit(xml, "P1", inst, {"note_id": selected["id"], "operation": "rhythm", "type": "half", "dots": 0})
    after = describe(output, "P1", inst)["notes"]
    assert after[2 if inst == "drums" else 1]["duration"] == "2"
    assert after[0]["onset"] == before[0]["onset"]
    assert ET.fromstring(output).find("part/measure/note/duration").text == "12"


def test_rhythm_shrink_creates_rests_and_keeps_next_onset_and_barline():
    xml = ready(straight())
    before = describe(xml, "P1", "vocal")["notes"]
    output = apply_edit(xml, "P1", "vocal", patch("rhythm", type="eighth", dots=0))
    rows = describe(output, "P1", "vocal")["notes"]
    assert rows[0]["duration"] == rows[1]["duration"] == "1/2"
    assert rows[1]["kind"] == "rest"
    assert rows[2]["onset"] == before[1]["onset"] == "1"
    assert ET.fromstring(output).find("part/measure/barline/repeat") is not None


def test_rhythm_cannot_grow_into_other_notes_or_consume_annotated_rest():
    root = straight()
    xml = ready(root)
    with pytest.raises(ValueError, match="인접 쉼표"):
        apply_edit(xml, "P1", "vocal", patch("rhythm", type="half", dots=0))
    rest = root.find("part/measure").findall("note")[2]
    add(add(rest, "lyric"), "text", "이쉼표가사유지")
    with pytest.raises(ValueError, match="인접 쉼표"):
        apply_edit(ready(root), "P1", "vocal", {**patch("rhythm", type="half", dots=0), "note_id": "m0n1"})


def test_lyric_add_delete_isolated_verse_retains_original_connected_verse():
    xml = ready(straight())
    added = apply_edit(xml, "P1", "vocal", patch("lyric_add", text="새로운 가사"))
    lyrics = ET.fromstring(added).find("part/measure/note").findall("lyric")
    assert lyrics[0].get("number") == "2" and lyrics[0].find("extend") is not None
    assert lyrics[1].get("number") == "1" and lyrics[1].findtext("syllabic") == "single"
    row = describe(added, "P1", "vocal")["notes"][0]
    assert not row["lyrics"][0]["deletable"] and row["lyrics"][1]["deletable"]
    removed = apply_edit(added, "P1", "vocal", patch("lyric_delete", lyric_index=1))
    assert encode(ET.fromstring(removed)) == encode(ET.fromstring(xml))
    with pytest.raises(ValueError):
        apply_edit(added, "P1", "vocal", patch("lyric_delete", lyric_index=0))


def test_delete_preserves_following_pitch_accidental_meaning():
    root = straight()
    first, second = root.find("part/measure").findall("note")[:2]
    for n in (first, second):
        n.find("pitch/step").text = "C"
        add(n.find("pitch"), "alter", 1)
    add(first, "accidental", "sharp")
    result = ET.fromstring(apply_edit(ready(root), "P1", "vocal", patch("delete")))
    first, second = result.find("part/measure").findall("note")[:2]
    assert first.find("rest") is not None and first.find("accidental") is None
    assert second.findtext("pitch/alter") == "1" and second.findtext("accidental") == "sharp"


def test_keyboard_rhythm_preserves_other_hand_backup_and_timeline():
    root = straight("piano")
    measure = root.find("part/measure")
    attrs = measure.find("attributes")
    attrs.find("clef").set("number", "1")
    add(add(attrs, "clef", number="2"), "sign", "F")
    add(attrs, "staves", 2)
    left = [copy.deepcopy(n) for n in measure.findall("note")]
    index = list(measure).index(measure.find("barline"))
    backup = ET.Element("backup")
    add(backup, "duration", 48)
    measure.insert(index, backup)
    for offset, n in enumerate(left, 1):
        n.find("staff").text, n.find("voice").text = "2", "2"
        measure.insert(index + offset, n)
    xml = ready(root, "piano")
    before = [r for r in describe(xml, "P1", "piano")["notes"] if r["staff"] == "2"]
    changed = apply_edit(xml, "P1", "piano", {**patch("rhythm", type="half", dots=0), "note_id": "m0n1"})
    after = [r for r in describe(changed, "P1", "piano")["notes"] if r["staff"] == "2"]
    assert [(r["onset"], r["duration"], r.get("pitch")) for r in before] == [(r["onset"], r["duration"], r.get("pitch")) for r in after]
    assert ET.fromstring(changed).findtext("part/measure/backup/duration") == "48"


@pytest.mark.parametrize("operation,values", [
    ("delete", {"extra": True}), ("tab_pitch", {"string": True, "fret": 0}),
    ("rhythm", {"type": "quarter", "dots": True}), ("rhythm", {"type": "breve", "dots": 0}),
    ("insert", {"kind": "pitched", "step": "C", "alter": 0}),
    ("chord", {"kind": "pitched", "step": "C", "alter": 0, "octave": False}),
    ("lyric_add", {"text": "\x00"}), ("lyric_delete", {"lyric_index": -1}),
])
def test_structural_patch_shape_and_values_fail_closed(operation, values):
    xml = ready(straight("guitar"), "guitar")
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "guitar", patch(operation, **values))


def test_direction_between_chord_base_and_member_does_not_break_group_guards():
    root = straight()
    measure = root.find("part/measure")
    first, second = measure.findall("note")[:2]
    second.insert(0, ET.Element("chord"))
    direction = ET.Element("direction")
    add(add(direction, "direction-type"), "words", "화음 내부 지시")
    measure.insert(list(measure).index(second), direction)
    xml = ready(root)
    row = describe(xml, "P1", "vocal")["notes"][0]
    assert not row["editable"]["delete"] and not row["editable"]["rhythm"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "vocal", patch("rhythm", type="eighth", dots=0))
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "vocal", patch("delete"))
    expanded = apply_edit(xml, "P1", "vocal", patch("chord", kind="pitched", step="G", alter=0, octave=4))
    rows = describe(expanded, "P1", "vocal")["notes"]
    assert [r["onset"] for r in rows[:3]] == ["0", "0", "0"]
    assert rows[3]["onset"] == "1"
    assert ET.fromstring(expanded).find("part/measure/direction/direction-type/pedal") is not None


def test_last_drum_exemplar_is_retained_in_bounded_nonmusical_palette():
    from backend.source_score_palette import PREFIX
    xml = ready(straight("drums"), "drums")
    before = ET.fromstring(xml)
    erased = apply_edit(xml, "P1", "drums", patch("delete"))
    root = ET.fromstring(erased)
    field = root.find("identification/miscellaneous/miscellaneous-field")
    assert field.get("name") == PREFIX + "P1"
    assert len(field.text.encode()) < 65_536
    assert "hat" in {n["id"] for n in describe(erased, "P1", "drums")["drum_options"]}
    assert encode(root.find("part-list")) == encode(before.find("part-list"))
    assert len(root.findall("part/measure/note")) == len(before.findall("part/measure/note"))
    # Reloading/re-preparing the exported canonical XML retains this option.
    reloaded = prepare(erased.encode(), "exported.musicxml", "P1", "drums")["xml"]
    inserted = ET.fromstring(apply_edit(reloaded, "P1", "drums", patch("insert", kind="unpitched", drum_id="hat")))
    first = inserted.find("part/measure/note")
    assert first.find("instrument").get("id") == "hat" and first.findtext("unpitched/display-step") == "G"
    assert first.findtext("notehead") == "x"


@pytest.mark.parametrize("case", ["checksum", "bank", "unknown-id", "duplicate-id", "unknown-schema", "overflow", "too-many", "duplicate-field", "entity", "active-resource", "nested-technique"])
def test_drum_palette_schema_bounds_checksum_bank_and_xml_safety(case):
    from backend.source_score_palette import _hash
    xml = apply_edit(ready(straight("drums"), "drums"), "P1", "drums", patch("delete"))
    root = ET.fromstring(xml)
    field = root.find("identification/miscellaneous/miscellaneous-field")
    payload = json.loads(field.text)
    if case == "checksum":
        payload["sha256"] = "0" * 64
    elif case == "bank":
        root.find("part-list/score-part/midi-instrument/midi-unpitched").text = "38"
    elif case == "unknown-id":
        payload["templates"][0]["id"] = "not-a-bank-id"
    elif case == "duplicate-id":
        payload["templates"].append(copy.deepcopy(payload["templates"][0]))
    elif case == "unknown-schema":
        payload["network_url"] = "https://example.invalid"
    elif case == "too-many":
        payload["templates"] = payload["templates"] * 50
    elif case == "duplicate-field":
        root.find("identification/miscellaneous").append(copy.deepcopy(field))
    elif case == "entity":
        payload["templates"][0]["note"] = '<!DOCTYPE note [<!ENTITY x "x">]><note>&x;</note>'
    elif case == "active-resource":
        shape = ET.fromstring(payload["templates"][0]["note"])
        add(shape, "image", source="https://example.invalid")
        payload["templates"][0]["note"] = ET.tostring(shape, encoding="unicode")
    elif case == "nested-technique":
        shape = ET.fromstring(payload["templates"][0]["note"])
        add(add(add(add(shape, "notations"), "technical"), "open-string"), "bend")
        payload["templates"][0]["note"] = ET.tostring(shape, encoding="unicode")
    if case not in {"checksum", "bank", "duplicate-field"}:
        payload.pop("sha256")
        payload["sha256"] = _hash(payload)
    field.text = json.dumps(payload)
    if case == "overflow":
        field.text = " " * 65_537
    changed = encode(root).decode()
    # Invalid metadata is never a source of drum templates. Other live observed
    # templates remain available; no invented kick/hat mapping is substituted.
    assert "hat" not in {n["id"] for n in describe(changed, "P1", "drums")["drum_options"]}
    with pytest.raises(ValueError, match="원본"):
        apply_edit(changed, "P1", "drums", patch("insert", kind="unpitched", drum_id="hat"))


def test_multi_instrument_keyboard_chord_and_rest_repair_preserve_instrument_reference():
    root = straight("synthesizer")
    definition = root.find("part-list/score-part")
    for ident, label in (("piano", "Piano"), ("strings", "Strings")):
        add(add(definition, "score-instrument", id=ident), "instrument-name", label)
    first = root.find("part/measure/note")
    first.insert(list(first).index(first.find("duration")) + 1, ET.Element("instrument", id="strings"))
    xml = ready(root, "synthesizer")
    expanded = ET.fromstring(apply_edit(xml, "P1", "synthesizer", patch("chord", kind="pitched", step="G", alter=0, octave=4)))
    notes = expanded.find("part/measure").findall("note")
    assert notes[0].find("instrument").get("id") == notes[1].find("instrument").get("id") == "strings"
    erased = apply_edit(xml, "P1", "synthesizer", patch("delete"))
    assert ET.fromstring(erased).find("part/measure/note/instrument").get("id") == "strings"
    restored = ET.fromstring(apply_edit(erased, "P1", "synthesizer", patch("insert", kind="pitched", step="E", alter=0, octave=4)))
    assert restored.find("part/measure/note/instrument").get("id") == "strings"


@pytest.mark.parametrize("case", ["undefined", "duplicate", "dynamic"])
def test_ambiguous_pitched_instruments_never_gain_new_unassigned_chord(case):
    root = straight("synthesizer")
    definition = root.find("part-list/score-part")
    add(add(definition, "score-instrument", id="strings"), "instrument-name", "Strings")
    first = root.find("part/measure/note")
    add(first, "instrument", id="undefined" if case == "undefined" else "strings")
    if case == "duplicate":
        add(first, "instrument", id="strings")
    elif case == "dynamic":
        add(root.find("part/measure/direction/sound"), "instrument-change", id="strings")
    xml = ready(root, "synthesizer")
    assert not describe(xml, "P1", "synthesizer")["notes"][0]["editable"]["chord"]
    with pytest.raises(ValueError):
        apply_edit(xml, "P1", "synthesizer", patch("chord", kind="pitched", step="G", alter=0, octave=4))
