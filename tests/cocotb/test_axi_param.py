"""spi_axi4lite with non-default parameters: DATA_WIDTH = 64 (register n at byte
offset 8*n), NUM_CS = 1, SPI_DATA_W != 8.

Configuration from the environment: AXI_DW, AXI_NUM_CS, AXI_SPI_W. The checks are
the documented programming model end to end, in loopback (MISO = MOSI).
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge
from cocotbext.axi import AxiLiteBus, AxiLiteMaster

DW = int(os.environ.get("AXI_DW", "32"))
NUM_CS = int(os.environ.get("AXI_NUM_CS", "4"))
SW = int(os.environ.get("AXI_SPI_W", "8"))
NB = DW // 8
OKAY, DECERR = 0, 3
CTRL, STATUS, IRQ_STAT, IRQ_MASK, CLK_DIV, TX_DATA, RX_DATA, FIFO_LVL, ID, VERSION = (
    i * NB for i in range(10))
SCRATCH = [i * NB for i in range(10, 16)]
IRQ_DONE = 4
SMASK = (1 << SW) - 1
CS_IDLE = (1 << NUM_CS) - 1


def val(sig):
    s = str(sig.value).lower()
    return None if any(c not in "01" for c in s) else int(s, 2)


def ctrl_word(start=0, lsb=0, cpol=0, cpha=0, inter=0, cs=0, setup=2, hold=2):
    return start | lsb << 1 | cpol << 2 | cpha << 3 | inter << 4 | cs << 8 | setup << 16 | hold << 24


class Sniffer:
    """Records MOSI bits per CS frame (mode 0, MSB first) and the CS lines used."""

    def __init__(self, dut):
        self.d = dut
        self.frames = []
        self.lines = set()

    async def run(self):
        d = self.d
        cur, prev = None, None
        while True:
            await RisingEdge(d.s_axi_aclk)
            await ReadOnly()
            cs, sck, mosi = val(d.cs_n_o), val(d.sck_o), val(d.mosi_o)
            if None in (cs, sck, mosi):
                prev = None
                continue
            if prev is not None:
                pcs, psck = prev
                if pcs == CS_IDLE and cs != CS_IDLE:
                    cur = []
                    self.lines |= {i for i in range(NUM_CS) if not (cs >> i) & 1}
                elif cur is not None and cs != CS_IDLE and sck == 1 and psck == 0:
                    cur.append(mosi)
                if pcs != CS_IDLE and cs == CS_IDLE and cur is not None:
                    self.frames.append(cur)
                    cur = None
            prev = (cs, sck)

    def words(self):
        out = []
        for bits in self.frames:
            for i in range(0, len(bits) - len(bits) % SW, SW):
                v = 0
                for b in bits[i:i + SW]:
                    v = (v << 1) | b
                out.append(v)
        return out


async def setup(dut):
    Clock(dut.s_axi_aclk, 10, unit="ns").start()
    for s in ("awvalid", "wvalid", "bready", "arvalid", "rready", "awaddr", "wdata", "wstrb", "araddr",
              "awprot", "arprot"):
        getattr(dut, "s_axi_" + s).value = 0
    dut.loopback_i.value = 1
    dut.miso_ext_i.value = 0
    dut.s_axi_aresetn.value = 0
    sn = Sniffer(dut)
    cocotb.start_soon(sn.run())
    await ClockCycles(dut.s_axi_aclk, 5)
    await FallingEdge(dut.s_axi_aclk)
    dut.s_axi_aresetn.value = 1
    axi = AxiLiteMaster(AxiLiteBus.from_prefix(dut, "s_axi"), dut.s_axi_aclk, dut.s_axi_aresetn,
                        reset_active_level=False)
    await ClockCycles(dut.s_axi_aclk, 2)
    return axi, sn


async def wr(axi, a, v):
    r = await axi.write(a, int(v).to_bytes(NB, "little"))
    return int(r.resp)


async def rd(axi, a):
    r = await axi.read(a, NB)
    return int.from_bytes(r.data, "little"), int(r.resp)


async def wait_idle(axi, n=400):
    for _ in range(n):
        v, _ = await rd(axi, STATUS)
        if v & 1 == 0:
            return True
    return False


@cocotb.test()
async def p01_map_and_decode(dut):
    """Reset values at the DATA_WIDTH stride, full-width RW, DECERR off the stride."""
    axi, sn = await setup(dut)
    errs = []
    exp = {CTRL: 0, STATUS: 0b01010, IRQ_MASK: 0, CLK_DIV: 0, TX_DATA: 0, RX_DATA: 0, FIFO_LVL: 0,
           ID: 0x5910017E, VERSION: 0x00010000}
    for a, e in exp.items():
        v, r = await rd(axi, a)
        if (v, r) != (e, OKAY):
            errs.append(f"reset 0x{a:02x}: 0x{v:x} resp {r} expected 0x{e:x}")
    pat = (0xA5A5_5A5A_1234_5678_9ABC_DEF0_0F0F_F0F0 >> (128 - DW)) & ((1 << DW) - 1)
    for a in SCRATCH:
        await wr(axi, a, pat ^ a)
    for a in SCRATCH:
        v, _ = await rd(axi, a)
        if v != pat ^ a:
            errs.append(f"scratch 0x{a:02x}: 0x{v:x} expected 0x{pat ^ a:x}")
    if DW == 64:
        # 32-bit offsets are misaligned on a 64-bit bus
        for a in (0x04, 0x14, 0x1C):
            r = await axi.write(a, (0x77).to_bytes(4, "little"))
            if int(r.resp) != DECERR:
                errs.append(f"64-bit write at 0x{a:02x} resp {int(r.resp)} (expected DECERR)")
        v, _ = await rd(axi, FIFO_LVL)
        if v & 0xFFFF:
            errs.append(f"misaligned write pushed into the TX FIFO (tx_count {v & 0xFFFF})")
    assert not errs, "; ".join(errs[:6])


@cocotb.test()
async def p02_transfer_loopback(dut):
    """TX_DATA x3, CTRL.start (inter-byte): MOSI carries the words on CS line 0,
    RX_DATA returns them in order, IRQ_STAT.done is set and W1C clears irq_o."""
    axi, sn = await setup(dut)
    errs = []
    words = [0x5A & SMASK, (0xC3C3C3C3 >> (32 - min(SW, 32))) & SMASK, 0x1 & SMASK]
    await wr(axi, CLK_DIV, 1)
    await wr(axi, IRQ_MASK, IRQ_DONE)
    for w in words:
        await wr(axi, TX_DATA, w)
    v, _ = await rd(axi, FIFO_LVL)
    if v & 0xFFFF != 3:
        errs.append(f"tx_count {v & 0xFFFF} after 3 TX_DATA writes")
    await wr(axi, CTRL, ctrl_word(start=1, inter=1))
    await wait_idle(axi)
    await ClockCycles(dut.s_axi_aclk, 4)
    if sn.words() != words:
        errs.append(f"MOSI {[hex(x) for x in sn.words()]} expected {[hex(x) for x in words]}")
    if sn.lines != {0}:
        errs.append(f"CS lines used {sn.lines}")
    st, _ = await rd(axi, IRQ_STAT)
    if not st & IRQ_DONE or val(dut.irq_o) != 1:
        errs.append(f"IRQ_STAT 0x{st:x} irq_o {val(dut.irq_o)} after the transfer")
    got = [(await rd(axi, RX_DATA))[0] for _ in range(3)]
    if got != words:
        errs.append(f"RX_DATA {[hex(x) for x in got]} expected {[hex(x) for x in words]}")
    empty, _ = await rd(axi, RX_DATA)
    if empty != 0:
        errs.append(f"RX_DATA on an empty FIFO 0x{empty:x}")
    await wr(axi, IRQ_STAT, IRQ_DONE)
    await ClockCycles(dut.s_axi_aclk, 3)
    if val(dut.irq_o) != 0:
        errs.append("irq_o still high after W1C")
    ctrl, _ = await rd(axi, CTRL)
    if ctrl & 1:
        errs.append("CTRL.start reads back 1")
    assert not errs, "; ".join(errs[:6])
