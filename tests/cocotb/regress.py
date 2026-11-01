#!/usr/bin/env python3
"""Run a profile of cocotb suites and print a summary table.

usage: regress.py [--sim verilator|icarus] [--profile ci|long] [--only SUBSTR] [--list]

Profiles (all seeds are fixed, so every run is deterministic):
  ci    every suite on default and edge parameters with shortened random
        runs; Icarus runs a subset (the wide-word and NUM_CS=1 master runs
        and the 32-bit slave run are Verilator only). About 4 minutes on
        Verilator and 2 on Icarus.
  long  word widths 2, 4, 5, 8, 13, 16, 32 and 64, thousands of random words
        per run, FIFO depths 1..17 and 32 in both read modes, non-default
        NUM_CS / FIFO_DEPTH; Verilator, except the AXI suites (both).

Exit status is non-zero if any suite fails or no test ran. The summary is
also written to results/regress_<sim>_<profile>.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run import RESULTS, crash_note, run_suite  # noqa: E402

BOTH = ("verilator", "icarus")
VERILATOR = ("verilator",)
ICARUS = ("icarus",)


def job(suite, w=8, params=(), env=(), sims=BOTH, tag=None):
    return dict(suite=suite, w=w, params=list(params), env=list(env), sims=sims, tag=tag)


def profile_ci():
    jobs = []
    for d in (1, 3, 4, 16):
        for f in (0, 1):
            jobs.append(job("fifo", params=[f"DEPTH={d}", f"FWFT={f}"], env=["FIFO_RANDOM_CYCLES=8000"]))
    jobs += [
        job("master", 8, env=["M_RANDOM_WORDS=1500"]),
        job("master", 13, env=["M_RANDOM_WORDS=400"], sims=VERILATOR),
        # the maximum-divider test dominates on Icarus: a 10-bit divider there
        job("master", 13, params=["DIV_WIDTH=10"], env=["M_RANDOM_WORDS=400"], sims=ICARUS),
        job("master", 32, env=["M_RANDOM_WORDS=300"], sims=VERILATOR),
        job("master", 8, params=["NUM_CS=1"], env=["M_RANDOM_WORDS=300"], sims=VERILATOR),
        job("slave", 8, env=["S_RANDOM_WORDS=1500"]),
        job("slave", 8, env=["S_RANDOM_WORDS=600", "S_PRELOAD=all"], tag="slave_preload_all"),
        job("slave", 5, env=["S_RANDOM_WORDS=300"]),
        job("slave", 32, env=["S_RANDOM_WORDS=200"], sims=VERILATOR),
        job("top", 8, env=["TOP_RANDOM_WORDS=1500"]),
        job("top", 16, env=["TOP_RANDOM_WORDS=300"]),
        job("xmaster", 8),
        job("xslave", 8),
        job("axi"),
        job("axip", params=["DATA_WIDTH=64"]),
        job("axip", params=["NUM_CS=1", "SPI_DATA_W=16"]),
        job("axip", params=["DATA_WIDTH=64", "SPI_DATA_W=32"]),
    ]
    return jobs


def profile_long():
    jobs = []
    for d in list(range(1, 18)) + [32]:
        for f in (0, 1):
            jobs.append(job("fifo", params=[f"DEPTH={d}", f"FWFT={f}"], sims=VERILATOR))
    for w in (2, 4, 5, 8, 13, 16, 32, 64):
        jobs.append(job("master", w, env=["M_RANDOM_WORDS=3000"], sims=VERILATOR))
        jobs.append(job("slave", w, env=["S_RANDOM_WORDS=3000"], sims=VERILATOR))
        jobs.append(job("top", w, env=["TOP_RANDOM_WORDS=3000"], sims=VERILATOR))
        jobs.append(job("xmaster", w, sims=VERILATOR))
        jobs.append(job("xslave", w, sims=VERILATOR))
    jobs += [
        job("master", 8, env=["M_RANDOM_WORDS=10000", "M_RANDOM_SEED=7"], tag="master_long_w8", sims=VERILATOR),
        job("slave", 8, env=["S_RANDOM_WORDS=10000", "S_RANDOM_SEED=8"], tag="slave_long_w8", sims=VERILATOR),
        job("slave", 8, env=["S_RANDOM_WORDS=5000", "S_PRELOAD=all"], tag="slave_long_preload_all", sims=VERILATOR),
        job("top", 8, env=["TOP_RANDOM_WORDS=10000"], tag="top_long_w8", sims=VERILATOR),
        job("master", 8, params=["NUM_CS=1"], env=["M_RANDOM_WORDS=2000"], sims=VERILATOR),
        job("master", 8, params=["NUM_CS=3"], env=["M_RANDOM_WORDS=2000"], sims=VERILATOR),
        job("master", 8, params=["FIFO_DEPTH=3"], env=["M_RANDOM_WORDS=2000"], sims=VERILATOR),
        job("slave", 8, params=["FIFO_DEPTH=1"], env=["S_RANDOM_WORDS=1000"], sims=VERILATOR),
        job("top", 8, params=["FIFO_DEPTH=5"], env=["TOP_RANDOM_WORDS=2000"], sims=VERILATOR),
        job("axi"),
        job("axip", params=["DATA_WIDTH=64"]),
        job("axip", params=["NUM_CS=1", "SPI_DATA_W=16"]),
        job("axip", params=["DATA_WIDTH=64", "SPI_DATA_W=32"]),
    ]
    return jobs


PROFILES = {"ci": profile_ci, "long": profile_long}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", default="verilator", choices=["verilator", "icarus"])
    ap.add_argument("--profile", default="ci", choices=sorted(PROFILES))
    ap.add_argument("--only", default=None, help="run only jobs whose suite/tag contains this text")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rtl", default=None, help="RTL directory (default: rtl/)")
    a = ap.parse_args()
    jobs = [j for j in PROFILES[a.profile]() if a.sim in j["sims"]]
    if a.only:
        jobs = [j for j in jobs if a.only in j["suite"] or a.only in (j["tag"] or "")]
    if a.list:
        for j in jobs:
            print(j)
        return 0
    rows = []
    t_all = time.time()
    for j in jobs:
        label = j["tag"] or f"{j['suite']} w={j['w']} {' '.join(j['params'])}".strip()
        tag = f"{j['tag']}_{a.sim}" if j["tag"] else None
        p, f, secs, xml, log, retries, sig = run_suite(j["suite"], a.sim, j["w"], j["params"], j["env"],
                                                       tag=tag, rtl=a.rtl)
        status = "PASS" if p > 0 and f == 0 else "FAIL"
        rows.append(dict(label=label, suite=j["suite"], sim=a.sim, passed=p, failed=f, secs=round(secs, 1),
                         retries=retries, exit_signal=sig, status=status, log=str(log)))
        print(f"{status}  {label:<40} {p:3d} passed {f:3d} failed {secs:7.1f} s{crash_note(retries, sig)}",
              flush=True)
    total = time.time() - t_all
    npass = sum(r["passed"] for r in rows)
    nfail = sum(r["failed"] for r in rows)
    bad = [r for r in rows if r["status"] != "PASS"]
    print(f"\n{a.sim} / {a.profile}: {len(rows) - len(bad)}/{len(rows)} suites passed, "
          f"{npass} tests passed, {nfail} failed, {sum(r['retries'] for r in rows)} crash retries, "
          f"{sum(1 for r in rows if r['exit_signal'])} crashes at exit after the results, {total:.0f} s")
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"regress_{a.sim}_{a.profile}.json").write_text(json.dumps(
        dict(sim=a.sim, profile=a.profile, seconds=round(total), rows=rows), indent=1))
    return 0 if rows and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
