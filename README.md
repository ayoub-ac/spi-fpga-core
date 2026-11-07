# spi-fpga-core

[![CI](https://github.com/ayoub-ac/spi-fpga-core/actions/workflows/ci.yml/badge.svg)](https://github.com/ayoub-ac/spi-fpga-core/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later or commercial](https://img.shields.io/badge/license-GPL--3.0%20%7C%20commercial-blue.svg)](LICENSE.md)

A small, parametrizable SPI master + slave IP core in SystemVerilog.
Implements the four classical Motorola modes (CPOL x CPHA), MSB / LSB
first, programmable clock divider, multi-CS master, TX / RX FIFOs, and
an optional AXI4-Lite control wrapper. Placed and routed with the
open-source flow for iCE40 UP5K and ECP5; Yosys also maps it to Xilinx
7-series. Intel and Gowin devices were not tried. See
[Verification status](#verification-status).

## Why

SPI is everywhere — flash, ADCs, DACs, displays, sensors, secure
elements, RF transceivers — but most open SPI cores are either
incomplete (single mode, no FIFOs) or vendor-locked (Xilinx Quad SPI,
Lattice IP catalog). This core gives you all four modes, both ends
(master AND slave), and a register-mapped wrapper.

## Quickstart

```bash
git clone https://github.com/ayoub-ac/spi-fpga-core.git
cd spi-fpga-core
make lint test            # Verilator lint + C++ regressions of spi_top and spi_axi4lite
make cocotb               # cocotb regression on Verilator and Icarus
make doc-numbers          # P&R + SYNTH_REPORT.md, refresh the numbers in the docs
```

Tested with OSS CAD Suite 2026-11-01 (Verilator 5.053, Icarus Verilog 14
devel, Yosys 0.70, nextpnr 0.11.1, SymbiYosys, GHDL) and, for `make cocotb`,
cocotb 2.1 with cocotbext-axi 0.1.28 and cocotbext-spi 0.5.0. Exact
versions are in [`VERIFICATION.md`](VERIFICATION.md); older versions are
untested.

## Features

- SPI master with all four modes (CPOL x CPHA = 0..3), MSB / LSB first, programmable SCK divider, parametrizable word width (DATA_WIDTH >= 2; 2..64 bits simulated) and chip-select count (NUM_CS >= 1, default 4).
- SPI slave with the same four modes, single CS input, configurable word width, 2-FF synchronizers on SCK, CS_n and MOSI, a MISO output enable and TX-underrun / RX-overrun strobes.
- Parametrizable sync FIFOs on TX and RX (any depth >= 1, default 16), optional first-word fall-through; Yosys maps the storage to block RAM or LUT RAM.
- Inter-byte CS hold for multi-byte streaming without de-asserting the slave.
- Programmable CS-to-SCK setup and hold delays.
- Interrupts: TX-empty, RX-full, transfer-complete, RX-overrun (one pulse per dropped word).
- Optional AXI4-Lite slave wrapper (`spi_axi4lite`) with a 16-register map (32-bit, or 64-bit with DATA_WIDTH = 64) for software control.
- VHDL-2008 entity wrapper for mixed-language designs.

## Port table (master, abridged)

| Signal                | Dir | Width        | Description                                  |
|-----------------------|-----|--------------|----------------------------------------------|
| `clk_i / rst_ni`      | in  | 1            | Clock / sync active-low reset.               |
| `cpol_i / cpha_i`     | in  | 1            | Mode select.                                 |
| `lsb_first_i`         | in  | 1            | 0 = MSB first, 1 = LSB first.                |
| `clk_div_i`           | in  | DIV_WIDTH    | SCK = clk_i / (2*(div + 1)).                 |
| `cs_sel_i`            | in  | max(1, log2(NUM_CS)) | Chip-select line to assert.          |
| `cs_setup/hold_i`     | in  | CS_DLY_W     | CS-to-SCK setup / hold cycles.               |
| `cs_inter_byte_i`     | in  | 1            | Hold CS through multi-byte streams.          |
| `start_i`             | in  | 1            | One-cycle pulse to launch a transfer.        |
| `tx_we_i / wdata`     | in  | DATA_WIDTH   | Push word into TX FIFO.                      |
| `rx_re_i / rdata`     | in / out | DATA_WIDTH | Pop word from RX FIFO.                    |
| `busy_o / xfer_done_o`| out | 1            | Status / one-cycle done pulse.               |
| `rx_overrun_o`        | out | 1            | One pulse per received word dropped (RX FIFO full). |
| `sck_o / mosi_o`      | out | 1            | Bus outputs.                                 |
| `miso_i`              | in  | 1            | Bus input.                                   |
| `cs_n_o`              | out | NUM_CS       | Chip-selects, active-low.                    |

Full slave port list, timing and latencies, and the AXI4-Lite register map
in [`PORT_DESCRIPTION.md`](PORT_DESCRIPTION.md).

## Headline numbers

Default parameters (DATA_WIDTH = 8, NUM_CS = 4, FIFO_DEPTH = 16,
DIV_WIDTH = 16, CS_DLY_W = 8). `spi_top` is the loopback of master, slave
and four 16 x 8 FIFOs; `spi_axi4lite` is the AXI4-Lite wrapper with the
master only.

<!-- BEGIN GENERATED pnr-table: make doc-numbers rewrites this section -->
Measured by `make pnr` (Yosys 0.70+286, nextpnr-0.11.1-85-ga2c64799,
OSS CAD Suite 2026-11-01), default parameters, nextpnr seeds 1, 2, 3.
Fmax is the register-to-register limit, without pin constraints or I/O timing.

| Design | Device | LUT4 | FF | RAM cells | Fmax (MHz) | nextpnr target |
|---|---|---|---|---|---|---|
| spi_top | iCE40 UP5K SG48 | 348 | 291 | 4 SB_RAM40_4K | 55.0 .. 57.6 | 50 MHz: met |
| spi_top | ECP5 LFE5U-25F CABGA256 | 298 | 231 | 8 TRELLIS_DPR16X4 | 213.4 .. 227.0 | 100 MHz: met |
| spi_axi4lite | iCE40 UP5K SG48 | 622 | 586 | 2 SB_RAM40_4K | 51.2 .. 53.4 | 50 MHz: met |
| spi_axi4lite | ECP5 LFE5U-25F CABGA256 | 729 | 556 | 4 TRELLIS_DPR16X4 | 178.5 .. 184.8 | 100 MHz: met |
<!-- END GENERATED pnr-table -->

Yosys-only counts, Xilinx 7-series included, are in
[`SYNTH_REPORT.md`](SYNTH_REPORT.md); [`RESOURCE_ESTIMATES.md`](RESOURCE_ESTIMATES.md)
explains what dominates the area. Vendor tools (Vivado, Quartus, Diamond,
Gowin) were not run.

## Build & test

```bash
make lint            # Verilator -Wall lint of the RTL and testbenches
make test            # Verilator C++ regressions: spi_top loopback, spi_axi4lite
make cocotb          # cocotb regression, profile ci, on Verilator and Icarus
                     #   (COCOTB_PROFILE=long: long random runs, all word widths)
make formal          # SymbiYosys proofs and covers: FIFO, master, AXI4-Lite slave
make gatelevel       # CS_n / MOSI glitch check on a gate netlist; FIFO vs its iCE40 RAM netlist
make params          # legal / illegal parameter sets in Yosys, Verilator and Icarus
make vhdl-test       # GHDL analysis and mixed-language elaboration of the VHDL wrapper
make pnr             # Yosys + nextpnr for iCE40 UP5K and ECP5-25 (build/pnr/)
make synth_report    # SYNTH_REPORT.md
make doc-numbers     # pnr + synth_report, then rewrite the numbers in the docs
make waves           # timing plots (needs matplotlib and vcdvcd)
```

A passing `make test` ends with `TESTS PASSED` and exits 0.
[GitHub Actions](.github/workflows/ci.yml) runs lint, test, params,
vhdl-test, the cocotb ci profile on both simulators, formal and gatelevel
with the same OSS CAD Suite build.

## Verification

**Directed tests** (`make test`, `tb/sim_main.cpp`): single-byte loopback
in modes 0/1/2/3, MSB/LSB-first, clock-divisor sweep, CS setup/hold
timing, multi-byte FIFO loopback, mid-transfer reset recovery, alternate
chip-select, 1000 random bytes in mode 0, CPHA = 1 words ending in 1, one
word per CS frame with words queued on both sides, 3000 random words over
all modes, bit orders, dividers and CS timings, full FIFOs with the
overrun and underrun strobes; `miso_oe_o` is checked on every cycle.
`tb/sim_axi.cpp` drives `spi_axi4lite`: reset values and decode errors,
TX_DATA / CTRL.start / RX_DATA, IRQ_STAT write-1-to-clear, the err flag
and a random loopback.

**SVA assertions** (`tb/spi_assertions.sv`, 9 properties, checked on every
cycle): SCK only moves to CPOL while idle, SCK at CPOL after two idle
cycles, no SCK change on the edge a CS asserts, MOSI quiet during idle,
at most one CS active, busy high while CS asserted, no done during idle,
done never two cycles long, bounded busy duration.

**Functional coverage** (`tb/spi_cov.sv`, 12 bins): 4 mode bins, MSB-first,
LSB-first, TX full, TX empty, RX full, RX empty, slave responded, alt-CS
used. Coverage report printed at end of `make test`; every bin must be hit.

**Beyond `make test`**: a cocotb regression with independent master and
slave models and cocotbext-spi / cocotbext-axi cross-checks, SymbiYosys
proofs, gate-level checks and parameter checks. Commands, results and tool
versions: [`VERIFICATION.md`](VERIFICATION.md).

## Verification status

Verified (details in [`VERIFICATION.md`](VERIFICATION.md)):

- Simulation on two simulators, Verilator and Icarus: the C++ regressions
  with SVA and coverage, and the cocotb regression.
- Formal (SymbiYosys, unbounded proofs): `spi_fifo`, the `spi_master`
  control logic at small parameters, the AXI4-Lite protocol engine and
  register file.
- Gate-level glitch check of CS_n and MOSI on a generic gate netlist with
  random gate delays; `spi_fifo` against its iCE40 block-RAM netlist.
- Open-source synthesis and place-and-route (Yosys, nextpnr) for iCE40
  UP5K and ECP5-25, without pin constraints or I/O timing.

Not verified:

- Vendor tools (Vivado, Quartus, Diamond, Gowin): no synthesis,
  implementation or timing closure.
- Real hardware: no FPGA board test, no silicon or ASIC flow.
- Gate-level timing with vendor cell models or SDF.
- Metastability MTBF and CDC sign-off of the slave synchronizers.
- Formal coverage of `spi_slave` and of the `spi_axi4lite` register side
  effects (simulation only).
- The VHDL wrapper in simulation, and power.

## License

Dual-licensed: **GPL-3.0-or-later** for open-source projects, **commercial license** for closed-source (SPDX: `GPL-3.0-or-later OR LicenseRef-Commercial`). See [`LICENSE.md`](LICENSE.md).

## Repository layout

```
rtl/             spi_fifo.sv  spi_master.sv  spi_slave.sv  spi_top.sv
                 spi_axi4lite.sv  axi4lite_slave.sv  axi4lite_regfile.sv
tb/              sim_main.cpp  spi_top_tb.sv  spi_assertions.sv  spi_cov.sv
                 sim_axi.cpp  spi_axi4lite_tb.sv
tests/cocotb/    cocotb suites, reference models, run.py / regress.py
tests/gatelevel/ gate-level glitch check, FIFO vs iCE40 RAM netlist
tests/params/    elaboration-time parameter checks
formal/          SymbiYosys jobs: fifo, master, axi
vhdl_wrapper/    VHDL-2008 entity for mixed-language designs
scripts/         pnr.py  synth_report.sh  update_numbers.py  vhdl_cosim.sh
.github/         CI workflow, commercial license request form
Makefile         lint / sim / test / cocotb / waves / formal / gatelevel / params /
                 vhdl-test / synth / synth_report / pnr / doc-numbers / clean
VERIFICATION.md  what was verified for v1.1, how to repeat it, what was not
CHANGELOG.md     release notes and interface changes
```

## Citation

```bibtex
@misc{spi-fpga-core,
  title  = {{spi-fpga-core}: a parameterizable SPI master + slave IP core in SystemVerilog},
  author = {Achour, Ayoub},
  year   = {2026},
  howpublished = {\url{https://github.com/ayoub-ac/spi-fpga-core}}
}
```

## References

- Motorola, *M68HC11 Reference Manual* §8.5, Serial Peripheral Interface (SPI).
- ARM IHI 0022, *AMBA AXI and ACE Protocol Specification* (for the AXI4-Lite wrapper).

## Author

Ayoub Achour - [github.com/ayoub-ac](https://github.com/ayoub-ac)
