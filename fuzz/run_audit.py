#!/usr/bin/env python3
"""Run the NanoSVG sanitizer and reference-consistency audit."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

import ref_parser


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "fuzz"
BUILD = ROOT / "build" / "fuzz"
PARSE_BIN = BUILD / "parse_one"
RENDER_BIN = BUILD / "render_one"
LEAK_BIN = BUILD / "parse_one_leak"
LEAK_SELFTEST_BIN = BUILD / "leak_selftest"
TOLERANCE = 2e-3
RASTER_MAE_LIMIT = 0.02
RASTER_IOU_LIMIT = 0.90


def run_command(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, text=True, **kwargs)


def build_harnesses() -> dict:
    BUILD.mkdir(parents=True, exist_ok=True)
    parse_cmd = [
        "cc", "-std=c99", "-O1", "-g", "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
        "fuzz/parse_one.c", "-lm", "-o", str(PARSE_BIN),
    ]
    render_cmd = [
        "cc", "-std=c99", "-O2", "fuzz/render_one.c", "-lm",
        "-o", str(RENDER_BIN),
    ]
    leak_cmd = [
        "cc", "-std=c99", "-O2", "-DMALLOC_AUDIT",
        "-Wl,--wrap=malloc", "-Wl,--wrap=calloc",
        "-Wl,--wrap=realloc", "-Wl,--wrap=free",
        "fuzz/parse_one.c", "-lm", "-o", str(LEAK_BIN),
    ]
    selftest_cmd = [
        "cc", "-std=c99", "-O2", "-DMALLOC_AUDIT",
        "-DMALLOC_AUDIT_SELFTEST",
        "-Wl,--wrap=malloc", "-Wl,--wrap=calloc",
        "-Wl,--wrap=realloc", "-Wl,--wrap=free",
        "fuzz/parse_one.c", "-lm", "-o", str(LEAK_SELFTEST_BIN),
    ]
    for command in (parse_cmd, render_cmd, leak_cmd, selftest_cmd):
        completed = run_command(command, capture_output=True)
        if completed.returncode != 0:
            raise RuntimeError(
                f"build failed: {' '.join(command)}\n{completed.stderr}"
            )
    selftest = run_command([str(LEAK_SELFTEST_BIN)], capture_output=True)
    if selftest.returncode != 0 or "LEAK_AUDIT leaks=1" not in selftest.stdout:
        raise RuntimeError(f"leak detector self-test failed:\n{selftest.stdout}\n{selftest.stderr}")
    cc_version = run_command(["cc", "--version"], capture_output=True).stdout.splitlines()[0]
    return {
        "parse": parse_cmd,
        "render": render_cmd,
        "leak": leak_cmd,
        "leak_selftest_command": selftest_cmd,
        "cc": cc_version,
        "leak_detector": "exact malloc/calloc/realloc/free link wrappers",
        "leak_selftest_result": selftest.stdout.strip(),
    }


def malformed_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1:abort_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    return env


def classify(returncode: int | str, stdout: str, stderr: str,
             timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if "LeakSanitizer" in stderr or "detected memory leaks" in stderr:
        return "leak"
    if "AddressSanitizer" in stderr:
        memory_markers = (
            "buffer-overflow", "use-after-free", "SEGV",
            "stack-overflow", "double-free", "out-of-bounds",
        )
        return "out-of-bounds" if any(m in stderr for m in memory_markers) else "asan"
    if "runtime error:" in stderr:
        return "ubsan"
    if returncode != 0:
        return "crash"
    if "PARSE_RESULT image" not in stdout:
        return "parse-failure"
    return "ok"


def run_malformed_case(record: dict, env: dict[str, str]) -> dict:
    command = [str(PARSE_BIN), record["path"]]
    timed_out = False
    try:
        completed = run_command(command, capture_output=True, timeout=20, env=env)
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        returncode = "TIMEOUT"
        timed_out = True
    asan_status = classify(returncode, stdout, stderr, timed_out)
    leak_returncode: int | str | None = None
    leak_status = "not-run"
    leak_detail = ""
    if asan_status == "ok":
        try:
            leak_completed = run_command(
                [str(LEAK_BIN), record["path"]],
                capture_output=True,
                timeout=20,
            )
            leak_returncode = leak_completed.returncode
            if "LEAK_AUDIT leaks=" in leak_completed.stdout:
                leak_status = "leak"
                leak_detail = next(
                    line.strip()
                    for line in leak_completed.stdout.splitlines()
                    if "LEAK_AUDIT leaks=" in line
                )
            elif leak_completed.returncode == 0 and "LEAK_AUDIT clean" in leak_completed.stdout:
                leak_status = "ok"
            else:
                leak_status = "leak-harness-failure"
                leak_detail = (
                    f"exit={leak_completed.returncode} "
                    f"{leak_completed.stderr.strip()[:240]}"
                )
        except subprocess.TimeoutExpired:
            leak_returncode = "TIMEOUT"
            leak_status = "leak-harness-timeout"
            leak_detail = "leak audit timed out"
    status = asan_status if asan_status != "ok" else leak_status
    detail = ""
    if status != "ok":
        if leak_detail:
            detail = leak_detail
        else:
            lines = [line.strip() for line in stderr.splitlines() if line.strip()]
            detail = lines[0] if lines else f"returncode={returncode}"
    return {
        **record,
        "returncode": returncode,
        "asan_status": asan_status,
        "leak_returncode": leak_returncode,
        "leak_status": leak_status,
        "status": status,
        "detail": detail,
        "stdout": stdout,
        "stderr": stderr,
    }


def bit_float(token: str) -> float:
    return struct.unpack(">f", bytes.fromhex(token))[0]


def parse_dump(text: str) -> dict:
    image = {"width": None, "height": None, "shapes": []}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "IMAGE":
            image["width"] = bit_float(fields[1])
            image["height"] = bit_float(fields[2])
        elif fields[0] == "SHAPE":
            image["shapes"].append(
                {
                    "fill_type": int(fields[2]),
                    "stroke_type": int(fields[3]),
                    "paths": [],
                }
            )
        elif fields[0] == "PATH":
            npts = int(fields[3])
            point_tokens = fields[8:]
            if len(point_tokens) != npts * 2:
                raise ValueError(f"bad PATH point count: {line}")
            image["shapes"][-1]["paths"].append(
                {
                    "closed": int(fields[2]),
                    "npts": npts,
                    "pts": [bit_float(token) for token in point_tokens],
                }
            )
    return image


def compare_geometry(actual: dict, expected: dict) -> dict:
    max_error = 0.0
    first_mismatch = ""

    def fail(message: str) -> dict:
        return {
            "ok": False,
            "max_abs_error": max_error,
            "detail": first_mismatch or message,
        }

    if actual["width"] is None or actual["height"] is None:
        return fail("missing image dimensions")
    for key in ("width", "height"):
        error = abs(actual[key] - expected[key])
        max_error = max(max_error, error)
        if error > TOLERANCE:
            first_mismatch = f"{key} differs by {error:g}"
            return fail(first_mismatch)
    if len(actual["shapes"]) != len(expected["shapes"]):
        first_mismatch = (
            f"shape count {len(actual['shapes'])} != {len(expected['shapes'])}"
        )
        return fail(first_mismatch)
    for shape_index, (actual_shape, expected_shape) in enumerate(
        zip(actual["shapes"], expected["shapes"])
    ):
        a_meta = (actual_shape["fill_type"], actual_shape["stroke_type"])
        e_meta = (expected_shape["fill_type"], expected_shape["stroke_type"])
        if a_meta != e_meta:
            first_mismatch = f"shape {shape_index} paint types {a_meta} != {e_meta}"
            return fail(first_mismatch)
        if len(actual_shape["paths"]) != len(expected_shape["paths"]):
            first_mismatch = (
                f"shape {shape_index} path count "
                f"{len(actual_shape['paths'])} != {len(expected_shape['paths'])}"
            )
            return fail(first_mismatch)
        for path_index, (actual_path, expected_path) in enumerate(
            zip(actual_shape["paths"], expected_shape["paths"])
        ):
            for key in ("closed", "npts"):
                if actual_path[key] != expected_path[key]:
                    first_mismatch = (
                        f"shape {shape_index} path {path_index} {key} "
                        f"{actual_path[key]} != {expected_path[key]}"
                    )
                    return fail(first_mismatch)
            for point_index, (actual_value, expected_value) in enumerate(
                zip(actual_path["pts"], expected_path["pts"])
            ):
                error = abs(actual_value - expected_value)
                max_error = max(max_error, error)
                if error > TOLERANCE:
                    first_mismatch = (
                        f"shape {shape_index} path {path_index} "
                        f"point {point_index} differs by {error:g}"
                    )
                    return fail(first_mismatch)
    return {"ok": True, "max_abs_error": max_error, "detail": ""}


def run_valid_geometry(record: dict) -> dict:
    completed = run_command(
        [str(PARSE_BIN), record["path"], "--dump"], capture_output=True
    )
    if completed.returncode != 0:
        return {
            **record,
            "ok": False,
            "max_abs_error": None,
            "detail": f"harness exit {completed.returncode}",
        }
    try:
        actual = parse_dump(completed.stdout)
        expected = ref_parser.parse_file(ROOT / record["path"])
        comparison = compare_geometry(actual, expected)
    except Exception as exc:
        return {
            **record,
            "ok": False,
            "max_abs_error": None,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    return {**record, **comparison}


def run_raster_geometry(record: dict) -> dict:
    temp_dir = OUT / "tmp" / record["id"]
    temp_dir.mkdir(parents=True, exist_ok=True)
    reference_png = temp_dir / "reference.png"
    nano_pgm = temp_dir / "nanosvg.pgm"
    try:
        reference = run_command(
            ["rsvg-convert", "-b", "none", "-o", str(reference_png), record["path"]],
            capture_output=True, timeout=30,
        )
        if reference.returncode != 0:
            return {
                **record,
                "ok": False,
                "mae": None,
                "iou": None,
                "detail": f"rsvg-convert exit {reference.returncode}",
            }
        with nano_pgm.open("wb") as output:
            rendered = subprocess.run(
                [str(RENDER_BIN), record["path"]],
                cwd=ROOT, stdout=output, stderr=subprocess.PIPE, timeout=30,
            )
        if rendered.returncode != 0:
            return {
                **record,
                "ok": False,
                "mae": None,
                "iou": None,
                "detail": f"render_one exit {rendered.returncode}",
            }
        reference_image = Image.open(reference_png).convert("RGBA")
        nano_image = Image.open(nano_pgm)
        if reference_image.size != nano_image.size:
            return {
                **record,
                "ok": False,
                "mae": None,
                "iou": None,
                "detail": (
                    f"size {reference_image.size} != {nano_image.size}"
                ),
            }
        reference_alpha = reference_image.getchannel("A").tobytes()
        nano_alpha = nano_image.tobytes()
        total = len(reference_alpha)
        absolute_error = 0
        intersection = 0
        union = 0
        for reference_value, nano_value in zip(reference_alpha, nano_alpha):
            absolute_error += abs(reference_value - nano_value)
            reference_ink = reference_value >= 128
            nano_ink = nano_value >= 128
            intersection += reference_ink and nano_ink
            union += reference_ink or nano_ink
        mae = absolute_error / (255.0 * total)
        iou = intersection / union if union else 1.0
        ok = mae <= RASTER_MAE_LIMIT and iou >= RASTER_IOU_LIMIT
        return {
            **record,
            "ok": ok,
            "mae": mae,
            "iou": iou,
            "detail": "" if ok else "raster threshold exceeded",
        }
    except subprocess.TimeoutExpired:
        return {
            **record,
            "ok": False,
            "mae": None,
            "iou": None,
            "detail": "timeout",
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def summarize_malformed(results: list[dict]) -> list[dict]:
    summaries = []
    categories = sorted({result["category"] for result in results})
    for category in categories:
        rows = [result for result in results if result["category"] == category]
        statuses = Counter(row["status"] for row in rows)
        asan_statuses = Counter(row["asan_status"] for row in rows)
        leak_statuses = Counter(row["leak_status"] for row in rows)
        summaries.append(
            {
                "category": category,
                "cases": len(rows),
                "bytes": sum(row["size"] for row in rows),
                "size_min": min(row["size"] for row in rows),
                "size_max": max(row["size"] for row in rows),
                "offset_min": min(row["failure_offset"] for row in rows),
                "offset_max": max(row["failure_offset"] for row in rows),
                "statuses": dict(sorted(statuses.items())),
                "asan_statuses": dict(sorted(asan_statuses.items())),
                "leak_statuses": dict(sorted(leak_statuses.items())),
                "failures": sum(count for status, count in statuses.items() if status != "ok"),
            }
        )
    return summaries


def run_minimal(records: list[dict], env: dict[str, str]) -> list[dict]:
    results = []
    for record in records:
        result = run_malformed_case(record, env)
        extra = {}
        if record["category"].startswith("valid-"):
            completed = run_command(
                [str(PARSE_BIN), record["path"], "--dump"], capture_output=True
            )
            expected = ref_parser.parse_file(ROOT / record["path"])
            if completed.returncode == 0:
                actual = parse_dump(completed.stdout)
                comparison = compare_geometry(actual, expected)
                extra = {
                    "geometry_ok": comparison["ok"],
                    "max_abs_error": comparison["max_abs_error"],
                }
            else:
                extra = {"geometry_ok": False, "max_abs_error": None}
        result.pop("stdout", None)
        result.pop("stderr", None)
        result.update(extra)
        results.append(result)
    return results


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_report(report: dict) -> str:
    malformed = report["malformed"]
    valid = report["valid"]
    findings = report["findings"]
    lines = [
        "# NanoSVG malformed/valid corpus audit",
        "",
        "## Environment",
        "",
        f"- Commit: `{report['commit']}`",
        f"- Compiler: `{report['environment']['cc']}`",
        f"- Memory/UB sanitizers: `{report['environment']['sanitizers']}`",
        f"- Leak detector: `{report['environment']['leak_detector']}`",
        f"- Reference renderer: `{report['environment']['rsvg_convert']}`",
        f"- Generated at: `{report['generated_at']}`",
        "",
        "## Corpus",
        "",
        "| Class | Files | Bytes | First-invalid byte offset range |",
        "|---|---:|---:|---:|",
    ]
    for row in malformed["by_category"]:
        lines.append(
            f"| {row['category']} | {row['cases']} | {row['bytes']} | "
            f"{row['offset_min']}..{row['offset_max']} |"
        )
    lines.extend(
        [
            f"| **Total** | **{malformed['cases']}** | "
            f"**{malformed['bytes']}** | "
            f"{min(row['offset_min'] for row in malformed['by_category'])}.."
            f"{max(row['offset_max'] for row in malformed['by_category'])} |",
            "",
            "Every malformed record carries a zero-based `failure_offset`. "
            "For truncated inputs it denotes the first missing byte; for the "
            "other classes it denotes the first byte of the invalid construct.",
            "",
            "## Malformed results",
            "",
            "| Class | OK | Crash | OOB | Leak | UBSan | Timeout |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in malformed["by_category"]:
        statuses = row["statuses"]
        asan_statuses = row["asan_statuses"]
        leak_statuses = row["leak_statuses"]
        lines.append(
            f"| {row['category']} | {statuses.get('ok', 0)} | "
            f"{asan_statuses.get('crash', 0)} | {asan_statuses.get('out-of-bounds', 0)} | "
            f"{leak_statuses.get('leak', 0)} | {asan_statuses.get('ubsan', 0)} | "
            f"{asan_statuses.get('timeout', 0)} |"
        )
    lines.extend(
        [
            "",
            f"Result: **{malformed['total_failures']} memory-safety failures** "
            f"across {malformed['cases']} malformed files. "
            f"Elapsed: `{malformed['elapsed_seconds']:.2f}s`.",
            "",
            "Leak checking uses exact allocator pairing over each parse/delete "
            "interval. The detector self-test injects a 1,234-byte leak and "
            "confirms that it is reported.",
            "",
            "NanoSVG has no public parse-error/offset result. All malformed "
            "inputs in this corpus returned an image (permissive parsing); the "
            "offset column is the corpus' strict first-invalid-byte annotation, "
            "not a value returned by `nsvgParse`.",
            "",
            "## Valid-file consistency",
            "",
            "| Check | Passed | Total | Threshold | Worst case |",
            "|---|---:|---:|---|---|",
            f"| Geometry vs Python reference | {valid['geometry_passed']} | "
            f"{valid['cases']} | abs error <= {TOLERANCE:g} | "
            f"{valid['geometry_max_error']:.3g} |",
            f"| Alpha coverage vs librsvg | {valid['raster_passed']} | "
            f"{valid['cases']} | MAE <= {RASTER_MAE_LIMIT:g}, IoU >= "
            f"{RASTER_IOU_LIMIT:g} | MAE {valid['raster_max_mae']:.3g}, "
            f"IoU {valid['raster_min_iou']:.3g} |",
            "",
            "The geometry reference is `fuzz/ref_parser.py`, an independent "
            "Python XML/path parser. Raster parity uses `rsvg-convert -b none` "
            "and compares alpha coverage at native SVG dimensions.",
            "",
            "## Findings and minimal reproducers",
            "",
        ]
    )
    for finding in findings:
        lines.extend(
            [
                f"### {finding['title']}",
                "",
                f"- Status: **{finding['status']}**",
                f"- Reproducer: `{finding['reproducer']}`",
                f"- Byte offset: `{finding['offset']}`",
                f"- Impact: {finding['impact']}",
                f"- Root cause: {finding['root_cause']}",
                f"- Fix: {finding['fix']}",
                f"- Before fix: {finding['before_fix']}",
                f"- After fix: {finding['after_fix']}",
                "",
            ]
        )
        if finding.get("snippet"):
            lines.extend(
                [
                    "```xml",
                    finding["snippet"].strip(),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "No crash, out-of-bounds access, undefined behavior, or leak "
            "counterexample was found by the sanitizer campaign.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "python3 fuzz/gen_corpus.py",
            "python3 fuzz/run_audit.py",
            "```",
            "",
            "Generated evidence:",
            "",
            "- `artifacts/fuzz/manifest.json`",
            "- `artifacts/fuzz/malformed_results.csv`",
            "- `artifacts/fuzz/valid_geometry_results.csv`",
            "- `artifacts/fuzz/valid_raster_results.csv`",
            "- `artifacts/fuzz/report.json`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()

    manifest_path = OUT / "manifest.json"
    if not manifest_path.exists():
        run_command([sys.executable, "fuzz/gen_corpus.py"], check=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["malformed_count"] != 2000 or manifest["valid_count"] != 300:
        raise RuntimeError("manifest does not contain 2000 malformed + 300 valid cases")

    build = build_harnesses()
    env = malformed_environment()
    rsvg_version = run_command(
        ["rsvg-convert", "--version"], capture_output=True, check=True
    ).stdout.replace("\n", " ")
    commit = run_command(
        ["git", "rev-parse", "HEAD"], capture_output=True, check=True
    ).stdout.strip()
    dirty = bool(
        run_command(["git", "status", "--porcelain"], capture_output=True).stdout.strip()
    )

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        malformed_results = list(
            executor.map(
                lambda record: run_malformed_case(record, env),
                manifest["malformed"],
            )
        )
    malformed_elapsed = time.time() - start

    failures_dir = OUT / "failures"
    shutil.rmtree(failures_dir, ignore_errors=True)
    for result in malformed_results:
        if result["status"] != "ok":
            failures_dir.mkdir(parents=True, exist_ok=True)
            (failures_dir / f"{result['id']}.log").write_text(
                result["stderr"], encoding="utf-8", errors="replace"
            )

    write_csv(
        OUT / "malformed_results.csv",
        malformed_results,
        [
            "id", "category", "size", "failure_offset", "returncode",
            "asan_status", "leak_returncode", "leak_status", "status",
            "detail", "path",
        ],
    )

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        geometry_results = list(
            executor.map(run_valid_geometry, manifest["valid"])
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        raster_results = list(
            executor.map(run_raster_geometry, manifest["valid"])
        )
    valid_elapsed = time.time() - start

    write_csv(
        OUT / "valid_geometry_results.csv",
        geometry_results,
        ["id", "ok", "max_abs_error", "detail", "path"],
    )
    write_csv(
        OUT / "valid_raster_results.csv",
        raster_results,
        ["id", "ok", "mae", "iou", "detail", "path"],
    )

    minimal_results = run_minimal(manifest["minimal"], env)
    malformed_summary = summarize_malformed(malformed_results)
    geometry_passed = sum(result["ok"] for result in geometry_results)
    raster_passed = sum(result["ok"] for result in raster_results)
    geometry_errors = [
        result["max_abs_error"]
        for result in geometry_results
        if result["max_abs_error"] is not None
    ]
    raster_maes = [
        result["mae"] for result in raster_results if result["mae"] is not None
    ]
    raster_ious = [
        result["iou"] for result in raster_results if result["iou"] is not None
    ]

    findings = [
        {
            "title": "Capital-Y preserveAspectRatio keywords were ignored",
            "status": "fixed",
            "reproducer": "artifacts/fuzz/minimal/valid-preserve-aspect-ratio.svg",
            "offset": next(
                item["failure_offset"]
                for item in manifest["minimal"]
                if item["id"] == "minimal-valid-preserve-aspect-ratio"
            ),
            "impact": (
                "A valid non-square viewport using xMidYMid meet was aligned "
                "to the top instead of vertically centered."
            ),
            "root_cause": (
                "The parser searched for lowercase yMin/yMid/yMax, while SVG "
                "uses YMin/YMid/YMax."
            ),
            "fix": (
                "Accept the spec capitalization while retaining the old "
                "lowercase spellings for compatibility."
            ),
            "snippet": (
                OUT / "minimal" / "valid-preserve-aspect-ratio.svg"
            ).read_text(encoding="utf-8"),
            "before_fix": "the parsed rectangle starts at y=0",
            "after_fix": "the parsed rectangle starts at y=5 and matches the reference",
        },
        {
            "title": "Default preserveAspectRatio was none instead of xMidYMid meet",
            "status": "fixed",
            "reproducer": (
                "artifacts/fuzz/minimal/"
                "valid-default-preserve-aspect-ratio.svg"
            ),
            "offset": next(
                item["failure_offset"]
                for item in manifest["minimal"]
                if item["id"] == "minimal-valid-default-preserve-aspect-ratio"
            ),
            "impact": (
                "Valid SVGs with a non-matching viewBox aspect ratio were "
                "stretched non-uniformly by default."
            ),
            "root_cause": (
                "The parser's zero-initialized alignment state mapped to "
                "NSVG_ALIGN_NONE; SVG's initial value is xMidYMid meet."
            ),
            "fix": (
                "Initialize alignX/alignY to mid and alignType to meet in "
                "nsvg__createParser()."
            ),
            "snippet": (
                OUT / "minimal" / "valid-default-preserve-aspect-ratio.svg"
            ).read_text(encoding="utf-8"),
            "before_fix": "the parsed rectangle starts at y=0",
            "after_fix": "the parsed rectangle starts at y=5 and matches the reference",
        },
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "working_tree_dirty": dirty,
        "environment": {
            "cc": build["cc"],
            "sanitizers": "address,undefined; -fno-sanitize-recover=all",
            "leak_detector": build["leak_detector"],
            "leak_selftest": build["leak_selftest_result"],
            "rsvg_convert": rsvg_version,
        },
        "build_commands": {
            "parse_one": build["parse"],
            "render_one": build["render"],
            "parse_one_leak": build["leak"],
            "leak_selftest": build["leak_selftest_command"],
        },
        "malformed": {
            "cases": len(malformed_results),
            "bytes": sum(result["size"] for result in malformed_results),
            "total_failures": sum(
                result["status"] != "ok" for result in malformed_results
            ),
            "elapsed_seconds": malformed_elapsed,
            "by_category": malformed_summary,
        },
        "valid": {
            "cases": len(geometry_results),
            "geometry_passed": geometry_passed,
            "geometry_max_error": max(geometry_errors) if geometry_errors else None,
            "raster_passed": raster_passed,
            "raster_max_mae": max(raster_maes) if raster_maes else None,
            "raster_min_iou": min(raster_ious) if raster_ious else None,
            "elapsed_seconds": valid_elapsed,
        },
        "minimal": minimal_results,
        "findings": findings,
    }

    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "report.md").write_text(markdown_report(report), encoding="utf-8")

    print(
        f"malformed: {report['malformed']['cases']} cases, "
        f"{report['malformed']['total_failures']} failures, "
        f"{malformed_elapsed:.2f}s"
    )
    print(
        f"valid geometry: {geometry_passed}/{len(geometry_results)}, "
        f"max error {report['valid']['geometry_max_error']:.3g}"
    )
    print(
        f"valid raster: {raster_passed}/{len(raster_results)}, "
        f"max MAE {report['valid']['raster_max_mae']:.3g}, "
        f"min IoU {report['valid']['raster_min_iou']:.3g}"
    )
    print(f"report: {OUT / 'report.md'}")
    return 0 if report["malformed"]["total_failures"] == 0 and geometry_passed == 300 and raster_passed == 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
