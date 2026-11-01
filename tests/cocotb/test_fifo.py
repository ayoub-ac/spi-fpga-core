"""spi_fifo against a cycle-accurate reference model.

The model is written from the contract in the rtl/spi_fifo.sv header:
  * a read is accepted when a word is available (FWFT=0: count != 0,
    FWFT=1: the head register is valid);
  * a write is accepted when the FIFO is not full, or when it is full and
    a read is accepted in the same cycle;
  * FWFT=0: rd_data_o is registered, updated with the popped word on an
    accepted read and held otherwise; empty = (count == 0);
  * FWFT=1: rd_data_o is the head word while empty_o is low; the head is
    loaded from the memory one cycle after it becomes available there;
  * overflow_o / underflow_o flag dropped write / read requests.
Every cycle all outputs are compared with the model.

Configuration from the environment: FIFO_DEPTH, FIFO_WIDTH, FIFO_FWFT, SEED,
FIFO_RANDOM_CYCLES (length of the random scoreboard run, default 20000).
"""
import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

DEPTH = int(os.environ.get("FIFO_DEPTH", "16"))
WIDTH = int(os.environ.get("FIFO_WIDTH", "8"))
FWFT = int(os.environ.get("FIFO_FWFT", "0"))
SEED = int(os.environ.get("SEED", "1"))
MASK = (1 << WIDTH) - 1


def val(sig):
    s = str(sig.value).lower()
    if any(c not in "01" for c in s):
        return None
    return int(s, 2)


class Model:
    """Reference FIFO. In FWFT mode the head register is modelled explicitly."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.mem = deque()       # words in the memory array (FWFT: behind the head)
        self.head = None         # FWFT head register contents (None = invalid)
        self.rd = 0              # rd_data_o
        self.ovf = self.udf = 0

    @property
    def count(self):
        return len(self.mem) + (1 if self.head is not None else 0)

    def avail(self):
        return (self.head is not None) if FWFT else (len(self.mem) > 0)

    def comb(self, wr, rd):
        do_rd = bool(rd) and self.avail()
        do_wr = bool(wr) and (self.count < DEPTH or do_rd)
        return do_wr, do_rd

    def step(self, wr, wd, rd):
        do_wr, do_rd = self.comb(wr, rd)
        self.ovf = int(bool(wr) and not do_wr)
        self.udf = int(bool(rd) and not do_rd)
        if FWFT:
            mem_rd = len(self.mem) > 0 and (self.head is None or do_rd)
            if mem_rd:
                self.head = self.mem.popleft()
            elif do_rd:
                self.head = None
            self.rd = self.head if self.head is not None else self.rd
        else:
            if do_rd:
                self.rd = self.mem.popleft()
        if do_wr:
            self.mem.append(wd)

    def outputs(self):
        empty = (self.head is None) if FWFT else (len(self.mem) == 0)
        return dict(empty=int(empty), full=int(self.count == DEPTH), count=self.count, rd_data=self.rd)


class Env:
    def __init__(self, dut):
        self.dut = dut
        self.m = Model()
        self.cyc = 0
        self.errors = []
        self.has_flags = hasattr(dut, "overflow_o")

    async def start(self):
        Clock(self.dut.clk_i, 10, unit="ns").start(start_high=False)
        self.dut.wr_en_i.value = 0
        self.dut.rd_en_i.value = 0
        self.dut.wr_data_i.value = 0
        self.dut.rst_ni.value = 0
        for _ in range(3):
            await self.cycle(rst=0)

    async def cycle(self, wr=0, wd=0, rd=0, rst=1):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.wr_en_i.value = wr
        d.wr_data_i.value = wd & MASK
        d.rd_en_i.value = rd
        d.rst_ni.value = rst
        await ReadOnly()
        if rst and self.has_flags:
            # strobes are combinational on the inputs of this cycle
            do_wr, do_rd = self.m.comb(wr, rd)
            exp = (int(bool(wr) and not do_wr), int(bool(rd) and not do_rd))
            got = (val(d.overflow_o), val(d.underflow_o))
            if exp != got:
                self.err(f"[wr={wr} rd={rd}] overflow/underflow expected {exp} got {got}")
        await RisingEdge(d.clk_i)
        self.cyc += 1
        if not rst:
            self.m.reset()
        else:
            self.m.step(wr, wd & MASK, rd)
        await ReadOnly()
        exp = self.m.outputs()
        got = dict(empty=val(d.empty_o), full=val(d.full_o), count=val(d.count_o), rd_data=val(d.rd_data_o))
        if exp != got:
            self.err(f"[wr={wr} wd=0x{wd & MASK:x} rd={rd} rst={rst}] expected {exp} got {got}")

    def err(self, msg):
        msg = f"cycle {self.cyc} {msg}"
        self.errors.append(msg)
        if len(self.errors) <= 5:
            self.dut._log.error(msg)

    def finish(self):
        assert not self.errors, f"{len(self.errors)} mismatches; first: {self.errors[0]}"


async def wait_head(e):
    """FWFT: one idle cycle so a word written into an empty FIFO reaches the head."""
    if FWFT:
        await e.cycle()


@cocotb.test()
async def t01_reset_values(dut):
    e = Env(dut)
    await e.start()
    await e.cycle(rst=0)
    await e.cycle()
    e.finish()


@cocotb.test()
async def t02_fill_overflow_drain(dut):
    """Fill to full (flag timing), 3 overflow attempts, drain, 3 underflow attempts."""
    e = Env(dut)
    await e.start()
    rng = random.Random(SEED)
    for _ in range(DEPTH + 3):
        await e.cycle(wr=1, wd=rng.randrange(1 << WIDTH))
    assert val(dut.full_o) == 1 or e.errors, "full not asserted after DEPTH pushes"
    for _ in range(DEPTH + 3):
        await e.cycle(rd=1)
    e.finish()


@cocotb.test()
async def t03_simul_push_pop_at_empty(dut):
    e = Env(dut)
    await e.start()
    await e.cycle(wr=1, wd=0x5A, rd=1)      # pop ignored (empty), push accepted
    await wait_head(e)
    await e.cycle(wr=1, wd=0x3C, rd=1)      # one word: both accepted
    await wait_head(e)
    await e.cycle(rd=1)
    await e.cycle(rd=1)                     # underflow attempt
    e.finish()


@cocotb.test()
async def t04_simul_push_pop_at_full(dut):
    """Full FIFO + write + read in one cycle: both accepted, nothing lost."""
    e = Env(dut)
    await e.start()
    for i in range(DEPTH):
        await e.cycle(wr=1, wd=i + 1)
    await wait_head(e)
    await e.cycle(wr=1, wd=0xEE, rd=1)
    for _ in range(DEPTH + 2):
        await e.cycle(rd=1)
    e.finish()


@cocotb.test()
async def t05_simul_push_pop_in_between(dut):
    e = Env(dut)
    await e.start()
    for i in range(max(1, DEPTH // 2)):
        await e.cycle(wr=1, wd=0x10 + i)
    for i in range(4 * DEPTH):
        await e.cycle(wr=1, wd=0x80 + i, rd=1)
    for _ in range(DEPTH + 2):
        await e.cycle(rd=1)
    e.finish()


@cocotb.test()
async def t06_reset_mid_operation(dut):
    e = Env(dut)
    await e.start()
    for i in range(max(1, DEPTH - 1)):
        await e.cycle(wr=1, wd=0x40 + i)
    await e.cycle(rd=1)
    await e.cycle(wr=1, wd=0x77, rd=1, rst=0)   # activity during reset is ignored
    for i in range(3):
        await e.cycle(wr=1, wd=0xA0 + i)
    for _ in range(5):
        await e.cycle(rd=1)
    e.finish()


@cocotb.test()
async def t07_random_scoreboard(dut):
    """20k cycles of random traffic, including illegal requests and rare resets."""
    e = Env(dut)
    await e.start()
    rng = random.Random(SEED * 1000 + DEPTH * 10 + FWFT)
    phases = [(0.9, 0.1), (0.1, 0.9), (0.5, 0.5), (0.7, 0.7), (1.0, 1.0), (0.3, 0.3)]
    for n in range(int(os.environ.get("FIFO_RANDOM_CYCLES", "20000"))):
        pw, pr = phases[(n // 300) % len(phases)]
        rst = 0 if rng.random() < 0.002 else 1
        await e.cycle(wr=int(rng.random() < pw), wd=rng.randrange(1 << WIDTH),
                      rd=int(rng.random() < pr), rst=rst)
        if len(e.errors) > 50:
            break
    e.finish()


@cocotb.test()
async def t08_legal_traffic_queue(dut):
    """A's check: legal traffic only (never write when full, never read when empty);
    every popped word must match a plain queue model, count must track it."""
    e = Env(dut)
    await e.start()
    rng = random.Random(SEED + 9)
    for _ in range(5000):
        full = val(dut.full_o)
        empty = val(dut.empty_o)
        await e.cycle(wr=int(rng.random() < 0.5 and not full), wd=rng.getrandbits(WIDTH),
                      rd=int(rng.random() < 0.5 and not empty))
    e.finish()
