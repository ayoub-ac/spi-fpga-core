"""Minimal shims so cocotbext-spi 0.5.0 (written for cocotb 1.x) runs on cocotb 2.1.

Only API renames are bridged (BaseClock, Timer(units=), get_sim_steps(units=), .integer);
the library's protocol logic is untouched.
"""
import cocotb
import cocotb.clock
import cocotb.triggers
import cocotb.utils
from cocotb.types import Logic, LogicArray

if not hasattr(cocotb.clock, "BaseClock"):
    class BaseClock:  # noqa: D401
        def __init__(self, signal):
            self.signal = signal

    cocotb.clock.BaseClock = BaseClock

for _T in (Logic, LogicArray):
    try:
        getattr(_T, "integer")
    except AttributeError:
        _T.integer = property(lambda s: int(s))

_gss = cocotb.utils.get_sim_steps
_gtfss = cocotb.utils.get_time_from_sim_steps


def _get_sim_steps(time, unit="step", *, units=None, round_mode="round", **kw):
    return _gss(time, units or unit, round_mode=round_mode)


def _get_time_from_sim_steps(steps, unit=None, *, units=None):
    return _gtfss(steps, units or unit)


cocotb.utils.get_sim_steps = _get_sim_steps
cocotb.utils.get_time_from_sim_steps = _get_time_from_sim_steps

import cocotbext.spi.spi as _spi  # noqa: E402

_Timer = cocotb.triggers.Timer


def _CompatTimer(t, units=None, unit=None, **kw):
    return _Timer(t, unit=(units or unit or "step"), **kw)


_spi.Timer = _CompatTimer

from cocotbext.spi import SpiBus, SpiConfig, SpiMaster  # noqa: E402,F401
from cocotbext.spi.devices.generic import SpiSlaveLoopback  # noqa: E402,F401
