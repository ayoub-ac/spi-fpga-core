"""Short scenarios that only dump VCDs for the timing plots (see waves.py)."""
import os
import random

import cocotb
from cocotb.triggers import FallingEdge

from spiv.drv import cycles, drain, env_int, master_init, pulse, push, start_clock, wait_rise
from spiv.models import CLK_PS, AsyncSpiMaster, SpiSlaveModel

W = env_int("SPI_W", 8)
DUT = os.environ.get("SPI_DUT", "")
MODE = env_int("WAVE_MODE", 0)
SCK_RATIO = float(os.environ.get("WAVE_SCK_RATIO", "4.0"))   # clk_i periods per SCK period
JITTER = float(os.environ.get("WAVE_JITTER", "0.03"))        # +- fraction of a half period


@cocotb.test(skip=DUT != "tb_master")
async def test_w_master_mode(dut):
    """Two-word burst in mode WAVE_MODE, clk_div = 2, after a warm-up transfer."""
    cpol, cpha = MODE >> 1, MODE & 1
    start_clock(dut)
    await master_init(dut, cpol=cpol, cpha=cpha, div=2, setup=2, hold=2, inter=1)
    model = SpiSlaveModel(dut.sck_o, dut.miso_i, dut.cs_n_o, 0, W, cpol, cpha, 0, tco_ps=1000)
    model.queue.extend([0x00])
    await push(dut, [0x00])
    await pulse(dut, "start_i")
    await wait_rise(dut.xfer_done_o, 20_000)
    await cycles(dut, 6)
    model.queue.extend([0xC3, 0x5A])
    await push(dut, [0xA5, 0x3D])
    await cycles(dut, 2)
    await pulse(dut, "start_i")
    await wait_rise(dut.xfer_done_o, 20_000)
    await cycles(dut, 6)
    rx = await drain(dut)
    dut._log.info("mode%d master RX %s", MODE, [hex(x) for x in rx])


@cocotb.test(skip=DUT != "tb_master")
async def test_w_master_idle(dut):
    """Reset with CPOL = 1, then two mode 3 transfers: SCK rests high from reset on."""
    start_clock(dut)
    await master_init(dut, cpol=1, cpha=1, div=2, setup=2, hold=2, inter=0)
    model = SpiSlaveModel(dut.sck_o, dut.miso_i, dut.cs_n_o, 0, W, 1, 1, 0)
    model.queue.extend([0x96, 0x96])
    await cycles(dut, 6)
    for _ in range(2):
        await push(dut, [0xA5])
        await pulse(dut, "start_i")
        await wait_rise(dut.xfer_done_o, 20_000)
        await cycles(dut, 8)


@cocotb.test(skip=DUT != "tb_slave")
async def test_w_slave_tight(dut):
    """Slave at SCK = clk / WAVE_SCK_RATIO (+- WAVE_JITTER), mode 0 then mode 3, two-word frames."""
    start_clock(dut)
    drv = AsyncSpiMaster(dut.sck_i, dut.mosi_i, dut.miso_o, dut.cs_n_i, W, random.Random(5))
    dut.rst_ni.value = 0
    for n in ("cpol_i", "cpha_i", "lsb_first_i", "tx_we_i", "tx_wdata_i", "rx_re_i"):
        getattr(dut, n).value = 0
    await cycles(dut, 5)
    dut.rst_ni.value = 1
    await cycles(dut, 3)
    for mode in (0, 3):
        cpol, cpha = mode >> 1, mode & 1
        await FallingEdge(dut.clk_i)
        dut.cpol_i.value = cpol
        dut.cpha_i.value = cpha
        drv.configure(cpol, cpha, 0)
        await drv.set_idle_level()
        await push(dut, [0xC3, 0x96])
        await drv.idle(4 * CLK_PS + 3700)
        half = int(SCK_RATIO * CLK_PS / 2)
        miso, _ = await drv.transaction([0xA5, 0x3C], half_ps=half, css_ps=3 * CLK_PS + 2300, csh_ps=half,
                                        jitter_ps=int(JITTER * half))
        await cycles(dut, 4)
        rx = await drain(dut)
        dut._log.info("mode%d slave RX %s master got %s", mode, [hex(x) for x in rx], [hex(x) for x in miso])
        assert rx == [0xA5, 0x3C] and miso == [0xC3, 0x96], "transfer failed at this SCK ratio"
        await cycles(dut, 6)


async def top_init(dut, mode, div=3, setup=4, hold=4):
    start_clock(dut)
    for n in ("m_lsb_first_i", "m_cs_sel_i", "m_cs_inter_byte_i", "m_start_i", "m_tx_we_i", "m_tx_wdata_i",
              "m_rx_re_i", "s_lsb_first_i", "s_tx_we_i", "s_tx_wdata_i", "s_rx_re_i"):
        getattr(dut, n).value = 0
    dut.m_cpol_i.value = mode >> 1
    dut.m_cpha_i.value = mode & 1
    dut.s_cpol_i.value = mode >> 1
    dut.s_cpha_i.value = mode & 1
    dut.m_clk_div_i.value = div
    dut.m_cs_setup_i.value = setup
    dut.m_cs_hold_i.value = hold
    dut.rst_ni.value = 0
    await cycles(dut, 5)
    dut.rst_ni.value = 1
    await cycles(dut, 3)


@cocotb.test(skip=DUT != "tb_top")
async def test_w_top_lastbit(dut):
    """Repo master -> repo slave, mode 1, 0x3D (last bit 1)."""
    await top_init(dut, 1)
    await push(dut, [0x5A], we="s_tx_we_i", wdata="s_tx_wdata_i")
    await push(dut, [0x3D], we="m_tx_we_i", wdata="m_tx_wdata_i")
    await pulse(dut, "m_start_i")
    await wait_rise(dut.m_xfer_done_o, 20_000)
    await cycles(dut, 4)
    rx = await drain(dut, re="s_rx_re_i", rdata="s_rx_rdata_o", empty="s_rx_empty_o")
    dut._log.info("slave RX %s (sent 0x3d)", [hex(x) for x in rx])
    assert rx == [0x3D]
