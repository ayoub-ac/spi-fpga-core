"""Event-based signal recorder with exact simulation timestamps (ps).

Each signal is stored as a step function: list of (t_ps, value) where the value
holds from t (inclusive) until the next entry. Multiple changes inside one
timestep are collapsed to the final value (delta glitches are counted).
"""
import bisect

import cocotb
from cocotb.triggers import ValueChange

try:
    from cocotb.simtime import get_sim_time
except ImportError:  # pragma: no cover
    from cocotb.utils import get_sim_time


def now_ps() -> int:
    return int(get_sim_time("ps"))


def sig_int(h) -> int:
    try:
        return int(h.value)
    except ValueError:
        return -1  # X / Z


class Trace:
    def __init__(self, sigs: dict):
        self.t = {}
        self.v = {}
        self.delta_glitches = {}
        self._tasks = []
        t0 = now_ps()
        for name, h in sigs.items():
            self.t[name] = [t0]
            self.v[name] = [sig_int(h)]
            self.delta_glitches[name] = 0
            self._tasks.append(cocotb.start_soon(self._mon(name, h)))

    async def _mon(self, name, h):
        ts, vs = self.t[name], self.v[name]
        while True:
            await ValueChange(h)
            t = now_ps()
            v = sig_int(h)
            if ts[-1] == t and len(ts) > 1:
                self.delta_glitches[name] += 1
                vs[-1] = v
                if vs[-2] == v:
                    ts.pop()
                    vs.pop()
            elif vs[-1] != v:
                ts.append(t)
                vs.append(v)

    def stop(self):
        for task in self._tasks:
            task.cancel()
        self._tasks = []

    # ---- queries -------------------------------------------------------
    def before(self, name, t):
        """Value strictly before time t (what a flop clocked at t samples)."""
        i = bisect.bisect_left(self.t[name], t) - 1
        return self.v[name][max(i, 0)]

    def at(self, name, t):
        """Value at time t after any change scheduled at t."""
        i = bisect.bisect_right(self.t[name], t) - 1
        return self.v[name][max(i, 0)]

    def changes(self, name, t0=None, t1=None):
        """List of (t, v) changes with t0 <= t <= t1 (initial record excluded)."""
        ts, vs = self.t[name], self.v[name]
        lo = 1 if t0 is None else max(1, bisect.bisect_left(ts, t0))
        hi = len(ts) if t1 is None else bisect.bisect_right(ts, t1)
        return list(zip(ts[lo:hi], vs[lo:hi]))

    def last_change_before(self, name, t):
        """Time of the latest change strictly before t (None if never changed)."""
        i = bisect.bisect_left(self.t[name], t) - 1
        if i <= 0:
            return None
        return self.t[name][i]

    def next_change_at_or_after(self, name, t):
        i = bisect.bisect_left(self.t[name], t)
        if i == 0:
            i = 1
        if i >= len(self.t[name]):
            return None
        return self.t[name][i]

    def bit_changes(self, name, bit, t0=None, t1=None):
        """Changes of one bit of a vector signal: list of (t, bitval)."""
        out = []
        prev = ((self.v[name][0] if t0 is None else self.before(name, t0)) >> bit) & 1
        for t, v in self.changes(name, t0, t1):
            b = (v >> bit) & 1
            if b != prev:
                out.append((t, b))
                prev = b
        return out
