"""Independent verification of rtl/spi_slave.sv driven by an asynchronous behavioural SPI master.

SCK/CS/MOSI are driven with ps resolution, arbitrary SCK/clk ratios, random phase and jitter.
"""
import json
import os
import random

import cocotb
from cocotb.triggers import FallingEdge, Timer

from spiv.drv import cycles, drain, env_int, push, start_clock
from spiv.models import CLK_PS, AsyncSpiMaster
from spiv.trace import Trace, now_ps, sig_int

W = env_int("SPI_W", 8)
DEPTH = env_int("SPI_DEPTH", 16)
MASK = (1 << W) - 1
# words per frame when every word of a frame is queued (TX) or kept (RX)
# without servicing the FIFOs during the frame
FW = min(3, DEPTH)
# clk_i / SCK ratio from which every swept configuration must pass (simulation,
# no metastability); measured at 3.1 for v1.1, asserted with margin.
SAFE_RATIO = float(os.environ.get("S_SAFE_RATIO", "3.5"))
# same with every input change delayed by up to one clock (metastability emulation)
SAFE_RATIO_META = float(os.environ.get("S_SAFE_RATIO_META", "4.5"))
RES = os.environ.get("RESULTS_DIR", ".")
TAG = os.environ.get("RUN_TAG", f"w{W}")


def dump(name, obj):
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, f"{name}_{TAG}.json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)


async def slave_env(dut, seed, pin_skew_ps=0):
    start_clock(dut)
    tr = Trace({"sck": dut.sck_i, "mosi": dut.mosi_i, "cs": dut.cs_n_i, "miso": dut.miso_o,
                "done": dut.xfer_done_o, "busy": dut.busy_o,
                "udr": dut.tx_underrun_o, "ovr": dut.rx_overrun_o})
    drv = AsyncSpiMaster(dut.sck_i, dut.mosi_i, dut.miso_o, dut.cs_n_i, W, random.Random(seed), tr,
                         pin_skew_ps=pin_skew_ps)
    dut.rst_ni.value = 0
    dut.cpol_i.value = 0
    dut.cpha_i.value = 0
    dut.lsb_first_i.value = 0
    dut.tx_we_i.value = 0
    dut.tx_wdata_i.value = 0
    dut.rx_re_i.value = 0
    await cycles(dut, 6)
    dut.rst_ni.value = 1
    await cycles(dut, 3)
    return tr, drv


async def set_mode(dut, drv, mode, lsb):
    cpol, cpha = mode >> 1, mode & 1
    await FallingEdge(dut.clk_i)
    dut.cpol_i.value = cpol
    dut.cpha_i.value = cpha
    dut.lsb_first_i.value = lsb
    drv.configure(cpol, cpha, lsb)
    await drv.set_idle_level()
    await cycles(dut, 2)


def timing_for(ratio, rng, jitter_frac=0.0, css_clk=3.0, gap_clk=3.0):
    half = int(round(ratio * CLK_PS / 2))
    j = int(half * jitter_frac)
    css = max(int(css_clk * CLK_PS), half) + rng.randint(0, CLK_PS)
    return dict(half_ps=half, css_ps=css, csh_ps=half + rng.randint(0, CLK_PS), jitter_ps=j), int(gap_clk * CLK_PS)


def miso_margins(tr, times):
    """Setup margin (ps) of MISO before each sampling edge and hold after it."""
    su, ho = [], []
    for ts in times:
        lc = tr.last_change_before("miso", ts)
        nc = tr.next_change_at_or_after("miso", ts)
        su.append(ts - lc if lc is not None else 10**9)
        ho.append(nc - ts if nc is not None else 10**9)
    return su, ho


async def run_frames(dut, drv, tr, rng, frames, timing, gap_ps, preload="exact", word_gap_ps=0,
                     resp=None, drain_each=True, extra=None):
    """frames: list of lists of MOSI words. Returns dict with observed MISO/RX and margins."""
    extra = extra or {}
    resp = resp or [[rng.getrandbits(W) for _ in f] for f in frames]
    if preload == "all":
        await push(dut, [w for f in resp for w in f])
    miso_seen, rx_all, times_all = [], [], []
    dones0 = len([c for c in tr.changes("done") if c[1] == 1])
    for fi, f in enumerate(frames):
        if preload == "exact":
            await push(dut, resp[fi])
        # random phase w.r.t. clk
        await drv.idle(gap_ps + rng.randint(0, CLK_PS - 1))
        mw, times = await drv.transaction(f, word_gap_ps=word_gap_ps, **timing, **extra)
        miso_seen.append(mw)
        times_all += times
        if drain_each:
            await cycles(dut, 4)
            rx_all += await drain(dut)
    if not drain_each:
        await cycles(dut, 4)
        rx_all += await drain(dut)
    su, ho = miso_margins(tr, times_all)
    dones = len([c for c in tr.changes("done") if c[1] == 1]) - dones0
    mosi_flat = [w for f in frames for w in f]
    return {
        "rx_ok": rx_all == mosi_flat,
        "miso_ok": miso_seen == resp,
        "rx": rx_all, "mosi": mosi_flat, "miso_seen": miso_seen, "miso_exp": resp,
        "min_miso_setup_ps": min(su) if su else None,
        "min_miso_hold_ps": min(ho) if ho else None,
        "xfer_done_pulses": dones,
    }


def hexs(v):
    if isinstance(v, list):
        return [hexs(x) for x in v]
    return hex(v) if isinstance(v, int) and v >= 0 else v


@cocotb.test()
async def test_s_matrix(dut):
    """4 modes x MSB/LSB x SCK/clk ratios (with random phase + jitter), 1- and 3-word frames."""
    rng = random.Random(2000 + W)
    tr, drv = await slave_env(dut, 1)
    out = []
    for mode in range(4):
        for lsb in (0, 1):
            await set_mode(dut, drv, mode, lsb)
            for ratio in (32, 16, 8, 6.3, 4, 3.3):
                for nw, jit in ((1, 0.0), (FW, 0.05)):
                    timing, gap = timing_for(ratio, rng, jitter_frac=jit)
                    frames = [[rng.getrandbits(W) for _ in range(nw)] for _ in range(2)]
                    r = await run_frames(dut, drv, tr, rng, frames, timing, gap)
                    r.update(mode=mode, lsb=lsb, ratio=ratio, nw=nw, jitter=jit)
                    out.append(r)
                    if not (r["rx_ok"] and r["miso_ok"]):
                        dut._log.warning("FAIL mode%d lsb%d ratio%s nw%d: rx=%s exp=%s miso=%s exp=%s", mode, lsb, ratio, nw,
                                         hexs(r["rx"]), hexs(r["mosi"]), hexs(r["miso_seen"]), hexs(r["miso_exp"]))
    dump("s_matrix", out)
    nbad = [r for r in out if not (r["rx_ok"] and r["miso_ok"])]
    dut._log.info("slave matrix: %d/%d configs failed", len(nbad), len(out))
    for ratio in (32, 16, 8, 6.3, 4, 3.3):
        rs = [r for r in out if r["ratio"] == ratio]
        dut._log.info("ratio %-5s: fails=%d min MISO setup=%.2f clk", ratio, sum(1 for r in rs if not (r["rx_ok"] and r["miso_ok"])),
                      min(r["min_miso_setup_ps"] for r in rs) / CLK_PS)
    assert not nbad


async def ratio_sweep(dut, name, pin_skew_ps, safe):
    rng = random.Random(3000 + W + pin_skew_ps)
    tr, drv = await slave_env(dut, 2, pin_skew_ps=pin_skew_ps)
    ratios = [round(8.0 - 0.1 * i, 2) for i in range(0, 65)] + [2.05, 2.02, 1.98, 1.95]
    out = []
    for ratio in ratios:
        row = {"ratio": ratio, "fail_rx": 0, "fail_miso": 0, "n": 0, "min_setup_ps": 10**9, "min_hold_ps": 10**9}
        for mode in range(4):
            for lsb in (0, 1):
                await set_mode(dut, drv, mode, lsb)
                for jit in (0.0, 0.03):
                    timing, gap = timing_for(ratio, rng, jitter_frac=jit)
                    frames = [[rng.getrandbits(W) for _ in range(FW)]]
                    r = await run_frames(dut, drv, tr, rng, frames, timing, gap)
                    row["n"] += 1
                    row["fail_rx"] += 0 if r["rx_ok"] else 1
                    row["fail_miso"] += 0 if r["miso_ok"] else 1
                    row["min_setup_ps"] = min(row["min_setup_ps"], r["min_miso_setup_ps"])
                    row["min_hold_ps"] = min(row["min_hold_ps"], r["min_miso_hold_ps"])
                    # resync the slave after a failed frame (reset) so failures do not cascade
                    if not (r["rx_ok"] and r["miso_ok"]):
                        dut.rst_ni.value = 0
                        await cycles(dut, 3)
                        dut.rst_ni.value = 1
                        await cycles(dut, 3)
        out.append(row)
        dut._log.info("ratio %.2f: rx_fail %d/%d miso_fail %d/%d  min MISO setup %.2f clk  min hold %.2f clk",
                      ratio, row["fail_rx"], row["n"], row["fail_miso"], row["n"], row["min_setup_ps"] / CLK_PS,
                      row["min_hold_ps"] / CLK_PS)
    passing = None
    for row in sorted(out, key=lambda r: -r["ratio"]):
        if row["fail_rx"] or row["fail_miso"]:
            break
        passing = row["ratio"]
    dump(name, {"rows": out, "all_pass_from_ratio": passing, "pin_skew_ps": pin_skew_ps})
    dut._log.info("%s: every ratio >= %s passes (pin skew %d ps)", name, passing, pin_skew_ps)
    bad = [r["ratio"] for r in out if r["ratio"] >= safe and (r["fail_rx"] or r["fail_miso"])]
    assert not bad, f"failures at clk/SCK ratios >= {safe}: {bad}"


@cocotb.test()
async def test_s_ratio_sweep(dut):
    """Tightest clk/SCK ratio the slave handles (3-word frames, 0/3% jitter, random phase, all modes)."""
    await ratio_sweep(dut, "s_ratio_sweep", 0, SAFE_RATIO)


@cocotb.test()
async def test_s_ratio_sweep_meta(dut):
    """Same sweep with every SCK/MOSI/CS_n change delayed by 0..1 clk at the pins, independently:
    emulates each synchronizer resolving a change one clock late."""
    await ratio_sweep(dut, "s_ratio_sweep_meta", CLK_PS, SAFE_RATIO_META)


@cocotb.test()
async def test_s_prefetch_skip(dut):
    """Several words queued in the slave TX FIFO, consumed by separate CS frames."""
    rng = random.Random(4000 + W)
    tr, drv = await slave_env(dut, 3)
    out = []
    for mode in range(4):
        shapes = [s for s in ([1, 1, 1, 1], [2, 2], [1, 3]) if sum(s) <= DEPTH] or [[1] * DEPTH]
        for shape in shapes:
            await set_mode(dut, drv, mode, 0)
            # flush the slave TX FIFO / shift register state
            dut.rst_ni.value = 0
            await cycles(dut, 3)
            dut.rst_ni.value = 1
            await cycles(dut, 3)
            resp_words = [(0x11 * (i + 1)) & MASK for i in range(sum(shape))]
            resp, k = [], 0
            for n in shape:
                resp.append(resp_words[k:k + n])
                k += n
            frames = [[rng.getrandbits(W) for _ in range(n)] for n in shape]
            timing, gap = timing_for(8, rng)
            r = await run_frames(dut, drv, tr, rng, frames, timing, gap, preload="all", resp=resp)
            out.append({"mode": mode, "frames": shape, "slave_tx_fifo": hexs(resp_words),
                        "master_received": hexs(r["miso_seen"]), "expected": hexs(resp), "miso_ok": r["miso_ok"],
                        "rx_ok": r["rx_ok"]})
            dut._log.info("mode%d frames=%s slave FIFO=%s -> master got %s", mode, shape, hexs(resp_words), hexs(r["miso_seen"]))
    dump("s_prefetch_skip", out)
    assert all(o["miso_ok"] for o in out), "slave skips queued TX words across CS frames"


@cocotb.test()
async def test_s_cs_abort(dut):
    """CS de-asserted in the middle of a word: nothing is pushed and the next frame is received correctly."""
    rng = random.Random(5000 + W)
    tr, drv = await slave_env(dut, 4)
    out = []
    for mode in range(4):
        for k in (1, W // 2, W - 1):
            await set_mode(dut, drv, mode, 0)
            dut.rst_ni.value = 0
            await cycles(dut, 3)
            dut.rst_ni.value = 1
            await cycles(dut, 3)
            A, B, C = 0xA5 & MASK, 0x3C & MASK, 0x81 & MASK
            await push(dut, [A, B, C][:DEPTH])
            timing, gap = timing_for(8, rng)
            d0 = len([c for c in tr.changes("done") if c[1] == 1])
            await drv.idle(gap)
            m_abort, _ = await drv.transaction([0xFF & MASK], abort_after_bits=k, **timing)
            await cycles(dut, 4)
            rx_after_abort = await drain(dut)
            d1 = len([c for c in tr.changes("done") if c[1] == 1])
            busy_after = sig_int(dut.busy_o)
            await drv.idle(gap)
            nxt = [0x5A & MASK, 0x99 & MASK][:FW]
            m_next, _ = await drv.transaction(nxt, **timing)
            await cycles(dut, 4)
            rx_next = await drain(dut)
            o = {"mode": mode, "abort_after_bits": k, "slave_fifo": hexs([A, B, C]),
                 "rx_pushed_by_aborted_frame": hexs(rx_after_abort), "done_pulses_in_aborted_frame": d1 - d0,
                 "busy_after_abort": busy_after,
                 "next_frame_master_got": hexs(m_next), "next_frame_slave_rx": hexs(rx_next),
                 "next_frame_rx_ok": rx_next == nxt}
            out.append(o)
            dut._log.info("%s", o)
    dump("s_cs_abort", out)
    assert all(o["next_frame_rx_ok"] and not o["rx_pushed_by_aborted_frame"] for o in out)


def pulses(tr, name, t0=None):
    return len([c for c in tr.changes(name, t0) if c[1] == 1])


@cocotb.test()
async def test_s_tx_empty(dut):
    """TX FIFO empty at CS fall and mid-burst: zeros on MISO, one tx_underrun_o pulse per
    word sent without data, the queued word is kept for the next word."""
    rng = random.Random(6000 + W)
    tr, drv = await slave_env(dut, 5)
    out = []
    for mode in (0, 3):
        await set_mode(dut, drv, mode, 0)
        dut.rst_ni.value = 0
        await cycles(dut, 3)
        dut.rst_ni.value = 1
        await cycles(dut, 3)
        timing, gap = timing_for(8, rng)
        await drv.idle(gap)
        t0 = now_ps()
        m0, _ = await drv.transaction([0x00, 0x00], **timing)
        await cycles(dut, 3)
        u0 = pulses(tr, "udr", t0)
        await push(dut, [0xC3 & MASK])
        await drv.idle(gap)
        t1 = now_ps()
        m1, _ = await drv.transaction([0x00, 0x00, 0x00], **timing)
        await cycles(dut, 3)
        u1 = pulses(tr, "udr", t1)
        await drv.idle(gap)
        t2 = now_ps()
        m2, _ = await drv.transaction([0x00], **timing)
        await cycles(dut, 4)
        u2 = pulses(tr, "udr", t2)
        await drain(dut)
        o = {"mode": mode, "after_reset_empty_fifo": hexs(m0), "fifo_one_word_0xC3_then_empty": hexs(m1),
             "next_frame_empty_fifo": hexs(m2), "tx_empty_flag": sig_int(dut.tx_empty_o),
             "underrun_pulses": [u0, u1, u2]}
        out.append(o)
        dut._log.info("%s", o)
    dump("s_tx_empty", out)
    for o in out:
        assert o["after_reset_empty_fifo"] == ["0x0", "0x0"] and o["next_frame_empty_fifo"] == ["0x0"]
        assert o["fifo_one_word_0xC3_then_empty"] == [hex(0xC3 & MASK), "0x0", "0x0"]
        assert o["underrun_pulses"] == [2, 2, 1], f"tx_underrun_o pulses {o['underrun_pulses']}"


@cocotb.test()
async def test_s_rx_overrun(dut):
    """Slave RX FIFO full: the first DEPTH words are kept, each further word is dropped with one rx_overrun_o pulse."""
    rng = random.Random(7000 + W)
    tr, drv = await slave_env(dut, 6)
    await set_mode(dut, drv, 0, 0)
    timing, gap = timing_for(8, rng)
    n = DEPTH + 4
    words = [(i + 1) & MASK for i in range(n)]
    await push(dut, [0] * min(n, DEPTH))
    for i in range(0, n, 4):
        await drv.idle(gap)
        await drv.transaction(words[i:i + 4], **timing)
    await cycles(dut, 4)
    full = sig_int(dut.rx_full_o)
    dones = len([c for c in tr.changes("done") if c[1] == 1])
    rx = await drain(dut)
    out = {"words_sent": n, "rx_full": full, "rx_kept": hexs(rx), "kept_first_depth_words": rx == words[:DEPTH],
           "xfer_done_pulses": dones, "rx_overrun_pulses": pulses(tr, "ovr")}
    dump("s_rx_overrun", out)
    dut._log.info("%s", out)
    assert out["kept_first_depth_words"] and out["rx_full"] == 1
    assert out["rx_overrun_pulses"] == n - DEPTH, "rx_overrun_o pulses != dropped words"


@cocotb.test()
async def test_s_reset_mid_transfer(dut):
    """Synchronous reset pulsed while CS is low in the middle of a word; master keeps clocking."""
    rng = random.Random(8000 + W)
    tr, drv = await slave_env(dut, 7)
    out = []
    for mode in (0, 1, 2, 3):
        await set_mode(dut, drv, mode, 0)
        await push(dut, [0x5A & MASK, 0x5A & MASK, 0x5A & MASK])
        timing, gap = timing_for(16, rng)
        half = timing["half_ps"]
        words = [0xF0 & MASK, 0x0F & MASK, 0xCC & MASK]

        async def pulse_reset():
            # 3.5 bits into the first word
            await Timer(timing["css_ps"] + 7 * half, unit="ps")
            await FallingEdge(dut.clk_i)
            dut.rst_ni.value = 0
            await cycles(dut, 2)
            dut.rst_ni.value = 1

        await drv.idle(gap)
        cocotb.start_soon(pulse_reset())
        m, _ = await drv.transaction(words, **timing)
        await cycles(dut, 4)
        rx = await drain(dut)
        # next, clean frame
        await push(dut, [0x66 & MASK])
        await drv.idle(gap)
        m2, _ = await drv.transaction([0x77 & MASK], **timing)
        await cycles(dut, 4)
        rx2 = await drain(dut)
        o = {"mode": mode, "frame_words": hexs(words), "rx_pushed_after_reset_same_frame": hexs(rx),
             "doc_expectation": "nothing until next CS fall", "next_frame_rx": hexs(rx2), "next_frame_miso": hexs(m2),
             "next_ok": rx2 == [0x77 & MASK] and m2 == [0x66 & MASK]}
        out.append(o)
        dut._log.info("%s", o)
    dump("s_reset_mid_transfer", out)
    assert all(not o["rx_pushed_after_reset_same_frame"] for o in out), "slave re-arms mid-frame after reset"


@cocotb.test()
async def test_s_min_cs_setup(dut):
    """CS-fall -> first SCK edge time swept 0.3..3.5 clk: the first word must be correct from 1 clk on."""
    rng = random.Random(9000 + W)
    tr, drv = await slave_env(dut, 8)
    out = []
    for mode in range(4):
        await set_mode(dut, drv, mode, 0)
        for css_clk in (0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 3.0, 3.5):
            nfail = 0
            for trial in range(6):
                half = 4 * CLK_PS
                timing = dict(half_ps=half, css_ps=int(css_clk * CLK_PS), csh_ps=half)
                frames = [[rng.getrandbits(W)]]
                r = await run_frames(dut, drv, tr, rng, frames, timing, 3 * CLK_PS)
                if not (r["rx_ok"] and r["miso_ok"]):
                    nfail += 1
                    dut.rst_ni.value = 0
                    await cycles(dut, 3)
                    dut.rst_ni.value = 1
                    await cycles(dut, 3)
            out.append({"mode": mode, "css_clk": css_clk, "fails": nfail, "trials": 6})
        dut._log.info("mode%d: %s", mode, [(o["css_clk"], o["fails"]) for o in out if o["mode"] == mode])
    dump("s_min_cs_setup", out)
    bad = [(o["mode"], o["css_clk"]) for o in out if o["css_clk"] >= 1.0 and o["fails"]]
    assert not bad, f"first word wrong with CS_n setup >= 1 clk: {bad}"


@cocotb.test()
async def test_s_mosi_hold(dut):
    """MOSI hold after the final sampling edge swept 0..1.25 clk: the last bit must be received from 1 clk on."""
    rng = random.Random(9100 + W)
    tr, drv = await slave_env(dut, 9)
    out = []
    for mode in range(4):
        await set_mode(dut, drv, mode, 0)
        for hold_clk in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25):
            nfail = 0
            for trial in range(8):
                half = 4 * CLK_PS
                timing = dict(half_ps=half, css_ps=4 * CLK_PS, csh_ps=half)
                w = rng.getrandbits(W) | 1  # last wire bit (LSB, MSB-first) = 1, idle MOSI = 0
                r = await run_frames(dut, drv, tr, rng, [[w]], timing, 3 * CLK_PS,
                                     extra={"mosi_hold_last_ps": int(hold_clk * CLK_PS)})
                nfail += 0 if r["rx_ok"] else 1
            out.append({"mode": mode, "mosi_hold_after_last_edge_clk": hold_clk, "fails": nfail, "trials": 8})
        dut._log.info("mode%d: %s", mode, [(o["mosi_hold_after_last_edge_clk"], o["fails"]) for o in out if o["mode"] == mode])
    dump("s_mosi_hold", out)
    bad = [(o["mode"], o["mosi_hold_after_last_edge_clk"]) for o in out
           if o["mosi_hold_after_last_edge_clk"] >= 1.0 and o["fails"]]
    assert not bad, f"last bit lost with MOSI hold >= 1 clk: {bad}"


@cocotb.test()
async def test_s_cs_gap(dut):
    """Minimum CS high time between frames."""
    rng = random.Random(9200 + W)
    tr, drv = await slave_env(dut, 10)
    out = []
    for mode in (0, 3):
        await set_mode(dut, drv, mode, 0)
        for gap_clk in (0.3, 0.6, 0.9, 1.2, 1.5, 2.0, 3.0):
            nfail = 0
            for trial in range(6):
                half = 4 * CLK_PS
                timing = dict(half_ps=half, css_ps=4 * CLK_PS, csh_ps=half)
                frames = [[rng.getrandbits(W)] for _ in range(FW)]
                resp = [[rng.getrandbits(W)] for _ in range(FW)]
                await push(dut, [x[0] for x in resp][:1])
                ok = True
                rx = []
                got = []
                for i, f in enumerate(frames):
                    if i:
                        await drv.idle(int(gap_clk * CLK_PS))
                    mw, _ = await drv.transaction(f, **timing)
                    got.append(mw)
                await cycles(dut, 4)
                rx = await drain(dut)
                ok = rx == [f[0] for f in frames]
                nfail += 0 if ok else 1
                dut.rst_ni.value = 0
                await cycles(dut, 3)
                dut.rst_ni.value = 1
                await cycles(dut, 3)
            out.append({"mode": mode, "cs_high_clk": gap_clk, "rx_fails": nfail, "trials": 6})
        dut._log.info("mode%d: %s", mode, [(o["cs_high_clk"], o["rx_fails"]) for o in out if o["mode"] == mode])
    dump("s_cs_gap", out)
    bad = [(o["mode"], o["cs_high_clk"]) for o in out if o["cs_high_clk"] >= 2.0 and o["rx_fails"]]
    assert not bad, f"RX errors with CS_n high >= 2 clk: {bad}"


@cocotb.test()
async def test_s_random_regression(dut):
    """Long randomized regression: random mode/bit order/SCK ratio (S_MIN_RATIO, default 4, ..16)/jitter/phase/frame length."""
    nwords = int(os.environ.get("S_RANDOM_WORDS", "3000"))
    preload = os.environ.get("S_PRELOAD", "exact")
    rng = random.Random(int(os.environ.get("S_RANDOM_SEED", "77")) + W)
    tr, drv = await slave_env(dut, 11)
    sent = 0
    err = {"rx": 0, "miso": 0}
    frames_n = 0
    ratios = []
    while sent < nwords:
        mode = rng.randrange(4)
        lsb = rng.randrange(2)
        await set_mode(dut, drv, mode, lsb)
        ratio = rng.uniform(float(os.environ.get("S_MIN_RATIO", "4.0")), 16.0)
        ratios.append(ratio)
        timing, gap = timing_for(ratio, rng, jitter_frac=rng.choice([0.0, 0.02, 0.05]),
                                 css_clk=rng.uniform(float(os.environ.get("S_MIN_CSS", "1.5")), 6.0), gap_clk=rng.uniform(2.0, 5.0))
        nf = rng.randint(1, 3)
        frames = [[rng.getrandbits(W) for _ in range(rng.randint(1, min(5, DEPTH)))] for _ in range(nf)]
        if preload == "all":   # every slave word of the run is queued before the first frame
            while sum(len(f) for f in frames) > DEPTH:
                frames.pop()
        nf = len(frames)
        wgap = rng.choice([0, 0, int(timing["half_ps"] * rng.uniform(0, 2))])
        r = await run_frames(dut, drv, tr, rng, frames, timing, gap, preload=preload, word_gap_ps=wgap)
        nw = sum(len(f) for f in frames)
        if not r["rx_ok"]:
            err["rx"] += sum(1 for a, b in zip(r["rx"], r["mosi"]) if a != b) + abs(len(r["rx"]) - len(r["mosi"]))
        if not r["miso_ok"]:
            err["miso"] += sum(1 for fa, fb in zip(r["miso_seen"], r["miso_exp"]) for a, b in zip(fa, fb) if a != b)
            if preload != "exact":
                dut.rst_ni.value = 0
                await cycles(dut, 3)
                dut.rst_ni.value = 1
                await cycles(dut, 3)
        sent += nw
        frames_n += nf
    out = {"words": sent, "frames": frames_n, "preload": preload, "errors": err,
           "ratio_range": [min(ratios), max(ratios)]}
    dump(f"s_random_regression_{preload}", out)
    dut._log.info("%s", json.dumps(out))
    assert err == {"rx": 0, "miso": 0}


@cocotb.test()
async def test_s_config_latched(dut):
    """cpol/cpha/lsb_first change in the middle of a frame: the frame keeps the
    configuration seen at CS_n assertion, the next frame uses the new one."""
    rng = random.Random(9300 + W)
    tr, drv = await slave_env(dut, 12)
    out = []
    for mode in range(4):
        for lsb in (0, 1):
            await set_mode(dut, drv, mode, lsb)
            other = (mode + 1 + rng.randrange(3)) % 4
            timing, gap = timing_for(8, rng)
            words = [rng.getrandbits(W) for _ in range(FW)]
            resp = [rng.getrandbits(W) for _ in range(FW)]
            await push(dut, resp)
            await drv.idle(gap)

            async def scramble():
                # a few clocks after CS_n falls, flip the configuration inputs
                await Timer(timing["css_ps"] + 3 * timing["half_ps"], unit="ps")
                await FallingEdge(dut.clk_i)
                dut.cpol_i.value = other >> 1
                dut.cpha_i.value = other & 1
                dut.lsb_first_i.value = 1 - lsb

            cocotb.start_soon(scramble())
            miso, _ = await drv.transaction(words, **timing)
            await cycles(dut, 4)
            rx = await drain(dut)
            ok = rx == words and miso == resp
            out.append({"mode": mode, "lsb": lsb, "changed_to_mode": other, "rx_ok": rx == words,
                        "miso_ok": miso == resp})
            # the next frame must use the new (scrambled) configuration
            drv.configure(other >> 1, other & 1, 1 - lsb)
            await drv.set_idle_level()
            await cycles(dut, 3)
            w2, r2 = [rng.getrandbits(W)], [rng.getrandbits(W)]
            await push(dut, r2)
            await drv.idle(gap)
            m2, _ = await drv.transaction(w2, **timing)
            await cycles(dut, 4)
            rx2 = await drain(dut)
            out[-1]["next_frame_ok"] = rx2 == w2 and m2 == r2
            if not (ok and out[-1]["next_frame_ok"]):
                dut._log.warning("config latch FAIL %s", out[-1])
    dump("s_config_latched", out)
    assert all(o["rx_ok"] and o["miso_ok"] and o["next_frame_ok"] for o in out)


@cocotb.test()
async def test_s_miso_oe(dut):
    """miso_oe_o follows !cs_n_i combinationally (no clock), also during reset."""
    rng = random.Random(9400 + W)
    start_clock(dut)
    dut.rst_ni.value = 0
    for n in ("cpol_i", "cpha_i", "lsb_first_i", "tx_we_i", "tx_wdata_i", "rx_re_i", "sck_i", "mosi_i"):
        getattr(dut, n).value = 0
    dut.cs_n_i.value = 1
    bad = []
    for i in range(200):
        if i == 20:
            dut.rst_ni.value = 1
        await Timer(rng.randint(1, 3 * CLK_PS), unit="ps")
        v = rng.randrange(2)
        dut.cs_n_i.value = v
        await Timer(1, unit="ps")
        if sig_int(dut.miso_oe_o) != 1 - v:
            bad.append((now_ps(), v, sig_int(dut.miso_oe_o)))
    dump("s_miso_oe", {"mismatches": bad[:10], "n": len(bad)})
    assert not bad, f"miso_oe_o does not follow !cs_n_i: {bad[:4]}"


@cocotb.test()
async def test_s_tx_write_to_cs_setup(dut):
    """TX word written d clk before CS_n falls (FIFO empty before): from d >= 3 clk the word
    must be sent in this frame. Shorter d is recorded (the word may slip to the next frame)."""
    rng = random.Random(9500 + W)
    tr, drv = await slave_env(dut, 13)
    out = []
    for mode in (0, 1, 2, 3):
        await set_mode(dut, drv, mode, 0)
        for d_clk in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
            for trial in range(4):
                dut.rst_ni.value = 0
                await cycles(dut, 3)
                dut.rst_ni.value = 1
                await cycles(dut, 5)
                w = rng.getrandbits(W) | 1
                await FallingEdge(dut.clk_i)
                dut.tx_we_i.value = 1
                dut.tx_wdata_i.value = w
                await FallingEdge(dut.clk_i)
                dut.tx_we_i.value = 0
                # the write was sampled on the rising edge half a clk ago
                await Timer(int(d_clk * CLK_PS) - CLK_PS // 2 + rng.randint(1, 999), unit="ps")
                timing = dict(half_ps=4 * CLK_PS, css_ps=4 * CLK_PS, csh_ps=4 * CLK_PS)
                m, _ = await drv.transaction([0x00], **timing)
                await cycles(dut, 4)
                await drain(dut)
                out.append({"mode": mode, "d_clk": d_clk, "sent": m[0], "word": w, "ok": m[0] == w,
                            "underrun": m[0] == 0})
    dump("s_tx_write_to_cs_setup", out)
    for d in sorted({o["d_clk"] for o in out}):
        rs = [o for o in out if o["d_clk"] == d]
        dut._log.info("write %.1f clk before CS_n fall: %d/%d sent, %d underrun, %d corrupted", d,
                      sum(o["ok"] for o in rs), len(rs), sum(o["underrun"] for o in rs),
                      sum(not o["ok"] and not o["underrun"] for o in rs))
    bad = [(o["mode"], o["d_clk"], hex(o["sent"])) for o in out if o["d_clk"] >= 3.0 and not o["ok"]]
    assert not bad, f"word written >= 3 clk before CS_n fall not sent: {bad}"
