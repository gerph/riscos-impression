# riscos-impression

A pure-Python decoder and converter for [Impression](https://www.davidpilling.com/wiki/index.php/Impression)
documents, the desktop-publishing format used by Computer Concepts' Impression
DTP applications on RISC OS.

The on-disk document format has never been officially published; this project
works from a from-scratch reverse-engineering of it, documented in
[`docs/impression-documents.xml`](docs/impression-documents.xml) (built from
the source of Impression's own `TransIMP` -> OvationPro converter). That
document lists every field, including the ones whose purpose is still
unconfirmed or unknown.

## What it does

`riscos-impression` decodes an Impression document (single file, or
directory-mode `!DocData`) into a class-structured Python object model, and
can then render that model to:

* **OvProDDL** -- OvationPro's own Document Description Language, the format
  Impression's original converter produces. Used here as a reference/baseline
  output.
* **PDF** -- written natively, with no external library.
* **Scrolling HTML5** -- a linear reflow of the document's text, dropping
  page layout.
* **Paged-media HTML5** -- an `@page`-based layout that keeps Impression's
  page and frame geometry, optionally rendered on to PDF via an external tool
  (Prince or WeasyPrint) if one is installed.
* **Markdown** -- a best-effort plain-text serialisation, inferring headings
  from relative font size and (when a page's bordered frames form a clean
  grid) simple tables.

Embedded `DrawFile` pictures are decoded and rendered as real vector content
(paths and text) in the PDF and HTML outputs; `Sprite` and `ArtWorks` pictures
render as a labelled placeholder box (see `src/riscos_impression/formats/`).
Anything not fully supported is logged, not silently dropped or fatal.

See [`PLAN.md`](PLAN.md) for the staged build plan and current progress.

## Usage

```sh
riscos-impression convert <input> --format {ddl,pdf,html-scroll,html-paged,markdown} [-o OUTPUT]
```

`<input>` is either a single Impression document file, or a directory for a
directory-mode document (one with a separate `!DocData`/story-file layout).
`-o`/`--output` defaults to `<input>` with the format's usual extension
(`.ddl`, `.pdf`, `.html`, `.md`).

Other flags:

* `--to-pdf` -- for `--format html-paged` only: also try to export a PDF via
  Prince or WeasyPrint, if either is found on `PATH`. Never a hard failure;
  logged either way (which tool exported it, or that neither was found).
* `--strict` -- raise on the first conversion problem instead of logging it
  and continuing (the default is best-effort: keep going, and report what
  couldn't be reproduced faithfully).
* `--log-level {info,best_effort,unsupported,error}` -- only print log
  entries at or above this level (default: print everything).
* `--json-log` -- print the conversion log as JSON instead of plain text.

The command exits `0` on a clean conversion, `1` if it couldn't even start
(bad input path, or a `--strict` failure), or `2` if it completed but the
log contains at least one `error`-level entry.

Example:

```sh
riscos-impression convert MyDocument --format pdf -o MyDocument.pdf
```

### Extracting content

```sh
riscos-impression extract <input> <output-directory>
```

Unlike `convert`, which renders the whole document as one file in one
format, `extract` pulls every story and picture out of the document as
separate, standalone files -- for reuse in other tools rather than reading
the document as Impression laid it out. `<output-directory>` is created if
it doesn't exist, with one subdirectory per kind of output (only created if
the document actually has anything of that kind):

* `text/NNNN.txt` -- a text story's own plain text, with all styling and
  page layout stripped.
* `html/NNNN.html` -- the same story as a standalone HTML file, with inline
  CSS approximating the source style table (font, colour, alignment,
  indents) -- not pixel-accurate, but enough to carry the document's general
  look into another tool.
* `images/NNNN.<ext>` -- a picture's own raw embedded content
  (`.draw`/`.sprite`/`.eps`/`.aff`, or `.bin` if not recognised).
* `svg/NNNN.svg` -- a DrawFile picture re-rendered as a standalone SVG, at
  its own native size.

`NNNN` is the story/picture's own object-dictionary index (stable within one
document, not meaningful across different documents). `extract` accepts the
same `--strict`/`--log-level`/`--json-log` flags as `convert`, and the same
exit-code convention (`0` clean, `1` couldn't start, `2` completed with at
least one logged error).

## Status

Stages 0-12 and 14 of [`PLAN.md`](PLAN.md) are complete: the decoder, all
five output converters, this CLI, and real DrawFile picture rendering.
Stage 13 (auditing `examples/` for a sanitised, committable test-fixture
subset) is an open follow-up.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Licence

MIT; see [`LICENSE`](LICENSE).
