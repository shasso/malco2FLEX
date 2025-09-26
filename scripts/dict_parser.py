#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Dictionary parser for Assyrian (Syriac) / Arabic entries from a Word .docm file.

Extended rules:
* Bulleted paragraph => top-level entry.
* Line starting with '-', '–', '—' => subentry of previous entry.
* Syriac lemma: first Syriac-script run (phrase allowed). Leading '*' marks foreign word.
* Plurals: Arabic letter 'ج' followed by Syriac forms.
* IPA: /.../ or [...] or fallback Latin/IPA run.
* Arabic senses & synonyms:
        - Primary sense separators: '/', '؛', ';'
        - Synonym/near-synonym separator within a sense: '.' (period) when between Arabic tokens.
* Parenthetical markers (token-level) mapped to attributes:
        (ث) feminine; (ذ) masculine; (ذ.ث) common gender; (ج) marks following plural form;
        (فا) agent (doer); (مثله) sameMeaningAsPrevious; (نحو) domain=linguistic; (ܪܘ) tradition=ancientSong;
        (ح) domain=animal; (نب) domain=plant; (ط) domain=bird; (أ. م) cuneiform marker; (ص) phoneticChange.
        Unrecognized parentheses become notes.
* '(مثله)' causes gloss inheritance: entry copies glosses from previous entry (or parent for subentry) unless it already has its own gloss text.
* '(ج)' inside parentheses is distinguished from standalone plural marker 'ج <forms>'. Parenthetical (ج) only sets attribute indicating plurality classification.
* Period '.' splitting only applies after primary sense segmentation and not inside Syriac text or numeric/abbreviation spans.

Output schema additions per entry:
    senses: [ { "gloss": str, "synonyms": [str] } ]
    attributes: { gender, foreign, domain[], agent, phoneticChange, cuneiform, sameMeaningAsPrevious, tradition }
    originalGloss: legacy combined gloss string (optional)

CLI:
    python scripts/dict_parser.py --input "باب الواو.docm" --format json --output "باب-الواو.json"

Dependencies: python-docx
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    from docx import Document  # type: ignore
except Exception as e:  # pragma: no cover
    print("Error: python-docx is required. Please install with 'pip install python-docx'", file=sys.stderr)
    raise


# Unicode ranges and basic regex helpers
SYRIAC_RANGE = "\u0700-\u074F"  # Syriac
ARABIC_RANGE = "\u0600-\u06FF"  # Arabic
IPA_LATIN_RANGE = (
    "A-Za-z"  # basic Latin letters
    "\u0250-\u02AF"  # IPA extensions
    "\u1D00-\u1DFF"  # Phonetic Extensions
    "\u02B0-\u02FF"  # Spacing Modifier Letters
)

RE_DASH_START = re.compile(r"^\s*[-\u2013\u2014]\s+")  # -, –, —
RE_TATWEEL = re.compile("\u0640")
RE_MULTISPACE = re.compile(r"\s{2,}")
RE_SYRIAC_RUN = re.compile(fr"([{SYRIAC_RANGE}][{SYRIAC_RANGE}\s\u0730-\u074A\.\-ܼ̈ܿܽ·ᵒʾʿ]+)")
RE_ARABIC_CHAR = re.compile(fr"[{ARABIC_RANGE}]")
RE_ARABIC_SEMI = re.compile(r"[؛;]")
RE_ARABIC_COMMA = re.compile(r"[،,]")
# Parenthetical token regex (captures inner token trimmed)
RE_PAREN_TOKEN = re.compile(r"\(([^()]{1,12})\)")

# Map parenthesis tokens to attribute keys/values
PAREN_MARKERS = {
    "ث": ("gender", "f"),
    "ذ": ("gender", "m"),
    "ذ.ث": ("gender", "mf"),
    "فا": ("agent", True),
    "نحو": ("domain", "linguistic"),
    "ܪܘ": ("tradition", "ancientSong"),
    "ح": ("domain", "animal"),
    "نب": ("domain", "plant"),
    "ط": ("domain", "bird"),
    "أ. م": ("cuneiform", True),
    "ص": ("phoneticChange", True),
    "مثله": ("sameMeaningAsPrevious", True),
    # (ج) inside parentheses indicates plural nature; we treat as attribute, not forms
    "ج": ("pluralIndicator", True),
}


def normalize_text(s: str) -> str:
    # Normalize dashes, remove tatweel, collapse spaces
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = RE_TATWEEL.sub("", s)
    s = s.strip()
    s = RE_MULTISPACE.sub(" ", s)
    return s


def is_subentry_line(text: str) -> bool:
    return bool(RE_DASH_START.match(text))


def strip_subentry_marker(text: str) -> str:
    return RE_DASH_START.sub("", text, count=1).strip()


def is_list_item(paragraph) -> bool:
    """Detect if a docx paragraph is a list item (bulleted/numbered)."""
    try:
        p = paragraph._p  # lxml element
        pPr = p.pPr
        return pPr is not None and pPr.numPr is not None
    except Exception:
        return False


def extract_plurals(text: str) -> Tuple[List[str], str]:
    """Extract plural forms following Arabic 'ج' and return (plurals, remaining_text)."""
    plurals: List[str] = []
    # Pattern: ج followed by Syriac forms and separators. Keep it conservative to Syriac block.
    pattern = re.compile(fr"(^|[\s؛،])ج\s*([{SYRIAC_RANGE}\s،,;/؛/]+)")
    m = pattern.search(text)
    if not m:
        return plurals, text

    forms_str = m.group(2)
    # Split on common separators
    raw_forms = re.split(r"[،,;/؛]", forms_str)
    for f in raw_forms:
        f = f.strip()
        if re.search(fr"[{SYRIAC_RANGE}]", f):
            # Clean repeated spaces
            f = RE_MULTISPACE.sub(" ", f)
            if f:
                plurals.append(f)

    # Remove the matched plural segment from text
    start, end = m.span()
    text = (text[:start] + text[end:]).strip()
    return plurals, text


def extract_ipa(text: str) -> Tuple[Optional[str], str]:
    """Extract IPA/transliteration. Prefer /.../ or [...] then fallback to Latin/IPA run."""
    # /.../
    m = re.search(r"/\s*([^/]+?)\s*/", text)
    if m:
        ipa = m.group(1).strip()
        text = (text[:m.start()] + text[m.end():]).strip()
        return ipa, text

    # [...] (but avoid Syriac-only content)
    m = re.search(r"\[\s*([^\]]+?)\s*\]", text)
    if m and re.search(fr"[{IPA_LATIN_RANGE}]", m.group(1)):
        ipa = m.group(1).strip()
        text = (text[:m.start()] + text[m.end():]).strip()
        return ipa, text

    # Fallback: longest Latin/IPA run
    latin_pattern = re.compile(fr"([{IPA_LATIN_RANGE}][{IPA_LATIN_RANGE}\s\.'ːˈˌ-]+)")
    m = latin_pattern.search(text)
    if m:
        ipa = m.group(1).strip()
        text = (text[:m.start()] + text[m.end():]).strip()
        return ipa, text

    return None, text


def extract_pos(text: str) -> Tuple[Optional[str], str]:
    # POS tokens in Arabic; match as standalone tokens
    pos_tokens = "اسم|فعل|صفة|حال|حرف|ضمير|عدد|ظرف|مصدر"
    pos_re = re.compile(fr"(?<!\S)({pos_tokens})(?!\S)")
    m = pos_re.search(text)
    if m:
        pos = m.group(1)
        start, end = m.span()
        text = (text[:start] + text[end:]).strip()
        return pos, text
    return None, text


def extract_lemma(text: str) -> Tuple[Optional[str], str]:
    m = RE_SYRIAC_RUN.search(text)
    if m:
        lemma = RE_MULTISPACE.sub(" ", m.group(1).strip())
        start, end = m.span()
        text = (text[:start] + text[end:]).strip()
        return lemma, text
    return None, text


def extract_notes(text: str) -> Tuple[List[str], str]:
    notes: List[str] = []
    # Capture (...) or [...] or «...» segments. We'll remove them.
    # Avoid matching nested brackets greedily.
    pattern = re.compile(r"[\(\[«]\s*([^\)\]»]+?)\s*[\)\]»]")
    while True:
        m = pattern.search(text)
        if not m:
            break
        note = m.group(1).strip()
        if note:
            notes.append(note)
        text = (text[:m.start()] + text[m.end():]).strip()
    return notes, text


def split_primary_senses(text: str) -> List[str]:
    # Split by '/', Arabic semicolon '؛', or normal ';'
    # Avoid splitting when slashes may appear inside transliteration (rare in Arabic gloss context)
    # We'll do a simple split then re-trim.
    parts = re.split(r"[\/؛;]", text)
    senses: List[str] = []
    for p in parts:
        p = p.strip()
        if p and RE_ARABIC_CHAR.search(p):
            senses.append(p)
    return senses


def split_synonyms(sense: str) -> Tuple[str, List[str]]:
    # Synonyms separated by '.' but not if trailing period or numeric abbreviation.
    # We'll split then filter.
    raw = [seg.strip() for seg in sense.split('.') if seg.strip()]
    if not raw:
        return sense, []
    if len(raw) == 1:
        return raw[0], []
    # First segment main gloss, rest synonyms
    main = raw[0]
    synonyms = raw[1:]
    return main, synonyms


def build_senses(gloss_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    primary = split_primary_senses(gloss_text)
    senses: List[Dict[str, Any]] = []
    linear: List[str] = []
    for p in primary:
        main, syns = split_synonyms(p)
        senses.append({"gloss": main, "synonyms": syns})
        linear.append(main)
        linear.extend(syns)
    return senses, linear


def extract_parenthetical_markers(notes: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    attributes: Dict[str, Any] = {}
    remaining_notes: List[str] = []
    for n in notes:
        token = n.strip()
        if token in PAREN_MARKERS:
            key, val = PAREN_MARKERS[token]
            if key == "domain":
                attributes.setdefault("domain", [])
                if val not in attributes["domain"]:
                    attributes["domain"].append(val)
            else:
                attributes[key] = val
        else:
            remaining_notes.append(n)
    return attributes, remaining_notes


def parse_entry_text(raw_text: str, *, infer_phrase_type: bool = True) -> Dict[str, Any]:
    text = normalize_text(raw_text)

    # Foreign marker '*' directly before Syriac lemma
    foreign_flag = False
    if text.startswith('*'):
        foreign_flag = True
        text = text.lstrip('*').lstrip()

    plurals, text = extract_plurals(text)
    ipa, text = extract_ipa(text)
    pos, text = extract_pos(text)
    lemma, text = extract_lemma(text)
    notes, text = extract_notes(text)

    # Remaining text assumed to be gloss text (Arabic)
    gloss_text = text.strip()
    senses: List[Dict[str, Any]] = []
    linear_glosses: List[str] = []
    if gloss_text:
        senses, linear_glosses = build_senses(gloss_text)

    attributes, notes = extract_parenthetical_markers(notes)
    if foreign_flag:
        attributes["foreign"] = True

    entry: Dict[str, Any] = {
        "id": None,
        "lemma": lemma,
        "ipa": ipa,
        "pos": pos,
        "plurals": plurals,
        "glosses": linear_glosses,  # backward compatibility
        "senses": senses,
        "notes": notes,
        "attributes": attributes,
        "subentries": [],
        "metadata": {},
    }

    if infer_phrase_type and lemma is None and linear_glosses:
        entry["metadata"]["type"] = "phrase"

    return entry


def parse_document(input_path: str) -> List[Dict[str, Any]]:
    doc = Document(input_path)
    entries: List[Dict[str, Any]] = []
    last_entry: Optional[Dict[str, Any]] = None

    prev_non_sub_entry: Optional[Dict[str, Any]] = None
    for para in doc.paragraphs:
        text = normalize_text(para.text)
        if not text:
            continue

        # Subentry lines take precedence
        if is_subentry_line(text):
            clean = strip_subentry_marker(text)
            if last_entry is None:
                # No parent to attach; skip gracefully
                continue
            sub = parse_entry_text(clean)
            last_entry.setdefault("subentries", []).append(sub)
            continue

        # New top-level entry must be a list item (bulleted/numbered)
        if is_list_item(para):
            entry = parse_entry_text(text)
            # Inheritance if attribute sameMeaningAsPrevious present
            if entry.get("attributes", {}).get("sameMeaningAsPrevious") and prev_non_sub_entry:
                if not entry.get("senses"):
                    # Copy senses/glosses
                    entry["senses"] = prev_non_sub_entry.get("senses", [])
                    entry["glosses"] = prev_non_sub_entry.get("glosses", [])
                    entry.setdefault("metadata", {})["inheritedFrom"] = prev_non_sub_entry.get("id")
            entries.append(entry)
            last_entry = entry
            prev_non_sub_entry = entry
            continue

        # Otherwise ignore non-list paragraphs (headers, adornments, etc.)
        # You could log or collect warnings here if desired.

    # Assign IDs and add minimal metadata
    base = os.path.splitext(os.path.basename(input_path))[0]
    for i, entry in enumerate(entries, start=1):
        entry_id = f"{base}:{i:04d}"
        entry["id"] = entry_id
        entry["metadata"].update({"source": os.path.basename(input_path), "index": i})
        # subentries
        subs = entry.get("subentries", [])
        for j, sub in enumerate(subs, start=1):
            sub_id = f"{entry_id}-{j}"
            sub["id"] = sub_id
            sub.setdefault("metadata", {}).update({"source": os.path.basename(input_path), "parent": entry_id, "subindex": j})
            # Inherit (مثله) for subentry if flagged
            if sub.get("attributes", {}).get("sameMeaningAsPrevious"):
                if not sub.get("senses") and entry.get("senses"):
                    sub["senses"] = entry.get("senses")
                    sub["glosses"] = entry.get("glosses")
                    sub.setdefault("metadata", {})["inheritedFrom"] = entry_id

    return entries


def to_xml(entries: List[Dict[str, Any]]) -> str:
    import xml.etree.ElementTree as ET

    def add_text(parent: ET.Element, tag: str, text: Optional[str], attrib: Optional[Dict[str, str]] = None):
        if text is None or text == "":
            return
        el = ET.SubElement(parent, tag, attrib or {})
        el.text = text

    root = ET.Element("entries")
    for e in entries:
        e_el = ET.SubElement(root, "entry", {"id": e.get("id") or ""})
        add_text(e_el, "lemma", e.get("lemma"), {"lang": "syc"} if e.get("lemma") else None)
        add_text(e_el, "ipa", e.get("ipa"))
        if e.get("pos"):
            add_text(e_el, "pos", e.get("pos"), {"lang": "ar"})
        # attributes
        attrs = e.get("attributes") or {}
        if attrs:
            attrs_el = ET.SubElement(e_el, "attributes")
            for k, v in attrs.items():
                if isinstance(v, list):
                    list_el = ET.SubElement(attrs_el, k)
                    for item in v:
                        add_text(list_el, "item", str(item))
                else:
                    add_text(attrs_el, k, str(v))
        # plurals
        plurals = e.get("plurals") or []
        if plurals:
            pls_el = ET.SubElement(e_el, "plurals")
            for p in plurals:
                add_text(pls_el, "form", p)
        # senses
        senses = e.get("senses") or []
        if senses:
            sens_el = ET.SubElement(e_el, "senses")
            for s in senses:
                s_el = ET.SubElement(sens_el, "sense")
                add_text(s_el, "gloss", s.get("gloss"), {"lang": "ar"})
                syns = s.get("synonyms") or []
                if syns:
                    syn_el = ET.SubElement(s_el, "synonyms")
                    for syn in syns:
                        add_text(syn_el, "syn", syn, {"lang": "ar"})
        # glosses
        glosses = e.get("glosses") or []
        if glosses:
            gl_el = ET.SubElement(e_el, "glosses")
            for g in glosses:
                add_text(gl_el, "gloss", g, {"lang": "ar"})
        # notes
        notes = e.get("notes") or []
        if notes:
            n_el = ET.SubElement(e_el, "notes")
            for n in notes:
                add_text(n_el, "note", n)
        # subentries
        subs = e.get("subentries") or []
        if subs:
            se_el = ET.SubElement(e_el, "subentries")
            for s in subs:
                s_el = ET.SubElement(se_el, "entry", {"id": s.get("id") or ""})
                add_text(s_el, "lemma", s.get("lemma"), {"lang": "syc"} if s.get("lemma") else None)
                add_text(s_el, "ipa", s.get("ipa"))
                if s.get("pos"):
                    add_text(s_el, "pos", s.get("pos"), {"lang": "ar"})
                # attributes for sub
                s_attrs = s.get("attributes") or {}
                if s_attrs:
                    sattrs_el = ET.SubElement(s_el, "attributes")
                    for k, v in s_attrs.items():
                        if isinstance(v, list):
                            list_el = ET.SubElement(sattrs_el, k)
                            for item in v:
                                add_text(list_el, "item", str(item))
                        else:
                            add_text(sattrs_el, k, str(v))
                subs_pl = s.get("plurals") or []
                if subs_pl:
                    spl_el = ET.SubElement(s_el, "plurals")
                    for p in subs_pl:
                        add_text(spl_el, "form", p)
                subs_sens = s.get("senses") or []
                if subs_sens:
                    ssens_el = ET.SubElement(s_el, "senses")
                    for ss in subs_sens:
                        ss_el = ET.SubElement(ssens_el, "sense")
                        add_text(ss_el, "gloss", ss.get("gloss"), {"lang": "ar"})
                        syns = ss.get("synonyms") or []
                        if syns:
                            syn_el = ET.SubElement(ss_el, "synonyms")
                            for syn in syns:
                                add_text(syn_el, "syn", syn, {"lang": "ar"})
                subs_gl = s.get("glosses") or []
                if subs_gl:
                    sgl_el = ET.SubElement(s_el, "glosses")
                    for g in subs_gl:
                        add_text(sgl_el, "gloss", g, {"lang": "ar"})
                subs_nt = s.get("notes") or []
                if subs_nt:
                    snt_el = ET.SubElement(s_el, "notes")
                    for n in subs_nt:
                        add_text(snt_el, "note", n)
                # metadata for subentry (optional)
                if s.get("metadata"):
                    md_el = ET.SubElement(s_el, "metadata")
                    for k, v in s["metadata"].items():
                        add_text(md_el, k, str(v))
        # metadata for entry
        if e.get("metadata"):
            md_el = ET.SubElement(e_el, "metadata")
            for k, v in e["metadata"].items():
                add_text(md_el, k, str(v))

    # Pretty-print
    def indent(elem, level=0):
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            for child in elem:
                indent(child, level + 1)
            if not child.tail or not child.tail.strip():  # type: ignore
                child.tail = i
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

    indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Convert Word .docm Assyrian/Arabic dictionary entries to JSON or XML.")
    ap.add_argument("--input", required=True, help="Path to input .docm file")
    ap.add_argument("--format", "-f", choices=["json", "xml"], default="json", help="Output format (json or xml)")
    ap.add_argument("--output", "-o", required=False, help="Path to output file (default: input base with .json/.xml)")
    args = ap.parse_args(argv)

    input_path = args.input
    if not os.path.isfile(input_path):
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 2
    if not input_path.lower().endswith((".docx", ".docm")):
        print("Warning: Input file extension is not .docx/.docm; attempting to parse anyway.", file=sys.stderr)

    entries = parse_document(input_path)

    # Determine output path
    if args.output:
        out_path = args.output
    else:
        base, _ = os.path.splitext(input_path)
        out_ext = ".json" if args.format == "json" else ".xml"
        out_path = base + out_ext

    # Write output
    if args.format == "json":
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    else:
        xml_str = to_xml(entries)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(xml_str)

    print(f"Wrote {args.format.upper()} to: {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
