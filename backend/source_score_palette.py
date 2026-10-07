"""Persist only previously observed drum notation templates, not inferred kits.

Deleting a last occurrence must not delete the user's ability to insert that
same known hit. A bounded MusicXML miscellaneous-field carries the observed
templates and checksums. Checksums detect corruption, not authorship; every
read also validates the bank IDs, minimal shape schema and XML safety.
"""
import copy
import hashlib
import json
import xml.etree.ElementTree as ET

from .score_import import safe_xml
from .score_preservation import _remove_resources

PREFIX = "akbo-source-drum-palette:"
LIMIT = 65_536


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _bank_hash(definition):
    return hashlib.sha256(ET.tostring(definition, encoding="utf-8")).hexdigest()


def _entries(root, part_id):
    return [n for n in root.findall("identification/miscellaneous/miscellaneous-field") if n.get("name") == PREFIX + part_id]


def read(root, definition, bank, shape_reader):
    entries = _entries(root, definition.get("id"))
    if len(entries) != 1:
        return {}
    text = entries[0].text or ""
    if not text or len(text.encode("utf-8")) > LIMIT:
        return {}
    try:
        payload = json.loads(text)
        if (not isinstance(payload, dict) or set(payload) != {"version", "part_id", "bank_sha256", "templates", "sha256"}
                or type(payload["version"]) is not int or payload["version"] != 1
                or payload["part_id"] != definition.get("id") or payload["bank_sha256"] != _bank_hash(definition)):
            return {}
        checksum = payload.pop("sha256")
        if not isinstance(checksum, str) or checksum != _hash(payload):
            return {}
        templates = payload["templates"]
        if not isinstance(templates, list) or not 1 <= len(templates) <= 128:
            return {}
        result = {}
        for item in templates:
            if not isinstance(item, dict) or set(item) != {"id", "note"}:
                return {}
            ident, xml = item["id"], item["note"]
            if not isinstance(ident, str) or ident not in bank or ident in result or not isinstance(xml, str) or len(xml.encode()) > 4096:
                return {}
            note = safe_xml(xml.encode("utf-8"))
            if (note.tag != "note" or note.attrib or
                    any(n.tag not in {"unpitched", "instrument", "notehead", "notations"} for n in note)
                    or _remove_resources(copy.deepcopy(note))):
                return {}
            for notation in note.findall("notations"):
                if notation.attrib or any(n.tag != "technical" for n in notation):
                    return {}
                for technical in notation.findall("technical"):
                    if any(n.tag != "open-string" or len(n) for n in technical):
                        return {}
            if len(note.findall("notations")) > 1 or len(note.findall("instrument")) != 1:
                return {}
            reference = note.find("instrument")
            if reference.attrib != {"id": ident} or len(reference):
                return {}
            shape = shape_reader(note)
            if shape is None:
                return {}
            result[ident] = shape
        return result
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        return {}


def store(root, part_id, bank):
    if not bank:
        return
    definition = next(n for n in root.findall("part-list/score-part") if n.get("id") == part_id)
    templates = []
    for ident, entry in bank.items():
        note = ET.Element("note")
        for node in entry["shape"]:
            if node.tag == "technical":
                ET.SubElement(note, "notations").append(copy.deepcopy(node))
            else:
                note.append(copy.deepcopy(node))
        templates.append({"id": ident, "note": ET.tostring(note, encoding="unicode")})
    if len(templates) > 128:
        raise ValueError("보존할 드럼 표기 종류가 제한을 초과해요.")
    payload = {"version": 1, "part_id": part_id, "bank_sha256": _bank_hash(definition), "templates": templates}
    payload["sha256"] = _hash(payload)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode()) > LIMIT:
        raise ValueError("보존할 드럼 표기 정보가 64KB를 초과해요.")
    identities = root.findall("identification")
    if len(identities) > 1:
        raise ValueError("악보 식별 정보가 중복되어 드럼 표기를 안전하게 보관할 수 없어요.")
    if identities:
        identity = identities[0]
    else:
        identity = ET.Element("identification")
        index = next((i for i, node in enumerate(root) if node.tag not in {"work", "movement-number", "movement-title"}), len(root))
        root.insert(index, identity)
    groups = identity.findall("miscellaneous")
    if len(groups) > 1:
        raise ValueError("악보 추가 정보가 중복되어 안전하게 보관할 수 없어요.")
    group = groups[0] if groups else ET.SubElement(identity, "miscellaneous")
    existing = _entries(root, part_id)
    if len(existing) > 1:
        raise ValueError("보관된 드럼 표기 정보가 중복되어 있어요.")
    entry = existing[0] if existing else ET.SubElement(group, "miscellaneous-field", name=PREFIX + part_id)
    entry.text = text
