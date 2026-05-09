# Resource estimates

Numbers below are from Yosys 0.x generic synthesis (`make synth_report`).
They are pre-place-and-route. See `SYNTH_REPORT.md` for the most recent
run; this document explains what dominates the area and how to shrink
it.

## Summary

| Target                         | Comment |
|--------------------------------|---------|
| Lattice iCE40 UP5K             | Default loopback top (master + slave + 4 FIFOs of 16x8) fits comfortably. The bit-clock generator (DIV_WIDTH-bit divisor) and the four FIFO storage arrays dominate the LUT count. |
| Lattice ECP5 LFE5UM-25         | `synth_ecp5 -abc9` packs the FIFO byte mux into LUT4 networks well. |
| Xilinx Artix-7 XC7A35T         | Yosys generic counts LUT6 + CARRY4. Vivado synth_design folds the FIFO storage into LUTRAM if the integrator opts in (`(* ram_style = "distributed" *)` on `mem_q`). |
| Cyclone V 5CSEMA5F31C6         | ALMs for the FIFOs + handshake; tiny. |
| Gowin GW1NR-9 (Tang Nano 9K)   | Not characterised. Comparable to ECP5 with vendor flow. |

For exact LUT/FF/BRAM numbers run `make synth_report` and read
`SYNTH_REPORT.md`.

## What dominates the area

For the default config (DATA_WIDTH = 8, NUM_CS = 4, FIFO_DEPTH = 16,
DIV_WIDTH = 16, CS_DLY_W = 8):

* **FIFO storage**: 4 FIFOs (master TX/RX + slave TX/RX) of 16 x 8 = 128 FFs each = 512 FFs total. With Yosys generic synth this stays as flops; with Vivado / Quartus the integrator can opt into LUTRAM.
* **Bit-clock divisor**: 16-bit count-down counter + 16-bit reload register. ~16 LUT and 32 FF.
* **Edge counter**: log2(2*DATA_WIDTH+2) bits for bit-progression in SHIFT state. ~5 FF.
* **Shift registers**: 4 x DATA_WIDTH = 32 FF for master TX/RX shift + slave TX/RX shift.
* **Master FSM**: 5 states + delay counter + cs_n decoder. ~30 FF, ~40 LUT.
* **Slave edge detection + bit counter**: 2-FF synchroniser per input + bit counter. ~10 FF, ~10 LUT.
* **Loopback top**: just wiring; no extra logic.

Total for the loopback top: roughly 600-1500 LUT and 700-900 FF on
iCE40 / ECP5 / Artix-7 generic synth. Master-only or slave-only builds
land at roughly half of those numbers.

## What you can do to shrink it

* **Reduce FIFO_DEPTH** if your CPU keeps up: a depth-4 FIFO is enough for many bare-metal use cases. Storage drops linearly.
* **Reduce DIV_WIDTH** if you only need a fixed-ratio SCK. DIV_WIDTH = 8 supports ratios 1..256 at clk_i/2..clk_i/512, plenty for most peripherals.
* **Drop the master or the slave** if you only need one direction. The loopback top is just for verification; ship `spi_master` or `spi_slave` standalone in production.
* **Drop the AXI4-Lite wrapper** if your CPU bus is a custom interface. The base master / slave have no bus dependency.

## What you can do to push throughput

The master can issue back-to-back bytes with `cs_inter_byte_i = 1`,
incurring only the FIFO load + shift register reset between bytes
(typically `cs_hold_cycles_i + 2` cycles). At div = 0, SCK = clk_i/2;
at clk_i = 100 MHz that is 50 MHz SCK and 12.5 MB/s effective for an
8-bit word, ignoring the inter-byte gap.

For higher sustained throughput, increase DATA_WIDTH (16 / 24 / 32-bit
words are supported). The shift register width grows linearly; the
inter-byte gap stays the same, so the per-byte overhead amortises.

## Frequency

* iCE40 UP5K, default toolchain (Yosys + nextpnr), no constraints: ~80-100 MHz typical.
* ECP5: ~150-200 MHz on generic synth.
* Artix-7 -1 speed grade: ~250+ MHz typical with Yosys; 300+ MHz with Vivado.
* Cyclone V: similar to Artix-7 with vendor flow.

The slave's SCK input must be at most ~clk_i/4 to avoid aliasing in
the 2-FF synchroniser; for higher SCK rates use a dedicated CDC
primitive.

## Power

Ballpark: 3-5 mW dynamic on iCE40 UP5K at 50 MHz with bus-active. The
bit-clock generator toggles every 2*(div+1) cycles; the FIFOs only
toggle on a push or pop. Static power dominated by the FPGA itself.

## How to reproduce

```bash
make synth_report
```

This runs Yosys with `synth_ice40`, `synth_ecp5` (with `-abc9`), and
`synth_xilinx`, then `stat`, and writes the result to
`SYNTH_REPORT.md`. Vendor flows (Vivado, Quartus) are detected
automatically when on PATH.
