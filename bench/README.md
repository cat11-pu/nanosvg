# Path parser benchmark

Tooling used to measure and verify the lexer / number-parsing optimization in
`nsvg__parsePath`. Nothing in this directory is part of the library build.

## Files

- `gen.py` — generates path-heavy SVG files mixing every supported number and
  separator style (signs, leading/trailing dots, exponents, `em`/`ex`,
  comma/whitespace/implicit separators, arc flags).
- `bench.c` — times `nsvgParse` (`bench`) or dumps every parsed field
  (`dump`) for field-by-field comparison.
- `lexbench.c` — micro-benchmark of only the lexer + float conversion.
- `lexcmp.c` — emits the numeric/command token stream for exact comparison.
- `ref/` — pristine upstream `nanosvg.h`, `opt/` — optimized `nanosvg.h`.

## Reproduce

```sh
python3 gen.py data 1 && python3 gen.py data 10 && python3 gen.py data 50

# end-to-end parse timing
./ref/bench_ref bench data/path_10mb.svg 40
./opt/bench_opt bench data/path_10mb.svg 40

# lexer-only timing
./ref/lex_ref  data/path_10mb.svg 30
./opt/lex_opt  data/path_10mb.svg 30

# field-by-field equivalence (no output means identical)
diff <(./ref/dump_ref dump data/path_10mb.svg) \
     <(./opt/dump_opt dump data/path_10mb.svg)

# token-level equivalence
diff <(./ref/lexcmp_ref data/path_10mb.svg) \
     <(./opt/lexcmp_opt data/path_10mb.svg)
```

Build flags used for the numbers in the change summary:
`gcc -O2 -march=native`.
