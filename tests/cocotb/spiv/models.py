"""Independent SPI bus models written from the Motorola SPI definition.

Mode  CPOL CPHA  leading edge   trailing edge
  0     0    0   rise: sample   fall: shift
  1     0    1   rise: shift    fall: sample
  2     1    0   fall: sample   rise: shift
  3     1    1   fall: shift    rise: sample

CPHA=0: the first bit must be valid before the first (leading) edge, i.e. it
is presented when CS asserts. CPHA=1: data is presented on each leading edge.
"""
import random
from collections import deque

import cocotb
from cocotb.triggers import First, Timer, ValueChange

from .trace import now_ps, sig_int

CLK_PS = 10_000


def word_bits(word, width, lsb_first):
    """Bits in wire order."""
    idx = range(width) if lsb_first else range(width - 1, -1, -1)
    return [(word >> i) & 1 for i in idx]


def bits_word(bits, lsb_first):
    w = 0
    n = len(bits)
    for k, b in enumerate(bits):
        pos = k if lsb_first else n - 1 - k
        w |= (b & 1) << pos
    return w


class SpiSlaveModel:
    """Behavioural SPI slave driving MISO for one chip-select line of a master DUT.

    The next word is only *consumed* when its first sampling edge happens, so a
    CS de-assertion never silently drops a queued word. MISO is driven tco_ps
    after the launching event (CS fall for the CPHA=0 first bit, otherwise the
    shift edge).
    """

    def __init__(self, sck, miso, cs_n, line, width, cpol, cpha, lsb_first,
                 tco_ps=1000, fill_word=None):
        self.sck, self.miso, self.cs_n = sck, miso, cs_n
        self.line = line
        self.width = width
        self.cpol, self.cpha, self.lsb = cpol, cpha, lsb_first
        self.tco_ps = tco_ps
        self.queue = deque()
        self.fill = fill_word if fill_word is not None else (0xA5A5A5A5 & ((1 << width) - 1))
        self.sent = []  # words whose first bit was sampled by the master
        self.frame_errors = []
        self._task = cocotb.start_soon(self._run())
        self.miso.value = 0

    def stop(self):
        self._task.cancel()

    def configure(self, cpol, cpha, lsb_first):
        self.cpol, self.cpha, self.lsb = cpol, cpha, lsb_first

    def _peek(self):
        return self.queue[0] if self.queue else self.fill

    def _consume(self):
        w = self.queue.popleft() if self.queue else self.fill
        self.sent.append(w)
        return w

    async def _drive_later(self, bit):
        await Timer(self.tco_ps, unit="ps")
        self.miso.value = bit

    def _drive(self, bit):
        cocotb.start_soon(self._drive_later(bit))

    def _cs_active(self):
        v = sig_int(self.cs_n)
        return v >= 0 and ((v >> self.line) & 1) == 0

    async def _run(self):
        while True:
            # wait for CS assertion
            while not self._cs_active():
                await ValueChange(self.cs_n)
            cur_bits = None
            idx = 0
            if self.cpha == 0:
                self._drive(word_bits(self._peek(), self.width, self.lsb)[0])
            sck_prev = sig_int(self.sck)
            while True:
                trig = await First(ValueChange(self.sck), ValueChange(self.cs_n))
                if not self._cs_active():
                    if cur_bits is not None and idx != 0:
                        self.frame_errors.append((now_ps(), idx))
                    break
                v = sig_int(self.sck)
                if v == sck_prev:
                    continue
                sck_prev = v
                leading = (v != self.cpol)
                if self.cpha == 0:
                    if leading:
                        if cur_bits is None:
                            cur_bits = word_bits(self._consume(), self.width, self.lsb)
                            idx = 0
                    else:
                        if cur_bits is None:
                            continue
                        idx += 1
                        if idx == self.width:
                            cur_bits = None
                            idx = 0
                            self._drive(word_bits(self._peek(), self.width, self.lsb)[0])
                        else:
                            self._drive(cur_bits[idx])
                else:
                    if leading:
                        if cur_bits is None:
                            cur_bits = word_bits(self._consume(), self.width, self.lsb)
                            idx = 0
                        self._drive(cur_bits[idx])
                    else:
                        if cur_bits is None:
                            continue
                        idx += 1
                        if idx == self.width:
                            cur_bits = None
                            idx = 0


class NaiveSlaveMiso:
    """Plain shift-register slave output stage (what most simple SPI peripherals are).

    Loads the next word on CS fall (presenting bit 0 for CPHA=0) and advances on *every*
    shift edge it sees while selected. sck_delay_ps / cs_delay_ps model pad/board skew:
    the slave sees each SCK (CS) change that much later than the master pin.
    """

    def __init__(self, sck, miso, cs_n, line, width, cpol, cpha, lsb_first,
                 sck_delay_ps=0, cs_delay_ps=0, tco_ps=1000):
        self.sck, self.miso, self.cs_n = sck, miso, cs_n
        self.line, self.width = line, width
        self.cpol, self.cpha, self.lsb = cpol, cpha, lsb_first
        self.dsck, self.dcs, self.tco = sck_delay_ps, cs_delay_ps, tco_ps
        self.queue = deque()
        self.sent = []
        self.selected = False
        self.bits = None
        self.idx = 0
        self.pending = False
        self.miso.value = 0
        self._tasks = [cocotb.start_soon(self._watch(self.sck, self.dsck, self._on_sck)),
                       cocotb.start_soon(self._watch(self.cs_n, self.dcs, self._on_cs))]

    def stop(self):
        for t in self._tasks:
            t.cancel()

    def configure(self, cpol, cpha, lsb_first):
        self.cpol, self.cpha, self.lsb = cpol, cpha, lsb_first

    async def _watch(self, sig, delay, fn):
        while True:
            await ValueChange(sig)
            v = sig_int(sig)
            cocotb.start_soon(self._later(delay, fn, v))

    async def _later(self, delay, fn, v):
        if delay:
            await Timer(delay, unit="ps")
        fn(v)

    def _drive(self, bit):
        cocotb.start_soon(self._later(self.tco, lambda _v: setattr(self.miso, "value", bit), 0))

    def _load(self):
        w = self.queue.popleft() if self.queue else 0
        self.sent.append(w)
        self.bits = word_bits(w, self.width, self.lsb)
        self.idx = 0
        self.pending = False

    def _on_cs(self, v):
        sel = ((v >> self.line) & 1) == 0
        if sel and not self.selected:
            self.selected = True
            self._load()
            if self.cpha == 0:
                self._drive(self.bits[0])
        elif not sel:
            self.selected = False

    def _on_sck(self, v):
        if not self.selected:
            return
        leading = v != self.cpol
        if self.cpha == 0:
            if leading:
                if self.pending:  # next word presented at the previous trailing edge is now in use
                    self._load()
                return
            self.idx += 1
            if self.idx == self.width:
                nxt = self.queue[0] if self.queue else 0
                self.bits = word_bits(nxt, self.width, self.lsb)
                self.idx = 0
                self.pending = True
            self._drive(self.bits[self.idx])
        elif leading:
            if self.idx == self.width:
                self._load()
            self._drive(self.bits[self.idx])
            self.idx += 1


class AsyncSpiMaster:
    """Behavioural SPI master driving an SPI slave DUT with arbitrary (asynchronous) timing.

    Times are in ps. Edges are never placed exactly on a clk rising edge (multiples of
    CLK_PS) to avoid zero-delay simulator races; that is a simulation artefact, not a
    property of the DUT.

    pin_skew_ps > 0 delays every change of SCK, MOSI and CS_n at the DUT pins by an
    independent random amount in [0, pin_skew_ps] (order per signal is kept), while
    MISO is still sampled at the nominal edge times. With pin_skew_ps = one clk period
    this emulates each 2-FF synchronizer resolving a change one clock late (metastable
    first stage), independently per input.
    """

    def __init__(self, sck, mosi, miso, cs_n, width, rng=None, trace=None, pin_skew_ps=0):
        self.sck, self.mosi, self.miso, self.cs_n = sck, mosi, miso, cs_n
        self.width = width
        self.rng = rng or random.Random(1)
        self.trace = trace
        self.cpol = 0
        self.cpha = 0
        self.lsb = 0
        self.samples = []  # (t_edge, bit) MISO samples
        self.pin_skew_ps = pin_skew_ps
        self._last_apply = {}
        self._pin(self.sck, 0)
        self._pin(self.mosi, 0)
        self._pin(self.cs_n, 1)

    def _pin(self, sig, v):
        """Drive a DUT input, optionally after a random pin skew."""
        if not self.pin_skew_ps:
            sig.value = v
            return
        t = now_ps()
        key = id(sig)
        at = max(t + self.rng.randint(0, self.pin_skew_ps), self._last_apply.get(key, t) + 1)
        if at % CLK_PS == 0:
            at += 1
        self._last_apply[key] = at
        cocotb.start_soon(self._apply(sig, v, at - t))

    async def _apply(self, sig, v, dt):
        if dt > 0:
            await Timer(dt, unit="ps")
        sig.value = v

    def configure(self, cpol, cpha, lsb_first):
        self.cpol, self.cpha, self.lsb = cpol, cpha, lsb_first

    @staticmethod
    def _legal(t):
        return t + 1 if t % CLK_PS == 0 else t

    async def _until(self, t_abs):
        t_abs = self._legal(int(t_abs))
        dt = t_abs - now_ps()
        if dt > 0:
            await Timer(dt, unit="ps")
        return now_ps()

    async def idle(self, t_ps):
        await self._until(now_ps() + t_ps)

    async def set_idle_level(self):
        self._pin(self.sck, self.cpol)
        await Timer(1 + self.pin_skew_ps, unit="ps")

    async def transaction(self, words, half_ps, css_ps, csh_ps, jitter_ps=0,
                          word_gap_ps=0, mosi_hold_last_ps=None, abort_after_bits=None,
                          mosi_idle=0):
        """One CS assertion carrying `words`.

        Returns (miso_words, sample_times). abort_after_bits: de-assert CS after this
        many complete bits (mid-word abort). mosi_hold_last_ps: when not None, MOSI is
        driven to `mosi_idle` this long after the final SCK edge (0 = released on the edge).
        """
        rng = self.rng
        W = self.width

        def jit():
            return rng.randint(-jitter_ps, jitter_ps) if jitter_ps else 0

        bits_all = [word_bits(w, W, self.lsb) for w in words]
        miso_words = []
        times = []
        t = now_ps()
        # CS fall (CPHA=0 presents the first bit together with CS)
        if self.cpha == 0:
            self._pin(self.mosi, bits_all[0][0])
        self._pin(self.cs_n, 0)
        t_cs = t
        t_next = t_cs + css_ps
        nbits_done = 0
        aborted = False
        last_edge_t = None
        for wi, bits in enumerate(bits_all):
            rx = []
            for bi, b in enumerate(bits):
                if abort_after_bits is not None and nbits_done >= abort_after_bits:
                    aborted = True
                    break
                # leading edge
                te = await self._until(t_next)
                if self.cpha == 0:
                    rx.append(sig_int(self.miso))
                    times.append(te)
                else:
                    self._pin(self.mosi, b)
                self._pin(self.sck, 1 - self.cpol)
                t_next = te + half_ps + jit()
                # trailing edge
                te = await self._until(t_next)
                if self.cpha == 1:
                    rx.append(sig_int(self.miso))
                    times.append(te)
                self._pin(self.sck, self.cpol)
                last_edge_t = te
                if self.cpha == 0:
                    if bi + 1 < W:
                        self._pin(self.mosi, bits[bi + 1])
                    elif wi + 1 < len(bits_all):
                        self._pin(self.mosi, bits_all[wi + 1][0])
                nbits_done += 1
                t_next = te + half_ps + jit()
                if bi == W - 1 and wi + 1 < len(bits_all):
                    t_next += word_gap_ps
            if aborted:
                break
            miso_words.append(bits_word(rx, self.lsb))
        # end of frame
        if mosi_hold_last_ps is not None and last_edge_t is not None:
            if mosi_hold_last_ps > 0:
                await self._until(last_edge_t + mosi_hold_last_ps)
            self._pin(self.mosi, mosi_idle)
        t_end = (last_edge_t if last_edge_t is not None else now_ps()) + csh_ps
        await self._until(max(t_end, now_ps() + 1))
        self._pin(self.cs_n, 1)
        if mosi_hold_last_ps is None:
            self._pin(self.mosi, mosi_idle)
        await Timer(1 + self.pin_skew_ps, unit="ps")
        return miso_words, times
