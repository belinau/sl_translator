# COBISS Bibliography Structure: Actual Data & Parsing

This document describes what the COBISS export actually contains, how it's currently parsed and classified, and what it can and cannot structurally provide for Phase 4 (bilingual title encoding).

---

## 1. What COBISS Export Actually Contains

### 1.1 Sample Entry Categories

The bibliography_belina.txt export contains several work types:

#### **Self-authored works (Belina as author)**

Entry #1 (lines 14–16 in bibliography_belina.txt):
```
BELINA, Urban. Brez dotikov. Vpogled : revija za književnost. jun.-dec.
2006, letn. 2, št. 3, str. 57-60, portret. ISSN 1854-3790. [COBISS.SI-ID
245447168]
```
- **Fields present:** author name, title, journal name, date, volume, issue, pages, ISSN, COBISS ID
- **Language:** Slovenian only
- **Role:** author explicitly listed
- **Bilingual marker:** none (= is absent)

#### **Translated work (foreign author, Belina NOT listed, but in his bibliography)**

Entry #7 (lines 56–58):
```
WHITE, Patrick. Drevo človeka. Dialogi. 2010, letn. 46, št. 11/12, str.
46-58. ISSN 0012-2068. [COBISS.SI-ID 267587328]
```
- **Fields present:** original author (Patrick White), Slovenian-translated title ("Drevo človeka"), journal, date, volume, issue, pages, ISSN
- **Language:** Only Slovenian side shown (the translation)
- **Role:** Belina is NOT listed as translator in agent block; inclusion in his bibliography means he translated it
- **Bilingual marker:** none (= is absent)
- **Original language:** NOT in COBISS data; must be inferred from author + external knowledge

#### **Bilingual work with `=` separator (self-authored, SL/EN bilingual)**

Entry #14 (lines 100–102):
```
SRDIĆ, Srđan. Medicine = Medicine. I.d.i.o.t. dec. 2013, [mednarodna
št.] balkan, str. 114-121. ISSN 1855-7481. [COBISS.SI-ID 304396288]
```
- **Fields present:** original author, SL title = EN title separated by `=`, journal, date, ISSN
- **Language:** Both Slovenian and English
- **Role:** This is an article BY Srđan Srdić (Serbian author), not translated by Belina. The title just happens to be the same in both languages
- **Bilingual marker:** `=` separator present
- **Interpretation:** The `=` does NOT necessarily indicate translation; it indicates a bilingual source. The semantics depend on the author and context.

#### **Translated work with `=` separator (foreign work with bilingual title in article)**

Entry #25 (lines 161–163):
```
MATOVIĆ, Petar. Skretničar = Kretničar ; Sa granice = Z meje.
I.d.i.o.t. dec. 2014, idba2 = idiot balkan 2, str. 76-77. ISSN 1855-7481.
[COBISS.SI-ID 304428032]
```
- **Fields present:** author (Petar Matović), two pairs of title translations: `Skretničar = Kretničar` and `Sa granice = Z meje`, journal, date, ISSN
- **Language:** Mixed Serbian/Croatian and Slovenian
- **Bilingual pattern:** Multiple `=` pairs suggest the original was Serbian/Croatian (`Skretničar`, `Sa granice`) and Slovenian is the translation side (`Kretničar`, `Z meje`)
- **Role:** Belina is not listed; inclusion in his bibliography means he was translator
- **Original language:** Inferred from context (Serbian/Croatian author, -ičar/-ar/-nje patterns)

#### **Exhibition catalogue with `=` separator (bilingual container)**

Entry #28 (lines 180–186):
```
BATIČ, Stojan (artist), KOMELJ, Miklavž, PERNE, Nika, SAVENC, Barbara
(editor, author), SKOČIR, Marija (editor, author). Človek in mit :
retrospektivna razstava = The man and the myth : retrospective exhibition.
Ljubljana: Muzej in galerije mesta Ljubljane, Galerija Jakopič: = Museum
and Galleries of Ljubljana, Jakopič Gallery, 2015. 262 str., ilustr. ISBN
978-961-93064-4-4. [COBISS.SI-ID 279268096]
```
- **Fields present:** artists, editors, authors, bilingual title with subtitle (`Slovenian title : SL subtitle = English title : EN subtitle`), publisher (bilingual: `SL Publisher: = EN Publisher:`), year, ISBN
- **Language:** Slovenian and English
- **Bilingual pattern:** Clear `=` separator for title AND publisher name
- **Role:** Multiple roles: artist, editor, author. Belina listed as co-editor.
- **Bilingual semantic:** This is truly a bilingual catalogue—two editions in two languages

#### **Translated monograph (non-bilingual, just SL translation)**

Entry #9 (lines 68–70):
```
WHYTE, Christopher. Gejevski Dekameron. Ljubljana: Škuc, 2011. 348 str.
Lambda, 94. ISBN 978-961-6751-50-6. [COBISS.SI-ID 258318080]
```
- **Fields present:** original author, Slovenian title (translation), publisher city, publisher, year, extent, series, ISBN, COBISS ID
- **Language:** Slovenian only
- **Bilingual marker:** none
- **Original work:** Christopher Whyte's *Decameron* (or similar—original English title not in COBISS)
- **Role:** Belina is not listed; inclusion means he translated it
- **Missing data:** Original language (English inferred from author name), original title (not in COBISS)

### 1.2 Language combinations in actual export

Scanning the export:
- **Slovenian only** (self-authored or translated works where only SL title is recorded): entries #1, #3, #4, #5, #6, #7, #8, #9, #10, #11, etc.
- **Slovenian = English** (bilingual titles via `=` separator): entries #14, #28, #29, #38, #39, #41, #42, #43, #45, etc.
- **SL title = Variant/Transliterated/EN form** (multiple `=` per title for multi-title articles): entries #20, #25, #26, #27, etc.
- **SL = Slovenian; EN = English**: most bilingual entries follow this pattern for exhibition catalogues and dual-language journals

**Critical observation:** COBISS does NOT distinguish which side of the `=` is the original language. It only records which languages are present in the work as printed/published.

---

## 2. CobissEntry Dataclass Structure

File: `translate_core/cobiss_parser.py`, lines 36–61.

```python
@dataclass
class CobissEntry:
    entry_number: int
    raw_text: str
    agents: list[CobissAgent] = field(default_factory=list)
    title: str = ""                  # Main title (SL side if bilingual)
    title_en: str = ""               # English side if bilingual (= separated)
    subtitle: str = ""               # After first :
    edition: str = ""
    publisher_city: str = ""
    publisher: str = ""
    year: Optional[int] = None
    extent: str = ""
    series: str = ""
    isbn: list[str] = field(default_factory=list)
    issn: str = ""
    cobiss_id: str = ""
    urls: list[str] = field(default_factory=list)
    awards: list[str] = field(default_factory=list)
    journal_name: str = ""
    journal_volume: str = ""
    journal_issue: str = ""
    pages: str = ""
```

### Field semantics:

| Field | Semantic Meaning | Source | Language Preservation |
|-------|------------------|--------|----------------------|
| `title` | Main title (parser comments: "SL side if bilingual") | Before ` = ` if separator present; entire title otherwise | Slovenian if bilingual; original language (unknown) if monolingual translation |
| `title_en` | "English side if bilingual (= separated)" | After ` = ` if separator present; empty otherwise | English or other language; NOT guaranteed to be English |
| `agents` | Parsed author/translator/editor list | Agent block at start of entry | Preserved as-is; translator role is ANNOTATED if present |
| `publisher` | Publisher name; may contain bilingual `: =` marker | Extracted from publisher block | Bilingual but not split; ` : = ` separator left in raw value |
| `year` | Publication year (inferred from entry or fallback year marker) | From publisher block or section year | Single year for entry (ambiguous if bilingual editions differ) |
| `issn`, `isbn` | Standard identifiers | Extracted regex-based | Preserved as-is |
| `cobiss_id` | COBISS system ID (unique) | From trailing `[COBISS.SI-ID ...]` | Unique per record; does not distinguish SL/EN editions |

### What CobissEntry does NOT capture:

1. **Original-language title** (`title_orig`): Not present in COBISS. For entry #7 (Patrick White's work), we parse only "Drevo človeka" (the Slovenian translation), not the English original.

2. **Original language code** (`orig_lang`): COBISS records do not include language metadata. The original language must be inferred from author name, publisher, or external knowledge.

3. **Translation language** (`translation_lang`): Implicitly Slovenian (since this is Belina's SL bibliography), but not explicitly encoded.

4. **Role clarity**: When an agent is NOT listed as translator, we must infer from context (presence in Belina's bibliography) that Belina is the translator. The `agents` list captures all listed agents with their roles; absence of Belina's name with `(translator)` role does not mean he did NOT translate.

5. **Bilingual publishing info**: Entry #28 has bilingual publisher (`Muzej in galerije mesta Ljubljane, Galerija Jakopič: = Museum and Galleries of Ljubljana, Jakopič Gallery`), but the parser leaves the `: = ` intact in `publisher` rather than splitting it into `publisher_sl` / `publisher_en`.

6. **Edition/year disambiguation**: When a work has both a SL and EN edition (entry #28 is exhibited, then published in bilingual form), COBISS records them as one entry. The `year` field is ambiguous: which edition's year is it?

---

## 3. Classification in cobiss_classifier.py

File: `translate_core/cobiss_classifier.py`, lines 126–193.

Function: `classify_entry(entry: CobissEntry) -> tuple[str | None, str | None]`

Returns: `(project_type, belina_role)` where:
- `project_type` ∈ `{"book_translation", "article_translation", "festival_programme", "exhibition_catalogue", "book", "magazine_article", "journal_article", "book_chapter", ...}` or `None` (unclassifiable)
- `belina_role` ∈ `{"author", "translator", "editor", ...}` or `None`

### Classification logic:

1. **Find Belina among agents** (lines 134–147):
   - Search `entry.agents` for Belina by fuzzy name match (lines 71–78).
   - If found: record position (`belina_is_first`), primary role from first role in the agent's roles list.

2. **Determine translator status** (lines 149–158):
   - `belina_is_translator = True` if:
     - Belina's agent record has `"translator"` in roles, OR
     - ANY agent in the entry has `"translator"` in roles

3. **Branch 1: Belina is first author with NO translator role** (lines 161–173):
   - Return `CITED_TYPE` (his own work): `book`, `magazine_article`, `journal_article`, etc.
   - Calls `_is_journal_article()` (ISSN or journal_name present), `_is_festival_programme()`, `_is_exhibition_catalogue()`

4. **Branch 2: All other cases (Belina not present OR is translator)** (lines 175–193):
   - Determine role output:
     - If Belina is absent → `"translator"` (implicit)
     - If Belina agent has `"translator"` role → `"translator"`
     - If Belina agent has `"editor"` role → `"editor"`
     - Else → use first role from Belina's agent
   - Return `CONTAINER_TYPE` (translated work): `book_translation`, `article_translation`, `festival_programme`, `exhibition_catalogue`

### What the classifier marks:

- **`belina_role` (second return value):** The role Belina played relative to the work (author, translator, editor, etc.). This is wired as the edge type in ingest (e.g., `written_by` for author, `translated_by` for translator).
- **`project_type` (first return value):** The work type (not the translation direction).

### What the classifier does NOT provide:

- **Original language** (`orig_lang`): Not determined.
- **Translation direction** (original→target language pair): Not determined. The classifier has no way to know whether Belina translated from English to Slovenian or Slovenian to English. The role is only `"translator"`, with no direction attached.
- **Bilingual title handling**: The classifier reads `entry.title` and `entry.title_en` but does not map them to `title_orig` / `title_translation` / `orig_lang` / `translation_lang`. Those are not populated.

---

## 4. What scripts/ingest_personal_bibliography.py Currently Produces

File: `scripts/ingest_personal_bibliography.py`, lines 91–305.

Function: `ingest_bibliography()` → creates KG nodes and edges from parsed COBISS entries.

### Records created:

| Record Type | When | KG Node Type | Edge to Translator |
|-------------|------|-----------------|-------------------|
| **Container** (book_translation, article_translation, etc.) | `ptype in CONTAINER_TYPES` | `source_text` with `project_type=ptype` | `translated_by → agent:urban-belina` |
| **Cited work** (book, magazine_article, journal_article) | `ptype in CITED_TYPES` | `source_text` with `project_type=ptype` | None (Belina is author, not translator) |
| **Agent** | For every agent in entry | `agent` | `written_by → source_text` (for all agents) |
| **Institution** | If `entry.publisher` is non-empty | `institution` | `published_by → source_text` |

### What the ingester populates on source_text nodes:

Lines 174–215:

```python
title_sl = entry.title
if entry.subtitle and entry.subtitle.lower() not in (entry.title or "").lower():
    title_sl = f"{entry.title}: {entry.subtitle}"
title_en = entry.title_en

kwargs = {}
if title_en:
    kwargs["title_en"] = title_en
if title_sl:
    kwargs["title_sl"] = title_sl
if entry.year:
    kwargs["year"] = entry.year
if entry.extent:
    kwargs["extent"] = entry.extent
if entry.series:
    kwargs["series"] = entry.series
if entry.edition:
    kwargs["edition"] = entry.edition
if entry.journal_name:
    kwargs["journal_name"] = entry.journal_name
# ... etc.

node_id = kg.add_source_text_node(
    text_id=source_id,
    title=title_sl or title_en or f"Entry #{entry.entry_number}",
    project_type=ptype,
    **kwargs,
)
```

### Bilingual encoding used:

- **`title_sl`** ← `entry.title` (the "main title" which parser says is "SL side if bilingual")
- **`title_en`** ← `entry.title_en` (the "English side if bilingual = separated")

### What is NOT populated:

- **`title_orig`**: Never populated. The original-language title is not in COBISS.
- **`orig_lang`**: Never populated.
- **`translation_lang`**: Never populated.
- **`sl_published_by` edge**: TODO comment on lines 269–271 says Phase 4 will handle this.

---

## 5. Bilingual Title Convention in COBISS

The parser comment (line 42–43 of cobiss_parser.py) says:

```python
title: str = ""                  # Main title (SL side if bilingual)
title_en: str = ""               # English side if bilingual (= separated)
```

### Actual COBISS convention observed:

The `=` separator is used to separate two **language sides** of a work's title. The COBISS convention does NOT explicitly specify which is "original" and which is "translation." Instead:

- **Left side (before `=`):** The Slovenian form or the first listed language
- **Right side (after `=`):** The English form or the second listed language

### Examples from entries:

**Entry #28 (exhibition catalogue):**
```
Človek in mit : retrospektivna razstava = The man and the myth : retrospective exhibition
```
- Left: SL title with subtitle
- Right: EN title with subtitle
- **Semantic:** This is a bilingual catalogue. Both editions were created. Which is "original"? The COBISS data does NOT say. (From context: this is an exhibition, and both SL and EN editions were published.)

**Entry #25 (translated article):**
```
Skretničar = Kretničar ; Sa granice = Z meje
```
- Pairs: Serbian/Croatian original on left, Slovenian translation on right
- **Semantic:** This is a translation. Left is the original author's work in Serbian/Croatian; right is the Slovenian translation.
- **But COBISS does not encode "this is a translation."** The only signal is that Belina's bibliography includes it.

**Entry #14 (self-authored bilingual article):**
```
Medicine = Medicine
```
- Both sides identical
- **Semantic:** This is not a translation; the author published in both languages simultaneously.

### Critical finding:

**COBISS does NOT carry semantic information about translation direction.** It only records:
1. The title(s) as they appear in the published work
2. Which language(s) the work was published in (via the `=` separator and language context)

For a translated work where Belina is the translator:
- The work appears in Belina's bibliography because he translated it
- The COBISS entry records the Slovenian title (translation) and possibly an English or original-language variant (if the work was published bilingually)
- But COBISS does NOT record whether Belina translated FROM English TO Slovenian or FROM Slovenian TO English, or which side of the `=` is the "original"

---

## 6. Review Queue Paths

Two paths found:

### data/extraction_review.json

- **Writer:** `translate_core/kg_ingest_entities.py`, line 1032–1035, function `write_to_kg()`
- **Trigger:** Records with `tier == ConfidenceTier.REVIEW` (medium confidence)
- **Record shape:** Same as extraction records: `{"kind": "translated_work" | "agent_person" | "institution" | "cited_work" | ..., "payload": {...}, "signals": {...}, "source": {...}, "confidence": float, "reason_codes": [...], "tier": "review"}`
- **Also written by:** `translate_core/citation_collector.py` (line 374), `run_entity_extraction.py`
- **Used by:** `translate_core/entity_extraction/vl_typed_extractor.py` (line 262)

### data/kg_review.json

- **Writer:** `kg_editor_ui.py` (via `_drop_kg_review()`, lines not in this run but inferred from grep)
- **Trigger:** Manual curation in the Streamlit UI; records flagged by the user for review
- **Record shape:** Simplified record with `{"id": "source:...", "type": "source_text", "reason": "...", "title": "...", "title_en": "...", "title_sl": "...", "year": int, "project_type": "...", "name": null, "role": null, "kind": null}`
- **Status:** 169.2 KB on disk, ~1000+ records

### Current usage by COBISS ingest:

**`scripts/ingest_personal_bibliography.py` does NOT write to either review queue.**

The ingest script (lines 140–147) captures unclassified entries in a local `report["unclassified"]` list and writes them to `data/cobiss_unclassified_entries.json` (line 300). It does NOT route entries to `data/extraction_review.json` or `data/kg_review.json`.

**Implication:** COBISS records currently bypass the review queue entirely. They go directly to KG (if classifiable) or to a separate unclassified list.

---

## 7. Ontology §2.4.2: Bilingual Title Encoding

File: `ontology.md`, lines 155–172.

**Canonical form for source_text carrying both original and translation:**

```markdown
- `title_orig` — the original-language title
- `title_translation` — the title in the translation language
- `orig_lang`, `translation_lang` — two-letter language codes

For citation-typed records that surfaced in both languages of the TM:
- `title_en` and `title_sl` — both populated
- `slovenian_edition: {publisher, city, year, translator}` — populated
  when the SL edition differs from the original
```

### What this means:

1. **For containers (books Belina translated):**
   - Should carry `title_orig` (original-language title), `orig_lang`, `title_translation` (SL translation), `translation_lang = "sl"`
   - OR legacy alias: `title_sl` = translation, `title_en` = original (if original is English)

2. **For cited works (articles/chapters cited in books):**
   - If surfaced in both SL and EN in the TM: use `title_en` + `title_sl` + optional `slovenian_edition` dict
   - If original is neither EN nor SL: use canonical `title_orig` + `orig_lang` + `title_translation` + `translation_lang`

### What COBISS can provide:

For entry #25 (Matović article, SL translation of Serbian/Croatian):
- `title_sl` ← "Kretničar ; Z meje" (SL translation, from `entry.title`)
- `title_en` ← "" (no English version in COBISS)
- `title_orig` ← ??? (not in COBISS; would need to be "Skretničar ; Sa granice")
- `orig_lang` ← ??? (not in COBISS; would need to be "sr" or similar)

For entry #28 (bilingual exhibition catalogue):
- `title_sl` ← "Človek in mit : retrospektivna razstava"
- `title_en` ← "The man and the myth : retrospective exhibition"
- `title_orig` ← ??? (ambiguous: which was created first? Both are in COBISS.)
- `orig_lang` ← ??? (ambiguous: could be SL or EN)

### Structural gap:

**COBISS does NOT provide enough information to populate `title_orig` + `orig_lang` for any entry.** For translated works where only the SL title is recorded (entry #7), the original title is missing entirely. For bilingual entries (entry #28), COBISS does not indicate which side is the "original."

The ontology §2.4.2 canonical form (`title_orig`, `orig_lang`, `title_translation`, `translation_lang`) **cannot be populated from COBISS alone.**

---

## 8. Phase 4 Redesign: Actual Scope

### What Phase 4 must recognize:

COBISS is **NOT a bilingual translation corpora like TMX.** It is a **bibliographic export listing works the translator has worked on**, with optional bilingual titles when the published work was multilingual. 

The two are fundamentally different:
- **TMX:** Language-pair alignment data. Every segment has a source side and a target side, with direction explicitly encoded.
- **COBISS:** Bibliographic metadata. The `=` separator indicates "this work exists in both languages," not "language A → language B translation direction."

### What can be populated from COBISS structure alone:

For any COBISS entry:

1. **`title_sl`** ← `entry.title` (always Slovenian, since Belina's bibliography)
2. **`title_en`** ← `entry.title_en` (when `=` separator present; usually English but not guaranteed)
3. **`year`** ← `entry.year` (single year; may be ambiguous if SL and EN editions differ)
4. **`project_type`** ← classifier output (container vs cited type)
5. **`translated_by` edge** ← agent:urban-belina (when container type)
6. **`published_by` edge** ← institution extracted from `entry.publisher`

### What CANNOT be populated from COBISS alone:

1. **`title_orig`** ← Original-language title. Not in COBISS for translated works. For bilingual works (entry #28), ambiguous which side is "original."
   - **Exception:** For works where the original title is identical to SL title (e.g., entry #14: "Medicine = Medicine"), could infer `title_orig = title_sl`. But this is fragile.

2. **`orig_lang`** ← Original language code. Not in COBISS. Must be inferred from author name, publisher, or external knowledge.

3. **`translation_lang`** ← Always "sl" for Belina's work, but not encoded. Could be assumed.

4. **`sl_published_by` edge** ← Requires parsing the bilingual publisher field (e.g., entry #28: `"Muzej in galerije mesta Ljubljane, Galerija Jakopič: = Museum and Galleries of Ljubljana, Jakopič Gallery"`) into two separate institutions. Currently not done.

5. **Translation direction** ← Whether Belina translated FROM original TO SL or vice versa. COBISS has no signal for this.

### What Phase 4's actual scope should be:

**Phase 4 is NOT about detecting translation direction from raw text markers.** That framing conflates COBISS (bibliographic metadata) with TMX (bilingual content alignment).

**Phase 4 should:**

1. **Accept the legacy encoding (`title_sl` / `title_en`)** for bilingual COBISS entries and persist it as-is. This is what COBISS directly provides: two language sides of a published work.

2. **Populate `translated_by` edges correctly** (already done by the current ingest, line 251–252 of ingest_personal_bibliography.py).

3. **Handle bilingual publisher info** (sl_published_by edge): Parse the `: =` separator in `entry.publisher` to extract SL and EN institution names separately. Route records with ambiguous publisher info to the review queue.

4. **Identify unresolvable cases and route to curator review:**
   - Works where `title_en` is empty and no `title_orig` is available (translator must provide the original title).
   - Bilingual works (both SL and EN recorded) where the "original" side is ambiguous (curator must designate).
   - Works with unparseable bilingual publisher fields.

5. **Defer the canonical mapping** (`title_orig` / `orig_lang` / `title_translation` / `translation_lang`) to a separate curator-input or import pipeline. These fields cannot be reliably inferred from COBISS.

---

## Summary

| Question | Answer |
|----------|--------|
| **Review queue paths** | `data/extraction_review.json` (entity extraction confidence-based review), `data/kg_review.json` (manual curator review). COBISS ingest currently does NOT use these; unclassified entries go to `data/cobiss_unclassified_entries.json`. |
| **Does COBISS carry canonical encoding?** | NO. It carries legacy `title_sl` / `title_en` encoding only. Canonical `title_orig` / `orig_lang` / `title_translation` / `translation_lang` cannot be populated. |
| **Phase 4 scope** | (a) Ingest legacy bilingual titles (`title_sl`/`title_en`); (b) wire `translated_by` edges; (c) parse bilingual publisher info; (d) route unresolvable cases to curator review. Do NOT attempt translation direction detection. |

