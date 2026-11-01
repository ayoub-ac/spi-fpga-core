"""Cross-check with third-party cocotbext-spi 0.5.0 models (SpiSlaveLoopback / SpiMaster)."""
import json
import os
import random

import cocotb
from cocotb.triggers import FallingEdge, Timer

from spiv.drv import cycles, drain, env_int, master_cfg, master_init, pulse, push, start_clock, wait_rise
from spiv.trace import sig_int

W = env_int("SPI_W", 8)
MASK = (1 << W) - 1
RES = os.environ.get("RESULTS_DIR", ".")
TAG = os.environ.get("RUN_TAG", "x")
DUT = os.environ.get("SPI_DUT", "tb_master")


def dump(name, obj):
    with open(os.path.join(RES, f"{name}_{TAG}.json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)


@cocotb.test(skip=DUT != "tb_master")
async def test_x_master_vs_cocotbext_loopback(dut):
    """Master DUT vs cocotbext-spi SpiSlaveLoopback (echoes the previous word, one word per CS)."""
    from spiv.compat_spi import SpiBus, SpiConfig, SpiSlaveLoopback

    rng = random.Random(77)
    start_clock(dut)
    await master_init(dut)
    out = []
    for mode in range(4):
        cpol, cpha = mode >> 1, mode & 1
        bus = SpiBus.from_entity(dut, sclk_name="sck_o", mosi_name="mosi_o", miso_name="miso_i", cs_name="cs0_n_o")
        slave = SpiSlaveLoopback(bus, SpiConfig(word_width=W, cpol=bool(cpol), cpha=bool(cpha), msb_first=True,
                                                frame_spacing_ns=1))
        await FallingEdge(dut.clk_i)
        master_cfg(dut, cpol, cpha, 0, 3, 0, 2, 2, 0)
        words = [rng.getrandbits(W) for _ in range(40)]
        rx = []
        for w in words:
            await push(dut, [w])
            await pulse(dut, "start_i")
            await wait_rise(dut.xfer_done_o, 20_000)
            await cycles(dut, 3)
            rx += await drain(dut)
        slave._run_coroutine_obj.cancel()
        # loopback returns the previous word: rx[k] == words[k-1]
        exp = words[:-1]
        got = rx[1:]
        bad = [(i + 1, hex(words[i]), hex(got[i])) for i in range(len(exp)) if got[i] != exp[i]]
        lastbit_only = [b for b in bad if (int(b[1], 16) ^ int(b[2], 16)) == 1]
        o = {"mode": mode, "words": len(exp), "mismatches": len(bad), "mismatch_last_bit_only": len(lastbit_only),
             "examples": bad[:6]}
        out.append(o)
        dut._log.info("cocotbext loopback mode%d: %d/%d echoed words wrong (%d differ only in the last bit) ex=%s",
                      mode, len(bad), len(exp), len(lastbit_only), bad[:4])
    dump("x_master_cocotbext", out)
    assert all(o["mismatches"] == 0 for o in out), "cocotbext-spi slave saw wrong MOSI words"


@cocotb.test(skip=DUT != "tb_slave")
async def test_x_slave_vs_cocotbext_master(dut):
    """Slave DUT driven by cocotbext-spi SpiMaster at SCK = clk/8 (single-word and burst frames)."""
    from spiv.compat_spi import SpiBus, SpiConfig, SpiMaster

    rng = random.Random(78)
    start_clock(dut)
    dut.rst_ni.value = 0
    dut.tx_we_i.value = 0
    dut.rx_re_i.value = 0
    dut.cs_n_i.value = 1
    await cycles(dut, 5)
    dut.rst_ni.value = 1
    await cycles(dut, 3)
    out = []
    for mode in range(4):
        cpol, cpha = mode >> 1, mode & 1
        dut.cpol_i.value = cpol
        dut.cpha_i.value = cpha
        dut.lsb_first_i.value = 0
        bus = SpiBus.from_entity(dut, sclk_name="sck_i", mosi_name="mosi_i", miso_name="miso_o", cs_name="cs_n_i")
        m = SpiMaster(bus, SpiConfig(word_width=W, sclk_freq=12.5e6, cpol=bool(cpol), cpha=bool(cpha),
                                     msb_first=True, frame_spacing_ns=100))
        await Timer(200, unit="ns")
        res = {"mode": mode}
        for burst in (False, True):
            words = [rng.getrandbits(W) for _ in range(12)]
            resp = [rng.getrandbits(W) for _ in range(12)]
            got_m = []
            if burst:
                await push(dut, resp)
                await m.write(words, burst=True)
                got_m = list(await m.read(len(words)))
            else:
                for w, r in zip(words, resp):
                    await push(dut, [r])  # exact preload per CS frame
                    await m.write([w])
                    got_m += list(await m.read(1))
            await cycles(dut, 5)
            rx = await drain(dut)
            res["burst" if burst else "single"] = {"slave_rx_ok": rx == words, "master_rx_ok": got_m == resp,
                                                   "slave_rx_bad": sum(1 for a, b in zip(rx, words) if a != b),
                                                   "master_rx_bad": sum(1 for a, b in zip(got_m, resp) if a != b)}
        m._run_coroutine_obj.cancel()
        out.append(res)
        dut._log.info("cocotbext SpiMaster mode%d: %s", mode, res)
    dump("x_slave_cocotbext", out)
    assert all(r[k]["slave_rx_ok"] and r[k]["master_rx_ok"] for r in out for k in ("single", "burst"))
