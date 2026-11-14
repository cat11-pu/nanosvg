#!/usr/bin/env python3
# Generates path-heavy SVG files of a target size for parser benchmarks.
# The path data deliberately mixes every number/separator style that the
# nanosvg lexer must accept: signs, leading dots, trailing dots, exponents,
# comma / whitespace / implicit separators and arc flags.
import os
import sys


def fmt(v, i):
    kind = i % 9
    if kind == 0:
        return "%d" % round(v)
    if kind == 1:
        return "%.2f" % v
    if kind == 2:
        return "%.4f" % v
    if kind == 3:
        return ".%03d" % (int(abs(v) * 100) % 1000)
    if kind == 4:
        return "%+0.3f" % v
    if kind == 5:
        return "%.1e" % (v if v != 0 else 1.0)
    if kind == 6:
        return "%d." % round(v)
    if kind == 7:
        return "  %0.5f " % v
    return "%0.6f" % v


def segment(rng, i):
    x = rng[i % len(rng)]
    y = rng[(i * 3 + 1) % len(rng)]
    x2 = rng[(i * 7 + 5) % len(rng)]
    y2 = rng[(i * 11 + 9) % len(rng)]
    x3 = rng[(i * 13 + 2) % len(rng)]
    y3 = rng[(i * 17 + 4) % len(rng)]
    cmd = i % 6
    if cmd == 0:
        return "M%s,%sL%s %s" % (fmt(x, i), fmt(y, i + 1), fmt(x2, i + 2), fmt(y2, i + 3))
    if cmd == 1:
        return "H%sV%s" % (fmt(x, i), fmt(y, i + 4))
    if cmd == 2:
        return "C%s,%s %s,%s %s,%s" % (
            fmt(x, i), fmt(y, i), fmt(x2, i), fmt(y2, i), fmt(x3, i), fmt(y3, i))
    if cmd == 3:
        return "Q%s %s %s %s" % (fmt(x, i), fmt(y, i), fmt(x2, i), fmt(y2, i))
    if cmd == 4:
        # Arc: rx ry rot large-arc sweep x y, flags written raw with
        # the full range of separator styles around them.
        return "A%s %s %s %d,%d %s %s" % (
            fmt(abs(x) + 1, i), fmt(abs(y) + 1, i + 1), fmt((i % 90), i + 2),
            i % 2, (i + 1) % 2, fmt(x2, i + 5), fmt(y2, i + 6))
    return "m%s,%sc%s,%s %s,%s %s,%sz" % (
        fmt(x, i), fmt(y, i), fmt(x2, i), fmt(y2, i),
        fmt(x3, i), fmt(y3, i), fmt(x - x2, i), fmt(y - y2, i))


def build(target_bytes):
    rng = [((n * 37.719) % 733.0) - 366.0 for n in range(4096)]
    parts = []
    size = 0
    i = 0
    while size < target_bytes:
        seg = segment(rng, i)
        parts.append(seg)
        size += len(seg) + 1
        i += 1
    body = " ".join(parts)
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" '
        'viewBox="0 0 1000 1000">\n'
        '<path d="%s" fill="none" stroke="#123456" stroke-width="2"/>\n'
        "</svg>\n" % body)
    return svg


if __name__ == "__main__":
    out_dir, mb = sys.argv[1], int(sys.argv[2])
    svg = build(mb * 1024 * 1024)
    path = os.path.join(out_dir, "path_%dmb.svg" % mb)
    with open(path, "w") as f:
        f.write(svg)
    print(path, len(svg))
