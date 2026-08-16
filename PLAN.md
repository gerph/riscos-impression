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
- [ ] Stage 13 (follow-up) — Real-document audit
- [x] Stage 14 — Real DrawFile decoding and PDF/SVG rendering

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
