"""Independent cocotb verification of the AXI4-Lite wrapper (spi_axi4lite +
axi4lite_slave + axi4lite_regfile) and the end-to-end register flow.

Expected behaviour is taken from the register map in the spi_axi4lite.sv
header and README / PORT_DESCRIPTION / SECURITY, never from the repo tests.
Every test asserts the documented behaviour and logs what was observed.
"""
import inspect
import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge
from cocotbext.axi import AxiLiteBus, AxiLiteMaster

OKAY, EXOKAY, SLVERR, DECERR = 0, 1, 2, 3
CTRL, STATUS, IRQ_STAT, IRQ_MASK = 0x00, 0x04, 0x08, 0x0C
CLK_DIV, TX_DATA, RX_DATA, FIFO_LVL = 0x10, 0x14, 0x18, 0x1C
ID, VERSION = 0x20, 0x24
SCRATCH = list(range(0x28, 0x40, 4))
ID_VAL, VERSION_VAL = 0x5910017E, 0x00010100
RO_REGS = [STATUS, RX_DATA, FIFO_LVL, ID, VERSION]
IRQ_TXE, IRQ_RXF, IRQ_DONE, IRQ_ERR = 1, 2, 4, 8


def val(sig):
    s = str(sig.value).lower()
    if any(c not in "01" for c in s):
        return None
    return int(s, 2)


def ctrl_word(start=0, lsb=0, cpol=0, cpha=0, inter=0, cs=0, setup=2, hold=2):
    return (start | lsb << 1 | cpol << 2 | cpha << 3 | inter << 4 |
            cs << 8 | setup << 16 | hold << 24)


# ----------------------------------------------------------------------------
# AXI4-Lite protocol monitor (slave-side rules), sampled mid-cycle.
# ----------------------------------------------------------------------------
class AxiMon:
    NAMES = ["awvalid", "awready", "awaddr", "wvalid", "wready", "wdata", "wstrb",
             "bvalid", "bready", "bresp", "arvalid", "arready", "araddr",
             "rvalid", "rready", "rdata", "rresp"]
    OUTS = ["awready", "wready", "bvalid", "bresp", "arready", "rvalid", "rdata", "rresp"]

    def __init__(self, dut):
        self.d = dut
        self.prev = None
        self.errs = []
        self.cycle = 0
        self.events = []   # (cycle, channel) handshake log
        self.reset_counts()

    def reset_counts(self):
        self.n = dict(aw=0, w=0, b=0, ar=0, r=0)

    def err(self, m):
        m = f"[AXI-MON cycle {self.cycle}] {m}"
        self.errs.append(m)
        if len(self.errs) < 10:
            self.d._log.error(m)

    async def run(self):
        d = self.d
        while True:
            await FallingEdge(d.s_axi_aclk)
            await ReadOnly()
            self.cycle += 1
            cur = {k: val(getattr(d, "s_axi_" + k)) for k in self.NAMES}
            if val(d.s_axi_aresetn) != 1:
                if self.prev is not None or self.cycle > 1:
                    if cur["bvalid"] != 0 or cur["rvalid"] != 0:
                        self.err(f"BVALID/RVALID not low during reset: {cur['bvalid']}/{cur['rvalid']}")
                self.prev = None
                self.reset_counts()
                continue
            for k in self.OUTS:
                if cur[k] is None:
                    self.err(f"X/Z on s_axi_{k}")
            p = self.prev
            if p is not None:
                for ch, v, r in (("aw", "awvalid", "awready"), ("w", "wvalid", "wready"),
                                 ("b", "bvalid", "bready"), ("ar", "arvalid", "arready"),
                                 ("r", "rvalid", "rready")):
                    if p[v] and p[r]:
                        self.n[ch] += 1
                        self.events.append((self.cycle, ch))
                if p["bvalid"] and not p["bready"]:
                    if not cur["bvalid"] or cur["bresp"] != p["bresp"]:
                        self.err("BVALID dropped or BRESP changed before BREADY")
                if p["rvalid"] and not p["rready"]:
                    if not cur["rvalid"] or cur["rdata"] != p["rdata"] or cur["rresp"] != p["rresp"]:
                        self.err("RVALID dropped or RDATA/RRESP changed before RREADY")
                if p["awvalid"] and not p["awready"] and (not cur["awvalid"] or cur["awaddr"] != p["awaddr"]):
                    self.err("(master) AWVALID dropped / AWADDR changed")
                if p["wvalid"] and not p["wready"] and (not cur["wvalid"] or cur["wdata"] != p["wdata"]):
                    self.err("(master) WVALID dropped / WDATA changed")
                if p["arvalid"] and not p["arready"] and (not cur["arvalid"] or cur["araddr"] != p["araddr"]):
                    self.err("(master) ARVALID dropped / ARADDR changed")
            new_b = cur["bvalid"] and (p is None or not p["bvalid"] or p["bready"])
            if new_b and not (self.n["aw"] > self.n["b"] and self.n["w"] > self.n["b"]):
                self.err(f"BVALID without accepted AW+W (aw={self.n['aw']} w={self.n['w']} b={self.n['b']})")
            new_r = cur["rvalid"] and (p is None or not p["rvalid"] or p["rready"])
            if new_r and not self.n["ar"] > self.n["r"]:
                self.err(f"RVALID without accepted AR (ar={self.n['ar']} r={self.n['r']})")
            if self.n["b"] > min(self.n["aw"], self.n["w"]) or self.n["r"] > self.n["ar"]:
                self.err(f"more responses than requests {self.n}")
            self.prev = cur


# ----------------------------------------------------------------------------
# SPI pin sniffer + optional slave model (functional, mode-aware).
# ----------------------------------------------------------------------------
class SpiModel:
    def __init__(self, dut, width=8):
        self.d = dut
        self.w = width
        self.cpol = self.cpha = self.lsb = 0
        self.drive = False
        self.miso_q = deque()
        self.frames = []
        self.cur = None
        self.prev = None
        self.cycle = 0
        self.idle_sck_edges = 0
        self.idle_sck_levels = set()
        self.cs_multi_low = 0
        self.x_after_reset = 0
        self._bits = []

    def _next_bit(self):
        if not self._bits:
            b = self.miso_q.popleft() if self.miso_q else 0xFF
            rng = range(self.w) if self.lsb else range(self.w - 1, -1, -1)
            self._bits = [(b >> i) & 1 for i in rng]
        return self._bits.pop(0)

    def bytes_of(self, frame):
        bits = frame["mosi"]
        out = []
        for i in range(0, len(bits) - len(bits) % self.w, self.w):
            chunk = bits[i:i + self.w]
            v = 0
            for j, bit in enumerate(chunk):
                v |= bit << (j if self.lsb else self.w - 1 - j)
            out.append(v)
        return out

    async def run(self):
        d = self.d
        while True:
            await RisingEdge(d.s_axi_aclk)
            await ReadOnly()
            self.cycle += 1
            cs, sck, mosi = val(d.cs_n_o), val(d.sck_o), val(d.mosi_o)
            if val(d.s_axi_aresetn) == 1 and None in (cs, sck, mosi, val(d.irq_o)):
                self.x_after_reset += 1
            if None in (cs, sck, mosi) or val(d.s_axi_aresetn) != 1:
                self.prev = None
                continue
            p, self.prev = self.prev, (cs, sck, mosi)
            if p is None:
                continue
            pcs, psck, pmosi = p
            if bin(cs ^ 0xF).count("1") > 1:
                self.cs_multi_low += 1
            new_miso = None
            active = cs != 0xF
            if pcs == 0xF and active:
                self.cur = dict(cs=[i for i in range(4) if not (cs >> i) & 1], mosi=[],
                                start=self.cycle, edges=0, sck_at_fall=psck)
                self._bits = []
                if self.cpha == 0:
                    new_miso = self._next_bit()
            elif active and self.cur is not None and sck != psck:
                self.cur["edges"] += 1
                leading = sck != self.cpol
                sample = leading if self.cpha == 0 else not leading
                if sample:
                    self.cur["mosi"].append(pmosi)
                else:
                    new_miso = self._next_bit()
            elif not active:
                self.idle_sck_levels.add(sck)
                if sck != psck and pcs == 0xF:
                    self.idle_sck_edges += 1
            if pcs != 0xF and cs == 0xF and self.cur is not None:
                self.cur["end"] = self.cycle
                self.frames.append(self.cur)
                self.cur = None
            if new_miso is not None and self.drive:
                await FallingEdge(d.s_axi_aclk)
                d.miso_ext_i.value = new_miso


# ----------------------------------------------------------------------------
# Testbench helpers
# ----------------------------------------------------------------------------
class TB:
    def __init__(self, dut):
        self.d = dut
        self.clk = dut.s_axi_aclk
        self.errs = []
        self.log = dut._log

    def soft(self, cond, msg):
        if not cond:
            self.errs.append(msg)
            self.log.error("CHECK FAILED: " + msg)
        return cond

    async def start(self, loopback=1, master=True):
        d = self.d
        c = Clock(self.clk, 10, unit="ns")
        r = c.start()
        if inspect.iscoroutine(r):
            cocotb.start_soon(r)
        for s in ("awvalid", "wvalid", "bready", "arvalid", "rready", "awaddr", "wdata",
                  "wstrb", "araddr", "awprot", "arprot"):
            getattr(d, "s_axi_" + s).value = 0
        d.loopback_i.value = loopback
        d.miso_ext_i.value = 0
        d.s_axi_aresetn.value = 0
        self.mon = AxiMon(d)
        cocotb.start_soon(self.mon.run())
        self.spi = SpiModel(d)
        cocotb.start_soon(self.spi.run())
        await ClockCycles(self.clk, 5)
        await FallingEdge(self.clk)
        d.s_axi_aresetn.value = 1
        if master:
            self.axi = AxiLiteMaster(AxiLiteBus.from_prefix(d, "s_axi"), self.clk,
                                     d.s_axi_aresetn, reset_active_level=False)
        await ClockCycles(self.clk, 2)

    def finish(self):
        self.soft(self.spi.x_after_reset == 0, f"X/Z on SPI pins or irq_o after reset in {self.spi.x_after_reset} cycles")
        self.soft(self.spi.cs_multi_low == 0, f"more than one CS low in {self.spi.cs_multi_low} cycles")
        allerrs = self.errs + self.mon.errs
        assert not allerrs, f"{len(allerrs)} check(s) failed: " + " || ".join(allerrs[:6])

    # cocotbext-axi accessors
    async def wr(self, addr, data):
        r = await self.axi.write(addr, int(data).to_bytes(4, "little"))
        return int(r.resp)

    async def rd(self, addr):
        r = await self.axi.read(addr, 4)
        return int.from_bytes(r.data, "little"), int(r.resp)

    async def rdv(self, addr):
        v, resp = await self.rd(addr)
        self.soft(resp == OKAY, f"read 0x{addr:02x} RRESP={resp} expected OKAY")
        return v

    async def wrv(self, addr, data):
        resp = await self.wr(addr, data)
        self.soft(resp == OKAY, f"write 0x{addr:02x} BRESP={resp} expected OKAY")

    # raw single-beat channel drivers (independent AW/W timing, back-pressure)
    async def raw_write(self, addr, data, strb=0xF, aw_delay=0, w_delay=0, b_delay=0, max_cycles=300):
        d, clk = self.d, self.clk
        aw_done = w_done = False
        b_seen = 0
        resp = None
        trace = []
        for cyc in range(max_cycles):
            await FallingEdge(clk)
            d.s_axi_awvalid.value = int(not aw_done and cyc >= aw_delay)
            d.s_axi_awaddr.value = addr
            d.s_axi_wvalid.value = int(not w_done and cyc >= w_delay)
            d.s_axi_wdata.value = data
            d.s_axi_wstrb.value = strb
            bv = val(d.s_axi_bvalid)
            if bv:
                b_seen += 1
            bready = int(bool(bv) and b_seen > b_delay)
            d.s_axi_bready.value = bready
            await ReadOnly()
            awhs = val(d.s_axi_awvalid) and val(d.s_axi_awready)
            whs = val(d.s_axi_wvalid) and val(d.s_axi_wready)
            bhs = val(d.s_axi_bvalid) and bready
            trace.append((cyc, val(d.s_axi_awready), val(d.s_axi_wready), val(d.s_axi_bvalid)))
            if bhs:
                resp = val(d.s_axi_bresp)
            await RisingEdge(clk)
            aw_done |= bool(awhs)
            w_done |= bool(whs)
            if bhs:
                break
        await FallingEdge(clk)
        d.s_axi_awvalid.value = 0
        d.s_axi_wvalid.value = 0
        d.s_axi_bready.value = 0
        return resp, trace

    async def raw_read(self, addr, ar_delay=0, r_delay=0, max_cycles=300):
        d, clk = self.d, self.clk
        ar_done = False
        r_seen = 0
        out = (None, None)
        for cyc in range(max_cycles):
            await FallingEdge(clk)
            d.s_axi_arvalid.value = int(not ar_done and cyc >= ar_delay)
            d.s_axi_araddr.value = addr
            rv = val(d.s_axi_rvalid)
            if rv:
                r_seen += 1
            rready = int(bool(rv) and r_seen > r_delay)
            d.s_axi_rready.value = rready
            await ReadOnly()
            arhs = val(d.s_axi_arvalid) and val(d.s_axi_arready)
            rhs = val(d.s_axi_rvalid) and rready
            if rhs:
                out = (val(d.s_axi_rdata), val(d.s_axi_rresp))
            await RisingEdge(clk)
            ar_done |= bool(arhs)
            if rhs:
                break
        await FallingEdge(clk)
        d.s_axi_arvalid.value = 0
        d.s_axi_rready.value = 0
        return out

    async def wait_frames(self, n, timeout=4000):
        for _ in range(timeout):
            if len(self.spi.frames) >= n:
                return True
            await RisingEdge(self.clk)
        return False

    async def wait_not_busy(self, timeout=200):
        for _ in range(timeout):
            if (await self.rdv(STATUS)) & 1 == 0:
                return True
        return False


# ----------------------------------------------------------------------------
# Register map tests
# ----------------------------------------------------------------------------
@cocotb.test()
async def t01_reset_values_and_ids(dut):
    """Reset value of every register, RRESP OKAY on every mapped address."""
    tb = TB(dut)
    await tb.start()
    exp = {CTRL: 0, STATUS: 0b01010, IRQ_MASK: 0, CLK_DIV: 0, TX_DATA: 0,
           RX_DATA: 0, FIFO_LVL: 0, ID: ID_VAL, VERSION: VERSION_VAL}
    exp.update({a: 0 for a in SCRATCH})
    for a in range(0, 0x40, 4):
        v, resp = await tb.rd(a)
        tb.log.info(f"reset read 0x{a:02x} = 0x{v:08x} resp={resp}")
        tb.soft(resp == OKAY, f"0x{a:02x} RRESP={resp}")
        if a in exp:
            tb.soft(v == exp[a], f"reset 0x{a:02x}: got 0x{v:08x} expected 0x{exp[a]:08x}")
    tb.finish()


@cocotb.test()
async def t02_rw_registers_patterns(dut):
    """RW registers hold all 32 bits; scratch walking-ones."""
    tb = TB(dut)
    await tb.start()
    pats = [0xFFFFFFFF, 0x00000000, 0xA5A5A5A5, 0x5A5A5A5A, 0x12345678]
    for a in [CTRL, IRQ_MASK, CLK_DIV, TX_DATA] + SCRATCH:
        for p in pats:
            await tb.wrv(a, p)
            v = await tb.rdv(a)
            exp = p & ~1 if a == CTRL else p      # CTRL.start is not stored (reads 0)
            tb.soft(v == exp, f"RW 0x{a:02x}: wrote 0x{p:08x} read 0x{v:08x} expected 0x{exp:08x}")
            if a == TX_DATA:
                await tb.wait_not_busy()
    for a in SCRATCH:
        for b in range(32):
            await tb.wrv(a, 1 << b)
        for b in (0, 31):
            await tb.wrv(a, 1 << b)
            tb.soft(await tb.rdv(a) == 1 << b, f"walking-1 0x{a:02x} bit {b}")
    # no aliasing between scratch registers
    for i, a in enumerate(SCRATCH):
        await tb.wrv(a, 0xC0DE0000 | i)
    for i, a in enumerate(SCRATCH):
        v = await tb.rdv(a)
        tb.soft(v == 0xC0DE0000 | i, f"alias check 0x{a:02x} got 0x{v:08x}")
    tb.finish()


@cocotb.test()
async def t03_read_only_registers(dut):
    """Writes to RO registers: BRESP OKAY (regfile doc) and value unchanged."""
    tb = TB(dut)
    await tb.start()
    before = {a: await tb.rdv(a) for a in RO_REGS}
    for a in RO_REGS:
        resp = await tb.wr(a, 0xFFFFFFFF)
        tb.soft(resp == OKAY, f"RO write 0x{a:02x} BRESP={resp}")
        resp = await tb.wr(a, 0x00000000)
    after = {a: await tb.rdv(a) for a in RO_REGS}
    for a in RO_REGS:
        tb.soft(before[a] == after[a], f"RO 0x{a:02x} changed 0x{before[a]:08x}->0x{after[a]:08x}")
    tb.finish()


@cocotb.test()
async def t04_wstrb_byte_enables(dut):
    """Aligned AWADDR, every WSTRB value, on a scratch register and CLK_DIV."""
    tb = TB(dut)
    await tb.start(master=False)
    for a in (0x28, CLK_DIV, IRQ_MASK):
        for strb in range(16):
            r, _ = await tb.raw_write(a, 0x11223344, 0xF)
            r, _ = await tb.raw_write(a, 0xAABBCCDD, strb)
            tb.soft(r == OKAY, f"0x{a:02x} strb={strb:04b} BRESP={r}")
            exp = 0
            for byte in range(4):
                src = 0xAABBCCDD if (strb >> byte) & 1 else 0x11223344
                exp |= src & (0xFF << (8 * byte))
            v, rr = await tb.raw_read(a)
            tb.soft(v == exp, f"0x{a:02x} strb={strb:04b}: got 0x{v:08x} expected 0x{exp:08x}")
    tb.finish()


@cocotb.test()
async def t05_unaligned_and_unmapped(dut):
    """Unaligned (byte-lane) and unmapped addresses: response and side effects."""
    tb = TB(dut)
    await tb.start(master=False)
    await tb.raw_write(0x28, 0x11223344)
    await tb.raw_write(CTRL, 0x00000000)
    results = []
    for a in (0x01, 0x02, 0x03, 0x29, 0x2A, 0x2B, 0x40, 0x44, 0x7C, 0x80, 0xA8, 0xFC):
        br, _ = await tb.raw_write(a, 0xDEADBEEF, 0xF)
        rd, rr = await tb.raw_read(a)
        results.append((a, br, rr, rd))
        tb.log.info(f"addr 0x{a:02x}: BRESP={br} RRESP={rr} RDATA=0x{rd:08x}")
        tb.soft(br == DECERR and rr == DECERR, f"0x{a:02x}: BRESP={br} RRESP={rr} (RTL header: DECERR)")
        tb.soft(rd == 0, f"0x{a:02x}: DECERR RDATA=0x{rd:08x} (expected 0)")
    v, _ = await tb.raw_read(0x28)
    tb.soft(v == 0x11223344, f"scratch 0x28 modified by unaligned/unmapped writes: 0x{v:08x}")
    for a in (CTRL, CLK_DIV, IRQ_MASK):
        v, _ = await tb.raw_read(a)
        tb.soft(v == 0, f"reg 0x{a:02x} aliased by out-of-range write: 0x{v:08x}")
    # A sub-word store from a CPU/bridge: byte address 0x29 + WSTRB=0010 (lane 1)
    br, _ = await tb.raw_write(0x29, 0x0000AB00, 0x2)
    v, _ = await tb.raw_read(0x28)
    tb.log.info(f"byte store @0x29 WSTRB=0010 -> BRESP={br}, reg 0x28 = 0x{v:08x}")
    tb.soft(br == DECERR, "byte store to 0x29 did not return DECERR")
    tb.finish()


@cocotb.test()
async def t06_write_channel_orderings_and_backpressure(dut):
    """AW before W, W before AW, simultaneous; BREADY held off; second write while B pending."""
    tb = TB(dut)
    await tb.start(master=False)
    cases = [(0, 0), (0, 1), (1, 0), (0, 4), (4, 0), (3, 3), (0, 9), (9, 0)]
    n = 0
    for (awd, wd) in cases:
        for bd in (0, 1, 6):
            n += 1
            data = 0x5A000000 | n
            r, trace = await tb.raw_write(0x2C, data, 0xF, awd, wd, bd)
            tb.soft(r == OKAY, f"aw_delay={awd} w_delay={wd} b_delay={bd}: BRESP={r}")
            v, _ = await tb.raw_read(0x2C)
            tb.soft(v == data, f"aw_delay={awd} w_delay={wd} b_delay={bd}: read 0x{v:08x} != 0x{data:08x}")
    # Second write presented while the first B is held off by the master
    d = tb.d
    first = cocotb.start_soon(tb.raw_write(0x30, 0x1111, 0xF, 0, 0, 12))
    await ClockCycles(tb.clk, 3)
    aw_ready_while_b_pending = val(d.s_axi_awready)
    w_ready_while_b_pending = val(d.s_axi_wready)
    await first
    r, _ = await tb.raw_write(0x34, 0x2222)
    v30, _ = await tb.raw_read(0x30)
    v34, _ = await tb.raw_read(0x34)
    tb.log.info(f"while B pending: AWREADY={aw_ready_while_b_pending} WREADY={w_ready_while_b_pending}")
    tb.soft(v30 == 0x1111 and v34 == 0x2222, f"pending-B sequence lost data: 0x{v30:x} 0x{v34:x}")
    tb.log.info(f"monitor handshake counts: {tb.mon.n}")
    tb.soft(tb.mon.n["aw"] == tb.mon.n["w"] == tb.mon.n["b"], f"AW/W/B count mismatch {tb.mon.n}")
    tb.finish()


@cocotb.test()
async def t07_read_backpressure(dut):
    tb = TB(dut)
    await tb.start(master=False)
    await tb.raw_write(0x38, 0xCAFEF00D)
    for ard in (0, 2):
        for rdl in (0, 1, 5, 25):
            v, rr = await tb.raw_read(0x38, ard, rdl)
            tb.soft(v == 0xCAFEF00D and rr == OKAY, f"ar_delay={ard} r_delay={rdl}: 0x{v:08x} resp={rr}")
            v, rr = await tb.raw_read(ID, ard, rdl)
            tb.soft(v == ID_VAL and rr == OKAY, f"ID with r_delay={rdl}: 0x{v:08x}")
    await ClockCycles(tb.clk, 2)
    tb.soft(tb.mon.n["ar"] == tb.mon.n["r"], f"AR/R count mismatch {tb.mon.n}")
    tb.finish()


@cocotb.test()
async def t08_back_to_back_throughput(dut):
    """Pipelined writes/reads (cocotbext-axi): data integrity plus the latencies in
    the rtl/axi4lite_slave.sv header: B 2 cycles after the later of AW / W, R 1 cycle
    after AR, the next AW / AR accepted 1 cycle after B / R. AXI_CLAIMS=orig checks
    the v1.0 header text instead (B and R after 1 cycle, next request same cycle)."""
    tb = TB(dut)
    await tb.start()
    N = 24
    vals = [random.getrandbits(32) for _ in range(N)]
    e0 = len(tb.mon.events)
    tasks = [cocotb.start_soon(tb.wr(SCRATCH[i % 6], vals[i])) for i in range(N)]
    for t in tasks:
        tb.soft(await t == OKAY, "b2b write resp")
    wev = tb.mon.events[e0:]
    e1 = len(tb.mon.events)
    rtasks = [cocotb.start_soon(tb.rd(ID)) for _ in range(N)]
    for t in rtasks:
        v, rr = await t
        tb.soft(v == ID_VAL, "b2b ID read")
    rev = tb.mon.events[e1:]
    for i in range(6):
        v, _ = await tb.rd(SCRATCH[i])
        last = [vals[j] for j in range(N) if j % 6 == i][-1]
        tb.soft(v == last, f"b2b readback scratch{i}: 0x{v:08x} != 0x{last:08x}")

    def cyc(ev, ch):
        return [c for c, k in ev if k == ch]
    aw, w, b = cyc(wev, "aw"), cyc(wev, "w"), cyc(wev, "b")
    ar, r = cyc(rev, "ar"), cyc(rev, "r")
    blat = [b[i] - max(aw[i], w[i]) for i in range(len(b))]
    rlat = [r[i] - ar[i] for i in range(len(r))]
    b2aw = [aw[i + 1] - b[i] for i in range(len(b) - 1)]
    r2ar = [ar[i + 1] - r[i] for i in range(len(r) - 1)]
    wper = (b[-1] - b[0]) / (len(b) - 1)
    rper = (r[-1] - r[0]) / (len(r) - 1)
    tb.log.info(f"THROUGHPUT writes {wper:.2f} cyc/txn, reads {rper:.2f} cyc/txn; "
                f"AW/W->B handshake latency {sorted(set(blat))}; AR->R latency {sorted(set(rlat))}; "
                f"B->next AW {sorted(set(b2aw))}; R->next AR {sorted(set(r2ar))}")
    if os.environ.get("AXI_CLAIMS", "v11") == "orig":
        tb.soft(min(blat) <= 1, f"B latency after AW+W = {min(blat)} cycles (header: 1)")
        tb.soft(min(rlat) <= 1, f"R latency after AR = {min(rlat)} cycles (header: 1)")
        tb.soft(min(b2aw) == 0, f"next AW accepted {min(b2aw)} cycle(s) after B (header: same cycle)")
        tb.soft(min(r2ar) == 0, f"next AR accepted {min(r2ar)} cycle(s) after R (header: same cycle)")
    else:   # v1.1 header text (measured)
        tb.soft(set(blat) == {2} and set(rlat) == {1}, f"latency B={set(blat)} R={set(rlat)}")
        tb.soft(set(b2aw) == {1} and set(r2ar) == {1}, f"B->AW {set(b2aw)} R->AR {set(r2ar)}")
    tb.finish()


@cocotb.test()
async def t09_ctrl_field_mapping(dut):
    """CTRL/CLK_DIV fields reach the master configuration inputs (white-box)."""
    tb = TB(dut)
    await tb.start()
    u = tb.d.u_dut
    for cpol in (0, 1):
        for cpha in (0, 1):
            for lsb in (0, 1):
                for inter in (0, 1):
                    cs = random.randrange(4)
                    setup, hold = random.randrange(256), random.randrange(256)
                    await tb.wrv(CTRL, ctrl_word(0, lsb, cpol, cpha, inter, cs, setup, hold))
                    await tb.wrv(CLK_DIV, 0x10000 | (setup * 7) & 0xFFFF)
                    got = dict(cpol=val(u.cfg_cpol), cpha=val(u.cfg_cpha), lsb=val(u.cfg_lsb_first),
                               inter=val(u.cfg_inter_byte), cs=val(u.cfg_cs_sel),
                               setup=val(u.cfg_cs_setup), hold=val(u.cfg_cs_hold), div=val(u.cfg_clk_div))
                    exp = dict(cpol=cpol, cpha=cpha, lsb=lsb, inter=inter, cs=cs, setup=setup, hold=hold,
                               div=(setup * 7) & 0xFFFF)
                    tb.soft(got == exp, f"field map: exp {exp} got {got}")
    tb.finish()


# ----------------------------------------------------------------------------
# End-to-end through the register map (documented programming model)
# ----------------------------------------------------------------------------
@cocotb.test()
async def t10_e2e_documented_flow(dut):
    """CLK_DIV, IRQ_MASK(done), TX_DATA=0xA5, CTRL.start=1 -> one SPI frame
    carrying 0xA5, done IRQ, IRQ_STAT.done=1, RX_DATA=0xA5 (loopback), W1C clears irq_o."""
    tb = TB(dut)
    await tb.start(loopback=1)
    await tb.wrv(CLK_DIV, 1)
    await tb.wrv(IRQ_MASK, IRQ_DONE)
    await tb.wrv(TX_DATA, 0xA5)
    lvl = await tb.rdv(FIFO_LVL)
    tb.soft(lvl & 0xFFFF == 1, f"FIFO_LVL.tx_count after one TX_DATA write = {lvl & 0xFFFF}")
    await tb.wrv(CTRL, ctrl_word(start=1))
    started = await tb.wait_frames(1, 600)
    tb.soft(started, "no SPI frame (CS never asserted) after CTRL.start=1")
    if started:
        mo = tb.spi.bytes_of(tb.spi.frames[0])
        tb.soft(mo == [0xA5], f"MOSI frame bytes {[hex(x) for x in mo]} expected ['0xa5']")
    await tb.wait_not_busy()
    await ClockCycles(tb.clk, 5)
    tb.soft(val(tb.d.irq_o) == 1, f"irq_o={val(tb.d.irq_o)} after done with IRQ_MASK=done")
    st = await tb.rdv(IRQ_STAT)
    tb.soft(st & IRQ_DONE, f"IRQ_STAT=0x{st:x}: done bit not set")
    rx = await tb.rdv(RX_DATA)
    tb.soft(rx == 0xA5, f"RX_DATA=0x{rx:02x} expected 0xa5 (loopback)")
    lvl = await tb.rdv(FIFO_LVL)
    tb.soft(lvl >> 16 == 0, f"rx_count after reading RX_DATA = {lvl >> 16}")
    await tb.wrv(IRQ_STAT, IRQ_DONE)
    await ClockCycles(tb.clk, 3)
    tb.soft(val(tb.d.irq_o) == 0, "irq_o still high after W1C of IRQ_STAT.done")
    tb.log.info(f"frames={len(tb.spi.frames)} irq_o={val(tb.d.irq_o)} IRQ_STAT=0x{st:x} RX=0x{rx:x}")
    tb.finish()


@cocotb.test()
async def t11_start_bit_semantics(dut):
    """CTRL.start: one write of 1 must start one transfer; writing 0 must not."""
    tb = TB(dut)
    await tb.start(loopback=1)
    await tb.wrv(TX_DATA, 0x11)
    await tb.wrv(TX_DATA, 0x22)
    await tb.wrv(TX_DATA, 0x33)
    obs = []
    await tb.wrv(CTRL, ctrl_word(start=1))
    await ClockCycles(tb.clk, 150)
    obs.append(("CTRL=start(1st write)", len(tb.spi.frames)))
    tb.soft(len(tb.spi.frames) == 1, f"after 1st CTRL.start=1 write: {len(tb.spi.frames)} frames (expected 1)")
    await tb.wrv(CTRL, ctrl_word(start=0))
    await ClockCycles(tb.clk, 150)
    obs.append(("CTRL=0", len(tb.spi.frames)))
    tb.soft(len(tb.spi.frames) == obs[0][1], f"writing CTRL=0 started a transfer (frames {obs[0][1]}->{len(tb.spi.frames)})")
    await tb.wrv(CTRL, ctrl_word(start=0, cpol=0, cs=1))
    await ClockCycles(tb.clk, 150)
    obs.append(("CTRL=0 (cs_sel=1)", len(tb.spi.frames)))
    tb.soft(len(tb.spi.frames) == 1, "writing CTRL with start=0 started a transfer")
    v = await tb.rdv(CTRL)
    obs.append(("CTRL readback", hex(v)))
    tb.soft(v == ctrl_word(start=0, cpol=0, cs=1), f"CTRL readback 0x{v:08x}")
    # read-modify-write after a start: START reads 0, so no second transfer
    await tb.wrv(CTRL, ctrl_word(start=1, cs=1))
    await ClockCycles(tb.clk, 150)
    n1 = len(tb.spi.frames)
    v = await tb.rdv(CTRL)
    tb.soft(v & 1 == 0, f"CTRL.start reads back as 1 (0x{v:08x})")
    await tb.wrv(CTRL, v | (1 << 1))            # set lsb_first by RMW
    await ClockCycles(tb.clk, 150)
    obs.append(("after RMW", len(tb.spi.frames)))
    tb.soft(n1 == 2 and len(tb.spi.frames) == n1, f"frames start/RMW {n1}/{len(tb.spi.frames)} (expected 2/2)")
    tb.log.info(f"start-bit observations: {obs}")
    tb.finish()


@cocotb.test()
async def t17_write_strobes_on_side_effects(dut):
    """CTRL.start needs byte lane 0; TX_DATA pushes the strobe-merged register value
    and only when a byte lane of the SPI word is written."""
    tb = TB(dut)
    await tb.start(loopback=1, master=False)
    await tb.raw_write(TX_DATA, 0x11)
    await tb.raw_write(CTRL, ctrl_word(start=1), strb=0xE)      # lane 0 not written
    await ClockCycles(tb.clk, 150)
    tb.soft(len(tb.spi.frames) == 0, "CTRL write without WSTRB[0] started a transfer")
    v, _ = await tb.raw_read(FIFO_LVL)
    tb.soft(v & 0xFFFF == 1, f"tx_count {v & 0xFFFF} after one TX_DATA write")
    await tb.raw_write(TX_DATA, 0xAB00, strb=0x2)               # lane 1 only: no SPI byte written
    v, _ = await tb.raw_read(FIFO_LVL)
    tb.soft(v & 0xFFFF == 1, f"TX_DATA write without lane 0 pushed a word (tx_count {v & 0xFFFF})")
    await tb.raw_write(TX_DATA, 0x00000077, strb=0x1)           # lane 0: pushes 0x77
    v, _ = await tb.raw_read(FIFO_LVL)
    tb.soft(v & 0xFFFF == 2, f"tx_count {v & 0xFFFF} after the second push")
    await tb.raw_write(CTRL, ctrl_word(start=1, inter=1), strb=0x1)
    await ClockCycles(tb.clk, 600)
    mo = [x for f in tb.spi.frames for x in tb.spi.bytes_of(f)]
    tb.soft(mo == [0x11, 0x77], f"MOSI bytes {[hex(x) for x in mo]} (expected 0x11 0x77)")
    v, _ = await tb.raw_read(TX_DATA)
    tb.soft(v == 0xAB77, f"TX_DATA readback 0x{v:x} (strobe-merged value 0xab77)")
    tb.finish()


@cocotb.test()
async def t12_tx_data_reaches_mosi(dut):
    """Bytes written to TX_DATA must appear on MOSI in order (inter-byte frame)."""
    tb = TB(dut)
    await tb.start(loopback=1)
    await tb.wrv(CTRL, ctrl_word(start=0, inter=1))
    sent = [0x11, 0x22, 0x33, 0x44]
    for b in sent:
        await tb.wrv(TX_DATA, b)
    await tb.wrv(CTRL, ctrl_word(start=1, inter=1))
    tb.soft(await tb.wait_frames(1, 2000), "no SPI frame after one CTRL.start write")
    await tb.wait_not_busy()
    mo = [x for f in tb.spi.frames for x in tb.spi.bytes_of(f)]
    tb.log.info(f"MOSI bytes observed: {[hex(x) for x in mo]} (written {[hex(x) for x in sent]})")
    tb.soft(mo == sent, f"MOSI {[hex(x) for x in mo]} != TX_DATA writes {[hex(x) for x in sent]}")
    tb.finish()


@cocotb.test()
async def t13_rx_data_read_returns_head(dut):
    """External slave returns 0xC3,0x5A,0x96; RX_DATA reads must return them in order."""
    tb = TB(dut)
    await tb.start(loopback=0)
    tb.spi.drive = True
    slave = [0xC3, 0x5A, 0x96]
    tb.spi.miso_q.extend(slave)
    await tb.wrv(CLK_DIV, 2)
    await tb.wrv(CTRL, ctrl_word(start=0, inter=1, setup=4, hold=2))
    for b in (0x01, 0x02, 0x03, 0x04):
        await tb.wrv(TX_DATA, b)
    await tb.wrv(CTRL, ctrl_word(start=1, inter=1, setup=4, hold=2))
    if not await tb.wait_frames(1, 400):
        await tb.wrv(CTRL, ctrl_word(start=1, inter=1, setup=4, hold=2))
        await tb.wait_frames(1, 3000)
    await tb.wait_not_busy()
    lvl = await tb.rdv(FIFO_LVL)
    nrx = lvl >> 16
    reads = []
    for _ in range(nrx + 1):
        reads.append(await tb.rdv(RX_DATA))
    tb.log.info(f"rx_count={nrx}; RX_DATA reads: {[hex(x) for x in reads]}; slave sent {[hex(x) for x in slave]}")
    tb.soft(reads[:3] == slave, f"RX_DATA reads {[hex(x) for x in reads[:3]]} != slave bytes {[hex(x) for x in slave]}")
    tb.finish()


@cocotb.test()
async def t14_irq_status_w1c_and_mask(dut):
    """IRQ_STAT readback, W1C clearing and irq_o gating by IRQ_MASK."""
    tb = TB(dut)
    await tb.start(loopback=1)
    d = tb.d
    tb.soft(val(d.irq_o) == 0, "irq_o high after reset with IRQ_MASK=0")
    # tx_empty is a level source: IRQ_STAT.bit0 should read 1 after reset
    st0 = await tb.rdv(IRQ_STAT)
    tb.log.info(f"IRQ_STAT after reset = 0x{st0:x} (TX FIFO empty)")
    tb.soft(st0 & IRQ_TXE, f"IRQ_STAT.tx_empty not set while TX FIFO empty (0x{st0:x})")
    await tb.wrv(IRQ_MASK, IRQ_TXE)
    await ClockCycles(tb.clk, 2)
    tb.soft(val(d.irq_o) == 1, "tx_empty irq not raised with mask")
    await tb.wrv(IRQ_MASK, 0)
    await tb.wrv(TX_DATA, 0x5A)
    await tb.wrv(CTRL, ctrl_word(start=1))
    tb.soft(await tb.wait_frames(1, 2000), "no SPI frame after one CTRL.start write")
    await tb.wait_not_busy()
    await tb.wrv(CTRL, 0)
    await tb.wait_not_busy()
    st1 = await tb.rdv(IRQ_STAT)
    tb.log.info(f"IRQ_STAT after transfer = 0x{st1:x}")
    tb.soft(st1 & IRQ_DONE, f"IRQ_STAT.done not readable after a transfer (0x{st1:x})")
    await tb.wrv(IRQ_MASK, IRQ_DONE)
    await ClockCycles(tb.clk, 2)
    tb.soft(val(d.irq_o) == 1, "done irq not raised when unmasked after transfer")
    await tb.wrv(IRQ_STAT, IRQ_DONE)
    await ClockCycles(tb.clk, 3)
    irq_after_w1c = val(d.irq_o)
    tb.soft(irq_after_w1c == 0, "irq_o stays high after W1C of IRQ_STAT.done (cannot be acknowledged)")
    await tb.wrv(IRQ_STAT, 0xFFFFFFFF)
    await ClockCycles(tb.clk, 3)
    tb.soft(val(d.irq_o) == 0, "irq_o stays high after W1C of all IRQ_STAT bits")
    await tb.wrv(IRQ_MASK, 0)
    await ClockCycles(tb.clk, 2)
    tb.soft(val(d.irq_o) == 0, "irq_o high with IRQ_MASK=0")
    tb.log.info(f"irq_o after W1C(done)={irq_after_w1c}")
    tb.finish()


@cocotb.test()
async def t15_overrun_error_flag(dut):
    """err (RX overrun) must be set only when an RX byte is lost."""
    tb = TB(dut)
    await tb.start(loopback=1)
    d = tb.d
    await tb.wrv(IRQ_MASK, IRQ_ERR)
    sent = 0
    first_err_at = None
    for i in range(17):
        await tb.wrv(TX_DATA, 0x40 + i)
        n0 = len(tb.spi.frames)
        await tb.wrv(CTRL, ctrl_word(start=1))
        await tb.wait_frames(n0 + 1, 600)
        await tb.wait_not_busy()
        await ClockCycles(tb.clk, 3)
        sent = len(tb.spi.frames)
        lvl = await tb.rdv(FIFO_LVL)
        if first_err_at is None and val(d.irq_o):
            first_err_at = (sent, lvl >> 16)
    tb.log.info(f"frames={sent}; err irq first seen at (frames, rx_count)={first_err_at}")
    tb.soft(first_err_at is not None and first_err_at[0] >= 17,
            f"err raised at frames/rx_count={first_err_at}: no byte lost yet (FIFO depth 16)")
    tb.finish()


@cocotb.test()
async def t16_modes_with_slave_model(dut):
    """Each CPOL/CPHA/LSB through CTRL: SCK idle level, MOSI frame, RX byte via AXI."""
    tb = TB(dut)
    await tb.start(loopback=0)
    tb.spi.drive = True
    await tb.wrv(CLK_DIV, 2)
    results = []
    for mode in range(4):
        for lsb in (0, 1):
            cpol, cpha = mode >> 1, mode & 1
            tb.spi.cpol, tb.spi.cpha, tb.spi.lsb = cpol, cpha, lsb
            tx = 0xB0 | (mode << 1) | lsb
            sl = 0x0D | (mode << 5) | (lsb << 4)
            tb.spi.miso_q.clear()
            tb.spi.miso_q.append(sl)
            await tb.wrv(TX_DATA, tx)
            cw = ctrl_word(start=1, lsb=lsb, cpol=cpol, cpha=cpha, inter=0, setup=4, hold=2)
            n0 = len(tb.spi.frames)
            await tb.wrv(CTRL, cw)
            tb.soft(await tb.wait_frames(n0 + 1, 2000), f"mode {mode} lsb {lsb}: no frame after CTRL.start")
            await tb.wait_not_busy()
            mo = tb.spi.bytes_of(tb.spi.frames[-1]) if len(tb.spi.frames) > n0 else []
            r1 = await tb.rdv(RX_DATA)
            results.append(dict(mode=mode, lsb=lsb, mosi=[hex(x) for x in mo], rx_read=hex(r1),
                                slave=hex(sl), idle_sck=val(tb.d.sck_o)))
            tb.soft(val(tb.d.sck_o) == cpol, f"mode {mode}: SCK idles at {val(tb.d.sck_o)} after transfer, CPOL={cpol}")
            tb.soft(mo == [tx], f"mode {mode} lsb {lsb}: MOSI {[hex(x) for x in mo]} != [{hex(tx)}]")
            tb.soft(r1 == sl, f"mode {mode} lsb {lsb}: RX_DATA {hex(r1)} != slave byte {hex(sl)}")
    for r in results:
        tb.log.info(f"MODE RESULT {r}")
    tb.soft(tb.spi.cs_multi_low == 0, f"more than one CS low in {tb.spi.cs_multi_low} cycles")
    tb.finish()
