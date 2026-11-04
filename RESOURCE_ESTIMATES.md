# Resource estimates

Numbers below come from the open-source flow: Yosys synthesis and
nextpnr place-and-route (`make pnr`) for iCE40 and ECP5, Yosys alone
(`make synth_report`, [`SYNTH_REPORT.md`](SYNTH_REPORT.md)) for Xilinx
7-series. `make doc-numbers` reruns both and rewrites the measured
tables in this file, README.md and VERIFICATION.md. This document also
explains what dominates the area and how to shrink it.

## Summary

<!-- BEGIN GENERATED pnr-table: make doc-numbers rewrites this section -->
Measured by `make pnr` (Yosys 0.70+286, nextpnr-0.11.1-85-ga2c64799,
OSS CAD Suite 2026-11-01), default parameters, nextpnr seeds 1, 2, 3.
Fmax is the register-to-register limit, without pin constraints or I/O timing.

| Design | Device | LUT4 | FF | RAM cells | Fmax (MHz) | nextpnr target |
|---|---|---|---|---|---|---|
| spi_top | iCE40 UP5K SG48 | 420 | 294 | 4 SB_RAM40_4K | 36.5 .. 38.3 | 50 MHz: not met |
| spi_top | ECP5 LFE5U-25F CABGA256 | 506 | 234 | 8 TRELLIS_DPR16X4 | 120.8 .. 124.3 | 100 MHz: met |
| spi_axi4lite | iCE40 UP5K SG48 | 1279 | 824 | 2 SB_RAM40_4K | 34.8 .. 37.8 | 50 MHz: not met |
| spi_axi4lite | ECP5 LFE5U-25F CABGA256 | 1430 | 794 | 4 TRELLIS_DPR16X4 | 112.8 .. 124.2 | 100 MHz: met |
<!-- END GENERATED pnr-table -->

| Target                         | Comment |
|--------------------------------|---------|
| Lattice iCE40 UP5K             | Placed and routed. Each 16 x 8 FIFO maps to one SB_RAM40_4K block RAM. |
| Lattice ECP5 LFE5U-25F         | Placed and routed with `synth_ecp5 -abc9`. Each 16 x 8 FIFO maps to two TRELLIS_DPR16X4 LUT RAMs. |
| Xilinx Artix-7 XC7A35T         | Yosys `synth_xilinx` only (FIFOs to RAM32M), see SYNTH_REPORT.md. Vivado was not run. |
| Cyclone V 5CSEMA5F31C6         | Not characterised (Quartus not run). |
| Gowin GW1NR-9 (Tang Nano 9K)   | Not characterised. |

The place-and-route Fmax comes from a wrapper that shifts every core
port through a serial register chain, because the cores have more ports
than these packages have pins: it is the internal register-to-register
limit, with no pin constraints and no I/O timing. Yosys results depend on
the source paths, so `scripts/pnr.py` and `synth_report.sh` read the RTL
through repository-relative paths; the Fmax of a seed also moves with
unrelated netlist changes, so read the seed range rather than one value.

## What dominates the area

For the default config (DATA_WIDTH = 8, NUM_CS = 4, FIFO_DEPTH = 16,
DIV_WIDTH = 16, CS_DLY_W = 8):

* **FIFO storage**: 4 FIFOs (master TX/RX + slave TX/RX) of 16 x 8 bits. Yosys infers memory: block RAM on iCE40 (SB_RAM40_4K), LUT RAM on ECP5 (TRELLIS_DPR16X4) and Xilinx (RAM32M), so the storage is not built from flip-flops. Each FIFO adds read / write pointers, a count and a registered read port.
* **Bit-clock divisor**: a DIV_WIDTH-bit count-down counter and the DIV_WIDTH-bit divisor latched at the start of a transfer.
* **Edge and bit counters**: $clog2(2*DATA_WIDTH+1) and $clog2(DATA_WIDTH+1) bits for the bit progression in SHIFT state.
* **Shift registers**: 4 x DATA_WIDTH bits for master TX/RX shift + slave TX/RX shift.
* **Master FSM**: 6 states, the configuration latched at start, a CS_DLY_W+1-bit delay counter and NUM_CS registered CS_n outputs.
* **Slave edge detection + bit counter**: 2-FF synchronizers on SCK, CS_n and MOSI, edge detection, a bit counter and the configuration latched at CS_n fall.
* **AXI4-Lite wrapper**: the 16-register file is built from flip-flops and dominates `spi_axi4lite` (which contains the master only).
* **Loopback top**: just wiring; no extra logic.

## What you can do to shrink it

* **Reduce FIFO_DEPTH** if your CPU keeps up: a depth-4 FIFO is enough for many bare-metal use cases. Any depth >= 1 is legal; whether the storage maps to block RAM, LUT RAM or flip-flops depends on depth and family.
* **Reduce DIV_WIDTH** if you only need a fixed-ratio SCK. DIV_WIDTH = 8 supports ratios 1..256 at clk_i/2..clk_i/512, plenty for most peripherals.
* **Drop the master or the slave** if you only need one direction. The loopback top is just for verification; ship `spi_master` or `spi_slave` standalone in production.
* **Drop the AXI4-Lite wrapper** if your CPU bus is a custom interface. The base master / slave have no bus dependency.

## What you can do to push throughput

The master can issue back-to-back words with `cs_inter_byte_i = 1`.
Between the last SCK edge of a word and the first SCK edge of the next,
CS_n stays low for `cs_hold_cycles_i + cs_setup_cycles_i + clk_div_i + 4`
clk_i cycles, so a burst word takes
`2 * DATA_WIDTH * (clk_div_i + 1) + cs_setup_cycles_i + cs_hold_cycles_i + 3`
cycles. At div = 0, SCK = clk_i/2; with setup = hold = 0 an 8-bit word
takes 19 cycles, about 5.3 MB/s at clk_i = 100 MHz.

For higher sustained throughput, increase DATA_WIDTH (DATA_WIDTH >= 2;
2..64 bits simulated). The shift register width grows linearly; the
inter-word gap stays the same, so the per-word overhead amortises.

## Frequency

* iCE40 UP5K and ECP5: see the table above. Neither is a timing sign-off:
  no pin constraints, no I/O timing, no vendor timing analysis.
* Artix-7, Cyclone V, Gowin: not measured (no vendor tools available).

The slave samples SCK, CS_n and MOSI through 2-FF synchronizers, so its
SCK period must be at least 4 clk_i periods plus the slave
clock-to-output, the board round trip and the master's MISO setup, with
SCK high and low times of at least 2 clk_i periods each (see
[`PORT_DESCRIPTION.md`](PORT_DESCRIPTION.md)).

## Power

Not measured or estimated. Use the vendor power estimator for your
device with your clock and toggle rates.

## How to reproduce

```bash
make pnr            # iCE40 / ECP5 synthesis and place-and-route, build/pnr/
make synth_report   # SYNTH_REPORT.md: Yosys iCE40 / ECP5 / Xilinx
make doc-numbers    # both, then rewrite the tables in the docs
```

`make synth_report` runs Yosys with `synth_ice40`, `synth_ecp5` (with
`-abc9`), and `synth_xilinx`, then `stat`, and writes the result to
`SYNTH_REPORT.md`. Vendor flows (Vivado, Quartus) are detected
automatically when on PATH.
