#!/usr/bin/env python3
"""Build and run one cocotb suite against the RTL in rtl/.

usage: run.py SUITE [--sim verilator|icarus] [--w N] [--param KEY=VAL ...]
              [--env KEY=VAL ...] [--testcase NAME] [--seed N] [--waves FILE]
              [--rtl DIR] [--tag TAG]

SUITE: fifo | master | slave | top | xmaster | xslave | axi | axip
       | wmaster | wslave | wtop   (short scenarios that dump VCDs)

Build directories go to tests/cocotb/build/, logs, JUnit XML and JSON
results to tests/cocotb/results/ (both ignored by git).
"""
import argparse
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HDL = HERE / "hdl"
BUILD = HERE / "build"
RESULTS = HERE / "results"

CORE = ["spi_fifo.sv", "spi_master.sv", "spi_slave.sv", "spi_top.sv"]
AXI = ["spi_fifo.sv", "spi_master.sv", "axi4lite_regfile.sv", "axi4lite_slave.sv", "spi_axi4lite.sv"]
P_MASTER = ["DATA_WIDTH", "NUM_CS", "FIFO_DEPTH", "DIV_WIDTH", "CS_DLY_W"]

# suite: (toplevel, test module, RTL files, testbench file or None, parameter names)
SUITES = {
    "fifo": ("spi_fifo", "test_fifo", ["spi_fifo.sv"], None, ["WIDTH", "DEPTH"]),
    "master": ("tb_master", "test_master", CORE, "tb_master.sv", P_MASTER),
    "slave": ("tb_slave", "test_slave", CORE, "tb_slave.sv", ["DATA_WIDTH", "FIFO_DEPTH"]),
    "top": ("tb_top", "test_top", CORE, "tb_top.sv", P_MASTER),
    "xmaster": ("tb_master", "test_xcheck_cocotbext", CORE, "tb_master.sv", P_MASTER),
    "xslave": ("tb_slave", "test_xcheck_cocotbext", CORE, "tb_slave.sv", ["DATA_WIDTH", "FIFO_DEPTH"]),
    "axi": ("tb_axi_top", "test_axi", AXI, "tb_axi_top.sv", []),
    "axip": ("tb_axi_top", "test_axi_param", AXI, "tb_axi_top.sv", ["DATA_WIDTH", "NUM_CS", "SPI_DATA_W"]),
    "wmaster": ("tb_master", "test_waves", CORE, "tb_master.sv", P_MASTER),
    "wslave": ("tb_slave", "test_waves", CORE, "tb_slave.sv", ["DATA_WIDTH", "FIFO_DEPTH"]),
    "wtop": ("tb_top", "test_waves", CORE, "tb_top.sv", P_MASTER),
}
# The embedded Python of the simulators occasionally segfaults in libpython
# (seen on both simulators with the OSS CAD Suite, independent of the RTL):
# either before a test starts, which leaves no results file and is retried,
# or at exit after the results were written, which is reported.
SIM_CRASH_RETRIES = 2


def read_results(xml):
    """(passed, failed, skipped) from a cocotb JUnit file, or None if missing."""
    try:
        root = ET.parse(xml).getroot()
    except (OSError, ET.ParseError):
        return None
    passed = failed = skipped = 0
    for tc in root.iter("testcase"):
        if tc.find("skipped") is not None:
            skipped += 1
        elif tc.find("failure") is not None or tc.find("error") is not None:
            failed += 1
        else:
            passed += 1
    return passed, failed, skipped


DEFAULTS = {"DATA_WIDTH": 8, "NUM_CS": 4, "FIFO_DEPTH": 16, "DIV_WIDTH": 16, "CS_DLY_W": 8,
            "WIDTH": 8, "DEPTH": 4}
OPTIONAL = {"FWFT", "SPI_DATA_W", "ADDR_WIDTH"}


def run_suite(suite, sim="verilator", width=8, params=(), env=(), testcase=None, seed=12345,
              waves=None, rtl=None, tag=None):
    """Build + run one suite.

    Returns (passed, failed, seconds, results_xml, log, retries, exit_signal):
    retries counts simulator crashes before any result was written,
    exit_signal is the signal that ended the last run after its results were
    written (0 for a normal exit).
    """
    top, module, rtl_files, tb, pnames = SUITES[suite]
    rtl_dir = Path(rtl) if rtl else REPO / "rtl"
    if suite == "axip":
        p = {"DATA_WIDTH": 32, "NUM_CS": 4, "SPI_DATA_W": 8}   # AXI bus width, not the SPI word
    else:
        p = {k: DEFAULTS[k] for k in pnames}
        for k in ("DATA_WIDTH", "WIDTH"):
            if k in p:
                p[k] = width
    for kv in params:
        k, v = kv.split("=", 1)
        if k not in p and k not in OPTIONAL:
            raise SystemExit(f"unknown parameter {k} for suite {suite}")
        p[k] = int(v)
    ptag = "_".join(f"{k[:3]}{v}" for k, v in p.items()) or "default"
    tag = tag or f"{suite}_{sim}_{ptag}"
    bdir = BUILD / f"{sim}_{top}_{ptag}{'_waves' if waves else ''}"
    srcs = [rtl_dir / f for f in rtl_files] + ([HDL / tb] if tb else [])
    if sim == "verilator":
        bargs = ["-Wno-fatal"]
    else:
        bargs = ["-g2012"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    runner = get_runner(sim)
    t0 = time.time()
    # Always build from scratch: reusing a Verilator build directory after
    # an RTL edit has produced crashing simulations.
    shutil.rmtree(bdir, ignore_errors=True)
    runner.build(sources=srcs, hdl_toplevel=top, parameters=p, build_dir=bdir,
                 build_args=bargs, timescale=("1ns", "1ps"), waves=bool(waves),
                 log_file=RESULTS / f"build_{tag}.log")
    e = {
        "SPI_W": str(p.get("DATA_WIDTH", p.get("WIDTH", 8))),
        "SPI_NUM_CS": str(p.get("NUM_CS", 1)),
        "SPI_DEPTH": str(p.get("FIFO_DEPTH", p.get("DEPTH", 16))),
        "SPI_DIV_W": str(p.get("DIV_WIDTH", 16)),
        "SPI_CSDLY_W": str(p.get("CS_DLY_W", 8)),
        "FIFO_DEPTH": str(p.get("DEPTH", 16)),
        "FIFO_WIDTH": str(p.get("WIDTH", 8)),
        "FIFO_FWFT": str(p.get("FWFT", 0)),
        "AXI_DW": str(p.get("DATA_WIDTH", 32)),
        "AXI_NUM_CS": str(p.get("NUM_CS", 4)),
        "AXI_SPI_W": str(p.get("SPI_DATA_W", 8)),
        "SPI_SIM": sim,
        "SPI_DUT": top,
        "RESULTS_DIR": str(RESULTS),
        "RUN_TAG": tag,
        "PYTHONPATH": str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    for kv in env:
        k, v = kv.split("=", 1)
        e[k] = v
    xml = RESULTS / f"{tag}.xml"
    log = RESULTS / f"{tag}.log"
    if xml.exists():
        xml.unlink()
    retries = 0
    while True:
        code = 0
        try:
            runner.test(test_module=module, hdl_toplevel=top, testcase=testcase, extra_env=e,
                        build_dir=bdir, test_dir=bdir, seed=seed, results_xml=str(xml),
                        timescale=("1ns", "1ps"), waves=bool(waves), log_file=log)
        except SystemExit as ex:     # the runner exits when the simulator fails
            code = ex.code if isinstance(ex.code, int) else 1
        res = read_results(xml)
        if res is None and code < 0 and retries < SIM_CRASH_RETRIES:
            retries += 1
            print(f"{tag}: simulator killed by signal {-code} before writing results, retry {retries}",
                  file=sys.stderr)
            continue
        break
    secs = time.time() - t0
    exit_signal = -code if (res is not None and code < 0) else 0
    if res is None:
        passed, failed = 0, 1
    else:
        passed, failed, _ = res
    if waves:
        Path(waves).parent.mkdir(parents=True, exist_ok=True)
        dump = bdir / "dump.vcd"
        if not dump.exists():
            dump = bdir / "dump.fst"
        shutil.copy(dump, waves)
    return passed, failed, secs, xml, log, retries, exit_signal


def crash_note(retries, exit_signal):
    notes = []
    if retries:
        notes.append(f"{retries} simulator crash retries")
    if exit_signal:
        notes.append(f"simulator exited with signal {exit_signal} after writing the results")
    return f"  ({'; '.join(notes)})" if notes else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite", choices=sorted(SUITES))
    ap.add_argument("--sim", default=os.environ.get("SIM", "verilator"), choices=["verilator", "icarus"])
    ap.add_argument("--w", type=int, default=8, help="DATA_WIDTH (or WIDTH for the FIFO)")
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--testcase", default=None)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--waves", default=None, help="copy the simulator dump to this path")
    ap.add_argument("--rtl", default=None, help="RTL directory (default: rtl/)")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    p, f, secs, xml, log, retries, sig = run_suite(a.suite, a.sim, a.w, a.param, a.env, a.testcase,
                                                   a.seed, a.waves, a.rtl, a.tag)
    print(f"{a.suite} ({a.sim}): {p} passed, {f} failed in {secs:.1f} s{crash_note(retries, sig)}  log: {log}")
    return 0 if (p > 0 and f == 0) else 1


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
