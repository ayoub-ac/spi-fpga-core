#!/usr/bin/env python3
"""Copy the measured synthesis and place-and-route numbers into the docs.

usage: scripts/update_numbers.py [--pnr DIR] [--check]

Reads DIR/summary.json (default build/pnr, written by scripts/pnr.py) and
rewrites the generated sections of README.md, RESOURCE_ESTIMATES.md and
VERIFICATION.md: the lines between

    <!-- BEGIN GENERATED <name> ... -->
    <!-- END GENERATED <name> -->

`make doc-numbers` runs `make pnr`, `make synth_report` (SYNTH_REPORT.md)
and then this script, so one command regenerates every number in the docs.
With --check nothing is written; the exit status is 1 if a section is out
of date.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Generated sections each document must contain.
DOCS = {
    "README.md": ["pnr-table"],
    "RESOURCE_ESTIMATES.md": ["pnr-table"],
    "VERIFICATION.md": ["pnr-v10"],
}

# v1.0.0 (commit ae8319d) through scripts/pnr.py, OSS CAD Suite 2026-11-01,
# measured 2026-11-07: LUT4, FF, Fmax for seeds 1, 2, 3.
V10_BASELINE = {
    ("spi_top", "ice40"): (417, 286, (39.92, 42.33, 42.66)),
    ("spi_top", "ecp5"): (363, 226, (163.48, 161.97, 154.04)),
    ("spi_axi4lite", "ice40"): (1338, 794, (38.14, 36.35, 37.05)),
    ("spi_axi4lite", "ecp5"): (1296, 764, (137.99, 135.63, 133.60)),
}

RAM_CELLS = {"ice40": ("SB_RAM40_4K", None), "ecp5": ("DP16KD", "TRELLIS_DPR16X4")}

SECTION = re.compile(r"(<!-- BEGIN GENERATED (?P<name>[\w-]+)[^\n]*-->\n)(?P<body>.*?)(<!-- END GENERATED (?P=name) -->)",
                     re.DOTALL)


def fmax_values(row):
    return [v for v in row["fmax"].values() if v is not None]


def span(values):
    return f"{min(values):.1f} .. {max(values):.1f}" if values else "n/a"


def target(row):
    v, t = fmax_values(row), row["target_mhz"]
    if v and min(v) >= t:
        return f"{t} MHz: met"
    if not v or max(v) < t:
        return f"{t} MHz: not met"
    return f"{t} MHz: met on some seeds"


def ram(row):
    bram, lutram = RAM_CELLS[row["family"]]
    parts = [f"{row['bram']} {bram}"] if row["bram"] else []
    if lutram and row["lutram"]:
        parts.append(f"{row['lutram']} {lutram}")
    return ", ".join(parts) or "none"


def versus(new, old):
    """Fmax change of the seed range new against old, in percent."""
    if min(new) <= max(old) and min(old) <= max(new):
        return "within the seed spread"
    a, b = sorted(round(abs(100 * (n / o - 1))) for n, o in ((min(new), min(old)), (max(new), max(old))))
    pct = f"{a}" if a == b else f"{a} .. {b}"
    return f"{pct} % {'faster' if min(new) > max(old) else 'slower'}"


def tools_line(s):
    t = s["tools"]
    seeds = ", ".join(map(str, s["seeds"]))
    return (f"Measured by `make pnr` ({t['yosys']}, {t['nextpnr']},\n"
            f"OSS CAD Suite {t['oss_cad_suite']}), default parameters, nextpnr seeds {seeds}.")


def render_pnr_table(s):
    lines = [tools_line(s),
             "Fmax is the register-to-register limit, without pin constraints or I/O timing.",
             "",
             "| Design | Device | LUT4 | FF | RAM cells | Fmax (MHz) | nextpnr target |",
             "|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        lines.append(f"| {r['design']} | {r['device']} | {r['lut']} | {r['ff']} | {ram(r)} | "
                     f"{span(fmax_values(r))} | {target(r)} |")
    return "\n".join(lines) + "\n"


def render_pnr_v10(s):
    lines = [tools_line(s),
             "",
             "| Design | Device | LUT4 | FF | RAM cells | Fmax (MHz) | v1.0, same flow | Fmax vs v1.0 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        lut, ff, f10 = V10_BASELINE[(r["design"], r["family"])]
        f11 = fmax_values(r)
        lines.append(f"| {r['design']} | {r['device']} | {r['lut']} | {r['ff']} | {ram(r)} | "
                     f"{span(f11)} (target {target(r)}) | {lut} LUT4, {ff} FF, {span(f10)} | "
                     f"{versus(f11, f10) if f11 else 'n/a'} |")
    return "\n".join(lines) + "\n"


RENDER = {"pnr-table": render_pnr_table, "pnr-v10": render_pnr_v10}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pnr", default=str(ROOT / "build" / "pnr"), help="scripts/pnr.py output directory")
    ap.add_argument("--check", action="store_true", help="only report out-of-date sections")
    a = ap.parse_args()
    path = Path(a.pnr) / "summary.json"
    try:
        summary = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path}: {e}; run `make pnr` first")
    if not isinstance(summary, dict) or not {"tools", "seeds", "rows"} <= summary.keys():
        raise SystemExit(f"{path}: not written by the current scripts/pnr.py; run `make pnr`")
    stale = []
    for doc, wanted in DOCS.items():
        p = ROOT / doc
        text = p.read_bytes().decode()
        found = [m.group("name") for m in SECTION.finditer(text)]
        missing = sorted(set(wanted) - set(found))
        unknown = sorted(set(found) - set(RENDER))
        if missing or unknown:
            raise SystemExit(f"{doc}: missing sections {missing}, unknown sections {unknown}")
        new = SECTION.sub(lambda m: m.group(1) + RENDER[m.group("name")](summary) + m.group(4), text)
        if new != text:
            stale.append(doc)
            if not a.check:
                p.write_bytes(new.encode())
    verb = "out of date" if a.check else "updated"
    print(f"update_numbers: {verb}: {', '.join(stale) or 'none'}")
    return 1 if a.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
