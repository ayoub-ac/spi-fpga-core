#!/usr/bin/env python3
"""VCD -> PNG timing plots with sampling edges marked and zoomed panels.

usage: plots.py VCD_DIR OUT_DIR    (normally called by waves.py)
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from vcdvcd import VCDVCD  # noqa: E402

CLK_NS = 10.0
COL = {"sample": "#d62728", "shift": "#1f77b4", "bad": "#ff7f0e", "clk": "#7f7f7f"}


class Wave:
    def __init__(self, path):
        self.vcd = VCDVCD(path, store_tvs=True)
        ts = self.vcd.timescale
        self.k = float(ts["factor"]) * 1e9  # vcd units -> ns
        self.end = self.vcd.endtime * self.k

    def name(self, suffix):
        cands = [s for s in self.vcd.signals if s.split("[")[0].endswith("." + suffix) or s.split("[")[0] == suffix]
        if not cands:
            raise KeyError(suffix)
        return sorted(cands, key=lambda s: (s.count("."), len(s)))[0]

    def tv(self, suffix, bit=None):
        sig = self.vcd[self.name(suffix)]
        out = []
        for t, v in sig.tv:
            try:
                iv = int(v, 2)
            except ValueError:
                iv = None
            if bit is not None and iv is not None:
                iv = (iv >> bit) & 1
            tn = t * self.k
            if out and out[-1][1] == iv:
                continue
            out.append((tn, iv))
        return out


def val_at(tv, t):
    v = tv[0][1]
    for tt, vv in tv:
        if tt <= t:
            v = vv
        else:
            break
    return v


def val_before(tv, t):
    v = tv[0][1]
    for tt, vv in tv:
        if tt < t:
            v = vv
        else:
            break
    return v


def edges(tv, t0=None, t1=None):
    return [(t, v) for t, v in tv[1:] if (t0 is None or t >= t0) and (t1 is None or t <= t1)]


def draw_bit(ax, tv, y, t0, t1, color="k", lw=1.6):
    xs, ys = [t0], [val_at(tv, t0)]
    for t, v in tv:
        if t0 < t < t1:
            xs += [t, t]
            ys += [ys[-1], v]
    xs.append(t1)
    ys.append(ys[-1])
    ys = [y + (0.8 * v if v is not None else 0.4) for v in ys]
    ax.plot(xs, ys, color=color, lw=lw, drawstyle="default")


def draw_bus(ax, tv, y, t0, t1, fmt="{:x}", color="#2ca02c"):
    pts = [(t0, val_at(tv, t0))] + [(t, v) for t, v in tv if t0 < t < t1] + [(t1, None)]
    for (ta, va), (tb, _) in zip(pts[:-1], pts[1:]):
        ax.plot([ta, tb], [y + 0.8, y + 0.8], color=color, lw=1)
        ax.plot([ta, tb], [y, y], color=color, lw=1)
        ax.plot([ta, ta], [y, y + 0.8], color=color, lw=1)
        if tb - ta > (t1 - t0) * 0.03:
            lbl = "x" if va is None else (va if isinstance(va, str) else fmt.format(va))
            ax.text((ta + tb) / 2, y + 0.4, lbl, ha="center", va="center",
                    fontsize=7, color=color)


def rows(ax, w, specs, t0, t1, title=None, marks=(), notes=()):
    """specs: list of (label, kind, tv). kind: bit|bus|clk."""
    n = len(specs)
    for i, (label, kind, tv) in enumerate(specs):
        y = (n - 1 - i) * 1.2
        if kind == "bus":
            draw_bus(ax, tv, y, t0, t1)
        else:
            draw_bit(ax, tv, y, t0, t1, color=COL["clk"] if kind == "clk" else "k", lw=0.9 if kind == "clk" else 1.6)
        ax.text(t0 - (t1 - t0) * 0.01, y + 0.4, label, ha="right", va="center", fontsize=8)
    for t, color, style, text in marks:
        if t0 <= t <= t1:
            ax.axvline(t, color=color, ls=style, lw=0.9, alpha=0.8)
            if text:
                ax.text(t, n * 1.2 - 0.1, text, color=color, fontsize=7, ha="center", va="bottom", rotation=0)
    for x, y, text, color in notes:
        ax.text(x, y, text, fontsize=7.5, color=color, ha="left", va="center",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.9))
    ax.set_xlim(t0, t1)
    ax.set_ylim(-0.3, n * 1.2 + 0.5)
    ax.set_yticks([])
    ax.set_xlabel("time [ns]", fontsize=8)
    ax.tick_params(axis="x", labelsize=7)
    for s in ("left", "right", "top"):
        ax.spines[s].set_visible(False)
    if title:
        ax.set_title(title, fontsize=9, loc="left")


def cs_windows(cs):
    wins, tf = [], None
    for t, v in cs[1:]:
        if v == 0:
            tf = t
        elif v == 1 and tf is not None:
            wins.append((tf, t))
            tf = None
    return wins


def classify(sck_edges, cpol, cpha):
    lead = [t for t, v in sck_edges if v != cpol]
    trail = [t for t, v in sck_edges if v == cpol]
    return (lead, trail) if cpha == 0 else (trail, lead)  # (sample, shift)


# ------------------------------------------------------------------ master per mode
def plot_master_mode(vcd, mode, out):
    cpol, cpha = mode >> 1, mode & 1
    w = Wave(vcd)
    clk, sck, mosi, miso = w.tv("clk_i"), w.tv("sck_o"), w.tv("mosi_o"), w.tv("miso_i")
    cs = w.tv("cs_n_o", bit=0)
    state = w.tv("dut.state_q")
    tf, tr = cs_windows(cs)[-1]
    e = edges(sck, tf, tr)
    samp, shift = classify(e, cpol, cpha)
    names = {0: "IDLE", 1: "LOAD", 2: "SETUP", 3: "SHIFT", 4: "HOLD", 5: "DONE"}
    st = [(t, names.get(v, v)) for t, v in state]
    marks = [(t, COL["sample"], "--", "") for t in samp] + [(t, COL["shift"], ":", "") for t in shift]
    fig = plt.figure(figsize=(15, 9.5))
    gs = fig.add_gridspec(2, 3, height_ratios=[1.05, 1], hspace=0.35, wspace=0.12)
    ax = fig.add_subplot(gs[0, :])
    specs = [("CS_n[0]", "bit", cs), ("SCK", "bit", sck), ("MOSI", "bit", mosi), ("MISO", "bit", miso),
             ("FSM", "bus", st)]
    rows(ax, w, specs, tf - 60, tr + 60,
         title=f"spi_master v1.1 mode {mode} (CPOL={cpol}, CPHA={cpha}), DATA_WIDTH=8, clk_div=2 (SCK=clk/6), "
               f"cs_setup=2, cs_hold=2, inter_byte=1, TX=0xA5,0x3D  MISO model=0xC3,0x5A   "
               f"red dashed = sampling edges, blue dotted = shift edges", marks=marks)
    for t in samp:
        ax.text(t, 2 * 1.2 + 0.95, str(val_before(mosi, t)), color=COL["sample"], fontsize=7, ha="center")
        ax.text(t, 1 * 1.2 + 0.95, str(val_before(miso, t)), color=COL["sample"], fontsize=7, ha="center")
    ax.text(tf - 58, 2 * 1.2 + 0.95, "MOSI@sample", color=COL["sample"], fontsize=6.5, ha="left")
    ax.text(tf - 58, 1 * 1.2 + 0.95, "MISO@sample", color=COL["sample"], fontsize=6.5, ha="left")
    z0, z1 = tf - 25, e[1][0] + 25
    ax1 = fig.add_subplot(gs[1, 0])
    specs_z = [("clk", "clk", clk)] + specs[:4]
    first = e[0][0]
    rows(ax1, w, specs_z, z0, z1, title="zoom: CS assertion and first bit", marks=marks + [(tf, "k", "-", "CS fall")],
         notes=[(z0 + 2, -0.15, f"CS fall -> 1st SCK edge = {(first - tf) / CLK_NS:.0f} clk "
                                f"(cs_setup + clk_div + 3 = 7)", "k")])
    mid = samp[3]
    z0, z1 = mid - 45, mid + 45
    ax2 = fig.add_subplot(gs[1, 1])
    lc = max([t for t, _ in mosi if t < mid] or [tf])
    nc = min([t for t, _ in mosi if t >= mid] or [tr])
    rows(ax2, w, specs_z, z0, z1, title="zoom: edge-to-data timing (bit 3)", marks=marks,
         notes=[(z0 + 2, -0.15, f"MOSI setup {mid - lc:.0f} ns / hold {nc - mid:.0f} ns at the sampling edge", "k")])
    last = samp[7]
    z0, z1 = last - 45, last + 55
    ax3 = fig.add_subplot(gs[1, 2])
    nc = min([t for t, _ in mosi if t >= last] or [tr])
    hold = nc - last
    rows(ax3, w, specs_z, z0, z1, title="zoom: last bit of word 0 (0xA5, last bit = 1)", marks=marks,
         notes=[(z0 + 2, -0.15, f"MOSI hold after the last sampling edge = {hold:.0f} ns"
                 + ("  <-- below 1 clk" if hold < CLK_NS else ""), COL["bad"] if hold < CLK_NS else "k")])
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)


def plot_master_idle(vcd, out):
    w = Wave(vcd)
    clk, sck, mosi = w.tv("clk_i"), w.tv("sck_o"), w.tv("mosi_o")
    cs = w.tv("cs_n_o", bit=0)
    rst = w.tv("rst_ni")
    wins = cs_windows(cs)
    tf, tr = wins[0]
    tf2, tr2 = wins[1]
    fig = plt.figure(figsize=(15, 7))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], hspace=0.4)
    ax = fig.add_subplot(gs[0, :])
    specs = [("rst_ni", "bit", rst), ("CS_n[0]", "bit", cs), ("SCK", "bit", sck), ("MOSI", "bit", mosi)]
    rows(ax, w, specs, 0, tr2 + 50,
         title="spi_master v1.1, CPOL=1 from reset: SCK rests high while every CS_n is high, so it is already at "
               "CPOL when CS_n falls",
         marks=[(tf, "k", "-", "1st CS fall"), (tf2, "k", "-", "2nd CS fall")])
    ax1 = fig.add_subplot(gs[1, 0])
    rows(ax1, w, [("clk", "clk", clk)] + specs[1:], tf - 30, tf + 40, title="zoom: first transfer after reset",
         marks=[(tf, "k", "-", "")])
    ax2 = fig.add_subplot(gs[1, 1])
    rows(ax2, w, [("clk", "clk", clk)] + specs[1:], tf2 - 30, tf2 + 40, title="zoom: second transfer",
         marks=[(tf2, "k", "-", "")])
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------ slave
def plot_slave_tight(vcd, out_prefix, ratio, jitter):
    w = Wave(vcd)
    clk, sck, mosi, miso = w.tv("clk_i"), w.tv("sck_i"), w.tv("mosi_i"), w.tv("miso_o")
    cs = w.tv("cs_n_i")
    scks = w.tv("dut.sck_s")
    bitc = w.tv("dut.bit_cnt_q")
    rxw = w.tv("dut.rx_fifo_wr_en")
    outs = []
    for (tf, tr), mode in zip(cs_windows(cs), (0, 3)):
        cpol, cpha = mode >> 1, mode & 1
        e = edges(sck, tf, tr)
        samp, shift = classify(e, cpol, cpha)
        margins = []
        for t in samp:
            lc = max([tt for tt, _ in miso if tt < t] or [tf])
            margins.append(t - lc)
        marks = [(t, COL["sample"], "--", "") for t in samp] + [(t, COL["shift"], ":", "") for t in shift]
        fig = plt.figure(figsize=(15, 9.5))
        gs = fig.add_gridspec(2, 3, height_ratios=[1.05, 1], hspace=0.35, wspace=0.12)
        ax = fig.add_subplot(gs[0, :])
        specs = [("CS_n", "bit", cs), ("SCK", "bit", sck), ("MOSI", "bit", mosi), ("MISO", "bit", miso),
                 ("SCK synchronized", "bit", scks), ("bit_cnt", "bus", bitc), ("rx push", "bit", rxw)]
        jit = f"+-{jitter * 100:.0f}% jitter, " if jitter else ""
        rows(ax, w, specs, tf - 30, tr + 40,
             title=f"spi_slave v1.1 mode {mode} at SCK = clk/{ratio:g} ({jit}async phase): MOSI=0xA5,0x3C  "
                   f"slave TX=0xC3,0x96   min MISO setup before the master sampling edge = "
                   f"{min(margins):.1f} ns ({min(margins) / CLK_NS:.2f} clk)",
             marks=marks)
        for t in samp:
            ax.text(t, 3 * 1.2 + 0.95, str(val_before(miso, t)), color=COL["sample"], fontsize=7, ha="center")
        zspecs = [("clk", "clk", clk)] + specs[:5]
        ax1 = fig.add_subplot(gs[1, 0])
        rows(ax1, w, zspecs, tf - 15, e[1][0] + 15, title="zoom: CS fall, MISO bit 0, first edge", marks=marks)
        i = min(range(len(margins)), key=lambda j: margins[j])
        t = samp[i]
        ax2 = fig.add_subplot(gs[1, 1])
        rows(ax2, w, zspecs, t - 40, t + 25, title=f"zoom: tightest MISO setup (bit {i})", marks=marks,
             notes=[(t - 39, -0.15, f"MISO changed {margins[i]:.1f} ns before the sampling edge", "k")])
        ax3 = fig.add_subplot(gs[1, 2])
        rows(ax3, w, zspecs, e[-3][0] - 10, tr + 25, title="zoom: last bit and CS rise", marks=marks)
        o = f"{out_prefix}_mode{mode}.png"
        fig.savefig(o, dpi=130, bbox_inches="tight")
        plt.close(fig)
        outs.append(o)
    return outs


# ------------------------------------------------------------------ loopback last bit
def plot_top_lastbit(vcd, out):
    w = Wave(vcd)
    clk, sck, mosi = w.tv("clk_i"), w.tv("sck_o"), w.tv("mosi_o")
    cs = w.tv("cs_n_o", bit=0)
    ssck = w.tv("u_slave.sck_s")
    srx = w.tv("u_slave.rx_shift_q")
    swd = w.tv("u_slave.rx_fifo_wdata")
    swr = w.tv("u_slave.rx_fifo_wr_en")
    tf, tr = cs_windows(cs)[-1]
    e = edges(sck, tf, tr)
    samp, shift = classify(e, 0, 1)
    marks = [(t, COL["sample"], "--", "") for t in samp] + [(t, COL["shift"], ":", "") for t in shift]
    fig = plt.figure(figsize=(15, 8.5))
    gs = fig.add_gridspec(2, 1, height_ratios=[1, 1.1], hspace=0.35)
    ax = fig.add_subplot(gs[0])
    specs = [("CS_n[0]", "bit", cs), ("SCK", "bit", sck), ("MOSI", "bit", mosi),
             ("slave SCK synchronized", "bit", ssck), ("slave rx_shift", "bus", srx),
             ("slave rx push", "bit", swr), ("slave rx_wdata", "bus", swd)]
    rows(ax, w, specs, tf - 30, tr + 50,
         title="v1.1, CPHA=1 (mode 1) spi_master -> spi_slave, 0x3D: MOSI keeps the last bit through CS hold, "
               "the slave stores 0x3D", marks=marks)
    last = samp[-1]
    changes = [t for t, _ in mosi if last < t < tr]
    held = (f"MOSI changes {changes[0] - last:.0f} ns after the last sampling edge" if changes else
            f"MOSI unchanged until CS_n rises, {tr - last:.0f} ns after the last sampling edge")
    ax2 = fig.add_subplot(gs[1])
    rows(ax2, w, [("clk", "clk", clk)] + specs, last - 45, last + 45, title="zoom: final (8th) sampling edge",
         marks=marks + [(last, "k", "-", "last sampling edge")],
         notes=[(last + 2, 9.3, held, "k")])
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main(vd, od, slave_runs=()):
    """slave_runs: (VCD file stem, SCK ratio, jitter) of each slave_tight dump."""
    os.makedirs(od, exist_ok=True)
    made = []
    for m in range(4):
        p = os.path.join(vd, f"master_mode{m}.vcd")
        if os.path.exists(p):
            o = os.path.join(od, f"master_mode{m}_cpol{m >> 1}_cpha{m & 1}_div2_zoom.png")
            plot_master_mode(p, m, o)
            made.append(o)
    p = os.path.join(vd, "master_idle.vcd")
    if os.path.exists(p):
        o = os.path.join(od, "master_sck_idle_cpol1_after_reset.png")
        plot_master_idle(p, o)
        made.append(o)
    for stem, ratio, jitter in slave_runs:
        p = os.path.join(vd, f"{stem}.vcd")
        if os.path.exists(p):
            made += plot_slave_tight(p, os.path.join(od, stem), ratio, jitter)
    p = os.path.join(vd, "top_lastbit.vcd")
    if os.path.exists(p):
        o = os.path.join(od, "loopback_cpha1_last_bit_mode1.png")
        plot_top_lastbit(p, o)
        made.append(o)
    return made


if __name__ == "__main__":
    print("\n".join(main(sys.argv[1], sys.argv[2])))
