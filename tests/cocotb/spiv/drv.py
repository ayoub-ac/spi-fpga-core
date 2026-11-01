"""Synchronous 'software side' drivers for the DUT control ports (driven at clk falling edges)."""
import os

from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, with_timeout

from .trace import sig_int


def env_int(name, default):
    return int(os.environ.get(name, default))


def start_clock(dut):
    Clock(dut.clk_i, 10, unit="ns").start(start_high=True)


async def cycles(dut, n):
    for _ in range(n):
        await FallingEdge(dut.clk_i)


# ---------------------------------------------------------------- master
async def master_init(dut, cpol=0, cpha=0, lsb=0, div=0, cs_sel=0, setup=0, hold=0,
                      inter=0, reset_cycles=8):
    dut.rst_ni.value = 0
    master_cfg(dut, cpol, cpha, lsb, div, cs_sel, setup, hold, inter)
    dut.tx_we_i.value = 0
    dut.tx_wdata_i.value = 0
    dut.rx_re_i.value = 0
    dut.start_i.value = 0
    dut.miso_i.value = 0
    await cycles(dut, reset_cycles)
    dut.rst_ni.value = 1
    await cycles(dut, 2)


def master_cfg(dut, cpol, cpha, lsb, div, cs_sel, setup, hold, inter):
    dut.cpol_i.value = cpol
    dut.cpha_i.value = cpha
    dut.lsb_first_i.value = lsb
    dut.clk_div_i.value = div
    dut.cs_sel_i.value = cs_sel
    dut.cs_setup_cycles_i.value = setup
    dut.cs_hold_cycles_i.value = hold
    dut.cs_inter_byte_i.value = inter


async def push(dut, words, we="tx_we_i", wdata="tx_wdata_i"):
    for w in words:
        await FallingEdge(dut.clk_i)
        getattr(dut, we).value = 1
        getattr(dut, wdata).value = w
    await FallingEdge(dut.clk_i)
    getattr(dut, we).value = 0


async def pulse(dut, name):
    await FallingEdge(dut.clk_i)
    getattr(dut, name).value = 1
    await FallingEdge(dut.clk_i)
    getattr(dut, name).value = 0


async def wait_rise(sig, timeout_ns):
    await with_timeout(RisingEdge(sig), timeout_ns, "ns")


async def drain(dut, re="rx_re_i", rdata="rx_rdata_o", empty="rx_empty_o", maxn=10_000):
    out = []
    await FallingEdge(dut.clk_i)
    while sig_int(getattr(dut, empty)) == 0 and len(out) < maxn:
        getattr(dut, re).value = 1
        await FallingEdge(dut.clk_i)
        getattr(dut, re).value = 0
        out.append(sig_int(getattr(dut, rdata)))
        await FallingEdge(dut.clk_i)
    return out
