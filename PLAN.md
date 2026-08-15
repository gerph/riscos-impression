# Impression document converter — staged build plan

This is the live build plan for `riscos-impression`. It is updated as work
progresses; see "Progress" below for current status.

## Context

`riscos-impression` decodes Impression documents (the DTP format used by
Computer Concepts' Impression applications on RISC OS) into a class-structured
object model, and renders that model out as:

* **OvProDDL** — the OvationPro DDL format the original TransIMP C tool
  produces. This is the reference/baseline output, since it's the one format
  whose correct behaviour we can already read straight out of existing,
  working C source (`c/frames`, `c/styles`, `c/colours`, `c/pxexp` in the
  sibling `riscos-source` repository).
* **PDF** — written natively (no external library), because PDF's
  page-plus-absolutely-positioned-content model matches Impression's own
  layout model closely, and gives the best-fidelity target of the three new
  formats.
* **Scrolling HTML5** — a linear reflow, walking frame chains in reading
  order and dropping page furniture (page/chapter numbers, fixed geometry).
* **Paged-media HTML5** — `@page`-based, frames placed by absolute position
  within each page, optionally rendered to PDF via an external tool
  (Prince or WeasyPrint) if one is present on the system.

`DrawFile`, `Sprite`, and `ArtWorks` (the embedded-picture formats Impression
documents reference) start as stub decoders and get filled in later; EPS gets
fuller treatment from the start since `docs/impression-documents.xml` already
describes its embedding layout precisely. Every place the converters can't do
a full, faithful job (irregular picture boundaries, non-decimal numbering
styles, unimplemented picture formats, undecoded style/frame fields) must be
"best effort": don't crash, and log clearly what was approximated or skipped,
via a shared `ConversionLog`.

40+ real Impression documents of varying lineage exist locally at `examples/`
for manual testing and, eventually, empirical validation — **these must never
be committed** (they may contain personal information; `.gitignore` excludes
the directory). A later stage will audit them and pick a sanitised subset for
automated fixtures. `docs/impression-documents.xml` is a living document and
gets corrected as real documents reveal more about fields that were
unconfirmed when it was first written.

## Ground rules carried through every stage

* **No runtime external dependencies** where reasonably avoidable (PDF, HTML,
  and DDL writers are all hand-rolled). Dev-only tooling (`pytest`, a linter)
  is fine. `prince`/`weasyprint` are optional, detected at runtime via
  `shutil.which`, never a hard import.
* **Best-effort, always logged.** Anything not fully implemented (a stub
  format, an unimplemented numbering style, an unrecognised control code,
  an irregular boundary the target format can't express) goes through
  `ConversionLog` with a level (`info` / `best_effort` / `unsupported`), not
  a silent no-op and not an uncaught exception, unless the caller opted into
  `strict=True`.
* **Model/output separation.** The decode side (`model/`) turns raw bytes
  into a clean, fully-resolved Python object graph — colours, style
  cascades, and inline colour words are decoded once, not re-derived by each
  output converter. Every output converter is a `Converter` subclass working
  only against that model.
* **Real documents in the loop early.** From Stage 1 onward, each stage's
  decoder is run across the whole (gitignored) `examples/` corpus as a
  smoke/diagnostic pass — catching crashes and empirically narrowing
  "unconfirmed" header/struct fields — not deferred to one big validation
  stage at the end.
* Every stage ends with a **functional, committed** unit (one commit for
  small stages, several for the larger ones), and this file gets updated to
  check off progress and record anything learned that changes the plan.

## Repository layout (target)

```
riscos-impression/
  README.md
  LICENSE                      (MIT)
  PLAN.md                      (this file)
  pyproject.toml
  .gitignore                   (excludes examples/, build artefacts)
  docs/
    impression-documents.xml   (format reference)
  src/riscos_impression/
    __init__.py
    binary.py                  # struct/bitfield reading helpers
    log.py                     # ConversionLog
    model/
      document.py              # FileHeader, ImpressionDocument
      colours.py                # Colour, ColourTint, inline colour-word codec
      styles.py                  # Style + variable-data codec
      numbering.py               # NumberingRecord
      dictionary.py               # DictionaryEntry, master-dictionary lookup
      frames.py                    # ObjectRecordStream + Page/Text/Picture/Blank/Guide/Group/Section/Branch
      story.py                      # Story/Paragraph/Run + control-code interpreter
    io/
      source.py                  # single-file vs directory-mode abstraction
      reader.py                  # ImpressionDocument.load() orchestration
    formats/
      eps.py                    # header parse + pass-through
      drawfile.py                # stub (bounding box only)
      sprite.py                  # stub (bounding box only)
      artworks.py                # stub
    output/
      base.py                   # Converter ABC: walking/coordinate/cascade helpers
      ovprodll.py                # OvProDDLConverter(Converter)
      pdfdoc.py                  # PDFConverter(Converter)
      html_base.py                # HTML5Converter(Converter)
      html_scrolling.py            # ScrollingHTMLConverter(HTML5Converter)
      html_paged.py                 # PagedHTMLConverter(HTML5Converter)
    cli.py
  tests/
    fixtures/                  # hand-built synthetic byte fixtures
    test_*.py
  scripts/
    dump_document.py           # local diagnostic: run the decoder over examples/, report
  .github/workflows/ci.yml     # lint + pytest
```

## Progress

- [x] Stage 0 — Scaffolding
- [ ] Stage 1 — Binary helpers, document source, file header
- [ ] Stage 2 — Colour table
- [ ] Stage 3 — Object dictionary and frame/object-record model
- [ ] Stage 4 — Style table
- [ ] Stage 5 — Numbering and text story decoding
- [ ] Stage 6 — Full document assembly
- [ ] Stage 7 — Conversion framework, logging, embedded-format stubs
- [ ] Stage 8 — OvProDDL output (reference converter)
- [ ] Stage 9 — Native PDF output
- [ ] Stage 10 — Scrolling HTML output
- [ ] Stage 11 — Paged-media HTML output
- [ ] Stage 12 — CLI and polish
- [ ] Stage 13 (follow-up) — Real-document audit

## Stages

### Stage 0 — Scaffolding
* Move the format doc to `docs/impression-documents.xml`.
* `.gitignore` excluding `examples/` and build artefacts.
* `README.md`, `LICENSE` (MIT), `pyproject.toml` (src layout, package name
  `riscos_impression`, console-script `riscos-impression`, Python >=3.10,
  `pytest` as a dev dependency only), empty package skeleton, this file.
* `.github/workflows/ci.yml`: install the package, run `pytest`.
* Commit: *"Scaffold riscos-impression Python package"*.

### Stage 1 — Binary helpers, document source, file header
* `binary.py`: little-endian struct helpers, bitfield extraction utilities.
* `io/source.py`: `DocumentSource` — detects single-file vs directory mode
  from the input path, gives uniform byte access either way.
* `model/document.py`: `FileHeader` dataclass/parser + version check
  (reject `v3 < 28`).
* Fold in empirical corrections found by inspecting real examples during
  planning: `v2` is a fixed magic word `0x12345678` (a format signature, not
  fully unknown), and `colour1`/`colour2`/`colour3`/`tints` all read as
  exactly `380` (`sizeof(FileHeader)`) on an empty-colour-table document,
  confirming the colour table sits immediately after the fixed header.
  Update `docs/impression-documents.xml` accordingly as part of this stage.
* `scripts/dump_document.py`: minimal CLI that loads a path and prints the
  decoded header; run it over every file in `examples/` (manually, not in
  CI) to sanity-check offsets/ranges against real files and refine
  remaining "unconfirmed" header fields where the data makes it possible.
* Tests: hand-built header byte fixtures (valid, and a too-old-version
  rejection case).
* Commit: *"Add binary helpers, document source abstraction, and file header parsing"*.

### Stage 2 — Colour table
* `model/colours.py`: `Colour`/`ColourTint`, on-disk `icolourstr` decode
  (RGB/CMYK/HSV branches, tint resolution), and the separate inline
  colour-value-word codec shared by frames and styles later.
* Tests hitting each colour-model branch plus a tint and a named-colour
  reference, via synthetic fixtures.
* Commit: *"Add colour table and inline colour-word decoding"*.

### Stage 3 — Object dictionary and frame/object-record model
* `model/dictionary.py`: `DictionaryEntry`, master-dictionary offset
  resolution.
* `model/frames.py`: generic `ObjectRecordStream` walker; `Frame` base plus
  `PageFrame`, `TextFrame`, `PictureFrame` (including its struct extension
  and irregular-boundary path decode), `BlankFrame`, `GuideFrame`,
  `GroupFrame`, `Section`, `Branch`. Wires in colour resolution from Stage 2
  for fill/border colours.
* Tests: synthetic object-record streams per frame type, plus an irregular
  picture-boundary path.
* Commit: *"Add object dictionary and frame/object-record model"*.

### Stage 4 — Style table
* `model/styles.py`: `Style`, decoding both presence-flags words and the
  full variable-data sequence (one-byte fields, four-byte fields, tab
  ruler, font name, trailing colour words) into resolved attributes.
* Tests: body style (slot 0) plus a couple of ordinary styles exercising
  different flag combinations.
* Commit: *"Add character/paragraph style table decoding"*.

### Stage 5 — Numbering and text story decoding
* `model/numbering.py`: `NumberingRecord` + running-value resolution.
* `model/story.py`: `Story`/`Paragraph`/`Run`; `ilinestr` walking; the full
  `CTRL_*` interpreter (paragraph/page breaks, page/chapter/number
  references, tabs, embed/merge markers, style stack, frame-chain
  construction for linked and repeating frames).
* Tests: synthetic story byte streams, one per control code family.
* Likely two commits given size: *"Add paragraph/heading numbering
  decoding"*, then *"Add text story and inline control-code decoding"*.

### Stage 6 — Full document assembly
* `io/reader.py` / `ImpressionDocument.load()`: wire header, colours,
  styles, numbering, dictionary, master pages, chapters, and stories into
  one coherent, navigable object graph (`document.chapters[i].pages`,
  `frame.resolved_fill_colour`, `frame.master_frame`, `story.paragraphs`, …).
* Directory-mode story/picture resolution in `io/source.py`
  (`MasterChap`/`ChapterN`/`StoryN`/`Text`-chunk lookup), reaching parity
  with single-file mode.
* Run `scripts/dump_document.py` (extended to print a full document
  summary) across `examples/` as an end-to-end smoke pass; fix crashes.
* Commit: *"Assemble full ImpressionDocument model and directory-mode story resolution"*.

### Stage 7 — Conversion framework, logging, embedded-format stubs
* `log.py`: `ConversionLog` (structured entries: level, area, message,
  source location; a human-readable `.summary()`).
* `output/base.py`: `Converter` ABC — shared page/frame walking, master-page
  resolution, frame-chain walking, coordinate transforms, style-cascade
  resolution; a template-method `convert()` that wraps best-effort areas in
  logged exception handling; abstract `emit_*` hooks for subclasses.
* `formats/drawfile.py`, `sprite.py`: stub decoders that at least read the
  format's native bounding box (both formats make this cheap) and log
  "rendered as placeholder, contents not decoded"; `formats/artworks.py`:
  full stub (fixed placeholder size, always logged).
* `formats/eps.py`: header parse (per the documented layout) and
  pass-through byte access, ready for Stage 9's PDF converter.
* Commit: *"Add conversion framework base class, logging, and embedded-format stubs"*.

### Stage 8 — OvProDDL output (reference converter)
* `output/ovprodll.py`: `OvProDDLConverter(Converter)`, porting the DDL
  emission logic from `c/frames`, `c/styles`, `c/colours`, `c/pxexp` in the
  `riscos-source` repo onto the new model.
* Manually diff a small document's output shape against the structure of
  the existing C source's emission to sanity-check the port (running the
  original AIF under Pyromaniac against a real sample document is a
  possible later validation step, noted as a follow-up rather than a
  blocker for this stage).
* Commit: *"Add OvationPro DDL output converter"*.

### Stage 9 — Native PDF output
* `output/pdfdoc.py`: minimal pure-Python PDF writer — xref table, catalog/
  pages/content streams, the 14 standard PDF fonts initially (embedding
  actual RISC OS outline fonts is out of scope for this stage), RGB/CMYK
  colour operators, image XObjects for rasterised/placeholder picture
  assets, and path clipping (`W n`) for irregular picture boundaries
  (a direct, good-fidelity use of the already-decoded path opcodes).
* EPS handling: modern PDF has **no reliable native mechanism to render
  embedded raw EPS/PostScript** (the legacy PDF "PS XObject" facility is
  deprecated and unsupported by most viewers) — treat this as best-effort:
  draw a placeholder box in the picture's place, and attach the raw EPS
  bytes as a non-rendered embedded file, both logged clearly.
* Commit: *"Add native PDF output converter"*.

### Stage 10 — Scrolling HTML output
* `output/html_base.py`: shared style→CSS and colour→CSS mapping used by
  both HTML variants.
* `output/html_scrolling.py`: `ScrollingHTMLConverter(HTML5Converter)` —
  walks frame chains in reading order, linear `<p>`/heading flow, pictures
  as `<img>` against rasterised/placeholder assets, page furniture dropped.
* Commit: *"Add scrolling HTML output converter"*.

### Stage 11 — Paged-media HTML output
* `output/html_paged.py`: `PagedHTMLConverter(HTML5Converter)` — `@page`
  rules per Impression page, frames placed by absolute position/size
  directly from the decoded geometry; optional `subprocess` call to
  `prince` or `weasyprint` (detected via `shutil.which`, entirely optional)
  to additionally produce a PDF, logged either way.
* Commit: *"Add paged-media HTML output converter with optional Prince/WeasyPrint PDF"*.

### Stage 12 — CLI and polish
* `cli.py`: `riscos-impression convert <input> --format {ddl,pdf,html-scroll,html-paged} [--to-pdf] [--strict] [-o output] [--log-level] [--json-log]`.
* README usage documentation.
* Commit: *"Add command-line interface"*.

### Stage 13 (follow-up, not blocking) — Real-document audit
* Audit `examples/` for documents free of personal information; add a
  sanitised subset as committed automated-test fixtures; extend CI to run
  against them.

## Verification per stage

* `pytest` (fixture-based unit tests) must pass after every commit; CI
  enforces this from Stage 0 onward.
* From Stage 1 onward, `scripts/dump_document.py <path>` run manually
  against files in the local (gitignored) `examples/` directory is the
  real-world smoke check — not part of CI, since those files aren't
  committed, but part of finishing each stage.
* From Stage 8 onward, spot-check converter output by opening it (DDL:
  visual inspection against the shape of known-good C output; PDF: open in
  a PDF viewer via `host-open`; HTML: open in a browser via `host-open`).
