"""Offline protocol checker for a master-driven SPI bus recorded with Trace.

All timing is reported in clk cycles (CLK_PS) because the master DUT is synchronous.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from .models import CLK_PS, bits_word

BIG = 10**12


@dataclass
class MasterCfg:
    width: int
    cpol: int
    cpha: int
    lsb: int
    div: int
    setup: int
    hold: int
    line: int
    num_cs: int = 4


@dataclass
class BusReport:
    viol: dict = field(default_factory=lambda: defaultdict(list))
    windows: list = field(default_factory=list)
    mosi_words: list = field(default_factory=list)
    idle_cpol_moves: int = 0   # SCK edges to the new cpol_i while every CS_n is high

    def add(self, cat, info):
        self.viol[cat].append(info)


def cs_windows(tr, line, t0, t1):
    ch = tr.bit_changes("cs_n", line, t0, t1)
    lvl = (tr.at("cs_n", t0) >> line) & 1
    wins = []
    tf = None
    if lvl == 0:
        tf = t0
    for t, b in ch:
        if b == 0:
            tf = t
        elif tf is not None:
            wins.append((tf, t))
            tf = None
    if tf is not None:
        wins.append((tf, None))
    return wins


def analyze(tr, t0, t1, cfg: MasterCfg, exp_windows):
    """exp_windows: list (per CS window) of lists of expected MOSI words."""
    W = cfg.width
    T = CLK_PS
    rep = BusReport()

    # ---- chip-select sanity -------------------------------------------------
    pts = [(t0, tr.at("cs_n", t0))] + tr.changes("cs_n", t0, t1)
    for t, v in pts:
        low = [i for i in range(cfg.num_cs) if not (v >> i) & 1]
        if len(low) > 1:
            rep.add("cs_more_than_one_active", (t, low))
        if low and low != [cfg.line]:
            rep.add("cs_wrong_line", (t, low))

    wins = cs_windows(tr, cfg.line, t0, t1)
    if len(wins) != len(exp_windows):
        rep.add("cs_window_count", (len(wins), len(exp_windows)))

    sck_edges = tr.changes("sck", t0, t1)
    inside = set()
    for tf, trs in wins:
        for t, v in sck_edges:
            if tf <= t <= (trs if trs is not None else BIG):
                inside.add(t)
    all_idle = (1 << cfg.num_cs) - 1
    for t, v in sck_edges:
        if t not in inside:
            # While every CS_n is high, SCK may only move to the cpol_i value
            # the master sampled on that clock edge (it parks at a new CPOL
            # before the next CS_n falls); any other edge is a violation.
            if "cpol" in tr.t and v == tr.before("cpol", t) and tr.before("cs_n", t) == all_idle \
                    and tr.at("cs_n", t) == all_idle:
                rep.idle_cpol_moves += 1
            else:
                rep.add("sck_edge_while_cs_idle", t)

    for k, (tf, trs) in enumerate(wins):
        exp = exp_windows[k] if k < len(exp_windows) else []
        wrep = {"t_fall": tf, "t_rise": trs}
        sck_before = tr.before("sck", tf)
        wrep["sck_before_cs_fall"] = sck_before
        if sck_before != cfg.cpol:
            rep.add("sck_not_at_cpol_when_cs_falls", (tf, sck_before))
        at_fall = [(t, v) for t, v in sck_edges if t == tf]
        if at_fall:
            rep.add("sck_edge_coincident_with_cs_fall", tf)
        e_in = [(t, v) for t, v in sck_edges if tf < t and (trs is None or t < trs)]
        at_rise = [(t, v) for t, v in sck_edges if trs is not None and t == trs]
        if at_rise:
            rep.add("sck_edge_coincident_with_cs_rise", trs)
        if trs is not None and tr.at("sck", trs) != cfg.cpol:
            rep.add("sck_not_at_cpol_when_cs_rises", trs)
        n_exp = len(exp)
        if len(e_in) != 2 * W * n_exp:
            rep.add("sck_edge_count", (tf, len(e_in), 2 * W * n_exp))
        nwords = len(e_in) // (2 * W)
        wrep["words"] = []
        prev_last = None
        for j in range(nwords):
            E = e_in[2 * W * j: 2 * W * (j + 1)]
            wd = {}
            # direction: first edge leaves the idle level
            for i, (t, v) in enumerate(E):
                want = (1 - cfg.cpol) if i % 2 == 0 else cfg.cpol
                if v != want:
                    rep.add("sck_edge_polarity", (t, i, v))
                    break
            halves = sorted({E[i + 1][0] - E[i][0] for i in range(len(E) - 1)})
            wd["half_period_cycles"] = [h / T for h in halves]
            if halves != [(cfg.div + 1) * T]:
                rep.add("sck_half_period", (E[0][0], [h / T for h in halves], cfg.div + 1))
            samp = E[0::2] if cfg.cpha == 0 else E[1::2]
            bits = []
            min_setup = BIG
            min_hold = BIG
            for bi, (ts, _) in enumerate(samp):
                b = tr.before("mosi", ts)
                bits.append(b)
                lc = tr.last_change_before("mosi", ts)
                nc = tr.next_change_at_or_after("mosi", ts)
                su = ts - lc if lc is not None else BIG
                ho = nc - ts if nc is not None else BIG
                min_setup = min(min_setup, su)
                min_hold = min(min_hold, ho)
                if ho < T:
                    rep.add("mosi_hold_lt_1clk_at_sample_edge", (ts, bi, ho / T))
                if su < T:
                    rep.add("mosi_setup_lt_1clk_at_sample_edge", (ts, bi, su / T))
            wd["min_mosi_setup_cycles"] = min_setup / T
            wd["min_mosi_hold_cycles"] = min_hold / T
            word = bits_word(bits, cfg.lsb)
            wd["mosi_word"] = word
            rep.mosi_words.append(word)
            if j < n_exp and word != exp[j]:
                rep.add("mosi_data", (E[0][0], j, hex(word), hex(exp[j])))
            if j == 0:
                wrep["cs_setup_cycles"] = (E[0][0] - tf) / T
                if cfg.cpha == 0:
                    first_bit_expected = bits[0]
                    wrep["mosi_at_cs_fall_is_first_bit"] = tr.at("mosi", tf) == first_bit_expected
                    lc0 = tr.last_change_before("mosi", E[0][0])
                    wrep["first_bit_setup_cycles"] = (E[0][0] - lc0) / T if lc0 is not None and lc0 > tf else (E[0][0] - tf) / T
            if prev_last is not None:
                wd["gap_from_prev_word_cycles"] = (E[0][0] - prev_last) / T
            prev_last = E[-1][0]
            wd["last_edge"] = E[-1][0]
            wd["last_shift_edge"] = E[-1][0] if cfg.cpha == 0 else E[-2][0]
            wrep["words"].append(wd)
        if wrep["words"] and trs is not None:
            wrep["cs_hold_cycles_from_last_edge"] = (trs - wrep["words"][-1]["last_edge"]) / T
            wrep["cs_hold_cycles_from_last_shift_edge"] = (trs - wrep["words"][-1]["last_shift_edge"]) / T
        rep.windows.append(wrep)
    return rep


def summarize(rep: BusReport):
    return {k: len(v) for k, v in rep.viol.items()}


def naive_slave_decode(tr, t0, t1, line, width, lsb, sample_on_rise, d_sck=0, d_cs=0, d_mosi=0):
    """Offline model of a plain shift-register SPI slave (no glitch filtering, no mode logic).

    It shifts MOSI in on every SCK sampling edge (rise or fall) while it sees CS low.
    d_sck/d_cs/d_mosi are board/pad arrival delays in ps (skew between the master's
    output pins as seen at the slave). Returns (words, partial_bits_per_window).
    A master with correct timing must give identical results for small +/- skews.
    """
    def cs_seen(t):
        return (tr.at("cs_n", t - d_cs) >> line) & 1

    words, partials = [], []
    bits = []
    edges = tr.changes("sck", t0, t1)
    cs_ch = [(t + d_cs, b) for t, b in tr.bit_changes("cs_n", line, t0, t1)]
    events = [(t + d_sck, "sck", v) for t, v in edges] + [(t, "cs", b) for t, b in cs_ch]
    # CS changes first when simultaneous at the slave (worst case for an asserting edge is
    # handled by the explicit skews, not by ordering)
    events.sort(key=lambda e: (e[0], 0 if e[1] == "cs" else 1))
    for t, kind, v in events:
        if kind == "cs":
            if v == 1:
                if bits:
                    partials.append(len(bits))
                bits = []
            continue
        if cs_seen(t) != 0:
            continue
        rising = v == 1
        if rising != bool(sample_on_rise):
            continue
        bits.append(tr.before("mosi", t - d_mosi))
        if len(bits) == width:
            words.append(bits_word(bits, lsb))
            bits = []
    if bits:
        partials.append(len(bits))
    return words, partials
