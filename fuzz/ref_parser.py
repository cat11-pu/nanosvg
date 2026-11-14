#!/usr/bin/env python3
"""Independent SVG geometry reference used by the NanoSVG audit.

This reference deliberately parses the XML and SVG path grammar in Python
instead of calling NanoSVG.  It emits the same canonical shape/path/point
model that fuzz/parse_one.c prints, allowing the audit to compare parser
results point by point.
"""

from __future__ import annotations

import math
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


KAPPA90 = 0.5522847493
PI = math.pi


def f32(value: float) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def xform_identity() -> list[float]:
    return [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def xform_multiply(t: list[float], s: list[float]) -> list[float]:
    t0 = t[0] * s[0] + t[1] * s[2]
    t2 = t[2] * s[0] + t[3] * s[2]
    t4 = t[4] * s[0] + t[5] * s[2] + s[4]
    t1 = t[0] * s[1] + t[1] * s[3]
    t3 = t[2] * s[1] + t[3] * s[3]
    t5 = t[4] * s[1] + t[5] * s[3] + s[5]
    return f32(t0), f32(t1), f32(t2), f32(t3), f32(t4), f32(t5)


def xform_premultiply(current: list[float], new: list[float]) -> list[float]:
    return xform_multiply(list(new), current)


def xform_point(x: float, y: float, t: list[float]) -> tuple[float, float]:
    return (
        f32(x * t[0] + y * t[2] + t[4]),
        f32(x * t[1] + y * t[3] + t[5]),
    )


NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
COMMAND_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")
UNITS_RE = re.compile(r"(px|pt|pc|mm|cm|in|%|em|ex)?", re.IGNORECASE)


def parse_first_number(text: str, default: float = 0.0) -> float:
    match = NUMBER_RE.search(text)
    return float(match.group(0)) if match else default


def parse_length(text: str, orig: float = 0.0, length: float = 0.0,
                 dpi: float = 96.0) -> float:
    match = NUMBER_RE.search(text or "")
    if not match:
        return 0.0
    value = float(match.group(0))
    unit = (text[match.end():] or "").strip().lower()
    if unit in ("", "px"):
        return value
    if unit == "pt":
        return value / 72.0 * dpi
    if unit == "pc":
        return value / 6.0 * dpi
    if unit == "mm":
        return value / 25.4 * dpi
    if unit == "cm":
        return value / 2.54 * dpi
    if unit == "in":
        return value * dpi
    if unit == "%":
        return orig + value / 100.0 * length
    if unit == "em":
        return value * 16.0
    if unit == "ex":
        return value * 16.0 * 0.52
    return value


def parse_float_list(text: str, limit: int | None = None) -> list[float]:
    values = [float(m.group(0)) for m in NUMBER_RE.finditer(text)]
    return values if limit is None else values[:limit]


def parse_transform_args(text: str, limit: int) -> list[float]:
    return parse_float_list(text, limit)


def transform_function(name: str, args: list[float]) -> list[float]:
    if name == "matrix":
        if len(args) != 6:
            return xform_identity()
        return list(args)
    if name == "translate":
        tx = args[0] if args else 0.0
        ty = args[1] if len(args) > 1 else 0.0
        return [1.0, 0.0, 0.0, 1.0, f32(tx), f32(ty)]
    if name == "scale":
        sx = args[0] if args else 0.0
        sy = args[1] if len(args) > 1 else sx
        return [f32(sx), 0.0, 0.0, f32(sy), 0.0, 0.0]
    if name in ("rotate", "skewX", "skewY"):
        angle = args[0] if args else 0.0
        radians = angle / 180.0 * PI
        if name == "rotate":
            cs, sn = math.cos(radians), math.sin(radians)
            rotation = [f32(cs), f32(sn), f32(-sn), f32(cs), 0.0, 0.0]
            m = xform_identity()
            if len(args) > 1:
                cx = args[1]
                cy = args[2] if len(args) > 2 else 0.0
                m = xform_multiply(m, [1.0, 0.0, 0.0, 1.0, -cx, -cy])
            m = xform_multiply(m, rotation)
            if len(args) > 1:
                m = xform_multiply(m, [1.0, 0.0, 0.0, 1.0, cx, cy])
            return m
        if name == "skewX":
            return [1.0, 0.0, f32(math.tan(radians)), 1.0, 0.0, 0.0]
        return [1.0, f32(math.tan(radians)), 0.0, 1.0, 0.0, 0.0]
    return xform_identity()


def parse_transform(text: str) -> list[float]:
    xform = xform_identity()
    pos = 0
    pattern = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
    for match in pattern.finditer(text or ""):
        name = match.group(1)
        arg_text = match.group(2)
        args = parse_transform_args(arg_text, 6)
        local = transform_function(name, args)
        xform = xform_premultiply(xform, local)
        pos = match.end()
    _ = pos
    return xform


@dataclass
class PathData:
    points: list[tuple[float, float]] = field(default_factory=list)
    closed: int = 0


class PathBuilder:
    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []
        self.start: tuple[float, float] | None = None
        self.paths: list[PathData] = []

    def move_to(self, x: float, y: float) -> None:
        if self.points:
            self.points[-1] = (f32(x), f32(y))
        else:
            self.points.append((f32(x), f32(y)))
        if self.start is None or len(self.points) == 1:
            self.start = (f32(x), f32(y))

    def line_to(self, x: float, y: float) -> None:
        if not self.points:
            return
        px, py = self.points[-1]
        dx, dy = x - px, y - py
        self.points.append((f32(px + dx / 3.0), f32(py + dy / 3.0)))
        self.points.append((f32(x - dx / 3.0), f32(y - dy / 3.0)))
        self.points.append((f32(x), f32(y)))

    def cubic_to(self, cx1: float, cy1: float, cx2: float, cy2: float,
                 x: float, y: float) -> None:
        if not self.points:
            return
        self.points.extend(
            [
                (f32(cx1), f32(cy1)),
                (f32(cx2), f32(cy2)),
                (f32(x), f32(y)),
            ]
        )

    def finish(self, closed: bool) -> None:
        if len(self.points) < 4:
            return
        if closed and self.points:
            first = self.points[0]
            self.line_to(*first)
        if len(self.points) % 3 != 1:
            return
        self.paths.append(PathData(list(self.points), 1 if closed else 0))
        self.points = []

    def reset_subpath(self, start: tuple[float, float]) -> None:
        self.points = []
        self.start = start
        self.move_to(*start)


def parse_path_data(text: str, xform: list[float]) -> list[PathData]:
    builder = PathBuilder()
    tokens: list[tuple[str, str]] = []
    for match in re.finditer(
        r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?",
        text,
    ):
        value = match.group(0)
        tokens.append(("cmd" if COMMAND_RE.fullmatch(value) else "num", value))

    i = 0
    cmd: str | None = None
    cpx = cpy = 0.0
    cpx2 = cpy2 = 0.0
    first_move = False

    def take(count: int) -> list[float] | None:
        nonlocal i
        if i + count > len(tokens):
            return None
        values = []
        for _ in range(count):
            if tokens[i][0] != "num":
                return None
            values.append(float(tokens[i][1]))
            i += 1
        return values

    while i < len(tokens):
        kind, value = tokens[i]
        if kind == "cmd":
            cmd = value
            i += 1
            if cmd in ("Z", "z"):
                if len(builder.points) > 0:
                    first = builder.points[0]
                    cpx, cpy = first
                    cpx2, cpy2 = first
                    builder.finish(True)
                    builder.reset_subpath(first)
                first_move = True
                continue
            continue

        if cmd is None:
            i += 1
            continue

        relative = cmd.islower()
        upper = cmd.upper()
        args = take({"M": 2, "L": 2, "H": 1, "V": 1, "C": 6,
                     "S": 4, "Q": 4, "T": 2, "A": 7}[upper])
        if args is None:
            i += 1
            continue

        if upper == "M":
            x, y = args
            if relative:
                x, y = cpx + x, cpy + y
            if builder.points:
                builder.finish(False)
            builder.points = []
            builder.start = (f32(x), f32(y))
            builder.move_to(x, y)
            cpx, cpy = x, y
            cpx2, cpy2 = cpx, cpy
            first_move = True
            cmd = "l" if relative else "L"
        elif upper == "L":
            x, y = args
            if relative:
                x, y = cpx + x, cpy + y
            builder.line_to(x, y)
            cpx, cpy = x, y
            cpx2, cpy2 = cpx, cpy
        elif upper == "H":
            x = args[0] + (cpx if relative else 0.0)
            builder.line_to(x, cpy)
            cpx = x
            cpx2, cpy2 = cpx, cpy
        elif upper == "V":
            y = args[0] + (cpy if relative else 0.0)
            builder.line_to(cpx, y)
            cpy = y
            cpx2, cpy2 = cpx, cpy
        elif upper == "C":
            x1, y1, x2, y2, x, y = args
            if relative:
                x1 += cpx
                y1 += cpy
                x2 += cpx
                y2 += cpy
                x += cpx
                y += cpy
            builder.cubic_to(x1, y1, x2, y2, x, y)
            cpx2, cpy2 = x2, y2
            cpx, cpy = x, y
        elif upper == "S":
            x2, y2, x, y = args
            if relative:
                x2 += cpx
                y2 += cpy
                x += cpx
                y += cpy
            x1 = 2 * cpx - cpx2
            y1 = 2 * cpy - cpy2
            builder.cubic_to(x1, y1, x2, y2, x, y)
            cpx2, cpy2 = x2, y2
            cpx, cpy = x, y
        elif upper == "Q":
            qx, qy, x, y = args
            if relative:
                qx += cpx
                qy += cpy
                x += cpx
                y += cpy
            x1 = cpx + 2.0 / 3.0 * (qx - cpx)
            y1 = cpy + 2.0 / 3.0 * (qy - cpy)
            x2 = x + 2.0 / 3.0 * (qx - x)
            y2 = y + 2.0 / 3.0 * (qy - y)
            builder.cubic_to(x1, y1, x2, y2, x, y)
            cpx2, cpy2 = qx, qy
            cpx, cpy = x, y
        elif upper == "T":
            x, y = args
            if relative:
                x += cpx
                y += cpy
            qx = 2 * cpx - cpx2
            qy = 2 * cpy - cpy2
            x1 = cpx + 2.0 / 3.0 * (qx - cpx)
            y1 = cpy + 2.0 / 3.0 * (qy - cpy)
            x2 = x + 2.0 / 3.0 * (qx - x)
            y2 = y + 2.0 / 3.0 * (qy - y)
            builder.cubic_to(x1, y1, x2, y2, x, y)
            cpx2, cpy2 = qx, qy
            cpx, cpy = x, y
        elif upper == "A":
            rx, ry, rotation, large, sweep, x, y = args
            if relative:
                x += cpx
                y += cpy
            arc_to(builder, cpx, cpy, rx, ry, rotation, large, sweep, x, y)
            cpx, cpy = x, y
            cpx2, cpy2 = cpx, cpy

    if builder.points:
        builder.finish(False)
    for path in builder.paths:
        transformed = [xform_point(x, y, xform) for x, y in path.points]
        path.points = transformed
    return builder.paths


def arc_to(builder: PathBuilder, x1: float, y1: float, rx: float, ry: float,
           rot_deg: float, fa: float, fs: float, x2: float, y2: float) -> None:
    x1, y1, rx, ry = f32(x1), f32(y1), f32(abs(rx)), f32(abs(ry))
    x2, y2 = f32(x2), f32(y2)
    rot = f32(f32(f32(rot_deg) / f32(180.0)) * f32(PI))
    fa = 1 if abs(fa) > 1e-6 else 0
    fs = 1 if abs(fs) > 1e-6 else 0
    dx, dy = f32(x1 - x2), f32(y1 - y2)
    distance = f32(math.sqrt(f32(f32(dx * dx) + f32(dy * dy))))
    if distance < 1e-6 or rx < 1e-6 or ry < 1e-6:
        builder.line_to(x2, y2)
        return
    sinrx, cosrx = f32(math.sin(rot)), f32(math.cos(rot))
    x1p = f32(f32(f32(cosrx * dx) / f32(2.0))
              + f32(f32(sinrx * dy) / f32(2.0)))
    y1p = f32(f32(f32(-sinrx * dx) / f32(2.0))
              + f32(f32(cosrx * dy) / f32(2.0)))
    ratio = f32(f32(f32(x1p * x1p) / f32(rx * rx))
                + f32(f32(y1p * y1p) / f32(ry * ry)))
    if ratio > 1:
        scale = f32(math.sqrt(ratio))
        rx, ry = f32(rx * scale), f32(ry * scale)
    sa = f32(f32(f32(f32(rx * rx) * f32(ry * ry))
                 - f32(f32(rx * rx) * f32(y1p * y1p)))
             - f32(f32(ry * ry) * f32(x1p * x1p)))
    sb = f32(f32(f32(rx * rx) * f32(y1p * y1p))
             + f32(f32(ry * ry) * f32(x1p * x1p)))
    if sa < 0:
        sa = 0
    s = f32(math.sqrt(f32(sa / sb))) if sb > 0 else 0.0
    if fa == fs:
        s = f32(-s)
    cxp = f32(f32(f32(s * rx) * y1p) / ry)
    cyp = f32(f32(f32(s * f32(-ry)) * x1p) / rx)
    cx = f32(f32(f32(x1 + x2) / f32(2.0))
             + f32(f32(cosrx * cxp) - f32(sinrx * cyp)))
    cy = f32(f32(f32(y1 + y2) / f32(2.0))
             + f32(f32(sinrx * cxp) + f32(cosrx * cyp)))
    ux = f32(f32(x1p - cxp) / rx)
    uy = f32(f32(y1p - cyp) / ry)
    vx = f32(f32(-x1p - cxp) / rx)
    vy = f32(f32(-y1p - cyp) / ry)

    def vecang(ax: float, ay: float, bx: float, by: float) -> float:
        rat = f32(f32(f32(ax * bx) + f32(ay * by))
                  / f32(f32(math.sqrt(f32(f32(ax * ax) + f32(ay * ay))))
                        * f32(math.sqrt(f32(f32(bx * bx) + f32(by * by))))))
        rat = max(-1.0, min(1.0, rat))
        sign = -1.0 if f32(ax * by) < f32(ay * bx) else 1.0
        return f32(sign * math.acos(rat))

    a1 = vecang(1.0, 0.0, ux, uy)
    da = vecang(ux, uy, vx, vy)
    if fs == 0 and da > 0:
        da -= 2 * PI
    elif fs == 1 and da < 0:
        da += 2 * PI
    t = [cosrx, sinrx, f32(-sinrx), cosrx, cx, cy]
    ndivs = int(f32(f32(abs(da)) / f32(f32(PI) * f32(0.5)) + f32(1.0)))
    hda = f32(f32(da / ndivs) / f32(2.0))
    if -1e-3 < hda < 1e-3:
        hda = f32(hda * f32(0.5))
    else:
        hda = f32(f32(f32(1.0) - f32(math.cos(hda))) / f32(math.sin(hda)))
    kappa = f32(abs(f32(f32(f32(4.0) / f32(3.0)) * hda)))
    if da < 0:
        kappa = f32(-kappa)
    px = py = ptanx = ptany = 0.0
    for index in range(ndivs + 1):
        angle = f32(a1 + f32(da * f32(index / ndivs)))
        ddx, ddy = f32(math.cos(angle)), f32(math.sin(angle))
        x = f32(f32(f32(f32(ddx * rx) * t[0])
                     + f32(f32(ddy * ry) * t[2])) + t[4])
        y = f32(f32(f32(f32(ddx * rx) * t[1])
                     + f32(f32(ddy * ry) * t[3])) + t[5])
        tanx = f32(f32(f32(f32(f32(-ddy) * rx) * kappa) * t[0])
                   + f32(f32(f32(ddx * ry) * kappa) * t[2]))
        tany = f32(f32(f32(f32(f32(-ddy) * rx) * kappa) * t[1])
                   + f32(f32(f32(ddx * ry) * kappa) * t[3]))
        if index > 0:
            builder.cubic_to(px + ptanx, py + ptany,
                             x - tanx, y - tany, x, y)
        px, py, ptanx, ptany = x, y, tanx, tany


def primitive_path(kind: str, attrs: dict[str, str],
                   xform: list[float]) -> list[PathData]:
    if kind == "rect":
        x = parse_length(attrs.get("x", "0"))
        y = parse_length(attrs.get("y", "0"))
        w = parse_length(attrs.get("width", "0"))
        h = parse_length(attrs.get("height", "0"))
        rx = -1.0
        ry = -1.0
        if "rx" in attrs:
            rx = abs(parse_length(attrs["rx"]))
        if "ry" in attrs:
            ry = abs(parse_length(attrs["ry"]))
        if rx < 0 and ry > 0:
            rx = ry
        if ry < 0 and rx > 0:
            ry = rx
        rx = max(0.0, rx)
        ry = max(0.0, ry)
        rx = min(rx, w / 2.0)
        ry = min(ry, h / 2.0)
        if w == 0 or h == 0:
            return []
        builder = PathBuilder()
        if rx < 0.00001 or ry < 0.0001:
            builder.move_to(x, y)
            builder.line_to(x + w, y)
            builder.line_to(x + w, y + h)
            builder.line_to(x, y + h)
        else:
            builder.move_to(x + rx, y)
            builder.line_to(x + w - rx, y)
            builder.cubic_to(x + w - rx * (1 - KAPPA90), y,
                             x + w, y + ry * (1 - KAPPA90), x + w, y + ry)
            builder.line_to(x + w, y + h - ry)
            builder.cubic_to(x + w, y + h - ry * (1 - KAPPA90),
                             x + w - rx * (1 - KAPPA90), y + h, x + w - rx, y + h)
            builder.line_to(x + rx, y + h)
            builder.cubic_to(x + rx * (1 - KAPPA90), y + h,
                             x, y + h - ry * (1 - KAPPA90), x, y + h - ry)
            builder.line_to(x, y + ry)
            builder.cubic_to(x, y + ry * (1 - KAPPA90),
                             x + rx * (1 - KAPPA90), y, x + rx, y)
        builder.finish(True)
        return transformed_paths(builder.paths, xform)
    if kind in ("circle", "ellipse"):
        cx = parse_length(attrs.get("cx", "0"))
        cy = parse_length(attrs.get("cy", "0"))
        if kind == "circle":
            rx = ry = abs(parse_length(attrs.get("r", "0")))
        else:
            rx = abs(parse_length(attrs.get("rx", "0")))
            ry = abs(parse_length(attrs.get("ry", "0")))
        if rx == 0 or ry == 0:
            return []
        builder = PathBuilder()
        builder.move_to(cx + rx, cy)
        builder.cubic_to(cx + rx, cy + ry * KAPPA90,
                         cx + rx * KAPPA90, cy + ry, cx, cy + ry)
        builder.cubic_to(cx - rx * KAPPA90, cy + ry,
                         cx - rx, cy + ry * KAPPA90, cx - rx, cy)
        builder.cubic_to(cx - rx, cy - ry * KAPPA90,
                         cx - rx * KAPPA90, cy - ry, cx, cy - ry)
        builder.cubic_to(cx + rx * KAPPA90, cy - ry,
                         cx + rx, cy - ry * KAPPA90, cx + rx, cy)
        builder.finish(True)
        return transformed_paths(builder.paths, xform)
    if kind == "line":
        x1 = parse_length(attrs.get("x1", "0"))
        y1 = parse_length(attrs.get("y1", "0"))
        x2 = parse_length(attrs.get("x2", "0"))
        y2 = parse_length(attrs.get("y2", "0"))
        builder = PathBuilder()
        builder.move_to(x1, y1)
        builder.line_to(x2, y2)
        builder.finish(False)
        return transformed_paths(builder.paths, xform)
    if kind in ("polyline", "polygon"):
        values = parse_float_list(attrs.get("points", ""))
        builder = PathBuilder()
        for index in range(0, len(values) - 1, 2):
            x, y = values[index], values[index + 1]
            if index == 0:
                builder.move_to(x, y)
            else:
                builder.line_to(x, y)
        builder.finish(kind == "polygon")
        return transformed_paths(builder.paths, xform)
    return []


def transformed_paths(paths: list[PathData], xform: list[float]) -> list[PathData]:
    result = []
    for path in paths:
        result.append(
            PathData([xform_point(x, y, xform) for x, y in path.points],
                     path.closed)
        )
    return result


def paint_type(attrs: dict[str, str], name: str, default_color: bool) -> int:
    value = attrs.get(name)
    if value is None:
        return 1 if default_color else 0
    if value.strip() == "none":
        return 0
    if value.strip().startswith("url("):
        return 2
    return 1


def one_shape(kind: str, attrs: dict[str, str], xform: list[float]) -> dict:
    if kind == "path":
        paths = parse_path_data(attrs.get("d", ""), xform)
        paths.reverse()
    else:
        paths = primitive_path(kind, attrs, xform)
    return {
        "fill_type": paint_type(attrs, "fill", True),
        "stroke_type": paint_type(attrs, "stroke", False),
        "paths": [
            {
                "closed": path.closed,
                "npts": len(path.points),
                "pts": [coordinate for point in path.points for coordinate in point],
            }
            for path in paths
        ],
    }


def view_align(content: float, container: float, kind: str) -> float:
    if kind == "min":
        return 0.0
    if kind == "max":
        return container - content
    return (container - content) * 0.5


def parse_svg(text: str) -> dict:
    root = ET.fromstring(text)
    tag = root.tag.rsplit("}", 1)[-1]
    if tag != "svg":
        raise ValueError("root is not svg")
    attrs = root.attrib
    image_width = parse_length(attrs.get("width", "0"))
    image_height = parse_length(attrs.get("height", "0"))
    view_minx = view_miny = view_width = view_height = 0.0
    viewbox = attrs.get("viewBox", "")
    values = parse_float_list(viewbox)
    if len(values) >= 4:
        view_minx, view_miny, view_width, view_height = values[:4]

    aspect = attrs.get("preserveAspectRatio", "")
    align_type = "meet"
    align_x = align_y = "mid"
    if aspect and "none" in aspect:
        align_type = "none"
    elif aspect:
        if "xMin" in aspect:
            align_x = "min"
        elif "xMax" in aspect:
            align_x = "max"
        if "yMin" in aspect:
            align_y = "min"
        elif "yMax" in aspect:
            align_y = "max"
        align_type = "slice" if "slice" in aspect else "meet"

    shapes: list[dict] = []
    path_depths: list[list[PathData]] = []

    def walk(element: ET.Element, parent_xform: list[float],
              in_defs: bool = False) -> None:
        element_tag = element.tag.rsplit("}", 1)[-1]
        if element_tag == "defs":
            return
        local_xform = parent_xform
        if "transform" in element.attrib:
            local_xform = xform_premultiply(
                local_xform, parse_transform(element.attrib["transform"])
            )
        if element_tag == "g":
            for child in element:
                walk(child, local_xform, in_defs)
            return
        if element_tag in ("path", "rect", "circle", "ellipse", "line",
                           "polyline", "polygon"):
            shapes.append(one_shape(element_tag, element.attrib, local_xform))
            return
        for child in element:
            walk(child, local_xform, in_defs)

    for child in root:
        walk(child, xform_identity())

    if view_width == 0:
        if image_width > 0:
            view_width = image_width
        else:
            view_width = 0.0
    if view_height == 0:
        view_height = image_height if image_height > 0 else 0.0
    if image_width == 0:
        image_width = view_width
    if image_height == 0:
        image_height = view_height
    tx, ty = -view_minx, -view_miny
    sx = image_width / view_width if view_width > 0 else 0.0
    sy = image_height / view_height if view_height > 0 else 0.0
    if align_type == "meet":
        sx = sy = min(sx, sy)
        if sx != 0:
            tx += view_align(view_width * sx, image_width, align_x) / sx
            ty += view_align(view_height * sy, image_height, align_y) / sy
    elif align_type == "slice":
        sx = sy = max(sx, sy)
        if sx != 0:
            tx += view_align(view_width * sx, image_width, align_x) / sx
            ty += view_align(view_height * sy, image_height, align_y) / sy

    for shape in shapes:
        for path in shape["paths"]:
            pts = path["pts"]
            for index in range(0, len(pts), 2):
                pts[index] = (pts[index] + tx) * sx
                pts[index + 1] = (pts[index + 1] + ty) * sy
    return {
        "width": image_width,
        "height": image_height,
        "shapes": shapes,
    }


def parse_file(path: str | Path) -> dict:
    return parse_svg(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(parse_file(sys.argv[1]), indent=2))
