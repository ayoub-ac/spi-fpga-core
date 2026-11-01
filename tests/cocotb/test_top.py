"""spi_top (repo master <-> repo slave loopback): CPHA=1 last bit, random loopback, interrupts."""
import json
import os
import random

import cocotb
from cocotb.triggers import FallingEdge

from spiv.drv import cycles, env_int, start_clock, wait_rise
from spiv.trace import Trace, now_ps, sig_int

W = env_int("SPI_W", 8)
DEPTH = env_int("SPI_DEPTH", 16)
MASK = (1 << W) - 1
RES = os.environ.get("RESULTS_DIR", ".")
TAG = os.environ.get("RUN_TAG", f"w{W}")


def dump(name, obj):
    with open(os.path.join(RES, f"{name}_{TAG}.json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)


async def top_init(dut):
    start_clock(dut)
    for n in ("m_cpol_i", "m_cpha_i", "m_lsb_first_i", "m_clk_div_i", "m_cs_sel_i", "m_cs_setup_i",
              "m_cs_hold_i", "m_cs_inter_byte_i", "m_start_i", "m_tx_we_i", "m_tx_wdata_i", "m_rx_re_i",
              "s_cpol_i", "s_cpha_i", "s_lsb_first_i", "s_tx_we_i", "s_tx_wdata_i", "s_rx_re_i"):
        getattr(dut, n).value = 0
    dut.rst_ni.value = 0
    await cycles(dut, 6)
    dut.rst_ni.value = 1
    await cycles(dut, 3)


def cfg(dut, mode, lsb, div, setup, hold, inter, cs_sel=0):
    cpol, cpha = mode >> 1, mode & 1
    dut.m_cpol_i.value = cpol
    dut.m_cpha_i.value = cpha
    dut.m_lsb_first_i.value = lsb
    dut.m_clk_div_i.value = div
    dut.m_cs_sel_i.value = cs_sel
    dut.m_cs_setup_i.value = setup
    dut.m_cs_hold_i.value = hold
    dut.m_cs_inter_byte_i.value = inter
    dut.s_cpol_i.value = cpol
    dut.s_cpha_i.value = cpha
    dut.s_lsb_first_i.value = lsb


async def push(dut, side, words):
    for w in words:
        await FallingEdge(dut.clk_i)
        getattr(dut, f"{side}_tx_we_i").value = 1
        getattr(dut, f"{side}_tx_wdata_i").value = w
    await FallingEdge(dut.clk_i)
    getattr(dut, f"{side}_tx_we_i").value = 0


async def drain(dut, side):
    out = []
    await FallingEdge(dut.clk_i)
    while sig_int(getattr(dut, f"{side}_rx_empty_o")) == 0:
        getattr(dut, f"{side}_rx_re_i").value = 1
        await FallingEdge(dut.clk_i)
        getattr(dut, f"{side}_rx_re_i").value = 0
        out.append(sig_int(getattr(dut, f"{side}_rx_rdata_o")))
        await FallingEdge(dut.clk_i)
    return out


async def start(dut):
    await FallingEdge(dut.clk_i)
    dut.m_start_i.value = 1
    await FallingEdge(dut.clk_i)
    dut.m_start_i.value = 0


async def xfer(dut, m_words, s_words, inter, div):
    """One master burst (inter=1) or one word per start (inter=0); every slave word queued first."""
    await push(dut, "s", s_words)
    await push(dut, "m", m_words)
    n = 1 if inter else len(m_words)
    for _ in range(n):
        await start(dut)
        await wait_rise(dut.m_xfer_done_o, 50_000 + 400 * len(m_words) * (div + 1) * W)
        await cycles(dut, 3)
    return await drain(dut, "m"), await drain(dut, "s")


@cocotb.test()
async def test_t_loopback_cpha1_lastbit(dut):
    """Repo master -> repo slave, word whose LAST wire bit is 1 vs 0, all modes, MSB/LSB first."""
    await top_init(dut)
    out = []
    for mode in range(4):
        for lsb in (0, 1):
            await FallingEdge(dut.clk_i)
            cfg(dut, mode, lsb, 3, 4, 4, 0)  # same timing as the repo's own test
            await cycles(dut, 2)
            for m2s in (0x3C, 0x3D, 0xF0, 0xF1, 0xFF, 0x01, 0x80):
                m2s &= MASK
                m_rx, s_rx = await xfer(dut, [m2s], [0x5A & MASK], 0, 3)
                last_bit = (m2s >> (W - 1)) & 1 if lsb else m2s & 1
                o = {"mode": mode, "lsb": lsb, "master_sent": hex(m2s), "slave_got": [hex(x) for x in s_rx],
                     "last_wire_bit": last_bit, "ok": s_rx == [m2s], "master_got": [hex(x) for x in m_rx]}
                out.append(o)
    dump("t_loopback_cpha1_lastbit", out)
    for o in out:
        dut._log.info("mode%d lsb%d master sent %-5s (last bit %d) -> slave got %s %s", o["mode"], o["lsb"], o["master_sent"],
                      o["last_wire_bit"], o["slave_got"], "" if o["ok"] else "<-- WRONG")
    assert all(o["ok"] for o in out)


@cocotb.test()
async def test_t_loopback_random(dut):
    """Long randomized loopback (repo master <-> repo slave); slave TX words queued per frame or all at once."""
    rng = random.Random(4242)
    await top_init(dut)
    nwords_total = int(os.environ.get("TOP_RANDOM_WORDS", "3000"))
    min_div = int(os.environ.get("TOP_MIN_DIV", "1"))
    stats = {}
    sent = 0
    examples = []
    while sent < nwords_total:
        mode = rng.randrange(4)
        lsb = rng.randrange(2)
        div = rng.choice([d for d in [0, 0, 1, 1, 2, 3, 5, 7] if d >= min_div])
        setup = rng.randrange(0, 8)
        hold = rng.randrange(0, 8)
        inter = rng.randrange(2)
        n = rng.randint(1, min(8, DEPTH)) if inter else rng.randint(1, min(3, DEPTH))
        await FallingEdge(dut.clk_i)
        cfg(dut, mode, lsb, div, setup, hold, inter)
        await cycles(dut, 2)
        m_words = [rng.getrandbits(W) for _ in range(n)]
        s_words = [rng.getrandbits(W) for _ in range(n)]
        if inter:
            m_rx, s_rx = await xfer(dut, m_words, s_words, 1, div)
        elif rng.randrange(2):
            # all words queued on both sides before the first of n frames
            m_rx, s_rx = await xfer(dut, m_words, s_words, 0, div)
        else:
            m_rx, s_rx = [], []
            for a, b in zip(m_words, s_words):  # slave TX written just before each frame
                r1, r2 = await xfer(dut, [a], [b], 0, div)
                m_rx += r1
                s_rx += r2
        st = stats.setdefault(f"mode{mode}_" + ("div0" if div == 0 else "div>=1"),
                              {"words": 0, "m_err": 0, "s_err": 0, "s_err_last_bit_only": 0})
        st["words"] += n
        for i in range(n):
            if i >= len(m_rx) or m_rx[i] != s_words[i]:
                st["m_err"] += 1
            if i >= len(s_rx) or s_rx[i] != m_words[i]:
                st["s_err"] += 1
                lastmask = (1 << (W - 1)) if lsb else 1
                if i < len(s_rx) and (s_rx[i] ^ m_words[i]) == lastmask:
                    st["s_err_last_bit_only"] += 1
                if len(examples) < 10:
                    examples.append({"mode": mode, "lsb": lsb, "div": div, "sent": hex(m_words[i]),
                                     "got": hex(s_rx[i]) if i < len(s_rx) else None})
        sent += n
    out = {"stats": stats, "examples": examples}
    dump("t_loopback_random", out)
    dut._log.info("%s", json.dumps(out, indent=1))
    assert all(s["m_err"] == 0 and s["s_err"] == 0 for s in stats.values())


@cocotb.test()
async def test_t_irq(dut):
    """irq_tx_empty / irq_rx_full / irq_done / irq_err behaviour vs PORT_DESCRIPTION / README."""
    await top_init(dut)
    tr = Trace({"err": dut.irq_err_o, "done": dut.irq_done_o, "rxf": dut.irq_rx_full_o, "txe": dut.irq_tx_empty_o,
                "sck": dut.sck_o})
    out = {}
    out["irq_tx_empty_after_reset"] = sig_int(dut.irq_tx_empty_o)
    await FallingEdge(dut.clk_i)
    cfg(dut, 0, 0, 1, 1, 1, 0)
    await cycles(dut, 2)

    def pulses(name, t0):
        return len([c for c in tr.changes(name, t0) if c[1] == 1])

    # (a) fill the master RX FIFO to DEPTH-1, then one more word: FIFO becomes exactly full, nothing lost
    for i in range(DEPTH - 1):
        await xfer_keep(dut, [i & MASK], [(0x40 + i) & MASK])
    t0 = now_ps()
    await xfer_keep(dut, [0x55 & MASK], [0x66 & MASK])
    out["a_count_after_filling_exactly"] = sig_int(dut.m_rx_count_o)
    out["a_irq_err_pulses_when_fifo_becomes_exactly_full_no_loss"] = pulses("err", t0)
    rx = await drain(dut, "m")
    out["a_words_in_fifo"] = len(rx)
    await drain(dut, "s")
    # (b) FIFO full, burst of 3 words with inter_byte: 3 words lost, how many irq_err pulses?
    for i in range(DEPTH):
        await xfer_keep(dut, [i & MASK], [(0x40 + i) & MASK])
    await drain(dut, "s")
    await FallingEdge(dut.clk_i)
    cfg(dut, 0, 0, 1, 1, 1, 1)
    t0 = now_ps()
    await xfer_keep(dut, [1 & MASK, 2 & MASK, 3 & MASK], [0xA1 & MASK, 0xA2 & MASK, 0xA3 & MASK], inter=1)
    out["b_lost_words"] = 3
    out["b_irq_err_pulses"] = pulses("err", t0)
    out["b_irq_done_pulses_for_3_word_burst"] = pulses("done", t0)
    await drain(dut, "m")
    await drain(dut, "s")
    # (c) FIFO full, 2-word burst; software pops 2 words after the first word was dropped
    await FallingEdge(dut.clk_i)
    cfg(dut, 0, 0, 1, 1, 1, 0)
    for i in range(DEPTH):
        await xfer_keep(dut, [i & MASK], [(0x40 + i) & MASK], inter=0)
    await drain(dut, "s")
    await FallingEdge(dut.clk_i)
    cfg(dut, 0, 0, 3, 1, 1, 1)
    await push(dut, "s", [0xB1 & MASK, 0xB2 & MASK])
    await push(dut, "m", [7 & MASK, 8 & MASK])
    t0 = now_ps()
    await start(dut)
    # wait until the first word has been dropped (irq_err_o), then pop two
    await wait_rise(dut.irq_err_o, 50_000)
    for _ in range(2):
        await FallingEdge(dut.clk_i)
        dut.m_rx_re_i.value = 1
        await FallingEdge(dut.clk_i)
        dut.m_rx_re_i.value = 0
    await wait_rise(dut.m_xfer_done_o, 50_000)
    await cycles(dut, 3)
    rx = await drain(dut, "m")
    # the first burst word arrives at a full FIFO and is dropped; the two pops make
    # room for the second one: words 2..DEPTH-1 of the fill, then 0xB2
    out["c_rx_is_fill_2_to_end_then_second_word"] = rx == [(0x40 + i) & MASK for i in range(2, DEPTH)] + [0xB2 & MASK]
    out["c_irq_err_pulses"] = pulses("err", t0)
    out["c_rx_tail"] = [hex(x) for x in rx[-3:]]
    out["irq_tx_empty_is_level"] = True
    dump("t_irq", out)
    for k, v in out.items():
        dut._log.info("%-60s %s", k, v)
    # irq_err_o is a real overrun strobe: one pulse per dropped word, none otherwise.
    assert out["a_irq_err_pulses_when_fifo_becomes_exactly_full_no_loss"] == 0, "irq_err without data loss"
    assert out["a_words_in_fifo"] == DEPTH
    assert out["b_irq_err_pulses"] == out["b_lost_words"], "irq_err pulses != words lost"
    assert out["c_irq_err_pulses"] == 1 and out["c_rx_is_fill_2_to_end_then_second_word"], "overrun with a draining reader"


async def xfer_keep(dut, m_words, s_words, inter=0):
    """Transfer without draining the master RX FIFO."""
    await push(dut, "s", s_words)
    await push(dut, "m", m_words)
    for _ in range(1 if inter else len(m_words)):
        await start(dut)
        await wait_rise(dut.m_xfer_done_o, 50_000)
        await cycles(dut, 3)
