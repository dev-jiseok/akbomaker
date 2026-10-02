import xml.etree.ElementTree as ET

import mido
import pytest

from backend.editing import ScoreEdit, create_document, persist, validate_edit
from backend.tablature import assign_positions
from backend.tests.test_audio import client, finish


def body(doc):
    return {"base_revision": doc["revision"], **{key: doc[key] for key in ("title", "bpm", "notes", "annotations", "layout", "tab", "lyrics")}}


def test_bass_two_staves_tuning_concert_midi_and_lyrics_on_rest(tmp_path):
    doc = create_document([(0, .5, 28, .8)], "bass", "TAB", 120, 2)
    doc["lyrics"] = [{"id": "entry", "start": 7, "text": "보컬 진입"}]
    persist(doc, tmp_path)
    tree = ET.parse(tmp_path / "bass.musicxml")
    assert tree.findtext(".//staves") == "2"
    assert tree.findtext(".//clef[@number='2']/sign") == "TAB"
    assert tree.findtext(".//staff-tuning[@line='1']/tuning-octave") == "1"
    tab_note = next(n for n in tree.findall(".//note[staff='2']") if n.findtext("notations/technical/fret") == "0")
    assert tab_note is not None and tab_note.findtext("notations/technical/string") == "4"
    assert tree.findtext(".//lyric/text") == "보컬 진입"
    lyric_note = tree.find(".//note[lyric]")
    assert lyric_note.find("rest") is not None and lyric_note.findtext("staff") == "2"
    for staff in ("1", "2"):
        assert sum(int(n.findtext("duration")) for n in tree.findall(f".//note[staff='{staff}']") if n.find("chord") is None) == 16
    midi = mido.MidiFile(tmp_path / "bass.mid")
    assert [n.note for n in midi.tracks[0] if n.type == "note_on"] == [28]


def test_guitar_chord_distinct_strings_and_unplayable_preserved():
    doc = create_document([(0, .5, p, .8) for p in (40, 45, 52, 55, 59, 64, 10)], "guitar", "test", 120, 2)
    # More detected pitches than strings: do not silently rewrite the notes.
    assert {n["pitch"] for n in doc["notes"]} == {40, 45, 52, 55, 59, 64, 10}
    assert next(n for n in doc["notes"] if n["pitch"] == 10)["string"] is None
    assert sum(n["string"] is not None for n in doc["notes"]) == 6
    notes = [{"id": str(p), "start": 0, "length": 4, "pitch": p, "velocity": 80} for p in (40, 45, 52, 55, 59, 64)]
    mapped = assign_positions(notes, [64, 59, 55, 50, 45, 40])
    assert len({n["string"] for n in mapped}) == 6 and all(n["fret"] is not None for n in mapped)


@pytest.mark.parametrize("case", ["wrong_pitch", "one_field", "same_string", "bad_tuning", "lyric_collision", "lyric_outside", "lyric_empty", "lyric_control"])
def test_invalid_tab_or_lyrics(case):
    doc = create_document([], "bass", "test", 120, 2)
    edit = body(doc)
    edit["notes"] = [{"id": "a", "start": 0, "length": 4, "pitch": 28, "velocity": 80, "string": 4, "fret": 0}]
    if case == "wrong_pitch":
        edit["notes"][0]["fret"] = 1
    elif case == "one_field":
        edit["notes"][0]["fret"] = None
    elif case == "same_string":
        edit["notes"].append({**edit["notes"][0], "id": "b", "pitch": 30, "fret": 2, "start": 2})
    elif case == "bad_tuning":
        edit["tab"]["tuning"] = [28, 33, 38, 43]
    else:
        edit["lyrics"] = [{"id": "l", "start": 0, "text": "진입"}]
        if case == "lyric_collision": edit["lyrics"].append({"id": "l2", "start": 0, "text": "중복"})
        if case == "lyric_outside": edit["lyrics"][0]["start"] = 16
        if case == "lyric_empty": edit["lyrics"][0]["text"] = " "
        if case == "lyric_control": edit["lyrics"][0]["text"] = "\x00"
    with pytest.raises(ValueError):
        validate_edit(ScoreEdit(**edit), doc)


def test_lyric_copy_preserves_target_notes_and_original_and_guards_revision(client):
    job = finish(client, client.post("/api/demo").json())
    base = f'/api/jobs/{job["id"]}/scores'
    original = client.get(base + "/bass").json()
    source = client.get(base + "/drums").json()
    edit = body(source)
    edit["lyrics"] = [{"id": "l", "start": 3, "text": "들어가요"}]
    saved = client.put(base + "/drums", json=edit).json()["document"]
    assert client.post(base + "/drums/copy-lyrics", json={"base_revision": source["revision"]}).status_code == 409
    result = client.post(base + "/drums/copy-lyrics", json={"base_revision": saved["revision"]})
    assert result.status_code == 200, result.text
    assert result.json()["copied"] == 5
    target = client.get(base + "/bass").json()
    assert target["notes"] == original["notes"] and target["tab"] == original["tab"]
    assert target["lyrics"][0]["start"] == 3 and target["lyrics"][0]["text"] == "들어가요"
    assert target["revision"] != original["revision"]
    assert client.get(base + "/bass?original=true").json()["lyrics"] == []
    assert client.put(base + "/bass", json=body(original)).status_code == 409


def test_tab_only_unassigned_notes_are_reported_and_midi_keeps_pitch(client):
    job = finish(client, client.post("/api/demo").json())
    url = f'/api/jobs/{job["id"]}/scores/bass'
    doc = client.get(url).json()
    edit = body(doc)
    edit["tab"]["mode"] = "tab"
    edit["notes"] = [{"id": "low", "start": 0, "length": 4, "pitch": 10, "velocity": 80}]
    saved = client.put(url, json=edit).json()
    stem = next(s for s in saved["job"]["stems"] if s["id"] == "bass")
    assert stem["score_tab_unassigned"] == 1 and stem["score_tab_mode"] == "tab"
    tree = ET.fromstring(client.get(stem["score_url"]).content)
    assert tree.findtext(".//clef/sign") == "TAB" and tree.find(".//technical/fret") is None
    from io import BytesIO
    midi = mido.MidiFile(file=BytesIO(client.get(stem["midi_url"]).content))
    assert [n.note for n in midi.tracks[0] if n.type == "note_on"] == [10]


def test_beams_precede_lyric_in_musicxml_schema_order(tmp_path):
    doc = create_document([(0, .1, 42, .8), (.25, .35, 42, .8)], "drums", "test", 120, 2)
    doc["lyrics"] = [{"id": "l", "start": 0, "text": "시작"}]
    persist(doc, tmp_path)
    note = ET.parse(tmp_path / "drums.musicxml").find(".//note[lyric]")
    tags = [c.tag for c in note]
    assert "beam" in tags and tags.index("beam") < tags.index("lyric")
