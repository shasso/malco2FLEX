# Assyrian/Arabic Dictionary Parser

This tool converts bulleted entries from a Word `.docm` (or `.docx`) file into structured JSON or XML, extracting lemma data, plural forms, senses, synonyms, and linguistic attributes.

Core parsing rules:

- Bulleted paragraph => top‑level entry.
- Line starting with `-` / `–` / `—` => subentry of preceding entry.
- Syriac lemma = first Syriac-script run (phrase allowed). Leading `*` marks a foreign word.
- Plurals: standalone Arabic letter `ج` followed by Syriac forms (split by `، , / ؛ ;`).
- IPA: text inside `/.../` or `[...]`, else longest Latin/IPA run.
- Senses: first split by `/` or `؛` or `;`; within each sense, `.` separates near-synonymous glosses (first is the main gloss, rest synonyms).
- Parenthetical markers (go to attributes, not notes):
  - `(ث)` feminine, `(ذ)` masculine, `(ذ.ث)` common gender
  - `(فا)` agent/doer, `(مثله)` inherit previous glosses, `(نحو)` domain=linguistic
  - `(ܪܘ)` tradition=ancientSong, `(ح)` domain=animal, `(نب)` domain=plant, `(ط)` domain=bird
  - `(أ. م)` cuneiform attestation, `(ص)` phonetic change, `(ج)` plural indicator (distinct from plural forms segment)
- Unrecognized parentheses become notes.
- `(مثله)` with no explicit gloss text inherits senses/glosses from previous entry (or parent for subentry) and records `metadata.inheritedFrom`.

```json
{
  "id": "باب الواو:0001",
  "lemma": "<SYRIAC>",
  "ipa": "<LATIN_OR_IPA>",
  "pos": "اسم", 
  "plurals": ["<SYRIAC_PLURAL>"],
  "senses": [
    { "gloss": "Arabic gloss", "synonyms": ["near synonym"] }
  ],
  "glosses": ["Arabic gloss", "near synonym"],
  "notes": ["free-form notes"],
  "attributes": {
    "gender": "m",
    "foreign": true,
    "domain": ["linguistic", "animal"],
    "agent": true,
    "phoneticChange": true,
    "cuneiform": true,
    "sameMeaningAsPrevious": true,
    "pluralIndicator": true,
    "tradition": "ancientSong"
  },
  "subentries": [],
  "metadata": { "source": "file.docm", "index": 1, "inheritedFrom": "باب الواو:0000" }
}
### XML structure (excerpt)

```xml
<entry id="باب الواو:0001">
  <lemma lang="syc">...</lemma>
  <ipa>...</ipa>
  <pos lang="ar">اسم</pos>
  <attributes>
    <gender>m</gender>
    <foreign>true</foreign>
    <domain>
      <item>linguistic</item>
      <item>animal</item>
    </domain>
    <agent>true</agent>
  </attributes>
  <plurals>
    <form>...</form>
  </plurals>
  <senses>
    <sense>
      <gloss lang="ar">...</gloss>
      <synonyms>
        <syn lang="ar">...</syn>
      </synonyms>
    </sense>
  </senses>
  <glosses>
    <gloss lang="ar">...</gloss>
  </glosses>
  <notes>
    <note>...</note>
  </notes>
  <subentries>...</subentries>
  <metadata>
    <source>...</source>
    <index>1</index>
  </metadata>
</entry>
```

## Limitations

- Heuristics may mis-split if periods are part of abbreviations not listed.
- Inheritance `(مثله)` only looks one entry back (top-level) or parent (for subentries).
- Parenthetical token length capped (<=12 chars) to avoid greedy capture.
- Only a predefined marker list is recognized; extend `PAREN_MARKERS` in `dict_parser.py` to add more.

## Project Structure

```text
Malco2FLEX/
├── data/                    # Input Word documents
│   ├── باب الواو.docm      # Main dictionary source (macro-enabled)
│   ├── باب الواو.docx      # Dictionary source (standard format)  
│   └── باب الواو_m.docm    # Modified dictionary source
├── scripts/                 # Python scripts
│   └── dict_parser.py       # Main dictionary parser
├── images/                  # Documentation and reference files
│   ├── abbreviations.docx   # Abbreviation reference
│   └── combined.odg         # Combined documentation
├── .venv/                   # Python virtual environment (ignored)
├── requirements.txt         # Python dependencies
├── README.md               # This file
├── .gitignore              # Git ignore rules
└── waw.json                # Example output (ignored)
```

## Development Setup

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd Malco2FLEX
   ```

2. **Set up Python environment:**

   ```bash
   python -m venv .venv
   # On Windows:
   .venv\Scripts\activate
   # On macOS/Linux:
   source .venv/bin/activate
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Run the parser:**

   ```bash
   python scripts/dict_parser.py --input "data/باب الواو.docm" --format json
   ```

## Extending

- Add new domain markers by inserting into `PAREN_MARKERS` dict.
- Modify sense separation logic in `split_primary_senses()` if additional delimiters arise.
- Add transliteration normalization inside `extract_ipa()` if required for consistency.
