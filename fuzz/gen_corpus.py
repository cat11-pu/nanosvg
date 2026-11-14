#!/usr/bin/env python3
"""Generate the deterministic NanoSVG audit corpus.

The malformed corpus contains exactly 2,000 files: 400 each in the
truncated, invalid-number, deep-nesting, cyclic-reference, and
huge-coordinate classes.  Every record carries a zero-based byte offset for
the first byte that makes the input invalid (or, for truncation, the first
missing byte).
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "fuzz"
MALFORMED_DIR = OUT / "corpus" / "malformed"
VALID_DIR = OUT / "corpus" / "valid"
MINIMAL_DIR = OUT / "minimal"

SVG_OPEN = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'width="96" height="96" viewBox="0 0 96 96">'
)
SVG_CLOSE = "</svg>"


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_record(
    records: list[dict],
    category: str,
    case_id: str,
    data: bytes,
    failure_offset: int,
    detail: str,
) -> None:
    path = MALFORMED_DIR / category / f"{case_id}.svg"
    write_bytes(path, data)
    records.append(
        {
            "id": case_id,
            "category": category,
            "path": str(path.relative_to(ROOT)),
            "size": len(data),
            "failure_offset": failure_offset,
            "detail": detail,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def base_svg(body: str, width: str = "100", height: str = "100",
             viewbox: str | None = "0 0 100 100") -> str:
    vb = "" if viewbox is None else f' viewBox="{viewbox}"'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}"{vb}>{body}</svg>'
    )


def truncation_seed(index: int) -> str:
    n = index % 17
    x = 3 + (index * 7) % 71
    y = 5 + (index * 11) % 67
    if index % 4 == 0:
        body = (
            f'<path d="M{x} {y} L{x + 12} {y + 3} '
            f'C{x + 18} {y + 20} {x + 25} {y - 8} {x + 31} {y + 9} Z" '
            'fill="rgb(10,20,30)"/>'
        )
    elif index % 4 == 1:
        body = (
            f'<g transform="translate({x} {y}) rotate({n * 9} 4 5)">'
            f'<rect x="1" y="2" width="{10 + n}" height="{9 + n}" '
            'rx="2" fill="#123456"/></g>'
        )
    elif index % 4 == 2:
        body = (
            f'<defs><radialGradient id="g{index}" cx="50%" cy="50%" r="50%">'
            '<stop offset="0" stop-color="white"/>'
            '<stop offset="1" stop-color="black"/>'
            f'</radialGradient></defs><circle cx="{x}" cy="{y}" '
            f'r="{4 + n}" fill="url(#g{index})"/>'
        )
    else:
        body = (
            f'<polyline points="{x},{y} {x + 10},{y + 2} '
            f'{x + 14},{y + 20} {x + 2},{y + 24}" '
            'fill="none" stroke="navy" stroke-width="2"/>'
        )
    return base_svg(body)


INVALID_SLOTS = [
    lambda: base_svg('<rect x="1" y="1" width="10" height="10"/>',
                     width="@@NUM@@"),
    lambda: base_svg('<rect x="1" y="1" width="10" height="10"/>',
                     height="@@NUM@@"),
    lambda: base_svg('<rect x="1" y="1" width="10" height="10"/>',
                     viewbox="@@NUM@@ 0 100 100"),
    lambda: base_svg('<rect x="1" y="1" width="10" height="10"/>',
                     viewbox="0 0 @@NUM@@ 100"),
    lambda: base_svg('<rect x="@@NUM@@" y="1" width="10" height="10"/>'),
    lambda: base_svg('<rect x="1" y="1" width="@@NUM@@" height="10"/>'),
    lambda: base_svg('<circle cx="@@NUM@@" cy="50" r="12"/>'),
    lambda: base_svg('<circle cx="50" cy="50" r="@@NUM@@"/>'),
    lambda: base_svg('<ellipse cx="50" cy="50" rx="@@NUM@@" ry="20"/>'),
    lambda: base_svg('<line x1="4" y1="4" x2="@@NUM@@" y2="90"/>'),
    lambda: base_svg('<path d="M@@NUM@@ 10 L80 80"/>'),
    lambda: base_svg('<path d="M10 10 L80 @@NUM@@"/>'),
    lambda: base_svg('<path d="M10 10 C30 @@NUM@@ 40 50 60 60"/>'),
    lambda: base_svg('<path d="M10 10 Q40 @@NUM@@ 70 70"/>'),
    lambda: base_svg('<path d="M10 10 A@@NUM@@ 10 0 0 1 80 80"/>'),
    lambda: base_svg('<polygon points="10,10 80,10 @@NUM@@,70"/>'),
    lambda: base_svg('<g transform="translate(@@NUM@@ 4)"><rect x="1" y="1" '
                     'width="10" height="10"/></g>'),
    lambda: base_svg(
        '<defs><linearGradient id="g" x1="@@NUM@@" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="red"/><stop offset="1" '
        'stop-color="blue"/></linearGradient></defs>'
        '<rect x="1" y="1" width="10" height="10" fill="url(#g)"/>'
    ),
    lambda: base_svg('<rect x="1" y="1" width="10" height="10" '
                     'stroke="black" stroke-width="@@NUM@@"/>'),
    lambda: base_svg('<path d="M10 10 L80 80" '
                     'transform="matrix(@@NUM@@ 0 0 1 0 0)"/>'),
]

INVALID_TOKENS = [
    "",
    "nan",
    "NaN",
    "inf",
    "Infinity",
    "1..2",
    "--1",
    "++1",
    "1e",
    "1e+",
    ".e2",
    "0x10",
    "1,2",
    "1 2",
    "1_000",
    "1e999999",
    "..",
    "-",
    "+",
    "1.2.3",
]

HUGE_SLOTS = INVALID_SLOTS
HUGE_TOKENS = [
    "10000000000",
    "10000000000000000000",
    "1000000000000000000000000000000",
    "340282346638528859811704183484516925440",
    "350000000000000000000000000000000000000",
    "1e38",
    "1e39",
    "1e100",
    "1e308",
    "1e99999",
    "-1e39",
    "+1e39",
    "999999999999999999999999999999999999999999999999999999999999999999",
    "9999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999",
    "1e+99999",
    "1e-99999",
    "79228162514264337593543950336",
    "179769313486231570000000000000000000000000000000000000000000000000000000",
    "99999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999",
    "0.0000000000000000000000000000000000000000000000000000000000000000000000000001",
]


def generate_malformed() -> list[dict]:
    records: list[dict] = []

    for seed_index in range(40):
        seed = truncation_seed(seed_index).encode("ascii")
        cut_points = sorted(
            {
                max(1, min(len(seed) - 1, (j + 1) * (len(seed) - 1) // 11))
                for j in range(10)
            }
        )
        for cut_index, cut in enumerate(cut_points[:10]):
            case_id = f"trunc_{seed_index:02d}_{cut_index:02d}"
            write_record(
                records,
                "truncated",
                case_id,
                seed[:cut],
                cut,
                f"cut after {cut} of {len(seed)} bytes; next byte missing",
            )

    for slot_index, slot in enumerate(INVALID_SLOTS):
        template = slot()
        marker = template.index("@@NUM@@")
        for token_index, token in enumerate(INVALID_TOKENS):
            data = template.replace("@@NUM@@", token, 1).encode("ascii")
            case_id = f"badnum_{slot_index:02d}_{token_index:02d}"
            write_record(
                records,
                "invalid-number",
                case_id,
                data,
                marker,
                f"invalid numeric token {token!r}",
            )

    depths = list(range(129, 520)) + [1000, 2000, 4000, 8000, 12000,
                                      16000, 20000, 24000, 32000]
    assert len(depths) == 400
    for depth_index, depth in enumerate(depths):
        data = (
            SVG_OPEN
            + "<g>" * depth
            + '<rect x="1" y="1" width="10" height="10"/>'
            + "</g>" * depth
            + SVG_CLOSE
        ).encode("ascii")
        failure_offset = data.index(b"<g>") + 128 * 3
        case_id = f"deep_{depth_index:03d}_{depth}"
        write_record(
            records,
            "deep-nesting",
            case_id,
            data,
            failure_offset,
            f"group depth {depth}; safe attribute depth limit is 128",
        )

    for cycle_length in range(1, 401):
        kind = "radialGradient" if cycle_length % 3 == 0 else "linearGradient"
        gradients = []
        for index in range(cycle_length):
            next_index = (index + 1) % cycle_length
            gradients.append(
                f'<{kind} id="g{index}" xlink:href="#g{next_index}"/>'
            )
        data = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="10" height="10">'
            "<defs>"
            + "".join(gradients)
            + "</defs>"
            '<rect x="0" y="0" width="10" height="10" fill="url(#g0)"/>'
            "</svg>"
        ).encode("ascii")
        last_href = data.rfind(b'xlink:href="#g0"')
        failure_offset = last_href + len(b'xlink:href="')
        case_id = f"cycle_{cycle_length:03d}"
        write_record(
            records,
            "cyclic-reference",
            case_id,
            data,
            failure_offset,
            f"{cycle_length}-node {kind} reference cycle",
        )

    for slot_index, slot in enumerate(HUGE_SLOTS):
        template = slot()
        marker = template.index("@@NUM@@")
        for token_index, token in enumerate(HUGE_TOKENS):
            data = template.replace("@@NUM@@", token, 1).encode("ascii")
            case_id = f"huge_{slot_index:02d}_{token_index:02d}"
            write_record(
                records,
                "huge-coordinate",
                case_id,
                data,
                marker,
                f"oversized coordinate token length={len(token)}",
            )

    assert len(records) == 2000, len(records)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["category"]] = counts.get(record["category"], 0) + 1
    assert counts == {
        "truncated": 400,
        "invalid-number": 400,
        "deep-nesting": 400,
        "cyclic-reference": 400,
        "huge-coordinate": 400,
    }
    return records


def number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def valid_case(index: int) -> tuple[str, str]:
    kind = index % 10
    variant = index // 10
    rnd = random.Random(0x4E414E4F + index)

    x = 6 + (variant * 3) % 20
    y = 7 + (variant * 5) % 18
    dx = 14 + (variant * 7) % 28
    dy = 11 + (variant * 9) % 25
    vx = 2 + (variant * 2) % 15
    vy = 3 + (variant * 4) % 16

    width = 96
    height = 96
    viewbox = "0 0 96 96"
    if variant % 4 == 1:
        viewbox = f"{vx} {vy} {72 + variant % 8} {72 + variant % 8}"
    elif variant % 4 == 2:
        width, height = 120, 80
    elif variant % 4 == 3:
        width, height = 80, 120

    if kind == 0:
        body = (
            f'<path d="M{x} {y} L{x + dx} {y + 2} H{x + dx + 9} '
            f'V{y + dy} L{x + 3} {y + dy} Z" fill="#174a7c"/>'
        )
    elif kind == 1:
        c1x, c1y = x + 5, y - 9
        c2x, c2y = x + dx - 4, y + dy + 11
        ex, ey = x + dx, y + dy // 2
        body = (
            f'<path d="M{x} {y} C{c1x} {c1y} {c2x} {c2y} {ex} {ey} '
            f'S{ex + 8} {ey + 16} {ex + 18} {ey + 4}" fill="rgb(30,120,60)"/>'
        )
    elif kind == 2:
        qx, qy = x + dx // 2, y - dy // 2
        tx, ty = x + dx, y + dy // 3
        body = (
            f'<path d="M{x} {y} Q{qx} {qy} {tx} {ty} '
            f'T{tx + 13} {ty + 7} Q{tx + 2} {ty + dy} {x + 2} {y + dy}" '
            'fill="rgb(140,40,90)"/>'
        )
    elif kind == 3:
        ex, ey = x + dx + 5, y + dy
        body = (
            f'<path d="M{x} {y} A{dx} {dy // 2} {variant % 360} 0 1 {ex} {ey} '
            f'A{dx // 2} {dy} 0 1 1 {x} {y + 2}" fill="#8a4b12"/>'
        )
    elif kind == 4:
        rx = (variant * 3) % 9
        ry = (variant * 5) % 11
        body = (
            f'<rect x="{x}" y="{y}" width="{dx}" height="{dy}" '
            f'rx="{rx}" ry="{ry}" fill="#246b55"/>'
        )
    elif kind == 5:
        body = f'<circle cx="{x + dx // 2}" cy="{y + dy // 2}" r="{6 + variant % 19}" fill="#7a2f31"/>'
    elif kind == 6:
        body = (
            f'<ellipse cx="{x + dx // 2}" cy="{y + dy // 2}" '
            f'rx="{7 + variant % 23}" ry="{5 + variant % 17}" fill="#304a8c"/>'
        )
    elif kind == 7:
        body = (
            f'<polyline points="{x},{y} {x + 8},{y + dy} '
            f'{x + 18},{y + 3} {x + 29},{y + 21}" fill="none" '
            'stroke="none"/>'
        )
    elif kind == 8:
        body = (
            f'<polygon points="{x},{y + 4} {x + 7},{y + dy} '
            f'{x + dx},{y + dy - 5} {x + 19},{y + 6}" fill="#c18b20"/>'
        )
    else:
        angle = (variant * 13) % 360
        tx = (variant * 7) % 11
        ty = (variant * 3) % 9
        if variant % 2 == 0:
            body = (
                f'<g transform="translate({tx} {ty}) rotate({angle} 16 16)">'
                f'<path d="M{x} {y} L{x + dx} {y + 3} L{x + dx - 4} {y + dy} Z" '
                'fill="#4a7c22"/></g>'
            )
        else:
            body = (
                f'<path d="M{x} {y} L{x + dx} {y + 3} L{x + dx - 4} {y + dy} Z" '
                f'transform="scale({number(0.75 + (variant % 4) * 0.1)} '
                f'{number(0.8 + (variant % 3) * 0.1)})" fill="#6b3f8c"/>'
            )

    aspect = ""
    if variant % 7 == 0:
        aspect = ' preserveAspectRatio="xMidYMid meet"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="{viewbox}"{aspect}>{body}</svg>',
        f"kind={kind}; variant={variant}",
    )


def generate_valid() -> list[dict]:
    records: list[dict] = []
    for index in range(300):
        text, detail = valid_case(index)
        data = text.encode("ascii")
        path = VALID_DIR / f"valid_{index:03d}.svg"
        write_bytes(path, data)
        records.append(
            {
                "id": f"valid_{index:03d}",
                "path": str(path.relative_to(ROOT)),
                "size": len(data),
                "detail": detail,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    assert len(records) == 300
    return records


def generate_minimal() -> list[dict]:
    aspect_data = (
        b'<svg width="10" height="20" viewBox="0 0 10 10" '
        b'preserveAspectRatio="xMidYMid meet">'
        b'<rect x="0" y="0" width="2" height="2"/></svg>'
    )
    default_aspect_data = (
        b'<svg width="10" height="20" viewBox="0 0 10 10">'
        b'<rect x="0" y="0" width="2" height="2"/></svg>'
    )
    cases = [
        (
            "truncated",
            b"<svg",
            4,
            "closing '>' is missing at EOF",
        ),
        (
            "invalid-number",
            b'<svg width="1..2"/>',
            12,
            "second decimal point in width",
        ),
        (
            "deep-nesting",
            SVG_OPEN.encode("ascii") + b"<g>" * 129 + b'<rect width="1" height="1"/>',
            None,
            "129th nested group exceeds the 128-level attribute stack",
        ),
        (
            "cyclic-reference",
            b'<svg xmlns:xlink="http://www.w3.org/1999/xlink">'
            b'<defs><linearGradient id="a" xlink:href="#a"/></defs>'
            b'<rect width="1" height="1" fill="url(#a)"/></svg>',
            None,
            "one-node gradient self-reference",
        ),
        (
            "huge-coordinate",
            b'<svg width="1e39"><rect width="1" height="1"/></svg>',
            None,
            "width overflows binary32",
        ),
        (
            "valid-preserve-aspect-ratio",
            aspect_data,
            aspect_data.index(b"xMidYMid"),
            "valid SVG exposing the pre-fix capital-Y preserveAspectRatio bug",
        ),
        (
            "valid-default-preserve-aspect-ratio",
            default_aspect_data,
            None,
            "valid SVG exposing the pre-fix default preserveAspectRatio=none bug",
        ),
    ]
    records = []
    for category, data, offset, detail in cases:
        if offset is None:
            if category == "cyclic-reference":
                offset = data.index(b"#a")
            elif category == "deep-nesting":
                offset = data.index(b"<g>") + 128 * 3
            elif category == "valid-default-preserve-aspect-ratio":
                offset = data.index(b"viewBox")
            else:
                offset = data.index(b"1e39")
        path = MINIMAL_DIR / f"{category}.svg"
        write_bytes(path, data)
        records.append(
            {
                "id": f"minimal-{category}",
                "category": category,
                "path": str(path.relative_to(ROOT)),
                "size": len(data),
                "failure_offset": offset,
                "detail": detail,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return records


def main() -> None:
    for directory in (MALFORMED_DIR, VALID_DIR, MINIMAL_DIR):
        if directory.exists():
            shutil.rmtree(directory)
    malformed = generate_malformed()
    valid = generate_valid()
    minimal = generate_minimal()
    manifest = {
        "generator": "fuzz/gen_corpus.py",
        "seed": "0x4E414E4F",
        "malformed_count": len(malformed),
        "valid_count": len(valid),
        "minimal_count": len(minimal),
        "malformed": malformed,
        "valid": valid,
        "minimal": minimal,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(malformed)} malformed, {len(valid)} valid, "
        f"{len(minimal)} minimal cases to {OUT.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
