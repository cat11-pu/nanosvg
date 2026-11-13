# Arc radius handling — reproduction, fix and comparison

## Problem

`nsvg__pathArcTo()` in `src/nanosvg.h` handled zero/negative radii (via
`fabsf` + an epsilon check) but did not handle **non-finite** radii. An SVG
arc token whose numeric exponent overflows a 32-bit float (any magnitude
`>= ~3.4e38`, e.g. `1e99`, `1e308`, or a 39+ digit integer) is parsed by
`nsvg__atof` into `+/-Inf`.

Downstream that value poisons the center parameterization:

- `sa`/`sb` become `NaN`/`Inf`, so the center `cxp`/`cyp` and sweep angle `da`
  become `NaN`.
- `ndivs = (int)(fabsf(NaN)/...)` is undefined-ish in principle and evaluates
  to `INT_MIN` (`-2147483648`) on the reference toolchain.
- The spline loop emits no usable points, so the whole subpath either vanishes
  (a standalone arc produces no committed path → the icon shape is wrong /
  missing) or leaves surrounding segments in an unexpected topology.

Per the SVG 1.1 arc implementation notes, an arc with a zero/negative or
otherwise unusable radius, or coincident endpoints, must be rendered as a
**straight line segment**, not dropped or corrupted.

## Fix (in `src/nanosvg.h`)

1. New portable helper `nsvg__isFinite()` that rejects `NaN` and `+/-Inf`
   without depending on the C99 `isfinite()`/`isinf()`/`isnan()` macros.
2. The early "degenerates to a line" guard now also triggers when either
   radius is non-finite, so overflowing radii fall back to `nsvg__lineTo()`.
3. Defensive guard after computing `ndivs`: a non-positive or non-finite
   segment count also falls back to a line, guaranteeing the spline loop can
   never run with a negative/garbage count (zero out-of-bounds risk).

Negative radii keep their existing, spec-correct behavior (sign ignored via
`fabsf`); only genuinely non-finite values take the new branch.

## Files

- `arc-invalid-radius.svg` — hand-written icon exercising zero, negative,
  subnormal, overflow and embedded-overflow arc radii.
- `arc_test.c` — C regression test; asserts finite geometry, valid
  `npts % 3 == 1` topology, and that every expected path is committed.

## Reproduce / verify

```sh
# Fixed header (sanitizers give the strongest crash/OOB guarantee)
gcc -std=c99 -g -fsanitize=address,undefined -I ../../src \
    arc_test.c -o arc_test -lm && ./arc_test
```

Against the pre-fix header the four overflow-radius cases fail with
`paths=0` (the arc is silently dropped); against the fixed header all 14
checks pass.

## Comparison data

`arc_test.c` (14 checks), fixed vs. original header:

| Result                         | Original | Fixed |
|--------------------------------|----------|-------|
| Checks passed                  | 10/14    | 14/14 |
| Overflow arcs dropped (`paths=0`) | 4     | 0     |
| Non-finite points emitted      | 0        | 0     |
| Invalid path topology          | 0        | 0     |

Randomized differential run, 4000 arc strings with radii drawn from
`{0, negative, normal, subnormal, 1e38, 1e39, 1e99, 1e308, 39-digit int}`:

| Metric (4000 cases)            | Original | Fixed |
|--------------------------------|----------|-------|
| Arc-bearing paths dropped      | 2376     | 0     |
| Non-finite points              | 0        | 0     |
| Invalid topology (`npts%3!=1`) | 0        | 0     |
| ASan / UBSan reports           | none     | none  |

Regression check: parsing the bundled `example/23.svg`, `drawing.svg` and
`nano.svg` produces byte-identical point output before and after the change
(normal, in-range arcs are untouched).
