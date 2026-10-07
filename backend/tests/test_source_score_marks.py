"""Safe paired notation marks in canonical scores for all six instruments."""
import copy
import time
import xml.etree.ElementTree as ET

import pytest

from backend.source_score import apply_edit, describe
from backend.source_score_marks import Context, augment


def add(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if text is not None:
        node.text = str(text)
    return node


def pitch(parent, midi, *, prefix=""):
    names = (("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
             ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0))
    step, alter = names[midi % 12]
    add(parent, prefix + "step", step)
    if alter:
        add(parent, prefix + "alter", alter)
    add(parent, prefix + "octave", midi // 12 - 1)


def fixture(instrument="vocal", *, linked=False, grand=False, frets=None, bars=2):
    root = ET.Element("score-partwise", version="4.0")
    add(add(root, "work"), "work-title", "기호 검토")
    add(add(root, "identification"), "creator", "원 작곡가", type="composer")
    listing = add(root, "part-list")
    definition = add(listing, "score-part", id="P1")
    add(definition, "part-name", instrument)
    if instrument == "drums":
        add(add(definition, "score-instrument", id="hat"), "instrument-name", "Hi-hat")
        midi = add(definition, "midi-instrument", id="hat")
        add(midi, "midi-channel", 10)
        add(midi, "midi-unpitched", 43)
    add(add(listing, "score-part", id="P2"), "part-name", "untouched")
    part = add(root, "part", id="P1")
    tab = instrument in {"guitar", "bass"}
    tuning = [64, 59, 55, 50, 45, 40] if instrument == "guitar" else [43, 38, 33, 28]
    for mi in range(bars):
        measure = add(part, "measure", number=str(mi + 1))
        if mi == 0:
            attrs = add(measure, "attributes")
            add(attrs, "divisions", 1)
            time_node = add(attrs, "time")
            add(time_node, "beats", 4)
            add(time_node, "beat-type", 4)
            if linked or grand:
                add(attrs, "staves", 2)
            add(add(attrs, "clef", number="1"), "sign", "G" if linked or grand or not tab else "TAB")
            if linked or grand:
                add(add(attrs, "clef", number="2"), "sign", "TAB" if linked else "F")
            if tab:
                details = add(attrs, "staff-details", number="2" if linked else "1")
                add(details, "staff-lines", len(tuning))
                for index, value in enumerate(reversed(tuning), 1):
                    pitch(add(details, "staff-tuning", line=str(index)), value, prefix="tuning-")
                add(details, "capo", 0)
        for staff in (1, 2) if linked or grand else (1,):
            if staff == 2:
                add(add(measure, "backup"), "duration", 4)
            for ni in range(4):
                note = add(measure, "note", **{"default-x": str(20 + ni * 50)})
                fret = frets[ni] if frets else 3
                if instrument == "drums":
                    up = add(note, "unpitched")
                    add(up, "display-step", "G")
                    add(up, "display-octave", 5)
                else:
                    pitch(add(note, "pitch"), tuning[0] + fret if tab else 60 if staff == 1 else 48)
                add(note, "duration", 1)
                if instrument == "drums":
                    add(note, "instrument", id="hat")
                add(note, "voice", 1)
                add(note, "type", "quarter")
                if instrument == "drums":
                    add(note, "notehead", "x")
                add(note, "staff", staff)
                if tab and (not linked or staff == 2):
                    technical = add(add(note, "notations"), "technical")
                    add(technical, "string", 1)
                    add(technical, "fret", fret)
                lyric = add(note, "lyric", number="1")
                add(lyric, "text", f"가사{ni}")
    other = add(add(root, "part", id="P2"), "measure", number="1")
    add(add(other, "note"), "rest")
    return ET.tostring(root, encoding="unicode")


def patch(xml, instrument="vocal", *, operation="connection", note_id="m0n0", mark="tie", action="add", target="m0n1"):
    body = {"operation": operation, "note_id": note_id, "mark": mark, "action": action}
    if operation == "connection":
        body["target_note_id"] = target
    return apply_edit(xml, "P1", instrument, body)


def invariant(xml):
    root = ET.fromstring(xml)
    return ([[(child.tag, ET.tostring(child)) for child in note if child.tag not in {"tie", "notations"}]
             for note in root.findall("part/measure/note")], ET.tostring(root.find("part[@id='P2']")))


@pytest.mark.parametrize("instrument", ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"])
@pytest.mark.parametrize("mark", ["accent", "staccato", "tenuto"])
def test_articulations_add_remove_keep_other_content(instrument, mark):
    xml = fixture(instrument)
    changed = patch(xml, instrument, operation="articulation", mark=mark)
    assert invariant(changed) == invariant(xml)
    assert ET.fromstring(changed).find(f"part/measure/note/notations/articulations/{mark}") is not None
    note = describe(changed, "P1", instrument)["notes"][0]
    assert mark in note["articulations"] and note["editable"]["articulation"]
    assert patch(changed, instrument, operation="articulation", mark=mark) == changed
    removed = patch(changed, instrument, operation="articulation", mark=mark, action="remove")
    assert ET.fromstring(removed).find(f".//articulations/{mark}") is None
    assert invariant(removed) == invariant(xml)


@pytest.mark.parametrize("instrument", ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"])
@pytest.mark.parametrize("mark", ["tie", "slur"])
def test_pair_add_and_remove_all_instruments(instrument, mark):
    xml = fixture(instrument)
    changed = patch(xml, instrument, mark=mark)
    assert invariant(changed) == invariant(xml)
    root = ET.fromstring(changed)
    nodes = root.findall(f"part/measure/note/notations/{'tied' if mark == 'tie' else 'slur'}")
    assert [node.get("type") for node in nodes] == ["start", "stop"]
    if mark == "tie":
        assert [node.get("type") for node in root.findall("part/measure/note/tie")] == ["start", "stop"]
    doc = describe(changed, "P1", instrument)
    assert {"mark": mark, "target_note_id": "m0n1", "number": "1"} in doc["notes"][0]["connections"]
    if mark == "tie":
        assert not doc["notes"][0]["editable"]["rhythm"]
        assert not doc["notes"][0]["editable"]["delete"]
    removed = patch(changed, instrument, mark=mark, action="remove")
    assert invariant(removed) == invariant(xml)
    assert not ET.fromstring(removed).findall(f".//{'tied' if mark == 'tie' else 'slur'}")
    assert not ET.fromstring(removed).findall(".//tie")


def test_cross_measure_tie_and_nonadjacent_slur():
    xml = fixture()
    changed = patch(xml, note_id="m0n3", target="m1n0")
    assert ET.fromstring(changed).find("part/measure[@number='2']/note/notations/tied").get("type") == "stop"
    slur = patch(xml, mark="slur", target="m1n3")
    assert patch(slur, mark="slur", target="m1n3", action="remove")


@pytest.mark.parametrize("instrument", ["guitar", "bass"])
@pytest.mark.parametrize("mark,frets", [("slide", [3, 5, 3, 3]), ("hammer-on", [3, 5, 3, 3]), ("pull-off", [5, 3, 3, 3])])
def test_tab_same_string_techniques_pair_and_remove(instrument, mark, frets):
    xml = fixture(instrument, frets=frets)
    changed = patch(xml, instrument, mark=mark)
    path = f".//{mark}"
    assert [node.get("type") for node in ET.fromstring(changed).findall(path)] == ["start", "stop"]
    if mark != "slide":
        assert ET.fromstring(changed).find(path).text == ("H" if mark == "hammer-on" else "P")
    assert invariant(changed) == invariant(xml)
    assert not ET.fromstring(patch(changed, instrument, mark=mark, action="remove")).findall(path)


@pytest.mark.parametrize("note,target", [("m0n0", "m0n1"), ("m0n4", "m0n5")])
@pytest.mark.parametrize("mark", ["tie", "slur"])
def test_linked_standard_and_tab_mirrored_from_either_side(note, target, mark):
    xml = fixture("guitar", linked=True)
    changed = patch(xml, "guitar", note_id=note, target=target, mark=mark)
    nodes = ET.fromstring(changed).findall(f".//{'tied' if mark == 'tie' else 'slur'}")
    assert len(nodes) == 4
    assert invariant(changed) == invariant(xml)
    removed = patch(changed, "guitar", note_id=note, target=target, mark=mark, action="remove")
    assert not ET.fromstring(removed).findall(f".//{'tied' if mark == 'tie' else 'slur'}")


def test_linked_technique_and_articulation_are_atomic():
    xml = fixture("bass", linked=True, frets=[3, 5, 3, 3])
    changed = patch(xml, "bass", note_id="m0n4", target="m0n5", mark="hammer-on")
    assert len(ET.fromstring(changed).findall(".//hammer-on")) == 4
    marked = patch(xml, "bass", operation="articulation", mark="accent", note_id="m0n0")
    assert len(ET.fromstring(marked).findall(".//articulations/accent")) == 2
    assert invariant(changed) == invariant(xml)


def test_piano_grand_staff_marks_do_not_mirror_to_unrelated_hand():
    xml = fixture("piano", grand=True)
    changed = patch(xml, "piano", operation="articulation", mark="tenuto")
    assert len(ET.fromstring(changed).findall(".//tenuto")) == 1
    with pytest.raises(ValueError, match="보표·성부"):
        patch(xml, "piano", target="m0n4", mark="slur")


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "wrong_fret"])
def test_ambiguous_linked_notation_rejected_without_touching_xml(mutation):
    root = ET.fromstring(fixture("guitar", linked=True))
    measure = root.find("part/measure")
    tab = measure.findall("note")[4]
    if mutation == "duplicate":
        duplicate = copy.deepcopy(tab)
        duplicate.insert(0, ET.Element("chord"))
        measure.insert(list(measure).index(tab) + 1, duplicate)
    elif mutation == "missing":
        tab.remove(tab.find("pitch"))
        tab.insert(0, ET.Element("rest"))
    else:
        tab.find("notations/technical/fret").text = "20"
    xml = ET.tostring(root, encoding="unicode")
    with pytest.raises(ValueError):
        patch(xml, "guitar", operation="articulation", mark="accent")
    assert ET.tostring(root, encoding="unicode") == xml


@pytest.mark.parametrize("case", ["gap", "different_pitch", "different_voice", "backwards", "different_staff", "pickup", "unknown_meter"])
def test_unsafe_ties_rejected(case):
    root = ET.fromstring(fixture())
    note, target = "m0n0", "m0n1"
    second = root.findall("part/measure/note")[1]
    if case == "gap":
        target = "m0n2"
    elif case == "different_pitch":
        second.find("pitch/step").text = "D"
    elif case == "different_voice":
        second.find("voice").text = "2"
    elif case == "backwards":
        note, target = target, note
    elif case == "different_staff":
        second.find("staff").text = "2"
    elif case == "pickup":
        root.find("part/measure").set("implicit", "yes")
        note, target = "m0n3", "m1n0"
    else:
        attrs = root.find("part/measure/attributes")
        attrs.remove(attrs.find("time"))
        note, target = "m0n3", "m1n0"
    with pytest.raises(ValueError):
        patch(ET.tostring(root, encoding="unicode"), note_id=note, target=target)


@pytest.mark.parametrize("instrument,mark,frets", [("vocal", "slide", None), ("drums", "hammer-on", None),
                                                  ("guitar", "hammer-on", [5, 3, 3, 3]),
                                                  ("bass", "pull-off", [3, 5, 3, 3]), ("guitar", "slide", [3, 3, 3, 3])])
def test_unsupported_techniques_rejected(instrument, mark, frets):
    with pytest.raises(ValueError):
        patch(fixture(instrument, frets=frets), instrument, mark=mark)


def test_technique_string_change_rejected():
    root = ET.fromstring(fixture("guitar", frets=[3, 5, 3, 3]))
    note = root.findall("part/measure/note")[1]
    note.find("notations/technical/string").text = "2"
    note.find("notations/technical/fret").text = "10"
    with pytest.raises(ValueError, match="같은 튜닝·줄"):
        patch(ET.tostring(root, encoding="unicode"), "guitar", mark="slide")


def test_slur_number_reuse_and_overlapping_span_allocation():
    xml = fixture()
    broad = patch(xml, mark="slur", target="m1n3")
    nested = patch(broad, mark="slur", note_id="m0n1", target="m1n2")
    assert [node.get("number") for node in ET.fromstring(nested).findall(".//slur[@type='start']")] == ["1", "2"]
    removed = patch(nested, mark="slur", note_id="m0n1", target="m1n2", action="remove")
    assert len(ET.fromstring(removed).findall(".//slur")) == 2


def test_dangling_and_inconsistent_connections_cannot_be_removed_or_replaced():
    changed = patch(fixture())
    root = ET.fromstring(changed)
    target = root.findall("part/measure/note")[1]
    target.remove(target.find("tie"))
    xml = ET.tostring(root, encoding="unicode")
    with pytest.raises(ValueError, match="재생용"):
        patch(xml, action="remove")
    with pytest.raises(ValueError, match="중복"):
        patch(xml)
    assert not describe(xml, "P1", "vocal")["notes"][0]["connections"]


@pytest.mark.parametrize("invalid", [{"action": []}, {"action": True}, {"mark": []}, {"mark": "glissando"},
                                    {"target_note_id": []}, {"note_id": []}, {"extra": 1}])
def test_mark_patch_strict_shape(invalid):
    body = {"operation": "connection", "note_id": "m0n0", "target_note_id": "m0n1", "mark": "tie", "action": "add", **invalid}
    with pytest.raises(ValueError):
        apply_edit(fixture(), "P1", "vocal", body)


def test_removing_one_mark_preserves_other_marks_and_original_formatting():
    changed = patch(fixture(), mark="slur", target="m0n3")
    changed = patch(changed, operation="articulation", mark="accent")
    root = ET.fromstring(changed)
    accent = root.find(".//accent")
    accent.set("placement", "above")
    slur_before = [ET.tostring(n) for n in root.findall(".//slur")]
    removed = patch(ET.tostring(root, encoding="unicode"), operation="articulation", mark="accent", action="remove")
    assert [ET.tostring(n) for n in ET.fromstring(removed).findall(".//slur")] == slur_before


def test_tie_does_not_guess_between_duplicate_unison_chord_tones():
    root = ET.fromstring(fixture())
    measure = root.find("part/measure")
    first = measure.find("note")
    duplicate = copy.deepcopy(first)
    duplicate.insert(0, ET.Element("chord"))
    measure.insert(list(measure).index(first) + 1, duplicate)
    with pytest.raises(ValueError, match="같은 음이 여러"):
        patch(ET.tostring(root, encoding="unicode"), target="m0n2")


def test_cross_bar_tie_does_not_guess_staff_specific_meter():
    root = ET.fromstring(fixture("piano", grand=True))
    attrs = root.find("part/measure/attributes")
    attrs.find("time").set("number", "1")
    lower = add(attrs, "time", number="2")
    add(lower, "beats", 3)
    add(lower, "beat-type", 4)
    with pytest.raises(ValueError, match="바로 이어지는"):
        patch(ET.tostring(root, encoding="unicode"), "piano", note_id="m0n3", target="m1n0")


def test_source_api_marks_persist_undo_and_cas(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    client = TestClient(app.app)
    created = client.post("/api/source-scores", files={"file": ("source.musicxml", fixture().encode())},
                          data={"part_id": "P1", "instrument": "vocal"})
    assert created.status_code == 201, created.text
    ident = created.json()["id"]
    original = client.get(f"/api/source-scores/{ident}").json()
    body = {"base_revision": original["revision"], "patch": {"note_id": "m0n0", "operation": "connection",
             "target_note_id": "m0n1", "mark": "tie", "action": "add"}}
    saved = client.post(f"/api/source-scores/{ident}/edit", json=body)
    assert saved.status_code == 200, saved.text
    document = saved.json()["document"]
    assert document["notes"][0]["connections"]
    assert document["history"]["undo"]
    assert client.post(f"/api/source-scores/{ident}/edit", json=body).status_code == 409
    undone = client.post(f"/api/source-scores/{ident}/history", json={"base_revision": document["revision"], "action": "undo"})
    assert undone.status_code == 200
    assert not undone.json()["document"]["notes"][0]["connections"]
    assert undone.json()["document"]["content_check"]["music_edited"] is False


def test_marks_metadata_is_bounded_and_indexed_for_5000_notes():
    # Exercise only the new marks pass; the legacy structural editor has its
    # own performance profile. Synthetic row indexes isolate this regression.
    from backend.source_score import _scan
    root = ET.fromstring(fixture(bars=1))
    part = root.find("part")
    original = part.find("measure")
    original.find("attributes/time/beats").text = "10"
    template = copy.deepcopy(original.find("note"))
    for note in original.findall("note"):
        original.remove(note)
    original.find("attributes/divisions").text = "2"
    for _ in range(10):
        original.append(copy.deepcopy(template))
    for index in range(1, 500):
        measure = copy.deepcopy(original)
        measure.set("number", str(index + 1))
        part.append(measure)
    rows = _scan(part)
    for row in rows:
        row.update(kind="pitched", pitch={"step": "C", "alter": 0, "octave": 4}, editable={}, reasons={})
    started = time.monotonic()
    augment(root, part, "vocal", rows, {})
    elapsed = time.monotonic() - started
    assert len(rows) == 5000
    assert all(len(row["connection_targets"]) <= 32 for row in rows)
    assert elapsed < 8, f"Indexed bounded mark analysis took {elapsed:.2f}s"
