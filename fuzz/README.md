# NanoSVG audit harness

Run the complete deterministic audit:

```bash
python3 fuzz/gen_corpus.py
python3 fuzz/run_audit.py
```

The generator writes exactly 2,000 malformed SVGs (400 each for truncation,
invalid numbers, deep nesting, cyclic references, and huge coordinates) and
300 valid SVGs under `artifacts/fuzz/corpus/`.

`run_audit.py` builds and runs:

- `parse_one.c` with ASan and UBSan for every malformed input.
- `parse_one.c` with exact `malloc/calloc/realloc/free` link wrappers for
  leak accounting over each parse/delete interval.
- `parse_one.c --dump` against `ref_parser.py` for all valid inputs.
- `render_one.c` against `rsvg-convert` alpha coverage for all valid inputs.

The audited result is in `artifacts/fuzz/report.md` and `report.json`.
`malformed_results.csv` contains one row per malformed file, including its
zero-based `failure_offset`.

NanoSVG does not expose a parse-error or error-offset API. Therefore the
offset is the corpus' first-invalid-byte annotation, not a value returned by
`nsvgParse`. All malformed inputs in this corpus are permissively parsed into
an image; the audit's hard requirements are that parsing and deletion never
crash, access out of bounds, execute undefined behavior, or leak.
