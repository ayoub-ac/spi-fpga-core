# spi-fpga-core

[![License: GPL-3.0-or-later or commercial](https://img.shields.io/badge/license-GPL--3.0%20%7C%20commercial-blue.svg)](LICENSE.md)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#verification)
[![Lint](https://img.shields.io/badge/Verilator%20lint-clean-brightgreen.svg)](#build--test)

A small, parametrizable SPI master + slave IP core in SystemVerilog.
Implements the four classical Motorola modes (CPOL x CPHA), MSB / LSB
first, programmable clock divider, multi-CS master, TX / RX FIFOs, and
an optional AXI4-Lite control wrapper. Targets iCE40, ECP5, Xilinx
7-series, Cyclone V, and Tang Nano 9K.

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
make lint test            # Verilator lint + 4-mode loopback + 1000 random
make synth_report         # SYNTH_REPORT.md across iCE40 / ECP5 / Xilinx
```

Requires Verilator 5.0+ for simulation, Yosys 0.30+ for the open synth
flow.

## Features

- SPI master with all four modes (CPOL x CPHA = 0..3), MSB / LSB first, programmable SCK divider, parametrizable word width (4..32 bits) and chip-select count (default 4).
- SPI slave with the same four modes, single CS input, configurable word width.
- Parametrizable sync FIFOs on TX and RX (default depth 16).
- Inter-byte CS hold for multi-byte streaming without de-asserting the slave.
- Programmable CS-to-SCK setup and hold delays.
- Interrupts: TX-empty, RX-full, transfer-complete, RX-overrun.
- Optional AXI4-Lite slave wrapper (`spi_axi4lite`) with 16 x 32-bit register map for software control.
- VHDL-2008 entity wrapper for mixed-language designs.

## Port table (master, abridged)

| Signal                | Dir | Width        | Description                                  |
|-----------------------|-----|--------------|----------------------------------------------|
| `clk_i / rst_ni`      | in  | 1            | Clock / sync active-low reset.               |
| `cpol_i / cpha_i`     | in  | 1            | Mode select.                                 |
| `lsb_first_i`         | in  | 1            | 0 = MSB first, 1 = LSB first.                |
| `clk_div_i`           | in  | DIV_WIDTH    | SCK = clk_i / (2*(div + 1)).                 |
| `cs_sel_i`            | in  | log2(NUM_CS) | Chip-select line to assert.                  |
| `cs_setup/hold_i`     | in  | CS_DLY_W     | CS-to-SCK setup / hold cycles.               |
| `cs_inter_byte_i`     | in  | 1            | Hold CS through multi-byte streams.          |
| `start_i`             | in  | 1            | One-cycle pulse to launch a transfer.        |
| `tx_we_i / wdata`     | in  | DATA_WIDTH   | Push word into TX FIFO.                      |
| `rx_re_i / rdata`     | in  | DATA_WIDTH   | Pop word from RX FIFO.                       |
| `busy_o / xfer_done_o`| out | 1            | Status / one-cycle done pulse.               |
| `sck_o / mosi_o`      | out | 1            | Bus outputs.                                 |
| `miso_i`              | in  | 1            | Bus input.                                   |
| `cs_n_o`              | out | NUM_CS       | Chip-selects, active-low.                    |

Full slave port list and timing diagrams in [`PORT_DESCRIPTION.md`](PORT_DESCRIPTION.md).

## Headline numbers

From `make synth_report` (Yosys, loopback top w/ master + slave + 4
FIFOs of 16 x 8):

| Target           | Result                  | Notes                  |
|------------------|-------------------------|------------------------|
| iCE40 UP5K       | see `SYNTH_REPORT.md`   | LUT4 generic, no DSP   |
| ECP5 LFE5UM-25   | see `SYNTH_REPORT.md`   | abc9 mapping           |
| Xilinx Artix-7   | see `SYNTH_REPORT.md`   | Yosys generic mapping  |

Vendor flows (Vivado / Quartus) typically pack 30-40% smaller. See
[`RESOURCE_ESTIMATES.md`](RESOURCE_ESTIMATES.md) for the breakdown.

## Build & test

```bash
make lint            # static check the RTL
make test            # build and run loopback + random-1000 regression
```

A passing run ends with `+PASS all tests passed` and exits 0.

## Verification

**Directed tests**: single-byte loopback in modes 0/1/2/3, MSB/LSB-first,
clock-divisor sweep at 1/10/25 MHz from 100 MHz, CS setup/hold timing,
multi-byte FIFO loopback, mid-transfer reset recovery, alternate
chip-select, 1000 random-byte round-trip.

**SVA assertions** (`tb/spi_assertions.sv`, 7 properties): CS asserted
before SCK edges, MOSI quiet during idle, at most one CS active, busy
high while CS asserted, done a one-shot pulse, no done during idle,
bounded busy duration.

**Functional coverage** (`tb/spi_cov.sv`, 11 bins): 4 mode bins,
MSB/LSB-first, TX/RX-full/empty, slave-responded, alt-CS used.
Coverage report printed at end of `make test`.

## License

Dual-licensed: **GPL-3.0-or-later** for open-source projects, **commercial license** for closed-source. See [`LICENSE.md`](LICENSE.md).

## Repository layout

```
rtl/    spi_fifo.sv  spi_master.sv  spi_slave.sv  spi_top.sv
        spi_axi4lite.sv  axi4lite_slave.sv  axi4lite_regfile.sv
tb/     sim_main.cpp  spi_top_tb.sv  spi_assertions.sv  spi_cov.sv
vhdl_wrapper/       VHDL-2008 entity for mixed-language designs
scripts/            synth_report.sh  vhdl_cosim.sh
Makefile  lint / sim / test / synth / synth_report / vhdl-test
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
