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
describes its embedding layout precisely. DrawFile got its own full decoder
and real PDF/SVG rendering in Stage 14, once a real-corpus survey showed it
accounts for essentially every embedded picture in practice; Sprite and
ArtWorks remain stubs (bounding-box-only placeholders). Every place the
converters can't do
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
- [x] Stage 1 — Binary helpers, document source, file header
- [x] Stage 2 — Colour table
- [x] Stage 3 — Object dictionary and frame/object-record model
- [x] Stage 4 — Style table
- [x] Stage 5 — Numbering and text story decoding
- [x] Stage 6 — Full document assembly
- [x] Stage 7 — Conversion framework, logging, embedded-format stubs
- [x] Stage 8 — OvProDDL output (reference converter)
- [x] Stage 9 — Native PDF output
- [x] Stage 10 — Scrolling HTML output
- [x] Stage 11 — Paged-media HTML output
- [x] Stage 11.5 — Markdown output
- [x] Stage 12 — CLI and polish
- [x] Stage 13 (follow-up) — Real-document audit
- [x] Stage 14 — Real DrawFile decoding and PDF/SVG rendering
- [x] Stage 15 — `extract` subcommand
- [x] Stage 16 — ArtWorks/Sprite/JPEG embedded-picture rendering (`artworks-experiment` branch)
- [ ] Stage 17 — Known gaps and follow-ups (tracked below; picked up one at a time)

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
* **Post-Stage-14 fix**: literal text bytes (`c >= 32`) were decoded via
  plain `chr(c)`, equivalent to ISO-8859-1 -- wrong for RISC OS's own
  "Latin1" alphabet (number 101), whose C1 control range (0x80-0x9F) is
  remapped to visible characters (smart quotes, dashes, ligatures, a
  few UI glyphs) rather than left as non-printing control codes. Found
  via a real document (`Fletcher,bc5`): a curly-quoted name decoded as
  literal `\x94`/`\x95` bytes instead of “ ”. Fixed by routing every
  text decode -- this one, plus `binary.cstring`/`binary.nul_string`
  (so also colour/style/font names, and DrawFile text) -- through a new
  `encoding.py` module with the correct alphabet-101 table, reproduced
  from the independent `python-codecs-riscos` project and cross-checked
  against the real document. See `docs/impression-documents.xml`,
  "Text and character encoding". Full-corpus validation confirmed 0
  raw C1 bytes remaining in any converter's output afterwards.

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
* **Post-Stage-14 fix**: `Converter.resolve_style()`'s cascade treated
  `tab_stops` as fully non-cascading (always the body style's own
  ruler, never a specific named style's), because a style with no tab
  bits set decodes to an *empty* tuple rather than `None`, and folding
  it into the generic "override if not None" cascade loop would have
  let that empty ruler wrongly wipe out a real one already cascaded
  from further out the stack. But excluding it entirely went too far
  the other way: no named style's own ruler was ever actually used, by
  any converter, anywhere -- confirmed against a real document
  (PCI_Spec) and two of the user's own reference images, where every
  tab-using paragraph across the whole page (a title block *and* its
  Contents/TOC list) landed on inconsistent, wrong columns instead of
  each other's own, differently-spaced rulers. Fixed with a dedicated
  cascade step just for `tab_stops`: override only when the applied
  style's own ruler is non-empty, otherwise keep whatever's already
  cascaded -- the same "None means absent" rule every other field
  already follows, just phrased for this field's own empty-tuple
  sentinel. Re-validated against all 48 real documents across all five
  output formats: 0 crashes. Visually confirmed against PCI_Spec's own
  reference images that both the title block and the Contents list now
  align correctly.

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
* Follow-up correction: the initial commit wrongly claimed the OvationPro
  XL transform library (`h.transform`/`c.transform`, needed to decompose a
  rotated+scaled picture into DDL's scale/aspect/angle/skew fields) wasn't
  part of this repository, and approximated skew as always 0. The library's
  source is actually present in the sibling `riscos-source` repo at
  `XL/Task/h/transform` and `XL/Task/c/transform`; `output/ovprodll.py` now
  ports `tr_setrotationa`/`tr_setscale`/`tr_multiply`/`tr_getbits` directly,
  so rotated and non-uniformly-scaled pictures get a genuine computed skew.
  Commit: *"Port the real OvationPro XL transform library for picture skew"*.
* **Post-Stage-14 fix**: output was written via `Path.write_text()` with
  no explicit encoding, so it landed on whatever the running platform's
  own default text encoding happens to be (UTF-8 on most systems this
  runs on) -- wrong for a RISC OS-native format read by a RISC OS-
  native importer. Per direction: DDL output now defaults to real RISC
  OS Latin1 (alphabet 101) bytes, via a new reverse `encoding.encode()`
  (the inverse of the decode fix earlier in this stage list), not
  UTF-8. A character with no RISC OS Latin1 representation at all falls
  back to `?`, the same "best available" choice already made for PDF's
  own WinAnsiEncoding transcoding.

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
* Confirmed the document's coordinate unit empirically while building this
  stage: millipoints (1/1000 PDF point), verified against a real A4 master
  page's exact PDF-point dimensions. Documented in
  docs/impression-documents.xml (see the note under "Frame object common
  layout"); this made frame placement a direct divide-by-1000 with no
  Y-flip needed (Impression's own coordinates are already bottom-left,
  Y-up, matching PDF's native page space).
* Real-corpus validation (all 46 documents in examples/, cross-checked
  structurally with `pypdf` as a local, non-dependency validation tool --
  not added to pyproject.toml) found and fixed two real bugs before this
  stage was considered done: (a) master-page furniture and master-linked
  frames were drawn using the *content* page's origin, when master pages
  actually keep their own, entirely separate absolute coordinate canvas
  (confirmed empirically: content pages within one chapter share one
  contiguous vertical canvas, but master pages live in a different
  object-record stream with their own origin) -- fixed by re-basing
  master-sourced geometry onto the master page's own origin rather than
  the content page's. (b) A paragraph's tab stop can be defined (by a
  shared style) far beyond the width of the particular frame it's used
  in; wrapping treated a tab as zero-width and only discovered its real
  jump distance at render time, letting the rest of the line run
  hundreds of points past the page edge -- fixed by tracking real
  absolute X position through tabs during wrapping itself, forcing a
  line wrap before an overflowing tab, and treating the tab as a no-op
  if even a fresh line still can't reach its target.
* Follow-up: the user spotted, from real PDF output, that a story
  spanning a genuine multi-frame chain (confirmed against
  `Converter.resolve_frame_chain`) was still only ever rendered
  (clipped) in the first frame encountered, and that a later
  same-page chain member's own opaque fill was painting directly over
  text already placed by an earlier one. Fixed by implementing real
  chain flow: a story's whole text is now laid out once across its
  full chain (moving to the next member whenever one fills up,
  re-wrapping for each member's own width), and a later same-page
  member whose box overlaps an earlier one skips its own fill/border
  entirely and doesn't start its content higher than the earlier
  member's own bottom edge (real documents hand-emulate text-repel
  this way, chaining a narrow frame beside an obstacle into a full-width
  one below it, rather than relying on dynamic repel, which still isn't
  implemented -- see PBServer's own remaining case, driven by the
  `repel`/`exx0..exy1` fields instead of chaining). Also found, while
  building this: some stories are repeated independently across several
  chapters via master-page linking (e.g. a running footer) rather than
  genuinely flowing; their `frame_chain` data (when present) is anchored
  to the master page they're defined on, not to any chapter, so
  resolving it as a content-page chain always failed. Fixed by detecting
  that case (the resolution doesn't fully succeed) and falling back to
  laying each occurrence out fresh and independently, matching how
  master furniture already works, instead of logging a bogus
  unresolved-offset error. Commit: *"Flow story text across its whole
  frame chain instead of clipping to the first frame"*.
* Follow-up: implemented dynamic text repel, closing PBServer's own
  remaining case noted above -- its letterhead needs body text to flow
  around a crest picture and an address block, neither of which is a
  frame-chain relationship. Each repel-flagged frame's own repel box
  (`exx0..exy1`, a deliberately larger margin than its outer box, not
  the outer box itself) is gathered per page; text layout now proceeds
  one line at a time (previously a whole paragraph was wrapped at a
  fixed width in one call) so a line's available width can be narrowed
  around whatever obstacles intersect its own Y-band, pushed in from
  whichever side has less room. Found and fixed one real bug building
  this: a frame that's itself repel-flagged (PBServer's address block)
  was including its own repel box as an obstacle to its own text,
  leaving zero usable width anywhere in its own frame and silently
  dropping the whole address -- fixed by excluding each container's own
  frame from its own obstacle list. Re-validated against all 48 real
  documents in examples/: 0 crashes, 0 errors, 0 structurally-invalid
  output. Four new regression tests cover the narrowing logic directly,
  a picture obstacle pushing text past it, and a repel-flagged frame no
  longer obstructing itself. Commit: *"Add dynamic text repel around
  obstacle frames"*.
* **Post-Stage-14 fix**: once the RISC OS Latin1 decode fix (see Stage 5's
  addendum) made real Unicode characters reach the PDF converter for the
  first time -- curly quotes, dashes, ligatures -- `_pdf_str`'s final
  content-stream encode step (`.encode("latin-1", errors="replace")` in
  `end_page`) silently replaced every one of them with a literal `?`,
  since none of those code points are representable in Latin-1 at all
  (this had never been visible before, because the previous bug meant
  the converter had only ever seen raw 0x80-0x9F byte values, which
  *are* representable in Latin-1, just as the wrong, invisible C1
  control characters). Fixed by transcoding through Windows-1252 in
  `_pdf_str` itself -- the encoding every text font here declares via
  `/Encoding /WinAnsiEncoding`, and a near-exact match for it -- before
  the later blanket Latin-1 pass-through. Confirmed against the real
  document that prompted the original report (`Fletcher,bc5`): the
  curly-quoted address now extracts and renders correctly. A handful of
  RISC OS Latin1 characters WinAnsiEncoding itself has no slot for at
  all (W/Y-circumflex, the RISC OS resize/close icon glyphs) still fall
  back to `?` -- a genuine, narrow limitation of a single-byte PDF text
  encoding, not a bug, and out of scope to fix without embedding a
  custom font program.
* **Post-Stage-14 fix (2)**: the user reported a real document
  (`PCI_Spec,bc5`) rendering with *no visible body text on almost every
  page*. Root-caused to `_flow_paragraphs_into_containers`: a
  paragraph's own `right_indent` (a delta from the frame's own right
  edge; see docs/impression-documents.xml, "ruler1") can be set up for
  a much wider frame than the one it's actually used in -- styles are
  shared across frames of any size, the same class of issue as the
  tab-ruler fix above -- and this document's body style's right_indent
  very nearly equalled the frame's own width, leaving under
  `_MIN_USABLE_WIDTH` on every line. That's handled the same way an
  obstacle leaving no room is: skip the line and try the next. But
  since the paragraph's tokens are never consumed when this happens,
  it burned through the *entire* container, then the whole chain,
  without ever placing a line -- silently dropping not just that one
  paragraph but every one after it in the whole story, since the loop
  never reaches them. Fixed by falling back to the container's own
  full width whenever the indent settings alone (before any obstacle
  is considered) already leave no usable room. Re-validated against
  all 48 real documents: 0 crashes, and no longer any document with
  zero extractable text on any page.
* **Post-Stage-14 fix (2)**: the user reported that right-aligned text
  (Fletcher's letterhead address block) didn't actually come out flush
  on the right in the PDF, and pointed out this converter's flat
  per-family average character width (0.52em for every Helvetica-
  mapped glyph, 0.46em for Times) was the likely cause. Fixed by
  adding `output/font_metrics.py`: real per-character advance widths
  for the eight Homerton/Trinity weight/slant combinations, reproduced
  from the independent `garethmccaughan-mkdrawf` project's own
  `Font_ScanString` emulation table (real RISC OS font metrics, not
  guessed) -- cross-checked against Adobe's own published Helvetica/
  Times AFM widths, which match exactly (Homerton/Trinity are RISC
  OS's alikes for those). `_approx_width` now sums real per-character
  widths when the resolved font maps to one of those eight, falling
  back to the flat average only for a font with no metrics table at
  all (Symbol, ZapfDingbats) or an individual character with no RISC
  OS Latin1 representation. Courier is untouched -- it was already
  exact, being genuinely fixed-pitch (confirmed against this same
  source data: every Corpus entry is uniformly 0.6em). Re-validated
  against all 48 real documents: 0 crashes, 0 new errors; visually
  confirmed against Fletcher itself that the address block's right
  edge is now flush.
* **Post-Stage-14 fix (3)**: PCI_Spec's own title block (Distribution/
  Title/Drawing Number/Issue/Author/Date/...) turned out to be
  genuinely missing from the PDF, not just overlapping other content --
  a *second*, unrelated bug from the same page. Root-caused to
  `_line_height_pt`'s handling of proportional (percentage) line
  spacing: the raw stored value is percent x100 (12000 = 120%), not a
  literal percent, but was being treated as the latter -- 12000% for a
  12pt style is a 1728pt line height, instantly overflowing a single
  line past the whole frame and silently dropping the rest of the
  story, the same failure shape as the right_indent fix above. Traced
  to c/styles in the sibling riscos-source repo: the original converter
  passes this field straight through, unscaled, to OvationPro's own
  `{leading 1 N}` DDL directive -- the x100 scaling is something
  OvationPro's own DDL interpreter does, not anything visible in the
  conversion source this project otherwise draws from, so this had to
  be confirmed empirically instead. Corpus-wide search found the exact
  same style (line_spacing=12000, font_size=192) reused verbatim across
  at least 14 of the 48 local example documents -- a shared corporate
  spec-document template -- so this one fix likely restores real body
  content across a substantial slice of the whole corpus, not just
  PCI_Spec. Re-validated: 0 crashes, 0 documents with entirely blank
  extracted text.
* **Post-Stage-14 fix (4)**: the user supplied a real page image
  showing PCI_Spec's own footer for comparison, and every frame's text
  sat visibly too low against it. Root cause: a container's first
  line's baseline was placed a full `_line_height_pt` below the box's
  top edge -- correct for the gap *between* two consecutive baselines
  (which includes descent and inter-line leading), but too large for
  the gap between a box's own top edge and its *first* baseline, which
  should only need to clear the font's ascent. Added `_ascent_pt`
  (Adobe's own standard AFM Ascender values -- 718/683/629 per 1000em
  for Helvetica/Times/Courier) and used it for exactly the first line
  placed into each container (chain member or single frame alike),
  leaving every subsequent line's spacing untouched. Confirmed against
  the supplied image that text now sits close to each frame's top edge
  as expected.
* **Post-Stage-14 fix (5)**: tab handling only ever understood left
  tabs (jump to the stop, following text starts there); the user
  pointed out (backed by two more real page images and the document's
  own exported DDF, generated by Impression itself, which confirmed
  the intended tab rulers exactly)
  that PCI_Spec's footer and numbered Contents list both use centre
  and right tabs, and were breaking as a result -- the footer's "Issue
  F ****LIVE****" was disappearing *entirely* (a right tab landed its
  text so close to the frame's right edge that it wrapped to a second
  line the single-line-tall frame had no room for -- silently dropping
  it, the same failure shape as the earlier right_indent/line_spacing
  bugs), and the Contents list's chapter numbers, titles, and page
  numbers landed on inconsistent columns row to row.
  `_next_tab_stop` now returns each stop's own kind alongside its
  position (skipping any stop whose kind isn't 0-3, a rule-line marker
  rather than a real stop); a new `_segment_width` looks ahead to the
  next tab/break to size the run of tokens a centre/right/decimal tab
  actually positions, and `_tab_target_x` uses that to work backward
  from the stop by half, all, or (decimal, simplified to the same as
  right) the segment's own width -- never past the tab's own starting
  position, the same "unreachable target is a no-op" fallback already
  used when a stop itself doesn't fit. Both the wrap decision and the
  final render now share this, so a right-tab segment that fits (once
  correctly positioned) no longer forces the spurious line break that
  was dropping it.
  Investigating the Contents list surfaced a second, independent bug:
  `_paragraph_tokens` fell back to the *document's* body style, not
  the paragraph's own, for any mark (typically a leading TabMark used
  to right-align a list's own number column) appearing before that
  paragraph's first Run -- using the wrong tab ruler for exactly the
  tab meant to right-align the number, while every later tab in the
  same line correctly used the right one (style is set per-Run as
  they're reached). Fixed by seeding the paragraph's initial style
  from its first Run's own style_slots instead of the passed-in body
  style, falling back to body only when a paragraph truly has no Run
  at all.
  Re-validated against all 48 real documents across all five output
  formats: 0 crashes. Visually and numerically confirmed against
  PCI_Spec (extracted PDF word coordinates, not just a screenshot) that
  the footer's right-aligned text is back and the whole Contents list
  now lands on three consistent columns, matching the document's own
  reference images and its own exported DDF tab rulers exactly.
* **Post-Stage-14 fix (6)**: the previous right_indent overflow
  fallback (see above) was too coarse -- it reset *both* the line's
  start and right edge back to the container's own full width whenever
  the two, taken together, left no usable room. That's correct when
  right_indent alone is the problem (a ruler set up for a wider
  frame), but a real document's title-block style (PCI_Spec's "Control
  Info", confirmed against the user's own reference image and directly
  against Impression's own ruler dialog -- a left bound of about 4cm)
  deliberately combines a large left_indent (a label column) with a
  right_indent close to the frame's own full width, since its real
  content is always short and tab-terminated. Resetting line_start
  back to 0 as well wiped out that real, intentional hanging indent,
  left-aligning every label flush against the frame's edge instead of
  under its intended column. Fixed by only dropping the right margin
  when the left position alone still leaves enough usable room,
  falling back to the full container width only when it doesn't.
  Re-validated against all 48 real documents: 0 crashes. Confirmed
  against PCI_Spec (extracted word coordinates) that every title-block
  label now starts at its own intended left column instead of the
  frame's own edge.
* **Post-Stage-14 fix (7)**: the user described a real frame's border
  configuration precisely (top and bottom borders only, confirmed
  against Impression's own ruler dialog) that let the previously-
  unconfirmed border0..border3-to-physical-edge mapping finally be
  established with confidence: border0=top, border1=left, border2=
  right, border3=bottom -- exactly the clockwise-from-top order the
  OvProDDL converter's own (border0, border2, border3, border1)
  reordering already encoded, not an arbitrary permutation as the
  format doc previously (correctly, at the time) described it.
  Documented in docs/impression-documents.xml; per-edge border
  rendering in pdfdoc.py/html_paged.py (currently draw/CSS a full
  rectangle whenever *any* edge has a border) is a following, not yet
  implemented, piece of work.

### Stage 10 — Scrolling HTML output
* `output/html_base.py`: `HTML5Converter(Converter)` — shared colour→CSS
  and style→CSS mapping, and picture rendering (dispatched by embedded
  type exactly like the PDF converter's placeholders, but as a small
  self-contained `data:image/svg+xml;base64,...` URI rather than a raster
  image, since there's no pixel data to rasterise and no external image
  library in use).
* `output/html_scrolling.py`: `ScrollingHTMLConverter(HTML5Converter)` —
  a linear reflow: each chapter's pages walked in order, each story
  rendered once (globally deduped by dictionary_index) as a run of `<p>`
  elements wherever its first frame is encountered, embedded pictures
  inline via `<img>`. Unlike the PDF converter, this format has no
  geometry or pagination of its own -- a browser wraps text natively
  from the CSS this module produces -- so the frame-chain-flow and
  dynamic-repel work pdfdoc.py needed is irrelevant here: a story's
  whole text is just one continuous run of paragraphs, with no need to
  work out which physical frame would have held which portion. Page
  furniture is dropped by construction: this converter never visits
  document.master_pages at all, only each chapter's own content pages,
  so master-only furniture is simply never seen (a master-*linked*
  frame's own dictionary_index is still honoured normally).
* Real-corpus validation (all 48 documents in examples/): 0 crashes, 0
  errors, and every generated file parses cleanly with Python's
  built-in `html.parser`.
* Commit: *"Add scrolling HTML output converter"*.

### Stage 11 — Paged-media HTML output
* `output/html_paged.py`: `PagedHTMLConverter(HTML5Converter)` — one
  page-sized `<div class="ro-page">` per Impression page (styled with
  `page-break-after` for both on-screen preview as stacked pages and
  correct pagination when exported), each frame absolutely positioned
  (`position: absolute`) directly from its own decoded geometry. Reuses
  output/base.py's `page_origin`/`to_page_coordinates` (the same
  top-left-origin, Y-down conversion the OvProDDL converter uses) rather
  than pdfdoc.py's bottom-left convention, since that's CSS's own native
  coordinate system.
* Deliberately simpler than the PDF converter: a browser's own block
  layout wraps text within a frame's sized `<div>` natively, so none of
  pdfdoc.py's approximate-metrics line-wrapping is needed. Two things
  that follow from staying simple, both logged: a story confined to one
  frame renders in full there, clipped (`overflow: hidden`) if it
  doesn't fit, with no attempt made to measure whether it actually does
  (that would need the same manual text-metrics work this format's own
  native wrapping exists to avoid); a story spanning a real multi-frame
  chain only ever renders in its first frame -- the same limitation
  pdfdoc.py started with before chain flow was added for it. Dynamic
  text repel (as pdfdoc.py does) is not attempted either -- frames are
  positioned independently, so an obstacle and a text frame can visually
  overlap exactly as positioned in the source document.
* Optional PDF export via `subprocess`, calling `prince` or
  `weasyprint` if either is found on PATH (`shutil.which`); logged
  either way (which tool exported it, or that neither was found and
  export was skipped, never a hard failure). Verified for real against
  this machine's own `prince` install: a real multi-page PDF with
  correctly extractable text came out the other end.
* Real-corpus validation (all 48 documents in examples/): 0 crashes, 0
  errors, and every generated file parses cleanly with Python's
  built-in `html.parser`.
* Commit: *"Add paged-media HTML output converter with optional Prince/WeasyPrint PDF"*.

### Stage 11.5 — Markdown output
* `output/markdown.py`: a best-effort plain-text/Markdown converter --
  serialises each story's text, inferring heading levels from a
  paragraph style's font size relative to the body style (larger,
  paragraph-scoped styles rank as headings; exact levels are a
  judgement call, not a confirmed document fact, and should be
  documented as such). Won't work well on everything; the goal is
  extracting most real text usefully, not full fidelity.
* Table detection: a best-effort attempt at recognising a grid of
  bordered frames (consistent rows/columns by position) on one page as
  a Markdown table; anything that doesn't look like a clean grid falls
  back to plain paragraphs.
* Pictures are left as simple placeholders (e.g. `[draw]`, matching the
  other converters' own placeholder labelling) -- no inline image
  support, Markdown isn't the place for it.
* Found and fixed one real modelling mistake while building the heading
  heuristic: `Converter.resolve_style()`'s cascade result always
  reports `is_body_text=True` and `paragraph_apply=False`, regardless
  of which named style was actually applied -- both are non-cascading
  fields, inherited from the body style by construction, so they're
  meaningless to check on a *resolved* style. Fixed by keying the
  heading heuristic off whether a run's own `style_slots` is non-empty
  (a named style was applied at all) rather than those two fields, then
  using the resolved font size for the ratio. Verified against a real
  document (PBServer2 from examples/): all five of its real headings
  ("Pinboard Server (v1.02)", "Introduction", "Pinboard server
  specification", "Messages for version 1.02", "Messages summary for
  version 1.02") are picked out correctly, at plausible relative levels.
* Real-corpus validation (all 48 documents in examples/): 0 crashes, 0
  errors; 43/48 documents produced at least one heading. No document in
  this corpus has four or more bordered text/blank frames on one page
  (confirmed by direct inspection), so the table detector is never
  exercised by real data here -- covered instead by two synthetic
  tests, a clean 2x2 grid that's recognised and a similar-looking but
  misaligned one that correctly falls back to plain paragraphs.
* Commit: *"Add best-effort Markdown output converter"*.

### Stage 12 — CLI and polish
* `cli.py`: `riscos-impression convert <input> --format {ddl,pdf,html-scroll,html-paged,markdown} [--to-pdf] [--strict] [-o output] [--log-level] [--json-log]`
  (`markdown` added to the original format list, matching Stage 11.5).
  Exit codes: 0 clean, 1 couldn't even start (bad input, or a `--strict`
  failure), 2 completed but the log contains an `error`-level entry.
* README usage documentation.
* Found and fixed one real robustness gap while building the CLI's own
  test suite (a genuine end-to-end run against a file on disk, unlike
  every other test so far, which built an in-memory `ImpressionDocument`
  directly): `MarkdownConverter.convert()` eagerly resolved the body
  style outside any `catch()` boundary, so a document with no styles at
  all (a real, valid edge case -- io/reader.py's own test already
  builds one) crashed uncaught before ever reaching its own chapter
  walk. Every other converter only resolves a style lazily, inside the
  walk, already protected by `catch()`; fixed Markdown's own eager call
  to fall back to a plausible default (10pt, matching every other
  converter's own fallback) instead of propagating the exception.
  Verified all five converters against the same empty-document fixture
  after the fix: none crash.
* Commit: *"Add command-line interface"*.

### Stage 14 — Real DrawFile decoding and PDF/SVG rendering
* Prompted by a real-corpus survey (see `docs/impression-documents.xml`,
  "Embedded object types"): every one of 113 embedded pictures across the
  48-document local corpus classified as DrawFile, so a real decoder
  covers essentially every picture actually in use, not just a slice.
* Verified the on-disc DrawFile format against the official PRM
  (https://www.riscos.com/support/developers/prm/fileformats.html) before
  writing the parser, since the `riscos-output` skill's own
  `drawfile-format.md` reference turned out to have several genuine
  errors (wrong header field sizes/offsets, wrong object type numbers,
  a missing Group-object name field, wrong path-style bit layout, wrong
  units for the Text object's font-size fields). Fixed those in a local
  shadow skill (`ai skill new riscos-output`) rather than the installed
  copy, and left a note of the correction in that shadow for future
  reference.
* `formats/drawfile.py`: rewritten from a bounding-box-only stub into a
  real object-stream decoder -- font tables, paths (fill/stroke colour,
  width, winding rule, move/line/curve/close ops), single-line text,
  groups and tagged objects (both recursed into). Sprite objects
  embedded within a DrawFile, and any other object type (text area,
  options, transformed text/sprite, or unrecognised), are kept as a
  bounding box only, matching the existing Sprite/ArtWorks stub scope.
  A corrupt/truncated object stream stops parsing rather than raising,
  returning whatever was already decoded.
* `output/pdfdoc.py`: DrawFile pictures are now rendered as real PDF
  vector content (`m`/`l`/`c`/`h` path construction, `f`/`S`/`B`
  painting with the correct winding-rule variant, `BT...Tj...ET` text
  using the existing standard-14 font-matching logic against the
  DrawFile's own font-table name), mapping the file's own bounding box
  onto the target frame's box. A Sprite object embedded within a
  DrawFile, or any other undecoded object type, still falls back to a
  placeholder box for just that object, logged once per picture. A
  picture that isn't a valid DrawFile at all still falls back to
  today's placeholder box entirely, as before.
* `output/html_base.py`: a parallel inline-SVG renderer, shared by both
  HTML converters, mirroring pdfdoc.py's approach closely -- the main
  difference is SVG's Y-down coordinate convention needing an explicit
  flip (PDF's own convention already matches Draw's Y-up one), and
  skipping pdfdoc.py's `Tz`-based horizontal text-scaling support (SVG
  has no equally direct equivalent without first knowing a run's
  natural glyph width; a deliberately narrower simplification for a
  rare case).
* Dash patterns and precise cap/join styles are parsed (so the path data
  that follows them still decodes at the right offset) but not honoured
  in rendering -- lines render solid with default caps/joins, logged
  once per picture rather than per path.
* Markdown output is unchanged, as directed -- it keeps its existing
  `[draw]` bracket placeholder regardless of a picture's real content.
* New shared test fixture builders (`tests/fixtures/drawfile_builders.py`)
  for constructing synthetic DrawFile bytes (font tables, paths, text,
  groups, tagged objects, sprites, unknown types), used by the parser's
  own tests and both the PDF and HTML renderer tests.
* Real-corpus validation (all 48 documents in `examples/`): 0 crashes,
  0 conversion errors, and 0 leftover `[Draw]`/`[Draw picture...]`
  placeholders across every format -- every one of the 113 DrawFile
  pictures found (2395 paths, 1280 text objects, 523 groups, 24
  embedded sprites, 30 unrecognised sub-objects) parsed successfully.
  Visually spot-checked the richest example (`Int_spec`, a hardware
  interface spec with a hand-drawn block diagram) by rasterising the
  PDF output and comparing its SVG output's path data directly: both
  reproduce the diagram's boxes and connecting lines correctly and
  match each other's coordinates.
* Commit: *"Add real DrawFile decoding and PDF/SVG rendering"*.
* **Post-Stage-14 fix**: the user pointed at a real page image
  (PCI_Spec) where two whole DrawFile diagrams were entirely invisible
  and guessed it was the same "unsupported object type" gap already
  logged. It wasn't -- two separate, real bugs, found by tracing the
  actual generated PDF content stream against the picture's own
  decoded objects:
  1. The picture frames' own `level` (front-to-back stacking; see
     "Frame flags word") put them *above* a big filled text frame
     drawn later in the object-record stream, but every converter
     walked `page.frames` in raw stream order, painting the text
     frame's opaque fill on top of the pictures instead of the other
     way round. Fixed by making `PageGroup.frames` a stable sort by
     `level` rather than stream order -- a shared, document-model-level
     fix benefiting every converter, not just the PDF one.
  2. Once visible, the pictures' own text was still invisible --
     rendering at roughly 1/100th its intended size (a ~0.01pt font),
     confirmed directly in the PDF content stream's own `Tf` operator.
     `DrawText.size_y` is already in points (1/640 point, per the
     format), unlike a `DrawPath`'s Draw-unit-denominated `line_width`,
     so scaling it directly by the picture's own points-per-Draw-unit
     ratio was a straight unit mismatch; the same bug, in the opposite
     direction, also affected (less visibly) stroke width. Fixed in
     both `pdfdoc.py` and `html_base.py`'s DrawFile renderers.
  Both fixes needed genuinely realistic Draw-unit-scale test fixtures
  to catch at all -- the original tests happened to use bounds equal
  to the target box in points, which cancels the unit mismatch by
  coincidence. Re-validated against all 48 real documents across all
  five output formats: 0 crashes. Visually confirmed against PCI_Spec
  that both diagrams' full text content (scale labels, box captions,
  address values) now renders.
* **Post-Stage-14 fix (2)**: the user reported that PCI_Spec's inline
  DrawFile diagrams were overlaying the running text instead of
  pushing it down. Root cause: a picture frame with a non-zero
  embed_tag is meant to be "anchored inline within a text story, at
  the point a matching CTRL_S embedded-object marker occurs, rather
  than being placed directly on the page in normal front-to-back
  order" (docs/impression-documents.xml, "Frame object common
  layout") -- but this converter drew it at its own raw page position
  regardless, while the story's own text flow just silently skipped
  the corresponding EmbedMark and carried on as if the picture didn't
  exist, so the two independently-positioned things visually collided.
  Fixed by teaching `pdfdoc.py`'s text flow (`_paragraph_tokens`,
  `_flow_paragraphs_into_containers`) to resolve an EmbedMark to its
  frame (a new `_embed_frame_map`, chapter-scoped like the existing
  `_frame_page_map`) and lay it out as its own block: it ends the
  current line, occupies its own space, and pushes every following
  line down below it; `_draw_frame`'s normal page walk now skips any
  embed_tag frame entirely, so it's drawn exactly once, inline, never
  at its stale raw position.
* **Post-Stage-14 fix (3)**: even once placed inline, the picture came
  out far too large -- almost the paragraph's full column width. The
  user supplied the picture's own values straight from Impression's
  info dialog (frame: 77.08mm x 51.14mm, 0 inset; picture: x=-6.07mm,
  y=23.07mm, angle=0, scale=50%, aspect=100%), which let two real,
  separate bugs be pinned down precisely: (a) the inline block's own
  size was derived from the paragraph's current column width, when
  the *frame's own box* (confirmed to match the info dialog's
  77.08mm x 51.14mm exactly) is the picture's real, correct on-page
  size regardless of how wide the surrounding text column is; and
  (b) `_draw_drawfile_picture` stretched the DrawFile's own native
  bounds to exactly fill whatever box it was given, which is wrong
  for *any* picture (inline or not) whose frame wasn't sized to
  exactly match its content at 100% scale -- confirmed by direct
  arithmetic against the real document's own DrawFile bounds, which
  didn't match a "stretch to fill" scale in either dimension. Fixed:
  the inline block now sizes itself from the frame's own real box
  (shrinking, preserving aspect, only if it doesn't fit the current
  column at all), and `_draw_drawfile_picture` now sizes content from
  its own native bounds times the frame's declared display scale
  (pict.xscale/yscale, confirmed stored as the *inverse* of the
  displayed scale -- 0x20000 raw is genuine 50%, matching
  ovprodll.py's own `_tr_setscale`), rather than stretching to fill.
  A plausible-looking interpretation of pict.xshift/yshift (as a
  bottom-left-relative offset) was tried and rejected: applied to
  every real inline picture in the same document, it clipped away
  real, visible picture content in every case, not just repositioned
  it within empty margin -- so the content is centred within its
  frame instead, a safer default until the real anchor/sign
  convention can be confirmed properly (a genuine, open follow-up).
  Picture rotation (pict.angle) is unimplemented regardless; a
  non-zero angle is logged once rather than silently ignored.
  Re-validated against all 48 real documents across all five output
  formats: 0 crashes. Visually confirmed against PCI_Spec's own
  reference image that both inline diagrams now render at their
  correct size, fully visible, positioned in the flow exactly where
  the reference shows them.
* **Post-Stage-14 fix (4)**: the user pointed out, from inspecting the
  real document directly in Impression, that the paragraph carrying
  the first inline picture has a "Centre" alignment effect applied to
  it -- and the picture (narrower than its own text column) was
  sitting flush against the column's left edge instead of centred, the
  one thing about inline pictures `_render_line`'s own alignment
  handling for ordinary text never covered. Tracing this found the
  real root cause one level down, in the document model itself, not
  just pdfdoc.py: `model/story.py`'s `EmbedMark` was built with no
  style information at all, unlike `Run` (which always carries the
  active `style_slots`) -- so a paragraph consisting of nothing but an
  embedded picture (the real, confirmed case here) had no way for
  *any* converter to discover what style, including alignment, applied
  to it. Fixed at the source: `EmbedMark` now carries `style_slots`
  too, populated the same way `Run`'s already are, from the CTRL_G/
  CTRL_H style stack active at that exact point in the story.
  `pdfdoc.py`'s `_paragraph_tokens` now resolves the embed's own style
  from that (also fixing the "leading item with no style to inherit"
  fallback added earlier this stage, which only checked for a leading
  `Run`, not a leading `EmbedMark`), and the inline block's own
  horizontal position now follows `para_style.alignment` exactly the
  way `_render_line` already does for text (1=centre, 2=right).
  Re-validated against all 48 real documents across all five output
  formats: 0 crashes. Confirmed against PCI_Spec that the resolved
  alignment for that exact paragraph is now `1` (centre), matching
  what the user read directly from the Impression editor, and that the
  picture now renders centred in its column, matching the reference
  image.
* **Post-Stage-14 fix (5)**: the user asked whether arrowheads on
  DrawFile paths were feasible, and pointed at a real, independent
  reference (a Pyromaniac PyModule implementation of the actual RISC
  OS DrawFile module -- a genuine emulation of the real renderer, not
  just documentation). That reference confirmed precisely what real
  arrowheads in classic Draw actually are: not a separate drawn
  object, but a *triangular cap* on a stroked path's own start/end
  (the same path-style-word cap fields the format doc already
  described, but whose exact bit layout -- join bits 0-1, end/
  "trailing" cap bits 2-3, start/"leading" cap bits 4-5, not a naive
  reading of the field names -- could now be independently confirmed
  against real rendering code rather than just the PRM's own field
  descriptions). This also explained a visual defect spotted earlier
  in PCI_Spec's second diagram (a short, wide line rendering as a
  filled black bar): it was a real triangular-capped arrow line,
  previously drawn as a plain uncapped stroke since caps/joins weren't
  decoded or honoured at all.
  `formats/drawfile.py`'s `DrawPath` now decodes join_style/start_cap/
  end_cap/triangle_cap_width/triangle_cap_length from the path style
  word. Neither PDF nor SVG has a native triangular line-cap option,
  so both `pdfdoc.py` and `html_base.py` draw one as an explicit
  filled triangle at the relevant subpath endpoint instead -- a base
  the cap's own declared width, centred on and perpendicular to the
  path's own tangent direction there, with an apex extending the
  cap's own declared length further out along that direction (both in
  the file's own 1/16ths-of-line-width unit, scaled via the stroke's
  own already-computed on-page width) -- matching the exact geometry
  Draw_Stroke itself would produce. A closed subpath has no real
  start/end to cap and is skipped. Dash patterns and non-triangular
  join styles remain unhonoured (a narrower, deliberate scope: they
  don't produce a visibly broken result the way an un-arrow-headed
  pointer line does, unlike this).
  Re-validated against all 48 real documents across all five output
  formats: 0 crashes. Visually confirmed against PCI_Spec's own
  diagrams, in both PDF and HTML/SVG output, that every pointer line
  now ends in a real, correctly-oriented arrowhead instead of a plain
  line end or a stray filled bar.
* **Post-Stage-14 fix (6)**: the confirmed border0..3-to-physical-edge
  mapping (fix (7) of Stage 9's own addenda: border0=top, border1=
  left, border2=right, border3=bottom) had only been applied to the
  format documentation and the *gate* for whether to draw a border at
  all (`Frame.has_border`); the actual drawing code in both
  `pdfdoc.py` (`_draw_box`) and `html_paged.py` (`_render_frame`)
  still unconditionally drew/styled all four edges together whenever
  *any* one was present. The user spotted this directly: PCI_Spec's
  own footer frame (top and bottom borders only) still had its left
  and right edges drawn. Fixed by checking each of
  border0/1/2/3 != 0xFF independently: `pdfdoc.py` now draws each
  present edge as its own line segment instead of a single full-
  rectangle stroke; `html_paged.py` now emits the matching per-edge
  CSS property (`border-top`/`border-left`/`border-right`/`border-
  bottom`) instead of the uniform `border` shorthand. Re-validated
  against all 48 real documents (PDF and paged HTML): 0 crashes.
  Visually confirmed against PCI_Spec that the footer frame now shows
  only its real top and bottom rules, with no fictional side borders.

* **Post-Stage-14 fix (7)**: the user reported that ForSimon3 (from the
  local moreexamples/ corpus) had a 26pt heading whose lines rendered
  heavily overlapping/mushed in the PDF, and correctly guessed the
  cause was line spacing defaulting off the wrong font size. Traced to
  `Converter.resolve_style`'s generic cascade: the heading's own style
  stack sets `font_size` (26pt) but not `line_spacing_raw`, so it fell
  through to BodyText's own value -- a FIXED (absolute-point, not
  proportional) 13.107pt leading, frozen for BodyText's own 12pt size.
  Applied verbatim to 26pt glyphs, this produced lines advancing only
  half their own height. Confirmed via `c/styles` (the original TransIMP
  conversion source): its DDL emitter only ever writes an explicit
  `{leading ...}` for a style whose own `linespace` presence bit is
  set; BodyText's is *always* forced present (`s->linespace ||
  bodytext`), everything else only if the author actually chose one --
  so BodyText's raw bytes were never meant to stand in for an unrelated
  style's leading, and the target renderer is expected to fall back to
  its own size-relative default when a style leaves it genuinely unset.
  A cross-corpus check confirmed the same fixed ~13.107pt value
  (`0x80013333`) appears on almost every example document's BodyText
  style regardless of BodyText's own font size, confirming it's a
  frozen per-document default, not something dynamically recomputed
  from font size at authoring time. Fixed in `resolve_style` with a
  narrow, tab_stops-style exception: when nothing in the applied style
  stack explicitly sets its own `line_spacing_raw`, and the value that
  fell through from BodyText is FIXED, and the resolved `font_size`
  differs from BodyText's own, the resolved `line_spacing_raw` is reset
  to `None` instead -- letting the existing "no line spacing set" path
  in both `pdfdoc.py` and `html_base.py` (120% of the run's own
  font_size) take over. Proportional (percentage) leading is
  scale-invariant, so it's left to cascade normally regardless of font
  size; an explicit `line_spacing_raw` set anywhere in the stack always
  wins outright, fixed or not. Four new `resolve_style` regression
  tests cover: the ForSimon3 scenario itself, plain BodyText keeping
  its own fixed leading unchanged, proportional leading cascading
  across font sizes, and an explicit override always winning. Full
  suite (310 tests) green; re-validated across all 111 real documents
  (48 in examples/, 63 in moreexamples/) with 0 crashes; visually
  confirmed against ForSimon3 itself -- both the body paragraph and
  the heading now render with correctly separated lines.

* **Post-Stage-14 fix (8)**: while re-checking ForSimon3 after fix (7),
  the user noticed the PDF's page order was still wrong: the real
  document is a page of body text, a blank page, a blank page, then
  the heading on the final page, but the PDF had the heading on page 2
  and both blank pages trailing at the end -- "I suspect the 'force
  new page' hasn't worked". Confirmed: ForSimon3's story has TWO
  consecutive `PageBreakMark` items (CTRL_N) right after its body text,
  across a three-frame chain. `model/story.py` already decodes CTRL_N
  correctly (its own docstring calls it "a forced page break"), and the
  conversion source (`c/styles`, `txwritedata`'s `CTRL_N` case) labels
  it "force to next" and emits a DDL `{newpage}` for it -- but
  `pdfdoc.py`'s line-wrapping (`_wrap_one_line`) was treating a "break"
  token as nothing more than an early line terminator, consumed in
  place, leaving the flow in the *same* container. Two consecutive
  breaks with no content between them therefore just produced one
  blank line, not two skipped frames -- landing the heading a full
  frame too early and leaving the chain's real last frame empty. Fixed
  by having `_wrap_one_line` leave a "break" token for the caller
  instead of consuming it (mirroring how it already handles "embed"),
  and having `_flow_paragraphs_into_containers` force a genuine
  `advance_container()` on it -- jumping to the next chain member
  regardless of how much room is left in the current one, the same as
  a genuine overflow does. `html_paged.py`, `html_scrolling.py`,
  `markdown.py` and `ovprodll.py` were all checked too: each already
  either doesn't flow text across a multi-frame chain at all (and says
  so, once, via a best_effort log) or already emits the equivalent
  `{newpage}` DDL directive, so none needed a matching change. One new
  regression test drives `_flow_paragraphs_into_containers` directly
  with a three-container chain and two consecutive `PageBreakMark`s,
  asserting the middle container gets no text at all. Full suite (311
  tests) green; re-validated across all 111 real documents with 0
  crashes; visually confirmed against ForSimon3 -- the PDF is now body
  text, a blank page, then the heading on the third (last) page,
  matching the real document's own three-frame chain. This still
  doesn't reproduce the user's fourth ("blank, blank") page from
  memory of the original -- the document's own frame_chain genuinely
  only has three members (matching this document's three physical
  pages), so a fourth page isn't something this fix can manufacture;
  flagged back to the user rather than guessed at further.

* **Post-Stage-14 fix (9)**: the user supplied a real reference image
  (ForDad-RealPage1.png) showing ForDad (from the local moreexamples/
  corpus) as a proper 2x2 grid of weather-icon captions ("Through
  sunshine," / "Through rain," over "Through storms," / "And through
  snow..."), but the PDF had "Through rain," rendered below "Through
  sunshine," in the same column instead of beside it, and logged an
  unexpected "text overflowed... and was clipped". ForDad's four
  caption frames are chained (dictionary entry 4, frame_chain of 4
  members) via the same PageBreakMark mechanism fixed in (8), one per
  quadrant. Traced to `_flow_paragraphs_into_containers`'s
  `page_floor`: a map from *page_key* to the lowest Y any container on
  that page had reached, meant (per its own docstring) for the case of
  a narrow frame chaining into a full-width one below it, whose box
  genuinely, horizontally overlaps -- but it was applied indiscrimin-
  ately to *any* two containers sharing a page, including these four
  side-by-side grid cells, which never overlap at all. Advancing from
  the top-left cell to the top-right one wrongly clamped the top-
  right's fresh Y down to wherever the top-left's own content had
  reached, starving it of most of its own height; its own content
  (which fits its full height easily) then overflowed into a third
  container that should have stayed empty, and the real second
  container was left with nothing -- exactly the "landed one frame
  over, real frame sitting empty" pattern from fix (8), but caused by
  a different mechanism this time. Fixed by keying the floor tracking
  by *container*, not by page, and only letting a later container
  inherit an earlier one's floor when their X-ranges genuinely overlap
  (not merely touch at a shared edge) -- `advance_container` now scans
  only-already-visited containers on the same page for that overlap
  before applying any floor. One new regression test drives
  `_flow_paragraphs_into_containers` directly with two side-by-side,
  same-page containers plus a third "only reachable if the bug
  regresses" one, confirmed to fail against the pre-fix code (the
  right container's content was clipped rather than placed) before
  being fixed. Full suite (312 tests) green; re-validated across all
  111 real documents with 0 crashes; visually confirmed against ForDad
  -- the PDF now matches the reference image's 2x2 grid exactly.

* **Post-Stage-14 fix (10)**: the user reported Telegraph (from the
  local moreexamples/ corpus) had its own two-line heading ("MODERN
  MAESTROS WHO" / "WRITE IN DOUBLE TIME") visibly colliding, and
  supplied both a real reference image (Telegraph-RealHeading.png) and
  a real DDF export (TelegraphT), generated by Impression itself, for
  comparison.
  Unlike fix (7) (ForSimon3), this wasn't a cascade problem: the
  heading style ("Main Heading", 28pt) sets its OWN explicit fixed
  leading (raw 0x80014ccc = 19.66pt) -- no inheritance involved. 19.66pt
  for 28pt text is barely 70% of the font size, guaranteeing overlap on
  any wrapped multi-line heading. The DDF's own `linespacep 130%` for
  this style (each style's declared spacing, not a re-derived display
  value -- confirmed by cross-checking that "Normal"'s own
  `linespacep 120%` closely matches its fixed 13.107pt/11pt ratio, but
  Main Heading's fixed 19.66pt/28pt ratio (70%) does not match its
  declared 130% at all) confirms the fixed value is a stale snapshot
  from some smaller font size that predates a later increase to 28pt --
  the same underlying failure as fix (7), just frozen directly on the
  style's own record via an inconsistent edit instead of via
  inheritance. Empirically cross-checked against the reference image
  (measuring the pixel gap between the two heading lines' bands,
  calibrated against the already-correct, unaffected 11pt body text's
  own known-correct 13.107pt spacing in the same image) gave a required
  line height of roughly 35.5pt -- far closer to 28pt's natural 120%
  default (33.6pt) than to the broken 19.66pt, confirming a size-
  relative fallback is the right general fix. `_line_height_pt`'s fixed
  branch now takes `max(fixed_value, size * 1.2)` -- a fixed value can
  still widen (loosen) spacing when genuinely larger than the natural
  default, but never shrinks it into overlap. The user's report also
  named "the paragraph before and after" as looking wrong: the gaps
  around the heading (spaceabove 20pt / spacebelow 15pt in the DDF)
  were partly missing -- `space_before` (the decoded `spaceabove`
  field) turned out to be completely unconsumed by any output
  converter, only `space_after` was ever applied. Added `space_before`
  handling to `_flow_paragraphs_into_containers`, mirroring
  `space_after`'s existing placement (added on top of the normal line-
  to-line gap) but suppressed at a container's own top (via
  `first_line_pending`) to avoid an unwanted gap when a paragraph
  starts fresh at the top of a page/frame. Two new regression tests
  (one for each half of the fix), both confirmed to fail against the
  pre-fix code before being fixed. Full suite (314 tests) green;
  re-validated across all 111 real documents with 0 crashes; visually
  confirmed against Telegraph -- the PDF now closely matches the
  reference image: a clean, non-overlapping heading with correct gaps
  before and after it.

* **Post-Stage-14 fix (11)**: the user asked whether the recent PDF
  fixes had made it across to the two HTML converters, and pointed at
  two concrete symptoms in PCI_Spec (from the local examples/ corpus)
  converted to `html-scroll`: no paragraph indentation at all, and
  DrawFile pictures at the wrong size with their own text misplaced.
  Both were real, substantial gaps rather than regressions:

  - `style_css_properties` (applied per-`<span>`) only ever carried
    font/colour attributes; nothing anywhere applied a resolved
    style's paragraph-level attributes (left/right margin, first-line
    indent, alignment, space before/after, line height) to the `<p>`
    element itself, in *either* HTML converter. Added
    `paragraph_css_properties` (html_base.py), applied by both
    converters' `_render_paragraph` to whichever style the paragraph's
    own first Run/EmbedMark carries -- mirroring pdfdoc.py's own
    `para_style` selection in `_paragraph_tokens`, including its
    "a leading mark with no style of its own must not fall back to
    body directly" handling. Includes its own copy of
    `_line_height_pt`'s fixed-value floor (fix (10) above) and a
    `right_indent`/`max_width_pt` sanity check mirroring pdfdoc.py's
    own oversized-right-indent fallback (confirmed against the same
    PCI_Spec style whose right_indent was authored for a much wider
    frame) -- meaningful only in `html-paged`, whose frames keep the
    source document's own real width (now threaded from `_render_frame`
    down through `_render_paragraph` as `content_width_pt`); `html-
    scroll` deliberately never applies `margin-right` at all, since it
    has no frame width of its own to check it against (by design --
    see that converter's own module docstring) and the one real
    right_indent value found in the corpus is wildly oversized for a
    reflowed, viewport-width column.
  - `_drawfile_svg` was stretching a DrawFile's own bounding box to
    exactly fill the picture frame's box (two independent x/y scale
    factors derived from `width_pt`/`height_pt`), ignoring the frame's
    own declared display scale (`pict.xscale`/`yscale`) entirely --
    the exact stretch-to-fit bug already found and fixed for the PDF
    converter's `_draw_drawfile_picture` (Stage 9's own addenda), just
    never carried across to this converter when that fix landed.
    Reworked to mirror `_draw_drawfile_picture` exactly: size from
    `pict.xscale`/`yscale`, centre within the picture's own box (not
    stretch to fill), relying on the SVG viewport's own default clipping
    (plus an explicit `overflow: hidden`) the same way pdfdoc.py clips
    via an explicit rectangle. This also fixes `_drawfile_svg_text`'s
    own font-size formula "for free": it already divided its scale
    factor back through `_DRAW_UNIT_TO_PT` to recover "the dimensionless
    magnification the picture is actually being drawn at" (a pict-scale
    concept), but was being fed a bounds/frame-box stretch ratio instead
    -- unrelated to the picture's own declared scale, and the real
    source of the "text badly misplaced" half of the user's report.
  - Ten new regression tests (`paragraph_css_properties`'s margins/
    indent/alignment/spacing/line-height mapping, its right_indent
    clamp both with and without a known width, its fixed-line-height
    floor, and `_drawfile_svg`'s pict-scale centring), confirmed to
    fail against the pre-fix code (an import error for the first
    group, since `paragraph_css_properties` didn't exist yet) before
    being fixed. Full suite (323 tests) green; re-validated across all
    111 real documents with 0 crashes in all five output formats.

* **Post-Stage-14 fix (12)**: after fix (11), the user reported PCI_Spec
  looked "much better" but still had three symptoms: bold/italic/
  underline appeared to be missing everywhere, DrawFile SVG text was
  wildly oversized and overlapping, and a paragraph's own literal
  leading spaces (used as a poor man's first-line indent) weren't
  showing. The user then spotted the real, single root cause
  themselves from a raw HTML snippet: `style="font-family: Times,
  "Times New Roman", serif; ...` -- `_FAMILY_HINTS` and
  `_DEFAULT_FONT_STACK` (html_base.py) quoted multi-word font names
  with `"`, but the CSS this produces is always embedded inside a
  double-quoted HTML/SVG `style="..."` attribute; the embedded `"`
  terminates that attribute early, silently corrupting every property
  after `font-family` in the same style -- font-weight, font-style,
  text-decoration, colour, and (in DrawFile SVG `<text>` elements, the
  worst-affected case) `font-size` itself, which explains the "text
  too big" report: with no font-size applied at all, SVG text renders
  at the browser's own default. Fixed by using single quotes for the
  font names instead (`'Times New Roman'` etc.) -- valid CSS either
  way, and avoids the nesting entirely. One new regression test
  asserts no font_family_css result ever contains a `"`. Full suite
  (324 tests) green; re-validated across all 111 real documents with 0
  crashes in both HTML formats.
  (The leading-space indent issue is separate -- HTML collapses
  literal leading/repeated spaces by default, unlike pdfdoc.py's own
  manual token layout -- and is still open; likely fix is
  `white-space: pre-wrap` on paragraph CSS.)

* **Post-Stage-14 fix (13)**: after fix (12), the user reported paged
  HTML output "still looks bad": text doesn't span from a story's
  first frame into its later chain members (confirmed against
  PCI_Spec: page 3 showed only its DrawFile pictures with the rest of
  its own text area empty, then trailing blank pages). This was a
  known, already-documented limitation, not a regression -- the
  module's own docstring had flagged it since the converter was first
  built: "a story spanning a real multi-frame chain only ever renders
  in its first frame... the equivalent follow-up here, if wanted,
  would look much the same" as pdfdoc.py's own chain-flow work. Asked
  the user how accurate the fix should be (full pdfdoc.py-style
  measured flow vs. a quicker rough-heuristic split); they chose
  accuracy.

  Implemented `_flow_chained_story`/`_flow_items_into_containers`
  (html_paged.py): resolves a story's full frame chain the same way
  pdfdoc.py's own `_compute_chain_layout` does (including its
  real-chain-vs-independently-repeated-master-content distinction,
  returning `None` for the latter so it still falls back to the
  original single-frame handling, now logged with clearer wording),
  then distributes `story.paragraphs` across every chain member's own
  box using a *duplicate*, self-contained port of pdfdoc.py's
  approximate per-character text metrics (`_approx_width`, reusing the
  shared `font_metrics` data module pdfdoc.py already draws on) to
  estimate how many wrapped lines -- and how much vertical space --
  each paragraph needs at that frame's own width. Splits only at
  paragraph and `PageBreakMark` boundaries (a paragraph too long for a
  container's remaining space moves wholesale to the next one) rather
  than pdfdoc.py's own per-line granularity: a browser still does the
  actual within-frame line-wrapping natively once it knows which
  slice belongs where, so line-level positioning isn't needed the way
  it is for a PDF content stream. `_render_paragraph` was refactored
  into a new `_render_items` (a paragraph's own item list, or a
  `PageBreakMark`-delimited slice of one) so both the original
  single-frame path and the new chain-flow path share the same
  rendering code; a slice continuing from an earlier frame
  (`is_continuation`) suppresses `text-indent`/`margin-top`, since it
  isn't a true paragraph start. New helpers `_frame_page_map`/
  `_content_box_pt_for` mirror pdfdoc.py's own `_frame_page_map`/
  `_inset_box_pt_for` for resolving a chain member's geometry
  independently of page-walk order. Two regression tests: the
  pre-existing "unresolvable chain falls back and logs once" test
  (updated for the new log wording) and a new
  `test_real_multi_frame_chain_flows_text_across_frames`, mirroring
  pdfdoc.py's own `test_multi_page_chain_flows_text_across_frames`
  fixture almost exactly. Full suite (325 tests) green; re-validated
  across all 111 real documents with 0 crashes; PCI_Spec's own page 3
  (previously empty of text) now carries real flowed paragraph content
  confirmed structurally (visible text length and `<p>` count both
  non-zero, up from empty).

* **Post-Stage-14 fix (14)**: immediately after fix (13), the user
  found the new chain flow itself misbehaving in PCI_Spec: a nine-row
  revision-history table (0.0.1 through 0.0.9) that fits entirely on
  one page in the PDF was split across three separate pages three
  frames apart in paged HTML, with unrelated DrawFile pictures from
  whichever frame the overflow landed on appearing mixed in. Traced to
  `_estimate_slice_height_pt` not applying the same oversized-
  right-indent fallback `paragraph_css_properties` already does when
  actually rendering (fix (11)/(12)'s own `_MIN_USABLE_WIDTH_PT`
  clamp): the history table's own style has a `right_indent` almost
  exactly equal to the frame's own width (the same PCI_Spec style
  already known from pdfdoc.py's own `test_oversized_right_indent_
  falls_back_to_the_full_container_width`), so the *measured* width
  collapsed to a ~10pt sliver while the *rendered* CSS correctly
  dropped the indent and used the frame's own full width -- nearly
  every word in the estimate wrapped onto its own line, wildly
  inflating each row's estimated height. Fixed by applying the
  identical fallback (drop right_indent if it would leave less than
  `_MIN_USABLE_WIDTH` of the frame's own width) before estimating.
  One new regression test (`_estimate_slice_height_pt` with an
  oversized vs. a zero right_indent on the same text must produce the
  same estimate), confirmed to fail against the pre-fix code (120pt vs
  48pt) before being fixed. Full suite (326 tests) green; re-validated
  across all 111 real documents with 0 crashes; PCI_Spec's history
  table now lands entirely on one page, matching the PDF converter's
  own output exactly.

* **Post-Stage-14 fix (15)**: the user supplied a reference image
  (PCISpec-HTMLOverlap.png) showing PCI_Spec's DrawFile diagrams
  appearing doubled and overlapping the running text on one page in
  paged HTML. `html_paged.py`'s own per-page frame walk had no
  equivalent of pdfdoc.py's own, already-fixed `_draw_frame` check: a
  `PictureFrame` with a non-zero `embed_tag` is meant to be anchored
  *inline* within a text story, at the matching `EmbedMark`'s own
  position (`_render_embed`) -- never drawn independently at its own
  raw, page-relative box. Without that check, an embed-tagged picture
  rendered both inline *and* independently. Confirmed by comparing SVG
  counts before/after: page 3 had 5 `<svg>` elements (two distinct
  diagrams each appearing twice, plus one belonging to page 4 leaking
  in) before the fix, exactly 2 (one of each) after; page 4's own
  count dropped from a duplicate to the correct single copy. This was
  a latent, pre-existing gap, not a regression from fix (13)/(14) --
  it was only ever made *visible* once the multi-frame chain flow fix
  let a story's own text actually reach far enough into a chain to
  render the matching `EmbedMark` at all; previously that portion of
  the story was simply never rendered. Fixed by adding the identical
  `embed_tag` check pdfdoc.py already has, at the very top of
  `_render_frame`. One new regression test, directly mirroring
  pdfdoc.py's own `test_inline_drawfile_picture_pushes_following_text_
  below_it` fixture, asserting the SVG appears exactly once. Full
  suite (327 tests) green; re-validated across all 111 real documents
  with 0 crashes.

* **Post-Stage-14 fix (16)**: a significant correction, not just
  another patch. The user pushed back on fix (14)'s own "PCI_Spec's
  history-table style has a right_indent authored for a wider frame"
  explanation: inspecting the document directly, no named style is
  applied there at all (it uses the base body style), and its own
  ruler's tabs end well short of the frame's own right edge -- nothing
  that looks like an authoring mistake. The user supplied a full DDF
  export of the non-master content (PCI_SpecDDFAll, generated by
  Impression itself, not OvationPro -- corrected mid-investigation)
  confirming the base style's own declared `rightmargin 510.2pt` on a
  frame that's itself 510.2pt wide.

  Re-reading the conversion source (c/styles' own txstylesize/
  txwritedata) settled it: a right_indent's raw stored value's sign
  selects one of two *entirely different* placements the DDL `{right
  indent kind value}` directive can mean, matching the commented-out
  `INDENTABS`/`INDENTDELTA`/`INDENTOFFSET` enum and formulas right
  above it in the source -- a POSITIVE raw value emits DDL kind 2
  (INDENTOFFSET: `ix1 = xboxleftmargin + xright`, an offset from the
  frame's own LEFT edge), a non-positive one emits kind 0 (the default/
  else-branch formula, `ix1 = xboxleftmargin + xboxwidth - xright`, a
  genuine inset from the right edge). This project's own model already
  captured this distinction correctly as `Style.right_indent_is_delta`
  (`right_indent_raw > 0`) -- ovprodll.py's own DDL round-trip emission
  already uses it correctly -- but every *layout* converter (pdfdoc.py,
  html_base.py, html_paged.py) had always ignored it, treating every
  right_indent as the kind-0 inset case unconditionally. For PCI_Spec's
  base style (right_indent_raw positive, so kind 2/offset-from-left),
  that produced an available width of `frame_width - left_indent -
  510.2pt` -- next to nothing -- which fixes (11)/(12)/(14) had each
  independently, and incorrectly, worked around by detecting "close to
  the frame's own width" and dropping the margin entirely, rather than
  fixing the actual formula. Correctly resolved, `rightmargin 510.2pt`
  on a 510.2pt-wide frame lands almost exactly at the frame's own right
  edge (a normal, narrow margin) -- exactly matching the user's own
  reading of the document and the DDF export's own numbers.

  Replaced all three converters' band-aid "drop if oversized" fallbacks
  with the correct dual formula: `resolve_right_edge` (pdfdoc.py, a new
  small closure capturing the paragraph's own right_indent_pt/
  right_indent_is_delta, called with each container's own cx0/cx1
  since a paragraph can span more than one container), and equivalent
  direct branches in html_base.py's `paragraph_css_properties` and
  html_paged.py's `_estimate_slice_height_pt`. pdfdoc.py keeps a
  narrower defensive fallback (full container width) purely for
  genuinely-degenerate cases (e.g. a kind-0 inset that's simply too
  large for its own frame) -- not the primary mechanism it was
  mistaken for. html_base.py's positive-right-indent case still needs
  a known frame width to resolve into a CSS margin-right at all (so
  still never applies in scrolling HTML, per its own module docstring),
  but a *negative* right_indent (genuine kind-0 inset) now applies
  directly and unconditionally there too, needing no width reference --
  new behaviour, since the old code treated every right_indent
  identically regardless of sign.

  Rewrote/added regression tests across all three test files reflecting
  the corrected semantics (positive vs. negative right_indent_raw,
  each exercised on its own), including a directly-computed PCI_Spec-
  matching case (leftmargin 19.8pt, rightmargin 510.2pt on a 510.236pt
  frame resolves to a single, unwrapped line, not one word per line).
  Full suite (330 tests) green; re-validated across all 111 real
  documents with 0 crashes across pdf/html-scroll/html-paged; PCI_Spec's
  history table still lands entirely on one page (now for the actually
  correct reason), confirmed structurally.

* **Post-Stage-14 fix (17)**: the user reported PCI_Spec's paged HTML
  footer ("Sheet 1 / Issue F ****LIVE****") not right-aligning "Issue
  F", and only appearing on the first two pages, then vanishing from
  every later one. Two distinct bugs:

  - `_render_text_frame`'s `_rendered_dictionary_indices` set
    deduplicated by dictionary_index across the *whole document*, not
    per occurrence. That's correct for a real, chapter-anchored chain
    (now handled separately and correctly via `_chain_html`'s own
    per-frame cache), but wrong for independently-repeated master
    content (a running footer, whose frame_chain doesn't resolve
    against any one chapter): a master's own furniture frame is
    literally the same Frame object on every page that uses that
    master, so deduplicating by dictionary_index (or even by frame
    identity) both fail the same way -- only the very first page
    showing that master's footer ever rendered it, every later one was
    silently skipped. Removed the dedup entirely for this path; every
    occurrence now renders fresh and independently, matching pdfdoc.py's
    own already-established handling for the identical case.
  - Tabs were emitted as a literal `&#9;` character, which HTML's
    default whitespace handling collapses to nothing more than a
    single space -- never a jump to the style's own declared tab
    stop. The Nth tab in a paragraph is now positioned at the Nth
    entry of the style's own tab ruler (sorted by position; a tab
    beyond the ruler's last entry falls back to a fixed default
    pitch), each its own `position: absolute` span within the
    paragraph's own box (`position: relative`, added unconditionally)
    -- a centre/right/decimal stop (decimal simplified to right,
    matching pdfdoc.py's own convention) uses a CSS `transform` to
    centre/right-align on its stop without needing to know any other
    segment's own rendered width. Cross-checked numerically against
    the already-validated PDF converter's own output for this exact
    real paragraph: "Issue F ****LIVE****"'s own right edge in the PDF
    sits at exactly 530.08pt from the frame's own left edge, precisely
    matching the new HTML `left:530.08pt; transform:translateX(-100%)`.
    Scrolling HTML has no frame width of its own to position an
    absolute tab stop against (same limitation as right_indent, fix
    (16)), so a tab there is left as a plain visual gap instead (a
    literal tab character wrapped in `white-space: pre`, at least
    visible now rather than fully collapsing) -- not a real fix, just
    a smaller, honest improvement over full collapse.

  Two new regression tests (multi-page independent rendering; tab
  positioning against a real tab ruler), both confirmed to fail
  against the pre-fix code before being fixed. Full suite (332 tests)
  green; re-validated across all 111 real documents with 0 crashes in
  both HTML formats; PCI_Spec's footer now appears on all 8 pages with
  "Issue F ****LIVE****" correctly right-aligned.

* **Post-Stage-14 fix (18)**: immediately after fix (17), the user
  supplied a reference image (PCISpec-HTMLPagedOverwrite.png) showing
  PCI_Spec's "On Entry:"/"On Exit:" SWI-parameter rows (register name,
  a LEFT tab, then an often multi-line description) rendering with
  heavy overlapping, garbled text on page 5. Fix (17) made *every* tab
  kind `position: absolute`, including left -- correct for
  centre/right/decimal (which need to know their own eventual
  rendered width to align against a point, impossible without taking
  them out of flow), but wrong for left: `position: absolute` content
  contributes nothing to its own paragraph's height, so a left-tabbed
  row whose own description wrapped onto more than one line left its
  `<p>`'s own box far too short, and the next row started immediately
  underneath, overlapping the wrapped-but-layout-invisible text above
  it. A left stop is now rendered as an inline-block spacer of the
  right width instead, staying in normal document flow (wrapping and
  contributing height correctly), while centre/right/decimal keep the
  `position: absolute` + CSS-transform approach from fix (17)
  unchanged. One new regression test (a left-tabbed row with a long,
  wrapping description must contain no `position:absolute` and use an
  inline-block spacer instead). Full suite (333 tests) green;
  re-validated across all 111 real documents with 0 crashes; PCI_Spec's
  own "On Entry:"/"On Exit:" rows now use inline-block spacers, staying
  in normal flow.

* **Post-Stage-14 fix (19)**: the user supplied two more reference
  images (PCI_Spec-RealContents.png vs PCISpec-HTMLPagedContents.png)
  showing PCI_Spec's title block ("Distribution:", "Title:", "Issue:",
  ...) landing its own values in two visibly different columns
  depending on the label's own length, and its numbered Contents list
  similarly scrambled. Fixes (17)/(18) mapped "the Nth tab in a
  paragraph" to "the Nth declared tab stop" -- correct only when a
  style's own tab ruler never has more than one stop a single tab
  could reasonably reach, but wrong in general: real tab stops are
  reached by jumping to the *first declared stop past the current
  cursor position*, not by counting how many tabs have been typed so
  far. A style whose own ruler has several stops (the Contents list's
  own numbered-heading style has three) means two rows with a single
  tab each, but different amounts of text before it, can legitimately
  land on two different stops -- exactly the "wrong tab stops" the
  user described. `_render_items` now tracks an approximate running
  cursor position (`cursor_pt`, via `_approx_width`, already used for
  chain-flow height estimation) across each paragraph's own runs and
  marks/tabs, mirroring pdfdoc.py's own `_next_tab_stop` exactly, and
  picks the first stop the cursor hasn't already passed -- falling
  back to the same fixed default pitch pdfdoc.py uses when a paragraph
  has more tabs than its style declares stops for. Also fixed a
  related, smaller bug this surfaced: the CSS `left` position for a
  tab-positioned segment is relative to the `<p>` element's own
  (already `margin-left`-shifted) box, not the frame's left edge, so a
  style's own `left_indent` needed subtracting back out of each
  computed stop position -- previously omitted, silently
  double-counting `left_indent` for any tab-containing paragraph that
  also had one.

  Numerically cross-checked the *entire* re-converted title block and
  Contents list against the already-validated PDF converter's own
  output for the exact same real document: every value in the title
  block now lands at the identical frame-relative X (212.6pt)
  regardless of label length, and the Contents list's own chapter
  number/name/page-number columns match PDF to within sub-point
  rounding on every row checked. (The Contents heading's own position,
  which the user also flagged as looking wrong, turned out to match
  the PDF converter's own already-trusted output exactly too -- not a
  new bug from this fix, and out of scope for it if it's wrong at all.)

  Two new regression tests: the real, single-stop title-block ruler
  (confirming per-row convergence on one stop despite very different
  label widths) and a genuinely multi-stop synthetic ruler (confirming
  a longer label's own cursor position skips a stop a shorter label
  would have landed on) -- both confirmed to fail against the pre-fix
  code before being fixed. Full suite (335 tests) green; re-validated
  across all 111 real documents with 0 crashes.

* **Post-Stage-14 fix (20)**: a further reference image
  (PCISpec-HTMLPagedContents2.png) showed fix (19)'s title block now
  correct, but the Contents list's own two-digit chapter numbers
  (10-14) still badly broken -- chapter number, name, and page number
  all overlapping/misplaced, while single-digit chapters (1-9) were
  fine. Root cause: `append_measured` unconditionally advanced
  `cursor_pt` by a Run's own measured width, even while accumulating
  text destined for a `position:absolute` span opened by a preceding
  right/centre/decimal tab (`tab_span_open`). `cursor_pt` is already
  set to that tab's own `stop_pt` when the tab is processed (a
  right-aligned segment's own right edge lands exactly on the stop, by
  construction), so adding the segment's width again on top
  double-counted it. Invisible for narrow content (a single digit's
  extra width still left `cursor_pt` short of the next stop) but wrong
  for wider content (two digits' extra width pushed `cursor_pt` past
  the intended next stop, so the following tab's "first stop past
  cursor" search skipped it and landed on a later stop instead) --
  confirmed by tracing chapter 10's row HTML against chapter 1's:
  identical style/ruler, but the second tab resolved to a different
  stop. Fixed by only advancing `cursor_pt` in `append_measured` when
  `tab_span_open` is `False` -- text inside an absolute-positioned
  span still renders normally, it just no longer affects where a
  subsequent tab lands.

  Numerically cross-checked the regenerated Contents list against the
  already-validated PDF converter's own output: chapter 10's row page
  number ("7") lands at frame-relative X 439.37pt in both, computed
  independently (PDF via each span's own absolute bounding box minus
  the frame's own left edge; HTML via the tab stop arithmetic). All 14
  chapter rows now share the same number/name/page-number column
  structure as chapters 1-9.

  New regression test: a 3-stop right/left/right ruler mirroring the
  real Contents list's own structure, with two rows differing only in
  the first (right-tab) segment's width (1 vs 2 digits) -- confirms
  the second and third tabs land on the same stops regardless,
  confirmed to fail against the pre-fix code before being fixed. Full
  suite (336 tests) green; re-validated across all 111 real documents
  with 0 crashes.

* **Post-Stage-14 fix (21)**: the user separately reported PCI_Spec's
  3 DrawFile diagrams appearing repeated in the *scrolling* HTML
  output (embed-tagged pictures, anchored inline in running text via
  `EmbedMark`, rendering twice: once inline at the correct position,
  once more independently). This is exactly the bug fix (15), earlier
  in this session, already fixed for `html_paged.py`'s own
  `_render_frame` -- a `PictureFrame` with a non-zero `embed_tag` is
  drawn inline at its matching `EmbedMark`'s position only, never
  drawn again at its own raw, top-level position in the page's frame
  list -- but `html_scrolling.py`'s own `_render_frame` never got the
  equivalent check, so the same underlying bug persisted there,
  unnoticed until now. Confirmed against the real document: 3 unique
  `<svg>` diagrams, each present twice (6 total) in the pre-fix
  output, all 6 identical in pairs by content hash; the second copy of
  each fell wherever the frame happened to sit in the page's flat
  frame list (in this document, at the very end -- hence "on the last
  page" in the user's report, even though this converter has no page
  concept of its own). Fixed by adding the identical `embed_tag` skip
  check to `html_scrolling.py`'s `_render_frame`.

  New regression test, modelled directly on html_paged.py's own
  `test_embed_tagged_picture_frame_is_not_also_drawn_independently`:
  a text frame with an inline `EmbedMark` plus a separate,
  embed-tagged `PictureFrame` placed at an unrelated raw page
  position; confirms exactly one `<svg>` renders, confirmed to fail
  against the pre-fix code before being fixed. Full suite (337 tests)
  green; re-validated across all 111 real documents (both html-paged
  and html-scroll) with 0 crashes.

* **Post-Stage-14 fix (22)**: PCISpec-HTMLPagedContents3.png showed
  fix (20)'s tab-stop-selection fix hadn't actually restored right
  alignment at all -- the Contents list's chapter numbers were still
  clustered flush-left rather than right-aligned near the chapter
  names, for every row, not just 10-14. Root cause, found by building
  an isolated repro and rendering it through Prince (the paged-media
  engine available in this environment, standing in for a real
  browser): a centre/right/decimal tab's segment was rendered
  `position:absolute`, anchored via a CSS `left` computed relative to
  the `<p>`'s own box -- and a hanging-indent paragraph's own negative
  `text-indent` (needed so the chapter number sits left of the chapter
  name's own margin) turns out to *also* shift every
  `position:absolute` descendant on the paragraph's first line by the
  text-indent amount. (The CSS spec says an explicitly-positioned
  absolute box's containing-block edge shouldn't be affected by
  text-indent at all; Prince's own rendering of an isolated two-
  paragraph repro -- one without text-indent, one with, both
  containing an identically-positioned absolute span -- showed
  otherwise: the "left" value came out shifted by exactly the
  text-indent amount in the second case, not the first. Given the
  underlying quirk isn't specific to any one property this project
  controls, other engines rendering the same markup can't be assumed
  to differ.) A second, compounding bug: because the right-tabbed
  segment was removed from flow entirely, it contributed no width for
  a *following* left tab's own spacer to measure from, so that spacer
  (computed from `cursor_pt`, which assumed normal in-flow behaviour)
  landed the chapter name right after the paragraph's own hanging-
  indent start rather than after the (elsewhere-positioned) number.

  Fixed by removing `position:absolute` from tab handling entirely:
  every tab kind, including centre/right/decimal, now renders as an
  ordinary, in-flow inline-block spacer. A centre/right/decimal tab
  measures its own upcoming segment's width ahead of time (a small
  look-ahead helper, `segment_width`, scanning `items` from just past
  the tab up to the next `TabMark`/end) so the spacer can be sized to
  land that segment centred on, or ending at, the stop rather than
  starting there -- mirroring pdfdoc.py's own already-working
  `_segment_width`/`_tab_target_x` (this project's real reference
  for tab-stop arithmetic, since PDF's absolute coordinate model was
  never vulnerable to this particular quirk in the first place). Since
  nothing is ever removed from flow any more, `cursor_pt` tracking
  stays exact throughout and the text-indent quirk never applies; the
  `<p>`'s own `position: relative` (only ever needed to anchor the
  now-removed absolute children) was dropped too.

  While implementing this, realised `html_scrolling.py`'s tabs (a
  plain, unaligned literal tab character, per its own docstring) had
  been under-scoped from the start: the stated reason -- "this format
  tracks no frame width" -- is true for `right_indent` (an inset from
  the frame's own *right* edge, needing the real frame width to
  resolve), but a tab stop's own declared position is an absolute
  point offset from the paragraph's own *left* margin, needing no
  frame width at all. Ported the same in-flow spacer/look-ahead
  mechanism there too (a duplicated, self-contained `_approx_width`
  plus `segment_width`, matching this project's convention of
  independent converters) -- scrolling HTML's chapter numbers now
  right-align the same way paged HTML's do.

  Verified against the real document by rendering the regenerated
  paged HTML through Prince to PDF and extracting text span bounding
  boxes: chapter 1's "1" and chapter 10's "10" now both end within
  ~1.7pt of each other (the small residual gap being this project's
  own `_approx_width` heuristic vs. a real renderer's exact glyph
  metrics, the same known-and-accepted imprecision every other
  numeric validation in this project has -- not a structural
  placement bug), and every row's chapter name now starts at the same
  column. Also re-rendered the scrolling HTML through Prince and
  confirmed the same visual alignment there.

  Two new/rewritten regression tests (one per converter, each
  confirmed to fail against the pre-fix code -- html_paged.py's by
  reverting just that file and re-running, html_scrolling.py's
  because `_approx_width` didn't exist there pre-fix at all):
  `test_right_tabbed_segment_lands_via_an_in_flow_spacer_not_position_absolute`
  and `test_tab_lands_on_declared_stop_via_an_in_flow_spacer`, both
  mirroring the real Contents list's own three-stop right/left/right
  ruler with two rows differing only in the first segment's width (1
  vs 2 digit chapter number), checking three checkpoints -- number
  end, name start, page-number end -- land identically regardless.
  Three pre-existing html_paged.py tests that asserted on the old
  `position:absolute`/`transform` CSS were rewritten to check the new
  spacer-based output instead (their own underlying intent --
  consistent stop landing across differently-sized labels -- was
  unchanged, only the mechanism being asserted on). Full suite (338
  tests) green; re-validated across all 111 real documents (both
  html-paged and html-scroll) with 0 crashes.

* **Post-Stage-14 fix (23)**: the user added a sixth document to
  `corpus/` (`FieldWork,bc5`) and, testing it against the *PDF*
  converter specifically, reported a location-marker map's own dots
  in the wrong place and its label cut off. `pict.xshift`/`yshift`
  were never applied at all -- a picture's content was always centred
  in its frame, ignoring them entirely (an earlier investigation had
  tried applying them and rejected the attempt after it clipped away
  real content on every inline picture checked). What followed was a
  multi-round investigation, refined against a series of precise
  Impression picture-info-dialog readings (X offset, Y offset,
  scale%, and drawfile native size) the user supplied for four
  different real pictures on the same page, arriving at a final,
  ground-truth-confirmed formula:

  - `xscale`/`yscale` are the inverse of the picture's own displayed
    scale (`0x10000/xscale`) -- matches `ovprodll.py`'s own
    `_tr_setscale`, which this project's DDL output already relies on
    for the same fields.

  - `xshift`/`yshift` anchor the drawfile content's own bottom-left
    corner at `(anchor_x - xshift, frame_y0 - yshift)`. The original C
    DDL emitter's own "picturedata" block (`c/frames`' `ixpictdata()`)
    matched a real *embedded* picture's own dialog reading with y NOT
    negated, but a real *page-positioned* picture only matched with y
    negated -- the DDL's own bottomleft-y convention and Impression's
    own dialog-displayed y are apparently not the same sign for a
    page-positioned picture. Cross-checked independently of the
    dialog reading too: negating y improves an already-good picture's
    own overlap with its frame from 95% to a perfect 100%.

  - `anchor_x` is the frame's own LEFT edge for an *ungrouped*
    picture, but its RIGHT edge for a *grouped* one -- confirmed by
    cross-checking two real pictures against each other: the anchor
    that fixes one grouped picture (18% to 100% overlap) makes an
    already-correct ungrouped picture's own overlap markedly worse if
    applied to it too (100% to 31%), confirming the split by grouping
    is real, not a coincidence.

  - `anchor_x` additionally moves inward by the frame's own `hinset`:
    `x0+hinset` for ungrouped, `x1-hinset` for grouped. Confirmed by
    an exact 2.00mm discrepancy between a real picture's dialog x
    reading and the unadjusted formula, matching that picture's own
    `hinset/UNIT` precisely; a separate `hinset=0` picture matched the
    unmodified formula exactly, confirming this is `hinset`-specific.

  - when the shift would leave the frame's own area under 50% covered
    by content, the picture instead centres at native scale (an
    implausible-result safety check -- a real picture frame is never
    deliberately left almost entirely empty), additionally shrinking
    to fit first if native-scale content is bigger than the frame, so
    an untrustworthy shift never crops to a wrong region, only ever
    shows the whole picture zoomed out.

  Two of this document's own pictures never resolve to a trustworthy
  anchor despite an exhaustive search (300+ combinations of reference
  frame, edge, and sign) -- their own raw `xshift`/`yshift` still
  convert to their own dialog readings exactly, so the numbers are
  right, but no anchor reconciles them with their own (much smaller)
  frame. Both share something the four resolved pictures don't: the
  user confirmed Impression's own "Lock Values" dialog option is
  unticked for both, ticked for every resolved picture -- suggestive
  of a stale, inapplicable stored offset rather than a real crop
  position, though unconfirmed as the actual mechanism. The existing
  implausible-result check already falls back to centring for both,
  which -- if that theory holds -- is likely already the correct
  rendering, not merely an unsolved fallback.

  A further methodological lesson worth recording: the "≥50%
  frame-area overlap" trustworthiness check relied on throughout is
  weaker evidence than it first appeared. When a frame is much
  smaller than its own content, many different, individually wrong
  anchor choices can each independently reach 100% overlap, so a high
  ratio confirms a candidate is *plausible*, not that it's *correct*.
  Only an exact numeric match to a real dialog reading, or a direct
  visual comparison against a real reference image, actually confirms
  it -- both were used throughout wherever available, not the overlap
  ratio in isolation.

  Full regression test coverage for the final formula (anchor sign,
  grouped vs ungrouped, `hinset` adjustment, and the
  implausible-result fallback) is in `tests/test_output_pdfdoc.py`.
  The two remaining unresolved pictures, and the overlap-check
  caveat, are documented in `_draw_drawfile_picture`'s own docstring.
  Full suite green; re-validated across all 117 real documents with 0
  crashes throughout.

* **Post-Stage-14 fix (24)**: the same `FieldWork,bc5` document also
  showed bordered picture frames missing their grey drop-shadow/mat
  effect entirely. Traced to the original C DDL emitter (`c/frames`):
  every frame type's own border emission unconditionally pairs a
  `{shadow 0 0x3 {colourvalue COL_01 0x10000 0} {w 5669}}` alongside
  the `{border ...}` whenever any edge is present, regardless of the
  frame's own declared border colour. Drawn as 4 separate bands just
  outside the frame's own box (not one rect underneath the whole
  frame), so an unfilled frame's own sparse content (a DrawFile
  picture that's mostly bare strokes) doesn't show grey through the
  gaps. `COL_01`'s own RGB value isn't available to this project (an
  OvationPro-internal name, not decoded from the document itself), so
  it's approximated by sampling a real document's own rendered
  reference screenshot (~120,120,120).

* **Post-Stage-14 fix (25)**: the same document's own running footer
  only ever appeared on its very first page, across the whole 19-page
  document. `_resolve_content_chain_quietly` resolves a story's
  `frame_chain` relative to *one particular* chapter; for this
  footer, resolution happened to succeed by coincidence on the very
  first chapter it was drawn in (a length-1 "chain" that's really
  just that one chapter's own frame), and every later chapter's own
  attempt legitimately failed. The resulting layout was cached under
  `dictionary_index` alone with no chapter scoping, so every later
  chapter's own (entirely different, unrelated) frame silently looked
  up an empty result in the first chapter's cached layout instead of
  ever computing its own fresh flow. Keyed the cache by
  `(id(chapter), dictionary_index)` instead.

* **Post-Stage-14 fix (26)**: the same document also had three
  picture captions, and two diagram labels elsewhere on the same
  page, that never appeared at all -- empty bordered boxes. Traced to
  their own small frames sitting entirely inside a much larger, also
  repel-flagged frame: the chapter's own main body-text container,
  which needs to repel *its own* text around the smaller frames
  layered within it, but was also being treated as an obstacle to
  those smaller frames' text in the other direction. Since a small
  frame's own box sits entirely inside the big one, narrowing left no
  usable width anywhere, silently dropping all of its text -- unlike
  the genuine picture-repel case, which only partially overlaps.
  `_repel_obstacles_for_page` now additionally drops any obstacle
  whose own rect *fully encloses* the frame currently being laid out.

* **Post-Stage-14 fix (27)**: the same document's own table header,
  styled with a non-100% font aspect ratio, rendered as regular,
  unstretched text. `_render_line` (the main body-text path) never
  emitted PDF's own `Tz` (horizontal scaling) operator at all, unlike
  `_draw_drawfile_text` (text *within* a DrawFile picture), which
  already did. `font_aspect_ratio` maps directly onto a PDF `Tz`
  percentage (`0x10000` raw = unity, no inversion -- confirmed
  against `ovprodll.py`'s own DDL emission, which maps this field
  straight onto a style's `scale` property). `Tz` is text *state*,
  not reset by `ET`/`BT` like `Tm`, so a later token with no aspect
  ratio at all now explicitly resets to 100% rather than silently
  inheriting an earlier token's stretched value. `_approx_width` also
  now folds in the same aspect ratio, not just `Tz` at render time:
  applying `Tz` alone stretched the header wide enough to visibly
  overlap the next column, since the column's own tab stop was still
  positioned assuming 100%-width text.

* **Post-Stage-14 fix (28)**: two further issues found in the same real
  document (FieldWork), reported with exact millimetre readings from
  Impression's own picture info dialog for the affected pictures:

  * A picture's own caption text ("Groyne") was cropped at the top of
    its frame. Root cause: `_draw_drawfile_picture` sized and
    positioned a DrawFile's content using only the file header's own
    declared bounding box (`draw.bounds`), but a real DrawFile's
    header bounds can be smaller than its own content's true extent --
    here, a `DrawText` object's own baseline+ascent extended above the
    file header's own declared top edge, even though that same text
    object's own individually-decoded bounds (from its own object
    header) correctly included it. Sizing from the header bounds alone
    under-measured the content's real height, letting its true top
    edge run past the picture frame's own clip rectangle. A new
    `_drawfile_effective_bounds` unions the file header's own bounds
    with every object's own bounds (recursing into groups and tagged
    objects) and is now used in place of the raw header bounds.
  * Two pictures rendered with a visible grey border/shadow box that
    Impression itself does not show for them (confirmed directly: the
    user reported both as bordered on the PDF but borderless in
    Impression). Both had `border0`..`border3` all `0`. At the time
    this was diagnosed as `has_border` wrongly treating `0` as present
    (matching the original TransIMP C converter's own `!= 0xFF` check
    verbatim) rather than as a second "no border" sentinel alongside
    `0xFF` -- **retracted by fix (30) below**, which found real
    evidence `0` is a genuine, deliberately-chosen border style, not a
    sentinel; the two pictures' own real explanation is still open.

  Each of fixes (23)-(28) was verified independently (re-rendering
  the real document and comparing against its own reference
  screenshot where one was supplied) and has its own regression test,
  each confirmed to fail against the pre-fix code before being fixed.
  Full suite green; re-validated across all 117 real documents
  (`examples/` + `moreexamples/` + `corpus/`) with 0 crashes
  throughout.

* **Post-Stage-14 fix (29)**: a regression introduced by fix (28)'s
  own `_drawfile_effective_bounds`. Unioning bounds from *every*
  object (not just the ones actually rendered) pulled in a real
  document's own run of 5 objects of an unrecognised type, all
  declaring an identical, tiny `(0, 0)`-`(10, 10)` bounding box
  unrelated to the picture's real visible content -- not a genuine
  spatial extent, apparently a fixed/dummy value that object kind
  always carries. This inflated an otherwise-correctly-positioned
  sibling picture's own measured size, corrupting its display scale
  and, via that, its own already-confirmed crop position. Fixed by
  restricting the union to `DrawPath`/`DrawText` objects only (still
  recursing into `DrawGroup`/`DrawTagged` wrappers, but not trusting
  their own declared bounds directly either). Regression test added;
  confirmed to fail against the pre-fix code.

* **Post-Stage-14 fix (30)**: retracts fix (28)'s border0-3 change.
  The user built a controlled test document (`corpus/TestDoc,bc5`)
  with frames explicitly set to each of Impression's own "Border 1"
  through "Border 10" styles, plus one with no border: the raw bytes
  confirmed the UI's 1-based numbering maps directly onto the stored
  byte 0-based ("Border 1" -> `0`, "Border 2" -> `1`, ... "Border 10"
  -> `9`), and only `0xFF` means no border. `0` is a real,
  deliberately-chosen style like any other, not a sentinel --
  `has_border` now goes back to the original `!= 0xFF` check (fix
  (28)'s own two pictures' real explanation is still open). Every
  non-`0xFF` style currently renders identically (a thick grey
  shadow box); matching each style's own real appearance (thin line,
  grey fill band, asymmetric drop-shadow, double-line, rounded
  corners -- confirmed visually distinct via the same test document)
  is tracked as further work, not yet started.

* **Post-Stage-14 fix (31)**: the picture xshift/yshift/hinset/
  grouped anchor formula (fixes (23)-(26)) positioned pictures using
  the *content's own declared bounding-box corner* as the anchored
  point. Two further purpose-built calibration documents (added to
  `corpus/TestDoc,bc5` as further pages: a grid of pictures at known
  xshift/yshift/scale values compared pixel-for-pixel against
  Impression's own rendering, and a second, cleaner one using plain
  shapes drawn starting exactly at a drawfile's own `(0, 0)`, at
  round-number millimetre offsets) showed this was wrong: the anchor
  point is actually the drawfile's own *native `(0, 0)` origin*, not
  either corner of its own declared bounds -- the two only coincide
  when a picture's own content happens to start exactly at `(0, 0)`,
  which is not guaranteed. `_draw_drawfile_picture`'s own docstring
  has the full derivation, including two superseded intermediate
  formulas that each matched some real data before being shown wrong
  by further calibration data -- notably, fixes (23)-(26)'s own
  formula had been confirmed against a real document's exact
  dialog-reported x/y/scale values, which is not by itself sufficient
  confirmation of the anchor point on a picture bigger than its own
  frame (the frame ends up fully covered regardless of which anchor
  is used). This also fixed two real pictures in the same document
  that an exhaustive search (300+ combinations) could never
  previously anchor correctly. The "implausible result" overlap
  safety net was also fixed alongside this: it compared the shifted
  content's own overlap against the *frame's* area alone, which is
  mathematically unreliable whenever the picture is smaller than its
  own frame (its own full area is the most it could ever cover, often
  under 50%) -- now measured against whichever of the frame or the
  content is smaller, with a much lower threshold (5%, down from
  50%), since the calibration data included legitimate crops covering
  as little as ~41% of a picture's own area. `corpus/TestDoc,bc5`
  (the calibration document itself, containing only synthetic
  geometric shapes, no personal content) was committed alongside this
  fix and is now exercised automatically by
  `tests/test_corpus_documents.py`. Regression tests added; confirmed
  to fail against the pre-fix code. Full suite green; re-validated
  across all 117 real documents with 0 crashes.

* **Post-Stage-14 fix (32)**: fix (31) carried forward, unconfirmed,
  an older special case: a grouped picture's own x anchored from the
  frame's own *right* edge rather than its left. The user reported a
  real document's own map still rendering wrong after fix (31) --
  missing its own compass marker and every other landmark entirely,
  clearly showing the wrong region of its own content -- despite the
  picture being grouped, the exact case that special case was meant
  for. Re-checking against the real document's own reference
  screenshot confirmed a plain left-edge anchor (the same formula as
  every ungrouped picture) was correct instead; a second grouped
  picture in the same document, previously cropping its own caption
  text, matched its own reference too once switched to the same
  left-edge anchor. The right-edge special case is now removed
  entirely -- "grouped" turned out to have no bearing on the anchor
  at all, and its earlier appearance of mattering was itself an
  artifact of the bounding-box-corner formula fix (31) already
  superseded (see `_draw_drawfile_picture`'s own docstring for the
  full history). Regression test updated to confirm a grouped and an
  ungrouped picture, given the identical frame/xshift/yshift, now
  render identically. Full suite green; re-validated across all 117
  real documents with 0 crashes.

* **Post-Stage-14 fix (33)**: implements DrawFile picture rotation
  (`pict.angle`), previously unimplemented (logged once per picture,
  drawn unrotated). The user extended `corpus/TestDoc,bc5` with a
  third row of otherwise-identical, unshifted pictures at 15, 30, and
  45 degrees; tracking the point where two adjacent shapes within the
  picture meet (pixel-for-pixel against Impression's own rendering)
  confirmed `angle` is a standard mathematical (counter-clockwise)
  rotation about the drawfile's own native `(0, 0)` origin -- the same
  point `xshift`/`yshift` anchor -- applied *before* that anchor's own
  translation. Implemented in both `pdfdoc.py` and `html_base.py`'s
  SVG output by rotating the raw drawfile-space point before the
  existing scale/translate in each converter's own `to_pt`/`to_svg`
  closure; the PDF side is pixel-matched against the calibration
  document's own reference screenshot exactly (including the 45-degree
  case, where the rotated square's own corner is cropped by the
  frame), the SVG side extended analogously but not independently
  re-verified against a reference image. A rotated picture's own
  bounding box is not itself recomputed for the rotated extent (still
  uses the unrotated bounds for the "does this shift look trustworthy"
  check and the shrink-to-fit fallback) -- a known, currently
  unexercised gap, not something the calibration document's own
  otherwise-small pictures needed. Regression test added (a clean
  90-degree case, chosen for exact rather than approximate expected
  coordinates); confirmed to fail against the pre-fix code.

  Also stops logging DrawFile "Options" objects (type 11: per-file
  editor settings such as grid/zoom state, with no rendering component
  of their own) as best-effort -- the user confirmed nearly every real
  DrawFile carries one, so logging it as "not decoded and omitted" was
  reporting nothing was actually lost. `formats/drawfile.py` now names
  this type (`OPTIONS_TYPE`) so both output converters can skip it
  specifically, while still logging any other genuinely-undecoded
  object type as before. Regression test added; confirmed to fail
  against the pre-fix code (two existing tests' own synthetic "unknown
  type" fixtures happened to already use type 11 for an unrelated
  reason and were updated to a genuinely-arbitrary type number
  instead, to keep testing what they originally intended).

  Full suite green (391 tests); re-validated across all 117 real
  documents with 0 crashes.

* **Post-Stage-14 fix (34)**: implements Impression's own ten "Border
  1".."Border 10" UI border styles distinctly, replacing the single
  generic rendering (a plain line plus a fixed grey drop-shadow,
  applied identically regardless of border0..3's stored style byte)
  every non-`0xFF` border used before. That generic rendering was
  itself inherited from the original C DDL emitter (`c/frames`), which
  never distinguished the ten styles either -- it always paired one
  hardcoded OvationPro border look ("1_Plain") with a fixed shadow
  regardless of the source document's own choice. The ten styles'
  actual appearance isn't recorded anywhere in the format itself, so
  it was reconstructed by extending `corpus/TestDoc,bc5` with a page 2
  containing one frame per style (labelled "Border 1".."Border 10")
  plus two frames each combining several styles across their own four
  edges, and measuring a screenshot of how Impression itself renders
  it (`TestDoc-Real2NoDots.png`) pixel-by-pixel -- including a
  connected-component trace of styles 6/7's own "notched shadow"
  outline to work out their exact per-edge geometry. See
  docs/impression-documents.xml's new note under "Frame object common
  layout" for the full description of all ten styles.

  `pdfdoc.py`'s `_draw_box` now dispatches each present edge to
  `_draw_border_edge` individually (border0..3 select per-edge, as
  Impression itself allows): styles 1-3 are progressively wider plain
  lines; 4-5 are solid grey/light-grey fill bands outside the frame's
  own box (reusing the old generic shadow's own measured width/colour
  for style 4 exactly, since that turned out to be a real, if
  coincidental, match); 6-7 are the same band with two diagonally
  opposite corners left as clean notches, mirrored between the two
  (`_draw_notched_shadow_edge`); 8 is a thin line plus a thicker one
  further out; 9 is two thicker lines with all four corners notched
  (not just two); 10 is a thick line, rendered as a single
  whole-frame rounded-rectangle stroke (`_draw_rounded_border`) when
  every edge shares the style -- a corner radius inherently needs to
  know about two edges at once, so a style-10 edge mixed with other
  styles falls back to a plain straight thick line instead. Five new
  regression tests added (styles 1, 4, 6, and 10-on-every-edge, plus
  the pre-existing all-four-edges/only-present-edges tests kept);
  confirmed to fail against the pre-fix code.

  `html_paged.py` (the CSS-based paged-media converter) gets a
  lower-fidelity approximation of the same ten styles
  (`_border_css_for_style`): it has no way to draw a separate filled
  band or a notched outline outside a `<div>`'s own box, so styles 3-7
  degrade to a plain, correspondingly widened/coloured line (4-5 still
  use the fixed grey/light-grey rather than the frame's own border
  colour); CSS's own native `double` border style is used for 8-9
  (a closer match than hand-building two lines would be); 10 rounds
  every corner of the whole frame via `border-radius` under the same
  all-four-edges condition as the PDF converter. `html_base.py`'s
  scrolling HTML output draws no frame borders at all by design (it
  drops page furniture entirely for a linear reflow) and needed no
  change. Three new regression tests added; confirmed to fail against
  the pre-fix code.

  Full suite green (397 tests); re-validated across all 111 real
  documents in examples/ and moreexamples/ with 0 crashes.

* **Post-Stage-14 fix (35)**: corrects three of fix (34)'s ten border
  styles after the user reviewed the rendered output against three
  further, more targeted reference images -- each showing a single
  style with one or more edges deliberately turned off, isolating
  exactly the detail fix (34) got wrong:
  - Styles 1-3 (plain lines) and 10 (rounded) were centred directly on
    the frame's own boundary (half in, half out), eating into the
    frame's own interior; TestDoc-Real2Border2+3.png (left edge off on
    both reference frames) showed the line sitting entirely outside
    the boundary instead, and its open (unjoined) end rounded rather
    than square. Fixed by offsetting every such line outward by half
    its own width before stroking (_draw_line_with_caps), and adding a
    filled-circle cap at whichever end has no adjoining edge to butt
    against (_edge_endpoints/_filled_circle). _draw_rounded_border
    (style 10 on every edge) gets the same outward expansion.
  - Styles 4-5 were a plain axis-aligned rectangle band with an
    equal-weight keyline on all four of its own sides, and style 5 was
    assumed to be a flat light grey. TestDoc-Real2Border4+5.png (top
    and bottom edges only, on both reference frames) showed a proper
    45-degree-mitred picture-frame-moulding band instead -- naturally
    forming a clean mitred joint wherever two edges share the style,
    with no special-casing needed -- with a markedly thicker keyline
    on the band's own *outer* edge only, and style 5 rendering as a
    transparency checkerboard in the screenshot rather than a filled
    colour at all (the earlier "light grey" reading had actually
    sampled that same checkerboard, aliased down to a near-uniform
    grey, from a different, already-flattened reference image).
    Rewrote _draw_border_band as a mitred trapezoid per edge, taking
    an `Optional` fill colour (None for style 5).
  - Styles 6-7 retracted their own "short" end only as far as the
    frame's own true corner, and drew no separate outline at all, so
    the notch corners this produced were plain white gaps rather than
    showing a thin line through them. TestDoc-Real2Border6+7.png (a
    single, otherwise-isolated reference frame per style, left edge
    off) showed the short end actually retracts a *further* band-width
    inward, past the frame's own corner, and that a thin outline at
    the frame's own true edge is drawn underneath every band
    regardless -- invisible wherever the thick band covers it, visible
    in the resulting gap. Rewrote _draw_notched_shadow_edge with the
    corrected full/short offsets and the added thin outline; this also
    reversed which end of the top/bottom edges is "full" vs "short"
    for both styles, which the single-frame reference image made
    unambiguous in a way the original multi-frame reference image
    (fix (34)'s only source for these two styles) hadn't been.

  All three corrections were verified the same way as fix (34)'s
  original derivation -- connected-run/gap pixel tracing of the new
  reference images, not eyeballing -- since eyeballing was exactly
  what missed these details the first time. 7 new/rewritten regression
  tests; confirmed to fail against the pre-fix code. Full suite green
  (398 tests); re-validated across all 111 real documents with 0
  crashes.

* **Post-Stage-14 fix (36)**: two further direct corrections from the
  user after reviewing fix (35)'s own output. Styles 1-3 (plain lines)
  were only round-capped at whichever end had no adjoining edge,
  keeping a flat/butt cap wherever a neighbour was present -- the user
  confirmed all four of a line's own ends (left, right, top, and
  bottom) are round-capped regardless, not just the open ones;
  `_draw_box` now always passes `True, True` to `_draw_line_with_caps`
  instead of computing per-edge neighbour presence. Style 5 ("Border
  5") had been changed to unfilled in fix (35), on the reasoning that
  TestDoc-Real2Border4+5.png's own checkerboard rendering meant no
  fill at all -- the user confirmed directly that it should be a light
  grey fill instead (the checkerboard was Impression's own editor
  showing an unfilled *selection* state, not the style's actual
  printed appearance); reinstated the original _BORDER5_COLOUR_RGB
  fill. 2 tests rewritten to match; confirmed to fail against the
  pre-fix code. Full suite green (398 tests); re-validated across all
  111 real documents with 0 crashes.

* **Post-Stage-14 fix (37)**: Style 9 ("Border 10") applied to fewer
  than all four of a frame's edges previously fell back to four
  independent straight thick lines (no rounding at all), since
  `_draw_box`'s whole-frame rounded-rectangle path was only attempted
  when all four edges were present. The user's own
  TestDoc-Real2Border10.png added a "Border 10, no left" reference
  frame specifically to show the real behaviour: the rounded path is
  still used whenever every *present* edge is style 9 (regardless of
  how many, now down to `present and all(...)` with the `len(...) ==
  4` requirement dropped), and the two corner arcs adjoining a missing
  edge are still drawn as complete quarter-circles -- only the
  straight run between them is omitted, ending each arc in a flat cut
  rather than either extending further or being replaced by a square
  corner. `_draw_rounded_border` now takes an `edge_present` map and
  builds its path from an explicit, always-drawn-arcs/
  conditionally-drawn-straights segment list, tracking pen-up/pen-down
  state to start a fresh subpath after each skipped edge and only
  closing the path (`h`) when every edge is actually present. New
  regression test (three edges present, left absent): asserts all four
  corner arcs are still drawn, the path is left open (no `h`), and it
  splits into two disconnected subpaths either side of the gap;
  confirmed to fail against the pre-fix code. Full suite green (399
  tests); re-validated across all 111 real documents with 0 crashes.

  The same reference image batch also included TestDoc-Real2Border8+9.png
  and its "NoDotted" counterpart (the dotted rectangle in both is
  Impression's own frame-bounds marker, confirmed by the user to not be
  part of the border itself, and already correctly never drawn by this
  converter) -- reviewed against the existing styles 8/9
  implementation from fix (34) and found consistent with them already
  (thin line at the frame boundary, a Border-2-weight line further out
  for style 8; two notched lines for style 9), so no further change was
  needed there.

* **Post-Stage-14 fix (38)**: two further direct corrections from the
  user after reviewing fix (37)'s own output.
  - "10 still looks too close to the frame": Border 10 was drawn
    touching the frame's own boundary directly, like every other
    line-family style. TestDoc-Real2Border10.png's own dotted
    frame-bounds marker (confirmed by the user to be purely a bounds
    indicator, not part of the border) sits well clear of the rounded
    line in the reference image -- pixel-measuring the gap against the
    marker's own known real-world width (from the frame's actual box
    coordinates, hinset/vinset both 0 in this test document, so the
    dotted marker and the frame's true edge coincide exactly) gave a
    gap roughly three times the border's own thickness. Both
    `_draw_rounded_border` (the whole-frame path) and the style-9
    branch of `_draw_border_edge` (its single-edge fallback, via
    `_draw_line_with_caps`'s own `base_offset`) now hold the border
    that same `thick * 3.0` gap off the frame's boundary before the
    usual non-encroaching half-width offset.
  - "8 and 9 don't seem to meet at the corners": fix (34)'s original
    style 7/8 implementation drew each edge's own line only the length
    of that edge (x0 to x1, or y0 to y1), so two perpendicular lines
    sharing the same style and offset fell a small diagonal gap short
    of actually meeting at the shared corner -- worse for style 8,
    which fix (34) had additionally (and, per this correction,
    wrongly) inset deliberately, based on a misreading of an earlier,
    less clear reference image as showing notched corners. New
    `_draw_border_ring_line` helper extends each line by its own
    offset at both ends instead, so it reaches exactly the point a
    same-offset perpendicular neighbour's own line crosses it -- a
    plain mitred join wherever both edges share the style, matching
    TestDoc-Real2Border8+9NoDotted.png's own "Border 8"/"Border 9"
    reference frames (every corner fully closed, no notches at all).

  3 new regression tests (one per correction); confirmed to fail
  against the pre-fix code. Full suite green (402 tests); re-validated
  across all 111 real documents with 0 crashes.

* **Post-Stage-14 fix (39)**: brings `html_base.py`/`html_paged.py` up
  to date with pdfdoc.py's own, since-corrected picture-positioning and
  border rendering, at the user's explicit request once the PDF side
  was considered good enough to commit. Both areas had fallen behind:
  `html_base.py`'s `_drawfile_svg` still only ever centred DrawFile
  content (never applying xshift/yshift at all) and used `draw.bounds`
  directly rather than `_drawfile_effective_bounds`'s own corrected
  union -- both pre-dating pdfdoc.py's own xshift/yshift formula and
  bounds fix entirely, simply never carried across; `html_paged.py`'s
  border styles hadn't been touched since fix (34)'s first pass, so
  none of fixes (35)-(38)'s corrections (non-encroaching/round-capped
  lines, mitred bands, the corrected offset-shadow geometry, Border
  10's own gap, styles 8/9 meeting cleanly at corners) had reached it.

  `_drawfile_svg` now ports pdfdoc.py's own `_draw_drawfile_picture`
  formula directly (a duplicate, self-contained copy, matching this
  project's own convention of independent converters rather than
  shared code): the frame's own box is treated as [0,0]-[width_pt,
  height_pt] in Y-up pt space, identical to pdfdoc.py's own
  page-absolute box, with the SVG-specific Y-down flip applied only
  once, at the very end, in `to_svg` itself, rather than threaded
  through the whole derivation the old centring-only version needed
  it for. `_drawfile_effective_bounds` is likewise a duplicated copy
  of pdfdoc.py's own static method. Every caller already passes the
  picture's own real, stored frame box (this module never recomputes
  one the way pdfdoc.py's own embedded-picture path does), so
  xshift/yshift apply unconditionally, with no pdfdoc.py-style
  apply_shift=False case needed at all.

  `html_paged.py`'s `_border_css_for_style` (used per-edge whenever a
  frame's four edges don't all share one style) gets updated
  thickness/colour constants matching the current pdfdoc.py styles,
  plus two new whole-frame special cases in `_render_frame` alongside
  the existing Border-10-on-every-edge one: styles 5/6 uniform across
  all four edges now use a CSS `box-shadow` (a much closer visual
  approximation of pdfdoc.py's own offset "hard shadow" band than a
  plain line, though not pixel-matched -- no mitred/notched geometry
  or separate thin outline). Border 10's own whole-frame case switches
  from `border`+`border-radius` to `outline`+`outline-offset`+
  `border-radius`: unlike `border`, an outline sits outside the div's
  own layout box without affecting it (no encroachment, matching
  pdfdoc.py's own non-encroaching offset for every line style) and
  `outline-offset` gives Border 10's own visible gap from the frame's
  boundary directly, both closer native matches than the plain
  border this replaced (which encroached and sat flush with no gap).
  A per-edge mix of styles (rare) still falls back to the simpler
  per-edge line approximation, since none of `outline`/`box-shadow`
  can vary by edge.

  4 new/rewritten regression tests, confirmed to fail against the
  pre-fix code (two for `_drawfile_svg`'s own xshift/yshift and
  correct-not-stretched sizing, two for the new box-shadow/outline
  whole-frame border cases). Full suite green (404 tests); re-validated
  across all 111 real documents, all three formats (`pdf`, `html-paged`,
  `html-scroll`), with 0 crashes.

* **Post-Stage-14 fix (40)**: two further direct corrections/requests
  from the user after reviewing fix (39)'s own output.
  - "it would be good to have the bordering on those elements on
    scrolling": html_scrolling.py drew no frame borders at all --
    unlike html_paged.py, it has no absolute frame geometry to
    position a border against, but border0..3 is independent of
    geometry, so there was no real reason borders couldn't be added
    via CSS regardless. The border-CSS generation itself (previously
    html_paged.py's own `_border_css_for_style`/`_render_frame` logic)
    is moved into html_base.py as a shared `border_css_declarations`
    function (plus `_border_css_for_style`, `_BORDER4_GREY_CSS`,
    `_BORDER5_GREY_CSS`, `_SHADOW_WIDTH_PT`) -- generating CSS text is
    presentation-mapping code like `colour_to_css`/`style_css_properties`
    already shared there, not a page-geometry concern the two HTML
    formats need to solve differently, so this is shared rather than
    duplicated (unlike, say, `_drawfile_svg`'s own positioning formula
    in fix (39), which mirrors pdfdoc.py's own page-geometry logic and
    stays a deliberate duplicate). html_paged.py's `_render_frame`
    now just calls the shared function; html_scrolling.py gains its
    own `border_width_pt` constructor parameter (matching
    html_paged.py's) and wraps picture/text frame content in a
    bordered `<div>` (`<span>` for an inline embedded picture, to stay
    valid HTML inside an already-open `<p>`), with hinset/vinset
    becoming CSS padding inside the border the same way html_paged.py's
    own frame div already does.
  - "the right [corners], the ones that join together ought to be
    joined with a curve": fix (39)'s own Border-10-rounding only
    special-cased all-four-edges-present (an `outline`, which can't
    vary per edge, rounded via `border-radius`); a frame with Border
    10 on only some edges (TestDoc-Real2Border10.png's own "Border 10,
    no left" reference frame) fell all the way back to plain, square
    -cornered per-edge lines, even at the two corners whose own edges
    were both still present and both still Border 10. `border_css_de
    clarations` now additionally sets `border-<corner>-radius`
    per-corner (independent of the whole-frame `outline` special
    case) whenever a specific corner's own two adjoining edges are
    both Border 10, regardless of the other two edges -- so two
    Border-10 edges that actually meet still curve into each other
    even when a third edge is a different style or missing outright,
    matching the reference image (only the two corners adjoining the
    genuinely-missing edge stay square).

  5 new regression tests (two scrolling-border, one partial-Border-10
  corner-rounding, matching pre-existing coverage for the other two
  html_paged.py cases already carried over unchanged); confirmed to
  fail against the pre-fix code. Full suite green (407 tests);
  re-validated across all 111 real documents, all three formats, with
  0 crashes.

* **Post-Stage-14 fix (41)**: dynamic text repel (fix (5)/PBServer)
  only ever narrowed a text line around a repel-flagged frame's plain
  rectangular `exx0..exy1` box, even when that frame is a picture with
  an irregular (non-rectangular) boundary of its own (`PictureFrame.
  boundary`, already decoded -- see model/frames.py's own
  `_decode_boundary_path` -- and already used to clip the picture's
  own drawn content, but never consulted for text repel). The user
  supplied a real document, NVMeFlyer,bc5, whose page 2 has exactly
  this: an octagonal picture boundary (confirmed against two reference
  screenshots the user also supplied, one showing Impression's own
  irregular-frame edit handles) that body text visibly hugs, rather
  than stopping at the picture's plain rectangular edge the way the
  previous PDF output did.

  `_boundary_repel_rects` (pdfdoc.py) approximates the boundary
  polygon as a stack of horizontal slice rectangles, one per Y
  interval between consecutive boundary vertices, each spanning the
  polygon's own X-extent within that band -- exact for a convex
  boundary (the common case for a hand-drawn crop shape), an
  over-inclusive approximation for a concave one. `_repel_obstacles_
  for_page`'s own `add()` now calls this (feeding several slice
  rectangles into its existing per-obstacle-rectangle list) instead of
  the plain box, whenever a repel-flagged frame is a picture with a
  boundary -- `_narrow_for_obstacles`'s own existing per-line Y-band
  overlap test needed no changes at all, since a slice is just another
  obstacle rectangle whose Y-range happens to be narrow.

  Each slice's own edges are sampled at both of the slice's own Y
  endpoints, not its midpoint: a first version sampled only the
  midpoint, which under-narrowed the real document's own first text
  line next to a steep top corner of the octagon, letting one extra
  word run fractionally under the picture's own drawn edge -- since
  every active edge within one slice is a straight line (no further
  vertex sits strictly inside a slice, by construction), its own X
  value moves monotonically between the slice's two endpoints, so the
  min/max across both endpoints is the true X-extent, not merely an
  approximation of it. Confirmed pixel-for-pixel against the real
  document once this was corrected: the first wrapped line's own text
  now breaks at exactly the same word as the reference screenshot.

  3 new tests (one direct `_boundary_repel_rects` slicing check on a
  diamond, one degenerate-boundary edge case, one confirming
  `_repel_obstacles_for_page` returns several narrow slices rather
  than one box for a boundaried picture); full suite green.

### Stage 15 (new, at the user's request) — `extract` subcommand

Once the PDF and HTML output was considered good enough to commit, the user
asked for a way to pull a document's own content out as separate,
standalone files for reuse in other tools, rather than only ever rendering
the whole document as one file in one format the way `convert` does.

* `output/extract.py`'s `ExtractConverter` (a new `HTML5Converter`
  subclass, reusing its shared CSS/SVG-object-emitter helpers rather than
  duplicating them, unlike this project's usual per-converter-duplication
  convention -- justified here since extraction genuinely needs no
  page-geometry logic of its own, only the same presentation mapping
  html_base.py already centralises) walks `document.dictionary` directly
  (not the chapter/page tree `convert`'s converters walk), one file per
  object-dictionary entry, keyed by that entry's own index:
  - `text/NNNN.txt`: a text story's own plain text, all styling/layout
    stripped (Run text concatenated with blank lines between paragraphs;
    tabs/merge-field/embedded-picture marks rendered as simple inline
    placeholders; page/chapter/heading-number marks and forced page breaks
    dropped entirely, since they're only meaningful relative to a specific
    page layout this format has no equivalent of).
  - `html/NNNN.html`: the same story as a standalone HTML document, with
    inline CSS from html_base.py's own `style_css_properties`/
    `paragraph_css_properties` (font family/size/weight/style, colour,
    paragraph alignment/indent/spacing) -- explicitly not required to be
    pixel-accurate (no line-wrapping, tab-stop measurement, or frame-chain
    flow the way html_scrolling.py's fuller machinery does), just a
    readable approximation of the source document's general look.
  - `images/NNNN.<ext>`: a picture's own raw embedded bytes. EPS is
    unwrapped to genuinely standalone PostScript via `EPSObject.data`
    (stripping Impression's own wrapper header); a "drawable-family"
    dictionary entry is decoded to tell a real DrawFile (`.draw`) from a
    Sprite (`.sprite`) apart, the same way html_base.py's own
    `_picture_html_for_data` already does; anything else falls back to
    `.aff` (ArtWorks) or `.bin` (unrecognised), dumped verbatim.
  - `svg/NNNN.svg`: a DrawFile picture re-rendered as a standalone SVG at
    its own *native* size -- deliberately not html_base.py's own
    `_drawfile_svg`, which renders one specific frame's own placement
    (scale/xshift/yshift/rotation) of a picture: a dictionary entry has no
    single owning frame in general (the same picture can be placed by more
    than one frame, each with its own scale/shift), so extraction renders
    the artwork itself rather than picking one placement of it arbitrarily.
    Reuses `HTML5Converter`'s own per-object SVG emitters
    (`_drawfile_svg_object` and everything under it) directly with a
    different, frame-free coordinate mapping, rather than duplicating path/
    text/group emission logic a third time.

  Each dictionary entry is extracted independently via `catch()`, so one
  broken entry doesn't stop the rest; subdirectories are only created for
  kinds of content the document actually has. `cli.py` gains a matching
  `extract <input> <output-dir>` subcommand alongside `convert`, with the
  same `--strict`/`--log-level`/`--json-log` flags and exit-code
  convention. 8 new regression tests (`test_output_extract.py` plus two CLI
  tests) covering text/HTML extraction, style CSS, DrawFile raw+SVG
  extraction, EPS unwrapping, the undecodable-drawable-picture fallback, and
  directories only being created when used. Full suite green (415 tests);
  smoke-tested (both `pytest` and a direct `extract` run) across all 111
  real documents in `examples/`/`moreexamples/` with 0 crashes.

### Stage 13 (follow-up, not blocking) — Real-document audit
* Audit `examples/` for documents free of personal information; add a
  sanitised subset as committed automated-test fixtures; extend CI to run
  against them.
* **Done.** Converted all 111 real documents (`examples/` + `moreexamples/`)
  to Markdown and scanned the extracted text for emails, UK postcodes, and
  phone-number-shaped strings, as a triage aid -- not a determination of
  what's safe on its own, since a keyword scan can't recognise a personal
  name or an informal note with no contact details in it. The scan found a
  large fraction of the corpus was genuinely personal correspondence (letters
  to named individuals, the user's own historical email addresses and phone
  number recurring throughout), so no subset was selected unilaterally by
  this project -- the audit's own findings were reported back to the user,
  who then chose and added five documents directly to a new, committed
  `corpus/` directory themselves: `Cover,bc5`, `ForDad,bc5`, `Lines,bc5`,
  `Project,bc5`, `TestImp,bc5` (the last a purpose-built lorem-ipsum styling
  test document; `Lines,bc5` wasn't part of the original examples/
  moreexamples corpus at all). Re-ran the same scan against these five specifically
  (0 hits) and spot-read each one's extracted text as a final check before
  they were committed.

  `tests/test_corpus_documents.py` runs every `corpus/` document through
  every output format (`ddl`, `pdf`, `html-scroll`, `html-paged`,
  `markdown`) via the real CLI entry point, asserting a clean exit and no
  error-level log entries -- parametrised so a future document added to
  `corpus/` is picked up automatically with no test-code changes needed.
  No separate CI job was needed: these are ordinary `pytest` tests, already
  covered by CI's existing `test` job now that `corpus/` is committed
  (unlike `examples/`/`moreexamples/`, which stay gitignored, local-only,
  and are never referenced by anything under `tests/`). 25/25 new tests
  green (5 documents × 5 formats); full suite (363 tests) green.

### Stage 16 — ArtWorks/Sprite/JPEG embedded-picture rendering (`artworks-experiment` branch)

Sprite and ArtWorks were still bounding-box-only placeholders after Stage
14 (which covered DrawFile only). This stage brought real rendering to
both, plus a DrawFile object type (JPEG) not previously decoded at all,
across several linked pieces of work:

* **ArtWorks (`formats/artworks_svg.py`, third-party `riscos_artworks`
  decoder, optional `artworks` extra)** — real path/fill/stroke/gradient/
  text SVG rendering, ported from the `riscos-artworks-js` reference and
  cross-checked against real files. Along the way, found and fixed two
  real bugs upstream in `riscos_artworks` itself (separate repo, own
  branches, not yet merged/released):
  - `denormalise()`: the decoder reads a flat run of sibling records
    exactly as stored on disk, but real ArtWorks documents (and the JS
    reference) expect that run re-nested into a parent/child chain before
    rendering -- without it, a shape's own local attribute override
    (stored as a flat *trailing* sibling, not a preceding one) was
    silently ignored. Confirmed with a minimal real repro
    (`AWDocs/TestDocs/TestDocs/BlueRect,d94` rendering black instead of
    blue) and is the same root cause behind a large stray-looking black
    rectangle seen in an early real CD-cover test picture.
  - "Registration Black" (`ColourIndex(0xFFFFFFFE)`, `Colour_RegBlack` in
    the original ArtWorks source) was resolved as a literal direct BGR
    colour (near-white) instead of the solid-black sentinel it actually
    is -- confirmed against `AWDocs/TestDocs/RegistrationBlackRect,d94`
    and the real "Shit Creek" corpus picture (77 uses, previously
    rendering near-white text/line art).
  - Text (`TextRecord`/`CharacterRecord`) rendered as real SVG `<text>`
    glyphs for the first time -- the field layout wasn't documented
    anywhere available, so it was reverse-engineered directly against
    "Shit Creek" (see the module's own docstring for the full derivation:
    position/count/angle fields, and the character code's low byte being
    the real character with unrelated garbage in the high bits).
* **DrawFile JPEG objects (type 16)** — not in the primary PRM source
  this project checks against (it predates RISC OS 3.6); reverse-engineered
  against a real RISC OS Paint-produced file and documented in the
  `riscos-output` skill's own `drawfile-format.md`. Embedded as a data:
  URI `<image>` in SVG, and a real `/DCTDecode` Image XObject in PDF (the
  first image-embedding code in `pdfdoc.py`; added the `/XObject` page
  resource plumbing this needed).
* **Sprite pixel decoding (third-party `riscos_sprites` decoder, from
  `github.com/gerph/riscos-dumpsprites`, `sprites-png-conversion` branch,
  optional `sprites` extra -- not yet published to PyPI)** — real pixel
  decoding for both top-level Sprite pictures and Sprite objects embedded
  within a DrawFile, in both SVG (`<img>` PNG data URI) and PDF (Indexed/
  RGB Image XObject, with colour-key `/Mask` or a real `/SMask` for
  genuine alpha). Found and fixed two real gaps upstream in
  `riscos-dumpsprites` too (own branch, not yet merged): `SpriteFile` had
  no `from_bytes()` (only a real-file `parse()`), and there was no way to
  get raw decoded pixel bytes without going through a full PNG file
  (`raw_scanline_bytes()`, needed for the PDF path). Also fixed a real,
  separate pre-existing bug in this project's own `formats/sprite.py`:
  `SpriteArea.from_bytes()` read the sprite-area header at the
  *in-memory* control-block offsets, not the *on-disk* file offsets a
  real file actually uses -- confirmed against `riscos-dumpsprites`' own
  test fixture.

Not attempted in this stage -- see Stage 17 below for tracking: ArtWorks
rendering in PDF (SVG only so far), ArtWorks blend interpolation, ArtWorks
distortion/perspective, ArtWorks' own embedded sprites, EPS content
rendering, DrawFile dash patterns.

### Stage 17 — Known gaps and follow-ups

A survey of every `unsupported`/`best_effort` log call site plus the
open items noted during Stage 16, turned into a tracked checklist so
they don't just live in conversation history. Picked up one functional
area at a time, each with its own commit(s); ArtWorks gets its own
sub-checklist since it's the area most likely to grow piecemeal.

- [x] **Non-decimal numbering styles.** All five converters (`ovprodll.py`,
  `html_scrolling.py`, `html_paged.py`, `pdfdoc.py`, `markdown.py`) only
  implemented `NumberingStyle.DECIMAL`; any other style (Roman numerals,
  alphabetic, bullet) silently rendered as an empty string, logged
  `unsupported` each time it happened.
  **Done:** `model/numbering.py` gained `format_number(value, style)` --
  standard subtractive-notation Roman numerals, bijective-base-26
  alphabetic (1=A, 2=B, ..., 26=Z, 27=AA, ...), and a fixed bullet glyph
  ignoring the running count -- called identically from all five
  converters' own `_resolve_number_text` (DECIMAL, and any unrecognised
  raw style byte, still fall back to a plain decimal string, now with a
  `best_effort` log note only for the genuinely-unrecognised case, not
  for every non-decimal style). Checked the original C conversion
  source (`c/styles`' own `expandnumber()`) first: it recognises all
  these style codes too, but every non-decimal branch there is
  genuinely empty -- this is real, additional behaviour beyond what
  that reference tool ever did, not a port of existing logic. 13 new
  unit tests (`test_model_numbering.py`) plus one converter-level
  end-to-end test (`test_output_html_scrolling.py`, confirming the real
  `HeadingNumberMark` -> numbering-table -> `format_number` wiring, not
  just the formatting function in isolation).
  - **Example documents still wanted:** nothing in `corpus/` uses a
    non-decimal numbering style for chapters, pages, or lists, so this
    is verified against `model/numbering.py`'s own unit tests and the
    original C source's documented style codes, not against real
    Impression output. A real (or purpose-built, like `TestImp,bc5`)
    document exercising Roman-numeral/alphabetic/bullet numbering would
    let this be checked against actual behaviour too. Flagged for the
    user to supply/create one.

- [x] **ArtWorks pictures in PDF output.** SVG rendering (Stage 16) never
  reached `pdfdoc.py` -- `_draw_picture_content`'s ArtWorks branch was
  still the placeholder box. Sub-checklist, ticked off as each piece
  landed (mirroring what SVG already covers). Now complete: geometry/
  fill/stroke/gradients/text/blends (below), embedded sprites (own
  item, "ArtWorks' own embedded sprites"), embedded JPEGs (own item,
  "Embedded JPEG records"), and direct-colour CMYK resolution (own
  item, the `0xFFFF9C00`/`0xFFFF9900` mystery) all land in both SVG and
  PDF output now.
  - [x] Path/rectangle/ellipse/rounded-rectangle geometry, flat fill/stroke.
    `_draw_artworks_picture` and its `_artworks_pdf_*` walker methods in
    `pdfdoc.py` mirror `formats/artworks_svg.py`'s `_SvgBuilder` record-tree
    walk and style cascade exactly, but emit PDF path operators (`m`/`l`/
    `c`/`h`) and fill/stroke operators (`f`/`f*`/`S`/`B`/`B*` chosen from
    `style["winding"]` and whether fill/stroke are active) instead of SVG
    markup. No Y-flip is needed anywhere (PDF and ArtWorks' own native
    coordinates are both Y-up), unlike the SVG converter's own outer
    `scale(1,-1)` plus local text counter-flip -- a genuine simplification.
    Stroke width uses PDF's own `w` operator directly: PDF's "0 = thinnest
    device line" convention matches ArtWorks' own zero-width semantics with
    no SVG-style hack needed. `JoinStyle`/`CapStyle` map onto PDF's `j`/`J`
    operators directly (confirmed numeric alignment for join; `CapStyle.
    TRIANGLE` has no PDF equivalent and falls back to butt, undocumented
    beyond a code comment since it's a decorative simplification, not
    logged). Text (`TextRecord`/`CharacterRecord`) draws via `BT`/`Tf`/
    `Tm`/`Tj`/`ET`, with a real rotation matrix (`cos sin -sin cos x y Tm`)
    for the character's own angle rather than SVG's translate+scale+rotate
    trick, since there is no ambient flip to cancel here.
  - [x] Linear/radial gradient fills. `_artworks_pdf_fill_shading` builds a
    real PDF Shading dictionary (Type 2 axial for linear, Type 3 radial,
    both with a Type 2 exponential colour Function) from the fill's own
    `gradient_line`/`start_colour`/`end_colour`, registered per-page via a
    new `self._page_shadings` resource dict (mirroring `_page_xobjects`).
    `_artworks_pdf_emit_path` clips to the path (`W`/`W* n`) and paints
    the shading with `sh`, then -- since clipping consumes the current
    path the same way a paint operator does -- rebuilds the path for a
    second stroke-only pass when the object also has a border. Falls
    back to `_artworks_pdf_fill_rgb`'s flat-colour approximation (now
    only reachable when the gradient line or either end colour can't be
    resolved, e.g. an out-of-range palette index with no palette),
    logged `best_effort`. Verified against the real reference fixtures
    the user pointed at, `AWDocs/TestDocs/RectWhiteLeftToBlackRight,d94`
    (linear) and `RectWhiteLeftToBlackRightRadial,d94` (radial), by
    rasterising the PDF output with PyMuPDF -- both show a real
    gradient, not a flat band. This also fixes what first looked like
    two separate rendering bugs in `corpus/TestDoc,bc5`'s own pictures
    (reported after the geometry/fill/stroke work above): entry 50's sky
    rendering as flat yellow instead of a blue-to-yellow gradient, and
    its building walls rendering as flat black instead of shaded --
    both were gradient *fills* (not blend groups), so they were exactly
    the case this item's own flat-colour fallback covered before this
    landed.
  - [x] Text (`TextRecord`/`CharacterRecord`) -- see above; landed together
    with geometry/fill/stroke in the same commit since both walk the same
    record tree via the same style cascade.
  - [x] Blends -- landed once the SVG/PDF blend-interpolation items below
    landed (`_artworks_pdf_process_blend_group`, reusing
    `formats/artworks_svg.py`'s own interpolation helpers directly). A
    blend whose keyframes have a gradient fill still only gets the
    discrete halfway switchover described under the SVG blend item below
    (not a continuously-interpolated gradient) -- gradients and blends
    combining smoothly is out of scope, matching upstream AWViewer's own
    documented uncertainty there too.

- [x] **ArtWorks blend interpolation — SVG.** `process_blend_group` in
  `formats/artworks_svg.py` (dispatched for `BlendGroupRecord`) now draws
  `blend_steps + 1` interpolated shapes rather than nothing. Confirmed
  against the real "Shit Creek" corpus picture (`corpus/TestDoc,bc5`'s
  own picture 50): all 8 real blend groups there have matching start/end
  point counts, and after `denormalise()` a `BlendGroupRecord`'s own
  `child_lists` always split cleanly into exactly three -- one
  `BlendOptionsRecord` list (giving `blend_steps`) and two single-path
  keyframe lists (start, then end) -- confirmed both against that real
  file and independently against riscos-artworks-js's own
  `createSimpleBlendGroup()` test-fixture builder. Geometry is linearly
  interpolated element-by-element; stroke colour/width continuously;
  join/cap/winding/dash discretely switched over at the halfway point;
  flat-to-flat fill colour continuously, any other fill combination
  (a gradient on either end) discretely -- all matching
  riscos-artworks-js's own `docs/blend-groups/README.md` research notes.
  When the two keyframes' own point counts or segment types don't match,
  AWViewer's own point-insertion algorithm for that case is a stated
  open research problem for that same reference project ("It's not
  fully understood how !AWViewer blends geometry") -- not attempted
  here; both keyframes are drawn as-is instead, closer to the real
  appearance than nothing. Visually verified by rendering "Shit Creek"'s
  own picture 50 to a standalone SVG and opening it in a browser.
  Unit-tested against hand-built `BlendGroupRecord`/`BlendOptionsRecord`
  fixtures in `tests/test_formats_artworks_svg.py` (geometry/colour
  interpolation including the exact t=0/t=0.5/t=1 values; the
  mismatched-point-count fallback; the hidden-group case).
  - **Example documents wanted anyway:** no dedicated blend test file
    exists yet in `AWDocs/TestDocs` (checked both `TestDocs/` and the
    older nested `TestDocs/TestDocs/` -- neither has one; there are
    gradient-fill examples there, `RectWhiteLeftToBlackRight[Radial],d94`,
    but a gradient *fill* and a *blend* are different ArtWorks features).
    A small, deliberately simple two-shape blend (matching the style of
    the other `TestDocs` fixtures) would make this easier to verify in
    isolation from "Shit Creek"'s own more complex real-world blends --
    and, in particular, a real example with *mismatched* start/end point
    counts would help decide whether AWViewer's own point-insertion
    algorithm is ever worth implementing here.

- [x] **ArtWorks blend interpolation — PDF.** `_artworks_pdf_process_blend_group`
  in `pdfdoc.py` reuses `formats/artworks_svg.py`'s own module-level
  `_interpolate_path`/`_interpolate_colour_index` helpers directly (not
  re-derived), with a parallel `_artworks_pdf_capture_blend_keyframe` and
  `_artworks_pdf_interpolate_blend_style` mirroring the SVG converter's
  own keyframe-capture/style-interpolation logic, but emitting PDF path
  operators instead of SVG markup -- exactly the "same interpolation
  logic, different emitter" this item originally anticipated.
  `_artworks_pdf_emit` was split so its drawing core
  (`_artworks_pdf_emit_path`) can be reused for a synthesised
  interpolated path that has no `Record` of its own behind it. Verified
  against the real corpus picture (entry 50) by rasterising the PDF with
  PyMuPDF and comparing crops directly: geometry/stroke/fill match the
  SVG rendering exactly wherever a feature is supported by both (the
  sky's flat-yellow fallback is the already-documented PDF gradient
  limitation, not a blend bug -- confirmed by rendering the *same* SVG
  through MuPDF's own SVG engine, where the walls are black in both
  outputs, ruling out a PDF-specific colour bug). Unit-tested with the
  same hand-built `BlendGroupRecord` fixtures as the SVG tests (geometry
  interpolation, stroke colour at t=0/1, and the mismatched-point-count
  fallback), calling `_artworks_pdf_process_blend_group` directly since
  it needs no document/page state. Full suite (471 tests) passes.

- [x] **ArtWorks character font size was ~20-40x too small (SVG and PDF).**
  A real, shipped bug, found after the gradient-fill work above by the
  user reporting a real document's CD-cover picture as having no
  visible text at all, compared against `TestDoc-Real5.png` (a real
  reference render of the same page). `FontSizeRecord.x_size/y_size`
  was being used directly as if it were already in native ArtWorks
  coordinate units (the same space path geometry lives in); it isn't --
  confirmed empirically against `CharacterRecord.bounding_box`/
  `TextRecord.bounding_box` heights across two different real pictures
  in `corpus/TestDoc,bc5` at several different declared font sizes,
  RISC OS's own "1/16th of a point" font-size convention (already used
  elsewhere in this project for Impression's own unrelated
  `Style.font_size` field) gives a consistent set of ordinary point
  sizes where "already native units" doesn't. New
  `FONT_SIZE_TO_NATIVE_UNITS = 480/16 = 30` constant in
  `formats/artworks_svg.py`, applied in both `_emit_character` (SVG)
  and `pdfdoc.py`'s `_artworks_pdf_emit_character` (PDF). The CD-cover
  picture's own text (previously invisible; the picture's frame was too
  small to make even the pre-existing ~20-40x undersizing incidentally
  legible, unlike a road-sign picture in a full-page frame, where it
  had been) now visually matches `TestDoc-Real5.png` exactly, verified
  by rasterising the PDF output with PyMuPDF and comparing directly.
  Regression-tested in both converters. Full suite (479 tests) passes.

- [x] **ArtWorks text renders with a generic substitute font instead of
  the real glyph outlines, when ArtWorks itself has already provided
  them.** Once font size was fixed (above), the user compared the
  road-sign picture directly against `TestDoc-Real5.png` and found the
  *shape* of the text wrong too: the reference uses distinctive
  handwritten/script fonts ("Architect", "Penultimat") this converter
  has no way to reproduce with only the 14 standard PDF fonts, and
  SVG's browser-font fallback fares no better for the same reason.
  Traced to `AWDocs/MethodsManual.md`'s own documented `PathifyText_*`
  behaviour: ArtWorks' text tool converts individual characters into
  real vector-traced outline paths (stored as that `CharacterRecord`'s
  own child object) whenever it can't rely on standard text rendering
  coping with the attributes applied -- typically an unusual font, for
  exactly this portability reason. Confirmed against the real picture:
  every visible character in the road-sign one has its own child
  `PathRecord`, a genuine filled bezier outline of that exact glyph in
  that exact font, already in the same native coordinate space as
  everything else -- while the CD-cover picture's own text (a common,
  likely-always-installed font, "AvantG.Book") has none, confirming
  pathifying only happens when ArtWorks itself decided it needed to.
  `_emit_character` (SVG) and `_artworks_pdf_emit_character` (PDF) now
  check for this and, when present, render the real outline(s) exactly
  like any other geometry (reusing `_emit`/`_artworks_pdf_emit`
  directly, so it gets the same fill/stroke/winding cascade) instead of
  a substitute-font glyph -- falling through to the previous `<text>`/
  `Tf`+`Tj` rendering only when no pathified outline exists. Verified
  by rasterising the PDF output with PyMuPDF: the road-sign picture's
  own text now visually matches `TestDoc-Real5.png`'s distinctive
  script fonts exactly, not a generic sans-serif approximation.
  Regression-tested in both converters (a pathified character renders
  a `<path>`/path operators, not `<text>`/`Tf`+`Tj`; one without a
  pathified glyph still falls back correctly). Full suite (482 tests)
  passes.

- [x] **PDF: an invisible selectable/searchable text layer behind
  pathified ArtWorks glyphs.** User idea. PDF viewers let you select/
  search text that isn't actually drawn as glyphs at all -- the same
  trick a scanned-and-OCR'd PDF uses, an invisible text run (`Tr 3`,
  the "invisible" text-rendering mode) positioned over or behind
  whatever *is* visually rendered. Since a pathified character (see
  the item above) already carries both the real `CharacterRecord`
  (the actual letter, still perfectly readable) and its own drawn
  outline, the same trick applies directly here.

  `_artworks_pdf_emit_character`'s own pathified branch now calls a
  new `_artworks_pdf_emit_invisible_text` after drawing the outline:
  `BT 3 Tr /F<n> <size> Tf <rotation matrix> Tm (<char>) Tj 0 Tr ET`,
  reusing the *same* position (`unknown_values[0:2]`) and font size
  (`style["font_size"] * FONT_SIZE_TO_NATIVE_UNITS * scale`) the
  substitute-font fallback path just below already uses -- not a new
  guess, since that's exactly what this same character would have
  been drawn at before being pathified, so it already lines up with
  the outline's own natural position closely enough to select
  sensibly. `Tr` is text *state*, not reset by `ET`/`BT` (matching
  this file's own established `Tz` precedent), so it's explicitly
  restored to `0` (visible) afterwards to avoid leaking into later,
  unrelated text elsewhere on the page.

  Verified two ways: visually, rasterising corpus/TestDoc,bc5's own
  "Shit Creek" picture (whose signage text is pathified) shows no
  change at all -- confirming the run really is invisible; and via
  PyMuPDF's own text extraction on the same PDF, which previously
  returned nothing at all for that picture's own text and now returns
  the real strings ("PADDLE SALE TODAY", "Cancelled", "Sold Out",
  "You are now entering", "SHIT CREEK", "Twinned with Johnathan Creek,
  England") -- genuine copy/search support gained with zero visual
  change.

  Kept PDF-only, matching the item's own original framing -- not
  extended to DrawFile text objects (their own real string is always
  present already and drawn as ordinary visible text, not pathified,
  so there's no missing-selectability gap to close there) or to SVG
  output (browsers/SVG viewers don't have an equivalent invisible-but-
  selectable text-rendering mode the way PDF's `Tr 3` provides).

- [x] **ArtWorks pictures ignored their own frame's xshift/yshift/
  xscale/yscale placement (both SVG and PDF).** The user noticed a real
  document placing similar ArtWorks content at two different picture
  frames render identically, instead of each showing its own declared
  size/position. `_draw_artworks_picture` (PDF) and `_artworks_svg`
  (HTML) always scaled the artwork's own native bounding box to fit and
  centred it, ignoring the frame's own placement fields entirely.

  Both now use the same xshift/yshift/xscale/yscale formula
  `_draw_drawfile_picture`/`_drawfile_svg` already use for DrawFile
  pictures (see that method's own docstring for the full derivation and
  calibration history), substituting the artwork's own native bounding
  box (from `artworks_svg_fragment`'s own `viewbox` string) for
  DrawFile's decoded `BoundingBox`, and `ARTWORKS_UNIT_TO_USER_UNITS`
  for `_DRAW_UNIT_TO_PT`. PDF also applies `pict.angle` rotation (via
  an explicit per-point `to_pt` closure, the same mechanism DrawFile
  uses); the SVG version can't -- `artworks_svg_fragment`'s own `inner`
  is opaque pre-rendered markup with no per-point hook to rotate
  through, unlike `_drawfile_svg`'s own `to_svg` -- so a non-zero angle
  is logged once there instead, a smaller follow-up of its own.

  Fixing this also exposed a real, separate bug: `_draw_artworks_picture`
  had no clip rectangle at all (unlike `_draw_drawfile_picture`'s own
  `re W n`), so a picture now legitimately scaled/shifted bigger than
  its own frame would bleed into whatever else shares the page -- fixed
  alongside this, with its own regression test.

  Verified against the real document (`corpus/TestDoc,bc5`): its own
  two different CD-cover pictures (dictionary entries 48 and 51, each
  with a different declared `xscale`) now render at visibly, correctly
  different sizes rather than identically. Regression-tested in both
  converters (xshift/yshift and xscale/yscale each independently change
  the rendered output; PDF's own clip rectangle matches the frame's
  box exactly). Full suite (485 tests) passes.

- [x] **HSV colours resolved wrong (both SVG and PDF): hue was
  normalised as a byte-range channel, not the angle it actually is.**
  Investigating a separate report -- a sprite's own transparent
  background not showing the expected purple frame fill through it --
  turned out not to be a masking bug at all (the `/Mask` colour-key
  entry was present and correct all along); the frame's own fill
  colour itself was resolving to orange instead of purple.
  `_to_rgb`/`colour_to_css`'s own HSV branch (`pdfdoc.py`/
  `html_base.py`, each with their own independent copy) normalised the
  hue channel by dividing by 255, the same as saturation and value --
  but unlike those two, hue isn't a byte-range (0-255) value at all,
  it's an angle (0-360 degrees) packed into the same on-disk slot (see
  `docs/impression-documents.xml`'s own "Colour channel encoding",
  updated with this confirmation). The user confirmed the real,
  intended value directly from their own colour picker dialog: 268
  degrees / 75% / 88%, and `h / MAXCV` for the real document's own raw
  value is exactly `268.0` -- dividing that by 255 instead of 360 sent
  the resolved colour more than a full turn round the colour wheel,
  landing on orange. Fixed in both converters (divide by 360, not
  255); `ovprodll.py`'s own HSV handling needed no change, since it
  passes the raw `{hsv ...}` values straight through to OvationPro's
  own DDL syntax rather than resolving them to RGB itself. Since this
  bug affects colour resolution generally, not anything ArtWorks/sprite
  -specific, it likely also explains other HSV-coloured elements
  throughout any document using them, not just this one frame fill.
  Regression-tested in both converters against the real document's own
  confirmed raw values. Full suite (487 tests) passes.

- [x] **ArtWorks pictures rendered about a third too big (both SVG and
  PDF).** After the xshift/yshift/xscale placement fix above, the user
  checked the real document's own picture-info dialog figures directly
  (graphic X/Y and scale%) and found the rendered *scale itself*
  matched exactly (16.2%, 40%), but the overall rendered size still
  looked roughly 33% too big regardless. Traced to
  `ARTWORKS_UNIT_TO_USER_UNITS`, the native-ArtWorks-unit-to-point
  conversion factor every placement/geometry/font-size calculation in
  both converters is built on: it carried an extra `*(4/3)` beyond the
  base `1/640`, described as "matching riscos-artworks-js's own
  ARTWORKS_UNITS_TO_USER_UNITS" -- true of that constant's own numeric
  value, but that project targets a browser's own CSS pixels (96 per
  inch) as its "user units", not points (72 per inch) the way this
  project's own "_pt"-suffixed fields do everywhere else; 96/72 is
  exactly 4/3, so copying the value verbatim into a points-based
  project silently introduced a 4/3 oversizing error throughout. It
  went unnoticed until now because the error is uniform across an
  entire picture's own content -- proportions *within* one picture
  (e.g. title text size relative to a disc's own diameter) still
  matched a real reference render exactly, which is what the earlier
  font-size and pathified-text fixes above were verified against.

  Corrected via a real, independent, and exact source:
  `ArtWorksHeader.american_paper_width/height` (391680, 506880 in
  every real picture checked) divide *exactly* -- no rounding -- by US
  Letter's own size in points (612 x 792, i.e. 8.5in/11in x 72pt/in):
  `391680 / 612 == 506880 / 792 == 640.0` precisely, confirming the
  correct native-units-per-point figure is exactly 640 with no
  additional factor (corroborated by `european_paper_width/height`,
  538808/380976, landing within A4's own point size's rounding of the
  same 640 figure). `ARTWORKS_UNIT_TO_USER_UNITS` is now `1/640`
  directly; `FONT_SIZE_TO_NATIVE_UNITS` (added by the earlier font-size
  fix) is now derived from it (`(1/ARTWORKS_UNIT_TO_USER_UNITS)/16`)
  rather than a second hardcoded constant, so the two can't drift apart
  again. Existing tests asserting the old (33%-too-big) numeric
  expectations updated to match. Full suite (487 tests) passes.

- [ ] **ArtWorks distortion/perspective envelopes.** Recursed into
  structurally but the distortion itself isn't applied to the content
  inside one.

- [x] **ArtWorks' own embedded sprites (`SpriteRecord`).** Three
  sub-problems, all now solved:
  - [x] **Decoding the record without crashing.** A real picture
    (corpus/TestDoc,bc5's own "SVG logo") failed entirely --
    riscos_artworks raised "sprite palette count exceeds record". Fixed
    upstream (riscos_artworks, branch `fix/sprite-record-palette-flag`):
    the word after the sprite's own fixed fields is the palette's own
    entry count directly, confirmed against three real files the user
    provided (`AWDocs/TestDocs/Sprite16ColourPalettedMasked,d94`,
    `Sprite256ColoursPaletedNoMask,d94`,
    `Sprite256oloursNoPaletteMasked-EX1EY2,d94`) -- 16 and 256
    respectively, each followed by that many real, sensible palette
    words. An implausible count (the original failing sprite's own
    case, a genuinely palette-less 32bpp sprite) degrades to an empty
    palette rather than raising. All 5 real ArtWorks pictures in
    corpus/TestDoc,bc5 now decode without error (previously 2 of 5
    failed) -- verified in riscos-impression's own venv (an editable
    install of the fixed riscos_artworks).
  - [x] **Locating the sprite's own raw pixel data.** Solved upstream
    (riscos_artworks, branch `feature/sprite-record-raw-data`, on top of
    the palette fix above). Five more real files the user supplied
    specifically for this (`Sprite1BPP-lefthandwastae,d94`,
    `Sprite2BPP-lefthandwastage,d94`, `Sprite4BPP-lethandwastage,d94`,
    `SpriteManyFlame,d94` -- 8 sprites, `SpritesLots,d94` -- 23 sprites)
    revealed the real structure: ArtWorks stores the pixel data for one
    or more sibling `SpriteRecord`s together, once, in a single shared
    RISC OS-format sprite area (a standard `[size, count, first_offset,
    size]` control block) placed after all of their own metadata
    blocks -- not per-record, and not at any fixed gap (the gap before
    that area varied 48-56 bytes across the single-sprite examples, for
    reasons not otherwise modelled). `riscos_artworks.SpriteRecord`
    gained a new `data: bytes` field, resolved during decoding by
    scanning forward for the shared area's own header and walking its
    native sprite chain matching by name -- naturally handling the
    multi-sprite case too, since every sibling record resolves against
    the same shared area independently. Verified against all 7 example
    files (23/23 sprites matched in `SpritesLots,d94`) and, in
    riscos-impression, by round-tripping the two real embedded sprites
    in `corpus/TestDoc,bc5` through
    `wrap_single_sprite_as_area`/`sprite_area_to_png_image` into
    correct, recognisable images.
  - [x] **Rendering.** Wired into both converters: `formats/
    artworks_svg.py` gained a `sprite_to_png` callback parameter
    (`artworks_to_svg`/`artworks_svg_fragment`) rather than importing
    riscos_impression's own Sprite/PNG modules directly, keeping that
    module's own no-riscos_impression-dependency rule intact (see its
    module docstring) -- `html_base.py`'s `_artworks_svg` supplies the
    callback via `wrap_single_sprite_as_area`/`sprite_area_to_png`, and
    the emitted `<image>` is wrapped in its own local counter-flip
    `<g>` to cancel out the builder's outer `scale(1,-1)` (which is
    correct for vector content but would otherwise flip a raster image
    upside down). `output/pdfdoc.py`'s PDF converter (which walks
    ArtWorks records directly rather than using the opaque SVG
    fragment) reuses the same `_draw_sprite_image`/`_draw_placeholder`
    pipeline a DrawFile-embedded Sprite object already uses. Verified
    against corpus/TestDoc,bc5's own sprite-bearing picture in both PDF
    (rasterised via PyMuPDF) and HTML output: the sprite now renders
    with its mask correctly applied (the purple background showing
    through, resolving the user's own original report from earlier in
    this stage) alongside the JPEG on the same page.

- [x] **Embedded JPEG records (`JpegRecord`, type `0x6D`).** The user
  reported two of three images on a real document's own page 1
  (`NVMeFlyer,bc5`) missing entirely from PDF output; both are JPEGs
  embedded within an ArtWorks picture, a record type riscos_artworks
  did not recognise at all -- decoded as two `UnknownRecord`s instead.
  Fixed upstream in riscos_artworks: the record body closely mirrors
  DrawFile's own embedded-JPEG object (one unknown word, pixel_width/
  pixel_height, dpi_x/dpi_y, a 24-byte "corner" field reusing the same
  3-point structure `EllipseRecord`/`RoundedRectangleRecord` call
  "triangle", a standard 6-word transform matrix, a length word, then
  the raw JPEG bytes themselves) -- confirmed against a real file
  (`AWDocs/TestDocs/JPEG,d94`) two ways independently: pixel_width/
  pixel_height match the embedded JPEG's own SOF0 marker exactly, and
  dpi_x/dpi_y match its own JFIF APP0 density fields exactly. `data`
  is the complete standalone JPEG file, no area wrapper to resolve
  (unlike `SpriteRecord`).

  Wired into both riscos-impression converters: `formats/
  artworks_svg.py` gained `_emit_jpeg` -- needing no external decode
  callback at all (unlike a sprite), just a base64 `data:` URI, reusing
  `_emit_sprite`'s own local counter-flip `<g>` trick for the same
  reason. `output/pdfdoc.py` gained `_artworks_pdf_emit_jpeg`, reusing
  the existing `_jpeg_info`/DCTDecode Image XObject pipeline a
  DrawFile-embedded JPEG object already uses. Verified against both
  the minimal single-JPEG example (renders as the expected Acorn logo)
  and the real document: all three of page 1's own images now render
  in both PDF and scrolling HTML output.

- [x] **ArtWorks "direct" (non-indexed) colour words don't resolve
  correctly.** The user reported a real document (corpus/TestDoc,bc5's
  own "SVG logo" picture) rendering a shape's own fill as blue instead
  of an unnamed CMYK colour (59.8% / 99.6% / 99.2% / 0% K, confirmed
  from the real document's own colour picker dialog). The fill word in
  question, `0xFFFF9C00`, satisfies `riscos_artworks.ColourIndex`'s own
  `value >= 0x01000000` "direct colour" test, and is currently unpacked
  as a raw BGR triple (giving RGB(0,156,255), a blue).

  Three further real example documents the user provided
  (`AWDocs/TestDocs/TextCMYK64,26,75,45_Process,d94`,
  `TextCMYK70,60,50,40_Spot,d94`, `PolygonStellated6Sides,d94`) resolved
  one branch of this investigation conclusively but not the reported
  bug itself: all three use a *palette-indexed* colour reference
  (`ColourIndex` value < 0x01000000, e.g. index 17), not a direct one.
  Confirmed exactly against both CMYK files' own filenames (component /
  2147483647 * 100 matches the declared percentages to 8+ significant
  figures for both a process and a spot colour), and confirmed
  riscos-impression already renders both correctly -- each
  `PaletteEntry` carries its own pre-baked preview `.colour` word
  (e.g. `0x20004a00` for "DGreen", CMYK 64/26/75/45), already resolved
  and used correctly by the existing `Palette.resolve()` /
  `_artworks_pdf_rgb` pipeline, with no CMYK-specific handling needed
  downstream at all. So: indexed CMYK colours were never actually
  broken.

  The original bug is therefore specifically about a *direct* colour
  word, still unreproduced by any example so far. Also confirmed
  `FillColourRecord`'s own on-disk layout (`fill_type`, `unknown_28`,
  `colour` -- three plain words, no room for a hidden extra field the
  way `SpriteRecord` had) is simple and correctly aligned, so
  `0xFFFF9C00` is genuinely what's stored on disk for this fill;
  the mystery is purely in how to interpret it correctly, not a
  decode/alignment bug.

  Two further real examples (`AWDocs/TestDocs/ShapeBlendRedoCyan,d94`,
  an RGB-named blend, and `ShapeBlendCMYK,d94`, a CMYK one) were
  checked on the theory that a blend's own interpolated *intermediate*
  colour might be stored as a direct word somewhere -- it isn't: both
  files' own two keyframes each reference a named palette colour
  (Red/Cyan, and two auto-named CMYK entries), never a direct one.
  Neither ArtWorks itself nor this project's own blend interpolation
  stores/needs the intermediate colour on disk at all (both compute it
  at render time), so there was never going to be an on-disk example of
  one to find this way -- confirmed, not just assumed. Still useful
  independently: confirms blend keyframe colour resolution already
  works correctly for CMYK-named endpoints too.

  **Example document wanted:** either a document with a shape filled
  via a picked-but-not-palette-added colour (so it stays a direct
  reference rather than being indexed), or confirmation from real
  ArtWorks/AWViewer's own Object Info dialog on the SVG-logo shape
  specifically, to pin down the intended resolved colour without
  further guessing.

  Two more real examples chased a promising but ultimately different
  lead: the user's own hunch that dragging a DrawFile into ArtWorks
  might be what creates "anonymous" colours like this one.
  `RO4Bugs,d94` (a real DrawFile-to-ArtWorks conversion) did show 9
  garbled, seemingly-uninitialised trailing palette entries -- but a
  second file, `FromDrawfileRGBCircles,d94`, showed conclusively that
  this was our own decoder's bug, not ArtWorks leaving anonymous
  colours behind: its palette's declared `count_word` (49) ran straight
  past the 18 genuinely populated entries into unrelated later file
  content (an entry at the boundary decoded as `"<Nothing>"`/`"Redo"`,
  ArtWorks' own undo-stack labels). `control_word` (a second, separate
  word in the same header) turned out to be the real, live count in
  every file checked (17/18/72 exactly, vs the false 17/49/81
  `count_word` gave) -- fixed upstream in riscos_artworks (branch
  `fix/palette-count-is-control-word`), confirmed against the whole
  27-file `AWDocs/TestDocs` corpus (zero garbage names anywhere now).
  So: neither of these two files was the direct-colour-word bug after
  all, but real DrawFile-to-ArtWorks conversion documents remain a
  good place to keep looking, now that this particular false lead is
  closed off.

  **Solved.** A second real colour in the very same document broke it
  open: the SVG logo picture's own background rectangle,
  `0xFFFF9900`, whose Object Info dialog reads RGB 40.2%/0.4%/0.8%
  (i.e. `(102,1,2)`). A direct colour's four bytes, LSB to MSB, are K,
  C, M, Y (each 0-255, not the 31-bit scale a palette entry's own
  component words use), decoded via a standard subtractive
  CMYK->RGB conversion -- `0xFFFF9900` decodes to exactly `(102,0,0)`
  against the target, within rounding of a percentage read to one
  decimal place. The original `0xFFFF9C00` mystery decodes to
  61.2/100/100/0% CMYK against its own target of 59.8/99.6/99.2/0%,
  just as close -- two independent real confirmations. This also
  explains the `value >= 0x01000000` "is this direct" test: a document
  realistically never has anywhere near 16 million palette entries, so
  any real index keeps its own top byte zero, while this is simply
  testing whether the Y (top) byte is non-zero -- and explains why it
  can't be inverted for a colour needing zero ink on every channel
  (pure white, or anything needing B=255 given this byte assignment):
  its own top byte would then read as zero too, indistinguishable from
  an index. Not yet seen in a real file.

  Fixed upstream in riscos_artworks (branch
  `fix/direct-colour-is-kcmy-not-bgr`): `ColourIndex.bgr` now performs
  this conversion, and `Palette.resolve()`/`ArtWorks.resolve_colour()`
  return the correctly-decoded preview word for a direct colour instead
  of the raw word verbatim, so no caller downstream needed to change
  how it consumes a resolved colour. This did need one further fix
  here in riscos-impression, though: this project's own ArtWorks blend
  interpolation constructs synthetic "direct" colours purely to carry
  an already-computed RGB result back through the normal style/resolve
  pipeline (never real on-disk data) -- re-resolving one through the
  new CMYK decode a second time would have corrupted every blended
  colour. `_interpolate_colour_index` now returns an already-resolved
  preview word directly, and a new `_resolve_style_colour()` helper
  (used everywhere a colour is pulled from a style dict for drawing, in
  both the SVG and PDF converters) tells an interpolated result apart
  from a genuine document `ColourIndex` needing the normal resolve.
  Verified against corpus/TestDoc,bc5: the SVG logo's background now
  renders the correct dark maroon, not blue.

- [ ] **EPS content rendering.** Always a placeholder box in both HTML
  and PDF; PDF at least attaches the raw EPS as an embedded file (no
  reliable native PDF EPS-rendering mechanism exists), HTML has no
  equivalent mechanism at all.

- [x] **Dash patterns on DrawFile paths — HTML.** `formats/drawfile.py`'s
  `DrawPath` now keeps the pattern's own `dash_offset`/`dash_elements`
  (previously parsed only far enough to skip over them) --
  `_drawfile_svg_path` in `html_base.py` (shared by both the scrolling
  and paged HTML converters) emits a real `stroke-dasharray`/
  `stroke-dashoffset`, scaled by the same Draw-unit-to-pt factor already
  used for stroke width. No odd-element-count sense-inversion handling
  is needed: SVG's own dasharray already repeats/alternates the same
  way DrawFile's own pattern does.

- [x] **Dash patterns on DrawFile paths — PDF.** Landed in the same
  commit as the HTML item above, since both consume the same
  `DrawPath.dash_offset`/`dash_elements` fields. `_draw_drawfile_path`
  in `pdfdoc.py` emits PDF's own `[on off ...] phase d` operator.
  Unlike SVG's per-element `stroke-dasharray` attribute, PDF's dash
  array is graphics *state* that persists until changed -- since a
  DrawFile's objects all share one `q`/`Q` pair (not one per object), a
  non-dashed path drawn after a dashed one now explicitly resets to
  `[] 0 d`, or it would otherwise inherit the earlier path's own
  pattern.

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
