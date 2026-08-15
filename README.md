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

Embedded `DrawFile`, `Sprite`, and `ArtWorks` pictures are decoded on a
best-effort basis (see `src/riscos_impression/formats/`); anything not fully
supported is logged, not silently dropped or fatal.

See [`PLAN.md`](PLAN.md) for the staged build plan and current progress.

## Status

Early development; nothing is implemented yet beyond project scaffolding.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Licence

MIT; see [`LICENSE`](LICENSE).
