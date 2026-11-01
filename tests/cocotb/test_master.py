"""Independent verification of rtl/spi_master.sv against an independent SPI slave model.

Configuration comes from env: SPI_W, SPI_NUM_CS, SPI_DEPTH, SPI_DIV_W, SPI_CSDLY_W.
Results (JSON) go to $RESULTS_DIR.
"""
import json
import os
import random

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from spiv.drv import cycles, drain, env_int, master_cfg, master_init, pulse, push, start_clock, wait_rise
from spiv.master_check import MasterCfg, analyze, naive_slave_decode, summarize
from spiv.models import CLK_PS, SpiSlaveModel
from spiv.trace import Trace, now_ps, sig_int

W = env_int("SPI_W", 8)
NUM_CS = env_int("SPI_NUM_CS", 4)
DEPTH = env_int("SPI_DEPTH", 16)
DIV_W = env_int("SPI_DIV_W", 16)
CSDLY_W = env_int("SPI_CSDLY_W", 8)
MASK = (1 << W) - 1
RES = os.environ.get("RESULTS_DIR", ".")
TAG = os.environ.get("RUN_TAG", f"w{W}")


def dump(name, obj):
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, f"{name}_{TAG}.json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)


def mk_trace(dut):
    return Trace({"sck": dut.sck_o, "mosi": dut.mosi_o, "miso": dut.miso_i, "cs_n": dut.cs_n_o,
                  "busy": dut.busy_o, "done": dut.xfer_done_o, "start": dut.start_i,
                  "rx_full": dut.rx_full_o, "tx_empty": dut.tx_empty_o, "ovr": dut.rx_overrun_o,
                  "cpol": dut.cpol_i})


async def env(dut, **cfg):
    start_clock(dut)
    tr = mk_trace(dut)
    await master_init(dut, **cfg)
    model = SpiSlaveModel(dut.sck_o, dut.miso_i, dut.cs_n_o, 0, W, 0, 0, 0)
    return tr, model


def timeout_ns(div, nwords, setup=255, hold=255):
    return (nwords * (2 * W * (div + 1) + setup + hold + 20) + 200) * 10 + 10_000


async def run_xfer(dut, tr, model, rng, mode, lsb, div, setup, hold, inter, nwords, cs_sel, words=None):
    """Push words, launch, wait, analyse. Returns dict with results."""
    cpol, cpha = mode >> 1, mode & 1
    model.configure(cpol, cpha, lsb)
    model.line = cs_sel
    await FallingEdge(dut.clk_i)
    master_cfg(dut, cpol, cpha, lsb, div, cs_sel, setup, hold, inter)
    words = words if words is not None else [rng.getrandbits(W) for _ in range(nwords)]
    miso_words = [rng.getrandbits(W) for _ in range(len(words))]
    model.queue.clear()
    model.queue.extend(miso_words)
    sent0 = len(model.sent)
    await push(dut, words)
    t0 = now_ps()
    starts = 1 if inter else len(words)
    for _ in range(starts):
        await pulse(dut, "start_i")
        await wait_rise(dut.xfer_done_o, timeout_ns(div, len(words) if inter else 1, setup, hold))
        await cycles(dut, 2)
    t1 = now_ps()
    exp_windows = [words] if inter else [[w] for w in words]
    cfg = MasterCfg(W, cpol, cpha, lsb, div, setup, hold, cs_sel, NUM_CS)
    rep = analyze(tr, t0, t1, cfg, exp_windows)
    rx = await drain(dut)
    got_miso = model.sent[sent0:]
    res = {
        "mode": mode, "lsb": lsb, "div": div, "setup": setup, "hold": hold, "inter": inter,
        "nwords": len(words), "cs_sel": cs_sel,
        "viol": summarize(rep),
        "viol_detail": {k: v[:4] for k, v in rep.viol.items()},
        "mosi_ok": rep.mosi_words == words,
        "miso_ok": rx == got_miso == miso_words,
        "tx": [hex(w) for w in words], "mosi_seen": [hex(w) for w in rep.mosi_words],
        "miso_sent": [hex(w) for w in miso_words], "rx": [hex(w) for w in rx],
        "cs_setup_cycles": [w.get("cs_setup_cycles") for w in rep.windows],
        "cs_hold_last_edge": [w.get("cs_hold_cycles_from_last_edge") for w in rep.windows],
        "cs_hold_last_shift_edge": [w.get("cs_hold_cycles_from_last_shift_edge") for w in rep.windows],
        "half_period": sorted({h for w in rep.windows for wd in w["words"] for h in wd["half_period_cycles"]}),
        "gaps": [wd.get("gap_from_prev_word_cycles") for w in rep.windows for wd in w["words"][1:]],
        "min_mosi_setup": min([wd["min_mosi_setup_cycles"] for w in rep.windows for wd in w["words"]] or [None]),
        "min_mosi_hold": min([wd["min_mosi_hold_cycles"] for w in rep.windows for wd in w["words"]] or [None]),
        "mosi_at_cs_fall_is_first_bit": [w.get("mosi_at_cs_fall_is_first_bit") for w in rep.windows],
        "first_bit_setup_cycles": [w.get("first_bit_setup_cycles") for w in rep.windows],
        "sck_before_cs_fall": [w.get("sck_before_cs_fall") for w in rep.windows],
        "t0": t0, "t1": t1,
    }
    return res


@cocotb.test()
async def test_m_matrix(dut):
    """All 4 modes x MSB/LSB x clk_div x CS setup/hold/inter-byte/cs_sel variants."""
    rng = random.Random(1000 + W)
    tr, model = await env(dut)
    divs = [0, 1, 2, 3, 7]
    variants = [(0, 0, 0, 1), (3, 2, 1, 3), (1, 5, 0, 2), (255, 255, 1, 2), (0, 0, 1, 4), (6, 1, 0, 3)]
    if CSDLY_W < 8:
        variants = [(min(s, (1 << CSDLY_W) - 1), min(h, (1 << CSDLY_W) - 1), i, n) for s, h, i, n in variants]
    results = []
    k = 0
    for mode in range(4):
        # first transfer after a CPOL change is reported separately (see test_m_idle_level)
        await run_xfer(dut, tr, model, rng, mode, 0, 3, 2, 2, 0, 1, 0)
        for lsb in (0, 1):
            for div in divs:
                s, h, inter, n = variants[k % len(variants)]
                n = min(n, DEPTH)   # a burst is queued in the TX FIFO before the start
                cs_sel = k % NUM_CS
                k += 1
                results.append(await run_xfer(dut, tr, model, rng, mode, lsb, div, s, h, inter, n, cs_sel))
    dump("m_matrix", results)
    # summary
    agg = {}
    for r in results:
        for cat, n in r["viol"].items():
            agg.setdefault(cat, {}).setdefault(f"mode{r['mode']}", 0)
            agg[cat][f"mode{r['mode']}"] += n
    bad_mosi = [r for r in results if not r["mosi_ok"]]
    bad_miso = [r for r in results if not r["miso_ok"]]
    dut._log.info("violations by category/mode: %s", agg)
    dut._log.info("MOSI data mismatches: %d / %d configs; MISO/RX mismatches: %d", len(bad_mosi), len(results), len(bad_miso))
    for r in results:
        dut._log.info("mode%d lsb%d div%-3d set%-3d hold%-3d inter%d n%d cs%d: cs_setup=%s (doc %d) hold_from_last_edge=%s (doc %d) half=%s gaps=%s minSU=%s minHO=%s viol=%s",
                      r["mode"], r["lsb"], r["div"], r["setup"], r["hold"], r["inter"], r["nwords"], r["cs_sel"],
                      r["cs_setup_cycles"], r["setup"], r["cs_hold_last_edge"], r["hold"], r["half_period"], r["gaps"],
                      r["min_mosi_setup"], r["min_mosi_hold"], r["viol"])
    assert not bad_mosi and not bad_miso, "data mismatch"
    hard = {c: v for c, v in agg.items()}
    assert not hard, f"protocol violations: {hard}"


@cocotb.test()
async def test_m_div_extremes(dut):
    """clk_div = 0, 255 (documented example) and the maximum 2**DIV_WIDTH-1; SCK frequency check."""
    rng = random.Random(7)
    tr, model = await env(dut)
    out = []
    maxdiv = (1 << DIV_W) - 1
    for mode in (0, 3):
        for div in (0, 255, maxdiv):
            r = await run_xfer(dut, tr, model, rng, mode, 0, div, 1, 1, 0, 1, 0)
            r["sck_freq_ratio_measured"] = [2 * h for h in r["half_period"]]
            r["sck_freq_ratio_doc"] = 2 * (div + 1)
            out.append(r)
            dut._log.info("mode%d div=%d: half period %s clk -> SCK=clk/%s (doc clk/%d) mosi_ok=%s miso_ok=%s viol=%s",
                          mode, div, r["half_period"], r["sck_freq_ratio_measured"], 2 * (div + 1),
                          r["mosi_ok"], r["miso_ok"], r["viol"])
    dump("m_div_extremes", out)
    assert all(r["mosi_ok"] and r["miso_ok"] and r["half_period"] == [r["div"] + 1] for r in out)


@cocotb.test()
async def test_m_idle_level(dut):
    """SCK idle level = CPOL after reset and across CPOL changes; impact on plain shift-register slaves.

    Pass 'robust': independent model that ignores pre-frame edges (MISO correct) + offline
    plain-slave MOSI capture with +/-200 ps SCK/CS skew.
    Pass 'naive_sck_late' / 'naive_cs_late': a plain shift-register slave drives MISO in-sim.
    """
    rng = random.Random(3)
    start_clock(dut)
    tr = mk_trace(dut)
    out = {"passes": {}}
    plan = [(3, "first transfer after reset, mode 3"), (3, "second transfer, mode 3"),
            (0, "switch mode3->mode0"), (2, "switch mode0->mode2"), (2, "mode2 again"),
            (1, "switch mode2->mode1"), (2, "switch mode1->mode2")]
    w = 0xA5 & MASK if W >= 8 else 0x5 & MASK
    for pname in ("robust", "naive_sck_late", "naive_cs_late"):
        await master_init(dut, cpol=1, cpha=1, div=2, setup=2, hold=2)
        await cycles(dut, 5)
        out["sck_after_reset_with_cpol1"] = sig_int(dut.sck_o)
        if pname == "robust":
            model = SpiSlaveModel(dut.sck_o, dut.miso_i, dut.cs_n_o, 0, W, 1, 1, 0)
        else:
            from spiv.models import NaiveSlaveMiso
            model = NaiveSlaveMiso(dut.sck_o, dut.miso_i, dut.cs_n_o, 0, W, 1, 1, 0,
                                   sck_delay_ps=200 if pname == "naive_sck_late" else 0,
                                   cs_delay_ps=200 if pname == "naive_cs_late" else 0)
        seq = []
        for mode, label in plan:
            r = await run_xfer(dut, tr, model, rng, mode, 0, 2, 2, 2, 0, 1, 0, words=[w])
            cpol, cpha = mode >> 1, mode & 1
            sample_on_rise = (cpol ^ cpha) == 0
            naive = {}
            for name, skw in (("sck_late_200ps", dict(d_sck=200)), ("cs_late_200ps", dict(d_cs=200, d_mosi=200))):
                words, partial = naive_slave_decode(tr, r["t0"], r["t1"], 0, W, 0, sample_on_rise, **skw)
                naive[name] = {"words": [hex(x) for x in words], "partial_bits": partial}
            seq.append({"label": label, "mode": mode, "sck_before_cs_fall": r["sck_before_cs_fall"],
                        "viol": r["viol"], "mosi_ok": r["mosi_ok"], "tx": r["tx"],
                        "miso_sent": r["miso_sent"], "master_rx": r["rx"], "miso_ok": r["miso_ok"],
                        "naive_mosi_capture": naive, "t0": r["t0"], "t1": r["t1"]})
            dut._log.info("[%s] %-36s sck_before_cs_fall=%s master_rx=%s (slave sent %s) naive_mosi=%s",
                          pname, label, r["sck_before_cs_fall"], r["rx"], r["miso_sent"], naive)
        model.stop()
        out["passes"][pname] = seq
    await FallingEdge(dut.clk_i)
    master_cfg(dut, 1, 0, 0, 2, 0, 2, 2, 0)
    await cycles(dut, 10)
    out["sck_idle_after_setting_cpol1_without_transfer"] = sig_int(dut.sck_o)
    dump("m_idle_level", out)
    assert out["sck_after_reset_with_cpol1"] == 1, "SCK does not idle at CPOL=1 after reset"
    assert out["sck_idle_after_setting_cpol1_without_transfer"] == 1, "SCK does not follow CPOL while idle"
    for pname, seq in out["passes"].items():
        for st in seq:
            assert st["sck_before_cs_fall"] == [st["mode"] >> 1], f"{pname} {st['label']}: SCK not at CPOL at CS fall"
            assert not st["viol"], f"{pname} {st['label']}: {st['viol']}"
            assert st["mosi_ok"] and st["miso_ok"], f"{pname} {st['label']}: data error"
            for name, cap in st["naive_mosi_capture"].items():
                assert cap["words"] == st["tx"] and not cap["partial_bits"], f"{pname} {st['label']} {name}: {cap}"


@cocotb.test()
async def test_m_cpha1_last_bit_hold(dut):
    """MOSI hold at the final sampling edge (CPHA=1) and what a plain slave latches with +/-200ps skew."""
    rng = random.Random(5)
    tr, model = await env(dut)
    out = []
    for mode in (1, 3):
        for lsb in (0, 1):
            for div in (0, 3):
                words = [MASK, 0x1 if not lsb else (1 << (W - 1)), rng.getrandbits(W) | (1 if not lsb else 1 << (W - 1))]
                r = await run_xfer(dut, tr, model, rng, mode, lsb, div, 1, 3, 1, len(words), 0, words=words)
                cpol, cpha = mode >> 1, mode & 1
                sor = (cpol ^ cpha) == 0
                n_late, _ = naive_slave_decode(tr, r["t0"], r["t1"], 0, W, lsb, sor, d_sck=200)
                n_early, _ = naive_slave_decode(tr, r["t0"], r["t1"], 0, W, lsb, sor, d_mosi=200, d_cs=200)
                r["naive_sck_late_200ps"] = [hex(x) for x in n_late]
                r["naive_sck_early_200ps"] = [hex(x) for x in n_early]
                out.append(r)
                dut._log.info("mode%d lsb%d div%d tx=%s min_hold=%s viol=%s naive(SCK +200ps)=%s naive(SCK -200ps)=%s",
                              mode, lsb, div, r["tx"], r["min_mosi_hold"], r["viol"].get("mosi_hold_lt_1clk_at_sample_edge"),
                              r["naive_sck_late_200ps"], r["naive_sck_early_200ps"])
    dump("m_cpha1_last_bit_hold", out)
    assert all(r["viol"].get("mosi_hold_lt_1clk_at_sample_edge", 0) == 0 for r in out), "zero MOSI hold at the last CPHA=1 sampling edge"


@cocotb.test()
async def test_m_start_busy_done(dut):
    """start_i / busy_o / xfer_done_o semantics and documented latencies."""
    rng = random.Random(11)
    tr, model = await env(dut)
    out = {}
    N, M, D = 4, 3, 2
    model.configure(0, 0, 0)
    master_cfg(dut, 0, 0, 0, D, 0, N, M, 0)
    model.queue.extend([0x5 & MASK])
    await push(dut, [0x3C & MASK])
    await FallingEdge(dut.clk_i)
    dut.start_i.value = 1
    t_sample = now_ps() + CLK_PS // 2  # rising edge that samples start_i
    await FallingEdge(dut.clk_i)
    dut.start_i.value = 0
    busy_after_1 = sig_int(dut.busy_o)
    await wait_rise(dut.xfer_done_o, 100_000)
    t_done = now_ps()
    await FallingEdge(dut.clk_i)
    done_width_ok = sig_int(dut.xfer_done_o) == 0
    await cycles(dut, 3)
    sck_e = tr.changes("sck", t_sample, t_done)
    busy_ch = tr.changes("busy", t_sample - CLK_PS, t_done + CLK_PS)
    cs_ch = tr.bit_changes("cs_n", 0, t_sample - CLK_PS, t_done + CLK_PS)
    last_sample = sck_e[-2][0]  # CPHA=0: sampling edges are the leading (even) ones
    out["start_to_first_sck_edge_cycles"] = (sck_e[0][0] - t_sample) / CLK_PS
    out["doc_start_to_first_sck_edge"] = N + 1
    out["cs_fall_to_first_sck_edge_cycles"] = (sck_e[0][0] - cs_ch[0][0]) / CLK_PS
    out["doc_cs_fall_to_first_sck_edge"] = N
    out["last_sample_to_done_cycles"] = (t_done - last_sample) / CLK_PS
    out["doc_last_sample_to_done"] = M + 2
    out["busy_rises_at_start_sampling_edge"] = busy_ch[0][0] == t_sample if busy_ch else None
    out["busy_falls_with_done"] = busy_ch[-1][0] == t_done if busy_ch else None
    dch = tr.changes("done", t_done - 1, t_done + 5 * CLK_PS)
    out["done_pulse_width_cycles"] = (dch[1][0] - dch[0][0]) / CLK_PS if len(dch) >= 2 else None
    out["busy_after_1_cycle"] = busy_after_1
    out["cs_rise_to_done_cycles"] = (t_done - cs_ch[-1][0]) / CLK_PS
    # start while busy (FIFO holds another word): dropped or queued?
    await push(dut, [0x11 & MASK, 0x22 & MASK])
    model.queue.extend([1, 2])
    await pulse(dut, "start_i")
    await cycles(dut, 10)
    await pulse(dut, "start_i")  # mid-transfer start
    await wait_rise(dut.xfer_done_o, 100_000)
    await cycles(dut, 60)
    out["words_left_in_tx_fifo_after_start_while_busy"] = sig_int(dut.tx_count_o)
    out["busy_after_mid_start"] = sig_int(dut.busy_o)
    await pulse(dut, "start_i")
    await wait_rise(dut.xfer_done_o, 100_000)
    await cycles(dut, 3)
    await drain(dut)
    # start with empty TX FIFO
    t_a = now_ps()
    await pulse(dut, "start_i")
    await cycles(dut, 30)
    out["start_with_empty_fifo_busy_changes"] = len(tr.changes("busy", t_a, now_ps()))
    out["start_with_empty_fifo_done_changes"] = len(tr.changes("done", t_a, now_ps()))
    # start in the same cycle as the TX push
    t_a = now_ps()
    await FallingEdge(dut.clk_i)
    dut.tx_we_i.value = 1
    dut.tx_wdata_i.value = 0x66 & MASK
    dut.start_i.value = 1
    await FallingEdge(dut.clk_i)
    dut.tx_we_i.value = 0
    dut.start_i.value = 0
    await cycles(dut, 40)
    out["start_same_cycle_as_push_started_transfer"] = len(tr.changes("busy", t_a, now_ps())) > 0
    out["tx_count_after_same_cycle_push_start"] = sig_int(dut.tx_count_o)
    # held start_i (level) with 3 words, inter_byte=0
    await pulse(dut, "start_i")  # flush the 0x66 word
    await wait_rise(dut.xfer_done_o, 100_000)
    await cycles(dut, 3)
    await push(dut, [1, 2, 3])
    t_a = now_ps()
    await FallingEdge(dut.clk_i)
    dut.start_i.value = 1
    await cycles(dut, 400)
    dut.start_i.value = 0
    await cycles(dut, 50)
    out["held_start_cs_windows"] = len([c for c in tr.bit_changes("cs_n", 0, t_a, now_ps()) if c[1] == 0])
    gaps = tr.bit_changes("cs_n", 0, t_a, now_ps())
    highs = [(gaps[i + 1][0] - gaps[i][0]) / CLK_PS for i in range(len(gaps) - 1) if gaps[i][1] == 1]
    out["min_cs_high_between_transfers_cycles"] = min(highs) if highs else None
    dump("m_start_busy_done", out)
    for k, v in out.items():
        dut._log.info("%-50s %s", k, v)


@cocotb.test()
async def test_m_fifo_streaming(dut):
    """inter_byte=0 with several words + one start; inter_byte=1 streaming with refill while busy."""
    rng = random.Random(13)
    tr, model = await env(dut)
    out = {}
    # (a) inter_byte=0, 4 words, ONE start pulse
    model.configure(0, 0, 0)
    master_cfg(dut, 0, 0, 0, 1, 0, 1, 1, 0)
    words = [rng.getrandbits(W) for _ in range(4)]
    model.queue.extend(words)
    await push(dut, words)
    t0 = now_ps()
    await pulse(dut, "start_i")
    await cycles(dut, 400)
    out["inter0_one_start_words_sent"] = len([c for c in tr.bit_changes("cs_n", 0, t0, now_ps()) if c[1] == 0])
    out["inter0_one_start_tx_left"] = sig_int(dut.tx_count_o)
    while sig_int(dut.tx_count_o):
        await pulse(dut, "start_i")
        await wait_rise(dut.xfer_done_o, 100_000)
    await cycles(dut, 3)
    await drain(dut)
    model.queue.clear()
    # (b) inter_byte=1 streaming 3*DEPTH words, refilling whenever not full
    for mode in range(4):
        cpol, cpha = mode >> 1, mode & 1
        model.configure(cpol, cpha, 0)
        model.line = 1
        await FallingEdge(dut.clk_i)
        master_cfg(dut, cpol, cpha, 0, 0, 1, 2, 2, 1)
        total = 3 * DEPTH
        words = [rng.getrandbits(W) for _ in range(total)]
        miso = [rng.getrandbits(W) for _ in range(total)]
        model.queue.clear()
        model.queue.extend(miso)
        s0 = len(model.sent)
        await push(dut, words[:DEPTH])
        t0 = now_ps()
        await pulse(dut, "start_i")
        sent = DEPTH
        rx = []
        while sent < total or sig_int(dut.busy_o):
            await FallingEdge(dut.clk_i)
            if sent < total and sig_int(dut.tx_full_o) == 0:
                dut.tx_we_i.value = 1
                dut.tx_wdata_i.value = words[sent]
                sent += 1
            else:
                dut.tx_we_i.value = 0
            if sig_int(dut.rx_empty_o) == 0 and sig_int(dut.rx_re_i) == 0:
                dut.rx_re_i.value = 1
            elif sig_int(dut.rx_re_i) == 1:
                dut.rx_re_i.value = 0
                rx.append(sig_int(dut.rx_rdata_o))
        dut.tx_we_i.value = 0
        await cycles(dut, 5)
        if sig_int(dut.rx_re_i) == 1:
            dut.rx_re_i.value = 0
            rx.append(sig_int(dut.rx_rdata_o))
        rx += await drain(dut)
        t1 = now_ps()
        cfg = MasterCfg(W, cpol, cpha, 0, 0, 2, 2, 1, NUM_CS)
        wins = [c for c in tr.bit_changes("cs_n", 1, t0, t1) if c[1] == 0]
        rep = analyze(tr, t0, t1, cfg, [words] if len(wins) == 1 else [[]] * len(wins))
        sent_m = model.sent[s0:]
        mm = [i for i in range(min(len(rx), len(miso))) if rx[i] != miso[i]]
        out[f"stream_mode{mode}_dbg"] = {"first_mismatch": mm[:3], "rx_head": [hex(x) for x in rx[:6]],
                                         "miso_head": [hex(x) for x in miso[:6]], "model_sent_head": [hex(x) for x in sent_m[:6]],
                                         "len_sent": len(sent_m)}
        out[f"stream_mode{mode}"] = {"cs_windows": len(wins), "mosi_ok": rep.mosi_words == words,
                                    "rx_ok": rx == model.sent[s0:] == miso, "n_rx": len(rx), "viol": summarize(rep)}
    dump("m_fifo_streaming", out)
    for k, v in out.items():
        dut._log.info("%-28s %s", k, v)
    assert all(out[f"stream_mode{m}"]["mosi_ok"] and out[f"stream_mode{m}"]["rx_ok"] for m in range(4))


@cocotb.test()
async def test_m_cs_lines(dut):
    """Every chip-select line: only the selected line is driven low."""
    rng = random.Random(17)
    tr, model = await env(dut)
    out = []
    for line in range(NUM_CS):
        r = await run_xfer(dut, tr, model, rng, 0, 0, 1, 1, 1, 1, 2, line)
        lows = set()
        for t, v in [(r["t0"], tr.at("cs_n", r["t0"]))] + tr.changes("cs_n", r["t0"], r["t1"]):
            lows |= {i for i in range(NUM_CS) if not (v >> i) & 1}
        out.append({"cs_sel": line, "lines_seen_low": sorted(lows), "viol": r["viol"], "mosi_ok": r["mosi_ok"], "miso_ok": r["miso_ok"]})
        dut._log.info("cs_sel=%d lines low=%s viol=%s data_ok=%s/%s", line, sorted(lows), r["viol"], r["mosi_ok"], r["miso_ok"])
    dump("m_cs_lines", out)
    assert all(o["lines_seen_low"] == [o["cs_sel"]] and o["mosi_ok"] and o["miso_ok"] for o in out)


@cocotb.test()
async def test_m_reset_mid_transfer(dut):
    """Synchronous reset in the middle of a word, then a clean transfer."""
    rng = random.Random(19)
    tr, model = await env(dut, cpol=1, cpha=0)
    out = {}
    model.configure(1, 0, 0)
    master_cfg(dut, 1, 0, 0, 3, 0, 2, 2, 1)
    await push(dut, [0xAA & MASK, 0x55 & MASK, 0x0F & MASK])
    model.queue.extend([1, 2, 3])
    await pulse(dut, "start_i")
    await cycles(dut, 40)
    t_r = now_ps()
    dut.rst_ni.value = 0
    await cycles(dut, 4)
    dut.rst_ni.value = 1
    await cycles(dut, 4)
    out["cs_after_reset"] = sig_int(dut.cs_n_o)
    out["sck_after_reset"] = sig_int(dut.sck_o)
    out["tx_count_after_reset"] = sig_int(dut.tx_count_o)
    out["sck_changes_during_reset"] = tr.changes("sck", t_r, now_ps())
    out["cs_changes_during_reset"] = tr.changes("cs_n", t_r, now_ps())
    r = await run_xfer(dut, tr, model, rng, 2, 0, 1, 2, 2, 0, 1, 0)
    out["post_reset_transfer"] = {"mosi_ok": r["mosi_ok"], "miso_ok": r["miso_ok"], "viol": r["viol"]}
    dump("m_reset_mid_transfer", out)
    for k, v in out.items():
        dut._log.info("%-28s %s", k, v)
    assert r["mosi_ok"] and r["miso_ok"]


@cocotb.test()
async def test_m_rx_overrun(dut):
    """Master RX FIFO full (no software reads): the first DEPTH words are kept, one rx_overrun_o pulse per dropped word."""
    rng = random.Random(23)
    tr, model = await env(dut)
    model.configure(0, 0, 0)
    master_cfg(dut, 0, 0, 0, 0, 0, 0, 0, 1)
    n = DEPTH + 3
    miso = [(i + 1) & MASK for i in range(n)]
    model.queue.extend(miso)
    words = [rng.getrandbits(W) for _ in range(n)]
    await push(dut, words[:DEPTH])
    await pulse(dut, "start_i")
    for w in words[DEPTH:]:
        while sig_int(dut.tx_full_o):
            await FallingEdge(dut.clk_i)
        await push(dut, [w])
    await wait_rise(dut.xfer_done_o, 200_000)
    await cycles(dut, 3)
    rx_full = sig_int(dut.rx_full_o)
    rx = await drain(dut)
    out = {"words_sent": n, "rx_full_at_end": rx_full, "rx_words_kept": len(rx),
           "kept_are_first_words": rx == miso[:DEPTH], "rx": [hex(x) for x in rx],
           "rx_overrun_pulses": len([c for c in tr.changes("ovr") if c[1] == 1])}
    dump("m_rx_overrun", out)
    dut._log.info("%s", out)
    assert out["kept_are_first_words"] and out["rx_full_at_end"] == 1
    assert out["rx_overrun_pulses"] == n - DEPTH, "rx_overrun_o pulses != dropped words"


@cocotb.test()
async def test_m_random_regression(dut):
    """Long randomized regression vs the independent slave model with a scoreboard.

    Random mode / bit order / clk_div / CS setup+hold / inter-byte / burst length / CS line.
    During bursts the configuration inputs are scrambled while busy (must be latched).
    No protocol violation is allowed; while every CS_n is high SCK may only move to
    the cpol_i value sampled on that edge.
    """
    nwords = int(os.environ.get("M_RANDOM_WORDS", "3000"))
    rng = random.Random(int(os.environ.get("M_RANDOM_SEED", "99")) + W)
    tr, model = await env(dut)
    sent = 0
    viol_tot = {}
    data_err = {"mosi": 0, "rx": 0}
    by_mode = {m: 0 for m in range(4)}
    hold_viol_not_last_bit = 0
    idle_moves = 0
    while sent < nwords:
        mode = rng.randrange(4)
        lsb = rng.randrange(2)
        div = rng.choice([0, 0, 1, 1, 2, 3, 5, 7, 15])
        setup = rng.choice([0, 1, 2, 3, 5, 7, 255]) & ((1 << CSDLY_W) - 1)
        hold = rng.choice([0, 1, 2, 3, 5, 7]) & ((1 << CSDLY_W) - 1)
        inter = rng.randrange(2)
        n = rng.randint(1, min(DEPTH, 12)) if inter else rng.randint(1, min(DEPTH, 3))
        cs_sel = rng.randrange(NUM_CS)
        cpol, cpha = mode >> 1, mode & 1
        model.configure(cpol, cpha, lsb)
        model.line = cs_sel
        await FallingEdge(dut.clk_i)
        master_cfg(dut, cpol, cpha, lsb, div, cs_sel, setup, hold, inter)
        words = [rng.getrandbits(W) for _ in range(n)]
        miso_words = [rng.getrandbits(W) for _ in range(n)]
        model.queue.clear()
        model.queue.extend(miso_words)
        s0 = len(model.sent)
        await push(dut, words)
        t0 = now_ps()
        for k in range(1 if inter else n):
            await pulse(dut, "start_i")
            if inter:
                # scramble the (latched) configuration while the burst runs, restore before it ends
                for _ in range(rng.randint(1, 4)):
                    await cycles(dut, rng.randint(1, 20))
                    if not sig_int(dut.busy_o):
                        break
                    master_cfg(dut, rng.randrange(2), rng.randrange(2), rng.randrange(2), rng.randrange(1 << min(DIV_W, 8)),
                               rng.randrange(NUM_CS), rng.randrange(1 << CSDLY_W), rng.randrange(1 << CSDLY_W), 1)
            limit = timeout_ns(div, n if inter else 1, setup, hold) // 10
            for _ in range(limit):
                if len([1 for c in tr.changes("done", t0) if c[1] == 1]) >= k + 1:
                    break
                await FallingEdge(dut.clk_i)
            else:
                raise AssertionError("xfer_done never pulsed")
            await FallingEdge(dut.clk_i)
            master_cfg(dut, cpol, cpha, lsb, div, cs_sel, setup, hold, inter)
            await cycles(dut, 1)
        t1 = now_ps()
        cfg = MasterCfg(W, cpol, cpha, lsb, div, setup, hold, cs_sel, NUM_CS)
        rep = analyze(tr, t0, t1, cfg, [words] if inter else [[w] for w in words])
        idle_moves += rep.idle_cpol_moves
        for c, v in rep.viol.items():
            viol_tot.setdefault(c, {}).setdefault(f"mode{mode}", 0)
            viol_tot[c][f"mode{mode}"] += len(v)
            if c == "mosi_hold_lt_1clk_at_sample_edge":
                hold_viol_not_last_bit += sum(1 for x in v if x[1] != W - 1)
        rx = await drain(dut)
        if rep.mosi_words != words:
            data_err["mosi"] += sum(1 for a, b in zip(rep.mosi_words, words) if a != b) + abs(len(words) - len(rep.mosi_words))
        if rx != miso_words or model.sent[s0:] != miso_words:
            data_err["rx"] += sum(1 for a, b in zip(rx, miso_words) if a != b) + abs(len(words) - len(rx))
        by_mode[mode] += n
        sent += n
    out = {"words": sent, "by_mode": by_mode, "data_errors": data_err, "protocol_violations": viol_tot,
           "hold_violations_not_on_last_bit": hold_viol_not_last_bit, "idle_sck_moves_to_cpol": idle_moves}
    dump("m_random_regression", out)
    dut._log.info("%s", json.dumps(out))
    assert data_err == {"mosi": 0, "rx": 0}
    assert not viol_tot, f"protocol violations: {viol_tot}"
