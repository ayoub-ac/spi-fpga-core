#!/usr/bin/env python3
"""Dump the scenarios of test_waves.py (Verilator, VCD) and render the PNG
timing plots: one per master mode, the master SCK idle level after reset,
the slave at the SCK/clk ratio limits, and the CPHA = 1 last bit through the
master -> slave loopback.

usage: waves.py [OUT_DIR]     (default: tests/cocotb/results/waves)

Requires matplotlib and vcdvcd. Exit status is non-zero if a scenario fails.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import plots  # noqa: E402
from run import RESULTS, run_suite  # noqa: E402

# (VCD stem, SCK period in clk_i periods, jitter as a fraction of a half
# period): the documented minimum ratio (SCK high / low >= 2 clk) with
# jitter, and the measured simulation limit without jitter.
SLAVE_RUNS = (("slave_ratio_4p0_jitter", 4.0, 0.03), ("slave_ratio_3p1", 3.1, 0.0))

SCENARIOS = [(f"master_mode{m}", "wmaster", "test_w_master_mode", [f"WAVE_MODE={m}"]) for m in range(4)]
SCENARIOS += [("master_idle", "wmaster", "test_w_master_idle", [])]
SCENARIOS += [(stem, "wslave", "test_w_slave_tight", [f"WAVE_SCK_RATIO={r}", f"WAVE_JITTER={j}"])
              for stem, r, j in SLAVE_RUNS]
SCENARIOS += [("top_lastbit", "wtop", "test_w_top_lastbit", [])]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS / "waves"
    vcd_dir = out / "vcd"
    vcd_dir.mkdir(parents=True, exist_ok=True)
    bad = []
    for stem, suite, testcase, env in SCENARIOS:
        p, f, secs, _, log, _, _ = run_suite(suite, "verilator", 8, (), env, testcase=testcase,
                                             waves=str(vcd_dir / f"{stem}.vcd"), tag=f"waves_{stem}")
        print(f"{'PASS' if p and not f else 'FAIL'}  {stem:<28} {secs:5.1f} s  log: {log}", flush=True)
        if not p or f:
            bad.append(stem)
    for png in plots.main(str(vcd_dir), str(out), SLAVE_RUNS):
        print(png)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
