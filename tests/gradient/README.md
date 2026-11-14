# Gradient paint parsing — stop offsets, spread method and inheritance

## Problem

The gradient parser (`nsvg__parseGradient`, `nsvg__parseGradientStop` and
`nsvg__createGradient` in `src/nanosvg.h`) only carried stop colors through
reliably. Several parts of the gradient model were missing or wrong:

1. **Radial focal point default.** `fx`/`fy` were left at zero when not
   specified, instead of defaulting to the center (`fx = cx`, `fy = cy`) as
   required by the SVG spec. Every focal radial gradient therefore rendered as
   if the focus were pinned to the origin.
2. **`xlink:href` inheritance was stops-only.** The `TODO` in
   `nsvg__createGradient` says it all: coordinates (`x1/y1/x2/y2`,
   `cx/cy/r/fx/fy`), `spreadMethod` and `gradientUnits` were *not* inherited
   from the referenced gradient. A child gradient such as
   `<linearGradient xlink:href="#base"/>` kept the parser's built-in defaults
   (horizontal, pad) and lost the parent's geometry and spread method.
3. **Stop offsets were not normalized.** An `offset` outside `[0,1]`
   (e.g. `-20%` or `150%`) was stored verbatim. The spec clamps such values to
   the nearest endpoint.
4. **SVG2 `href` was unsupported.** Only the namespaced `xlink:href` form was
   recognized, so an SVG2 document using plain `href="#base"` failed to link
   the gradients (the referencing shape lost its paint entirely).

## Fix (in `src/nanosvg.h`)

1. **Explicit-attribute bitmask.** `NSVGgradientData` gains a `set` field with
   one bit per inheritable attribute (`NSVG_GRAD_X1 … NSVG_GRAD_UNITS`). The
   parser sets the corresponding bit whenever an attribute is present, so a
   value of zero that the author actually wrote is distinguished from an
   unset attribute.
2. **Focal default.** When parsing a radial gradient, `fx`/`fy` are initialized
   to `cx`/`cy`; they are overwritten (and flagged) if explicitly given.
3. **Full reference resolution.** New helper `nsvg__resolveGradient()` walks
   the `xlink:href`/`href` chain and fills in every attribute the current
   gradient did not set, including the stop list. `nsvg__createGradient()`
   resolves into a local copy so the shared parsed definition is never mutated
   and can be reused by multiple shapes. Cycles and dangling/missing references
   are guarded (iteration cap, self/NULL checks) and cannot crash or loop
   forever; a gradient with local stops still renders when its reference is
   missing.
4. **Offset clamping.** `nsvg__parseGradientStop()` stores
   `clamp(offset, 0, 1)` using the parser's existing `nsvg__minf`/`nsvg__maxf`
   helpers.
5. **SVG2 `href`.** The reference branch now accepts both `xlink:href` and
   `href`, and tolerates a value with or without the leading `#`.

The public output structures (`NSVGgradientStop.offset`, `NSVGgradient.spread`,
`NSVGgradient.fx/fy`) are unchanged; only their values are now correct.

## Structure comparison

### Parsed definition (`NSVGgradientData`, parser-internal)

| Field                        | Before                              | After                                            |
|------------------------------|-------------------------------------|--------------------------------------------------|
| `linear` / `radial` coords   | present, never inherited via `href` | present; unset entries inherited down the ref chain |
| `spread`                     | parsed, never inherited             | parsed; inherited when not specified             |
| `units`                      | parsed, never inherited             | parsed; inherited when not specified             |
| `stops` / `nstops`           | inherited (first non-empty only)    | inherited as part of full resolution              |
| `set` (new)                  | —                                   | bitmask of explicitly specified attributes        |

### Resolved paint (`NSVGgradient`, per-shape output)

| Field          | Before                                              | After                                                       |
|----------------|-----------------------------------------------------|-------------------------------------------------------------|
| `stops[i].offset` | raw value, could be `<0` or `>1`                 | clamped to `[0,1]`                                          |
| `spread`       | always the local value (default `PAD` on a child)   | local value, else inherited `pad`/`reflect`/`repeat`        |
| `fx`, `fy`     | `0,0` when omitted                                  | normalized center when omitted; explicit value when given   |
| `xform`        | built from local/default coordinates only           | built from inherited coordinates when local ones are unset  |

## Files

- `gradient_test.c` — standalone regression test (23 checks): offset
  clamping, linear/radial spread, focal default, `xlink:href`/`href`
  inheritance, overrides, and malformed-reference robustness.
- `gradient-linear.svg`, `gradient-radial.svg`, `gradient-nested.svg` —
  fixtures exercising each case.

Build and run:

```sh
gcc -std=c99 -g -fsanitize=address,undefined -I ../../src \
    gradient_test.c -o gradient_test -lm && ./gradient_test
```

## Before / after behavior

Observed output of the bundled fixtures (`NSVGgradient` dump):

| Case                                   | Before                          | After                              |
|----------------------------------------|---------------------------------|------------------------------------|
| linear stop `-20%` / `150%`            | offsets `-0.2`, `1.5`           | offsets `0.0`, `1.0`               |
| radial without `fx`/`fy`               | `fx=fy=0` (origin focus)        | `fx=fy` = normalized center        |
| child `xlink:href` (no own attributes) | `spread=PAD`, default geometry  | `spread=REFLECT`, parent geometry  |
| child overriding only `spreadMethod`   | geometry reset to defaults      | inherited geometry, local spread   |
| SVG2 plain `href="#base"`              | shape had no gradient paint     | fully inherited gradient           |

Regression check: parsing the bundled `example/23.svg`, `drawing.svg` and
`nano.svg` produces identical shape/gradient output before and after the
change; the CMake library build and the `example2` rasterizer run are clean
under AddressSanitizer and UndefinedBehaviorSanitizer.
