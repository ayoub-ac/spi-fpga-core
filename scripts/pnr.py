#!/usr/bin/env python3
"""Open-source synthesis and place-and-route of spi_top and spi_axi4lite.

usage: scripts/pnr.py [--out DIR] [--seeds N ...]

Targets, default parameters: iCE40 UP5K (SG48, nextpnr --freq 50) and ECP5
LFE5U-25F (CABGA256, nextpnr --freq 100).

Resource counts come from Yosys synthesis of the core alone. Fmax comes from
nextpnr on a wrapper that shifts every core port through a serial register
chain (the cores have more ports than these packages have pins), so it is
the internal register-to-register limit, without pin constraints or I/O
timing; the wrapper adds one flip-flop per port bit to the P&R utilisation.
Every log stays in the output directory (default build/pnr); the summary
is printed and written to summary.md there.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RTL = ROOT / "rtl"
DESIGNS = {
    "spi_top": ("clk_i", ["spi_fifo.sv", "spi_master.sv", "spi_slave.sv", "spi_top.sv"]),
    "spi_axi4lite": ("s_axi_aclk", ["spi_fifo.sv", "spi_master.sv", "axi4lite_regfile.sv",
                                    "axi4lite_slave.sv", "spi_axi4lite.sv"]),
}
FAMILIES = {
    "ice40": dict(synth="synth_ice40", device="iCE40 UP5K SG48", freq=50,
                  pnr=["nextpnr-ice40", "--up5k", "--package", "sg48", "--pcf-allow-unconstrained"],
                  result=["--asc"], ext="asc",
                  lut=("SB_LUT4",), ff=("SB_DFF",), bram=("SB_RAM40_4K",), lutram=()),
    "ecp5": dict(synth="synth_ecp5 -abc9", device="ECP5 LFE5U-25F CABGA256", freq=100,
                 pnr=["nextpnr-ecp5", "--25k", "--package", "CABGA256", "--lpf-allow-unconstrained"],
                 result=["--textcfg"], ext="config",
                 lut=("LUT4",), ff=("TRELLIS_FF",), bram=("DP16KD",), lutram=("TRELLIS_DPR16X4",)),
}


def run(cmd, log):
    with open(log, "w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}), see {log}: {' '.join(map(str, cmd))}")


def yosys(script, log):
    run(["yosys", "-q", "-l", str(log), "-p", script], str(log) + ".out")


def count(cells, prefixes):
    return sum(n for name, n in cells.items() if any(name.startswith(p) for p in prefixes))


def wrapper(ports_json, top, clk, name):
    """Serial-in / serial-out wrapper: every input from a shift register, every
    output captured in parallel and shifted out on one pin."""
    ports = json.loads(Path(ports_json).read_text())["modules"][top]["ports"]
    ins = [(n, len(p["bits"])) for n, p in ports.items() if p["direction"] == "input" and n != clk]
    outs = [(n, len(p["bits"])) for n, p in ports.items() if p["direction"] == "output"]
    ni, no = sum(w for _, w in ins), sum(w for _, w in outs)
    conns, o = [f".{clk}(clk)"], 0
    for n, w in ins:
        conns.append(f".{n}(isr[{o + w - 1}:{o}])")
        o += w
    o = 0
    for n, w in outs:
        conns.append(f".{n}(ow[{o + w - 1}:{o}])")
        o += w
    body = ",\n    ".join(conns)
    return ni + no, (
        f"module {name} (input logic clk, input logic din, input logic ld, output logic dout);\n"
        f"  logic [{ni - 1}:0] isr;\n"
        f"  logic [{no - 1}:0] ow, osr;\n"
        f"  always_ff @(posedge clk) isr <= {{isr[{ni - 2}:0], din}};\n"
        f"  always_ff @(posedge clk) osr <= ld ? ow : {{osr[{no - 2}:0], 1'b0}};\n"
        f"  assign dout = osr[{no - 1}];\n"
        f"  {top} u_dut (\n    {body});\n"
        f"endmodule\n")


def pnr_log(log):
    text = Path(log).read_text()
    fmax = [float(m) for m in re.findall(r"Max frequency for clock\s+'[^']*':\s+([\d.]+) MHz", text)]
    util = {}
    block = text.split("Device utilisation:")[-1].split("\n\n")[0]
    for m in re.finditer(r"Info:\s+(\w+):\s+(\d+)/\s*(\d+)", block):
        util[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return (fmax[-1] if fmax else None), util


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "build" / "pnr"))
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for top, (clk, files) in DESIGNS.items():
        srcs = " ".join(str(RTL / f) for f in files)
        ports = out / f"{top}.ports.json"
        yosys(f"read_verilog -sv {srcs}; hierarchy -check -top {top}; proc; write_json {ports}",
              out / f"{top}.ports.log")
        nbits, text = wrapper(ports, top, clk, f"wrap_{top}")
        (out / f"wrap_{top}.sv").write_text(text)
        for fam, cfg in FAMILIES.items():
            tag = f"{top}.{fam}"
            stat = out / f"{tag}.stat.json"
            yosys(f"read_verilog -sv {srcs}; hierarchy -check -top {top}; {cfg['synth']} -top {top}; "
                  f"tee -q -o {stat} stat -json", out / f"{tag}.synth.log")
            cells = json.loads(stat.read_text())["design"]["num_cells_by_type"]
            net = out / f"{tag}.wrap.json"
            yosys(f"read_verilog -sv {srcs} {out / f'wrap_{top}.sv'}; {cfg['synth']} -top wrap_{top} "
                  f"-json {net}", out / f"{tag}.wrap.synth.log")
            seeds = {}
            for s in a.seeds:
                log = out / f"{tag}.pnr_seed{s}.log"
                run(cfg["pnr"] + ["--json", str(net), "--freq", str(cfg["freq"]), "--timing-allow-fail",
                                  "--seed", str(s)] + cfg["result"] + [str(out / f"{tag}.seed{s}.{cfg['ext']}")],
                    log)
                seeds[s] = pnr_log(log)
            rows.append(dict(design=top, family=fam, device=cfg["device"], target_mhz=cfg["freq"],
                             lut=count(cells, cfg["lut"]), ff=count(cells, cfg["ff"]),
                             bram=count(cells, cfg["bram"]), lutram=count(cells, cfg["lutram"]),
                             cells=cells, wrapper_ff=nbits,
                             fmax={s: f for s, (f, _) in seeds.items()},
                             pnr_util=seeds[a.seeds[0]][1]))
    lines = ["| Design | Device | LUT4 | FF | Block RAM | LUTRAM | Fmax over seeds "
             f"{', '.join(map(str, a.seeds))} (MHz) | nextpnr target |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = [v for v in r["fmax"].values() if v is not None]
        fr = f"{min(f):.1f} .. {max(f):.1f}" if f else "n/a"
        lines.append(f"| {r['design']} | {r['device']} | {r['lut']} | {r['ff']} | {r['bram']} | "
                     f"{r['lutram'] if FAMILIES[r['family']]['lutram'] else '-'} | {fr} | {r['target_mhz']} MHz |")
    summary = "\n".join(lines)
    (out / "summary.md").write_text(summary + "\n")
    (out / "summary.json").write_text(json.dumps(rows, indent=1))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
