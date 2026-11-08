# Verification of spi-fpga-core v1.1

This document records what was verified for v1.1, how to repeat it, the
results, and what was not verified. Every number below was measured with
the commands shown, on the tool versions listed in section 1; anything not
measured is called out as such.

## 1. Environment

| Tool | Version |
|---|---|
| Verilator | 5.053 devel (rev v5.052-654-gdd8a7d5e4) |
| Icarus Verilog | 14.0 devel (s20260301-794-gdfc800afd) |
| Yosys | 0.70+286 (git 63eecf3dd) |
| SymbiYosys / Yices | SBY v0.70-9-g79fc9ed / Yices 2.7.0 |
| nextpnr-ice40, nextpnr-ecp5 | nextpnr-0.11.1-85-ga2c64799 |
| GHDL | 7.0.0-dev |
| Python / cocotb | Python 3.11.6 / cocotb 2.1.0.dev0 (bundled with the OSS CAD Suite); the ci profile also with cocotb 2.1.0 from PyPI, as the CI installs it |
| cocotbext-axi / cocotbext-spi | 0.1.28 / 0.5.0 |
| matplotlib / vcdvcd (plots only) | 3.11.2 / 2.6.0 |
| C++ compiler | g++ 15.2.0 |
| Host | Ubuntu 26.04 under WSL2 (Linux 6.6, 8 virtual CPUs), OSS CAD Suite build |

## 2. How to run

All commands run from the repository root with the OSS CAD Suite (or the
tools above) on `PATH`.

| Command | What it does | Time measured |
|---|---|---|
| `make lint` | Verilator `-Wall` lint of the spi_top testbench, `spi_axi4lite` and its testbench | 0.2 s |
| `make test` | Verilator C++ regressions: `tb/sim_main.cpp` (spi_top loopback, SVA, coverage) and `tb/sim_axi.cpp` (spi_axi4lite) | 10 s incl. both Verilator builds |
| `make cocotb` | cocotb regression, profile `ci`, on Verilator and Icarus | 5 min 17 s (209 s + 108 s) |
| `COCOTB_PROFILE=long make cocotb` | long random runs, all word widths (Icarus: the AXI suites) | 15 min 3 s (901 s + 2 s) |
| `make formal` | SymbiYosys: 25 tasks (proofs, bounded check, covers) | 42 s |
| `make gatelevel` | CS_n / MOSI glitch check on a gate netlist with random gate delays; spi_fifo against its iCE40 block-RAM netlist | 25 s |
| `make params` | 32 legal / illegal parameter sets through Yosys, Verilator, Icarus | 4.0 s |
| `make pnr` | Yosys + nextpnr, iCE40 UP5K and ECP5-25, 3 seeds | 37 s |
| `make doc-numbers` | `make pnr` and `make synth_report`, then `scripts/update_numbers.py` rewrites the generated tables in README.md, RESOURCE_ESTIMATES.md and this file | 106 s (repository on the Windows file system under WSL) |
| `make waves` | timing plots (PNG) in `tests/cocotb/results/waves/` | 28.6 s |
| `make vhdl-test` | GHDL analysis and mixed-language elaboration check of the VHDL wrapper | 0.6 s |

`make lint test cocotb formal gatelevel params` takes about 7 minutes on
this host (WSL2 with 8 virtual CPUs). Every random
run uses a fixed seed, so every result here is reproducible.
`tests/cocotb/run.py SUITE --sim verilator|icarus [--w N] [--param K=V]
[--env K=V] [--testcase NAME] [--waves FILE]` runs one cocotb suite.

## 3. Results

### 3.1 `make lint`, `make test`

* `make lint`: clean (exit 0, no warning with the repository's `-Wall`
  flags).
* `make test`, spi_top harness (`tb/sim_main.cpp`): all 12 tests pass;
  every SVA property in `tb/spi_assertions.sv` (9 properties, checked on
  every cycle by `--assert`) holds; coverage 12/12 bins. The tests added
  in v1.1: 64 CPHA = 1 words whose last bit is 1 or 0 (modes 1 and 3,
  MSB / LSB first, two dividers), one word per CS frame with six words
  queued on both sides (the slave TX count drops by exactly one per
  frame), 3000 random words in 1377 CS frames over all modes, bit orders,
  dividers 1..7 and CS setup / hold 0..7, full FIFOs with exactly one
  `irq_err_o` and one `s_rx_overrun_o` strobe per dropped word and none
  without loss, one `s_tx_underrun_o` strobe for a frame with an empty
  slave TX FIFO, and `miso_oe_o == !cs_n_o[0]` on every cycle.
* `make test`, AXI4-Lite harness (`tb/sim_axi.cpp`): all 5 tests pass:
  reset values, ID / VERSION, DECERR on unaligned and unmapped addresses,
  byte strobes, TX_DATA push of the written value, CTRL.start from the
  same write (and reading back 0), RX_DATA head and pop, IRQ_STAT
  write-1-to-clear and masking, IRQ_STAT.err only on a dropped word, and
  1112 random words in 400 transfers over all modes, bit orders,
  dividers 0..3 and the four CS lines.

### 3.2 cocotb, profile `ci` (`make cocotb`)

| Simulator | Suites | Tests | Failed | Wall time | Simulator crashes |
|---|---|---|---|---|---|
| Verilator | 24/24 | 195 | 0 | 209 s | none |
| Icarus | 21/21 | 160 | 0 | 108 s | 4 at exit after the results (the four AXI suites) |

Suites: FIFO (DEPTH 1, 3, 4, 16, both read modes, 8000-cycle random
scoreboard each), master (W = 8, 13, 32, NUM_CS = 1), slave (W = 5, 8, 32,
TX words queued per frame and all at once), loopback (W = 8, 16),
cocotbext-spi cross-checks, AXI4-Lite (17 tests) and AXI4-Lite with
DATA_WIDTH = 64, NUM_CS = 1 / SPI_DATA_W = 16, DATA_WIDTH = 64 /
SPI_DATA_W = 32. Icarus skips the W = 32 master and slave runs and the
NUM_CS = 1 master run, and runs the W = 13 master with DIV_WIDTH = 10
(at DIV_WIDTH = 16 the maximum-divider test alone took 153 s on Icarus
for W = 32).

Simulator crashes: the embedded Python of both simulators occasionally
segfaults inside libpython3.11 (`dmesg` shows the fault in
`libpython3.11.so`), either before the first test starts or after the
results file is complete. A crash before any result is written is
retried up to twice by `run.py` and reported; a crash at exit after the
results file is complete is reported and the results count. Neither is a
test result. Earlier runs of the same profile also had single crashes
before any result, which the retry absorbed. The rate varies over time:
during the reruns for the timing restructure (3.9) two Icarus runs lost
10 and 2 suites to crashes before any result, and the unmodified v1.1
RTL crashed the same way in the same period; the tables show complete
runs. The cause was not found.

### 3.3 cocotb, profile `long`

| Simulator | Suites | Tests | Failed | Wall time | Simulator crashes |
|---|---|---|---|---|---|
| Verilator | 89/89 | 642 | 0 | 901 s | none |
| Icarus | 4/4 | 23 | 0 | 2 s | 4 at exit after the results |

Suites: FIFO DEPTH 1..17 and 32 in both read modes (36 suites); master,
slave, loopback and both cocotbext-spi cross-checks at W = 2, 4, 5, 8,
13, 16, 32 and 64 (40 suites); 10000-word master, slave and loopback
runs and a 5000-word slave run with every TX word queued first (W = 8);
master at NUM_CS = 1, NUM_CS = 3 and FIFO_DEPTH = 3, slave at
FIFO_DEPTH = 1, loopback at FIFO_DEPTH = 5; the AXI4-Lite suites on both
simulators.

Random traffic, all without a data error:

* master: 40029 words in 12 runs against the independent slave model,
  no protocol violation; 4910 SCK edges while every CS_n was high, all
  moving SCK to the sampled `cpol_i`;
* slave: 40033 words in 14035 frames in 11 runs from the asynchronous
  master model (SCK = clk/4 .. clk/16, random phase and jitter);
* loopback (repo master to repo slave): 36012 words in 10 runs.

Slave timing at every word width: every SCK period >= 3.1 T works
without input skew (>= 3.0 T at W = 2, 4, 5) and >= 4.0 T with the
0..1 T input skew (>= 3.8 T at W = 2, >= 3.9 T at W = 4); the first word
is correct for every CS setup swept (0.3 T .. 3.5 T); MOSI hold failures
only below 1 T; no failure for any CS high time swept. The first run of
this profile failed four suites at non-default FIFO depths and at W = 2
because the tests queued more words than the FIFOs hold or wrote values
wider than W; the tests were corrected (commit 870e93c), the RTL was not
changed.

### 3.4 Timing behaviour measured in simulation

Master (`clk` = clk_i period T; from the cocotb master suite):

* CS_n fall to first SCK edge = `cs_setup_cycles_i + clk_div_i + 3` T;
  last SCK edge to CS_n rise = `cs_hold_cycles_i + 1` T, without
  exception over the 40 configurations of the W = 8 matrix (all modes,
  MSB / LSB first, clk_div_i 0, 1, 2, 3, 7, cs_setup 0, 1, 3, 6, 255,
  cs_hold 0, 1, 2, 5, 255, 1..4 words, all four CS lines).
* With cs_inter_byte_i = 1, last SCK edge of a word to first SCK edge
  of the next = `cs_setup_cycles_i + cs_hold_cycles_i + clk_div_i + 4` T,
  CS_n low throughout, in all 120 burst configurations of the matrix
  runs of the ci profile (W = 8, 13, 32, NUM_CS = 1 and 4, both
  simulators).
* CPHA = 0: the first bit is on MOSI from one cycle after CS_n falls, at
  least `cs_setup_cycles_i + clk_div_i + 2` T before the first SCK edge.
* CS_n falls and busy_o rises on the clock edge that samples start_i
  (SCK already at CPOL); xfer_done_o is a one-cycle pulse one cycle after
  CS_n rises, and busy_o falls with it. A start with an empty TX FIFO,
  including one in the cycle of the first TX write, is ignored
  (`test_m_start_busy_done`, cs_setup 4, cs_hold 3, clk_div 2: start to
  first SCK edge 9 cycles, last sampling edge to xfer_done_o 8 cycles).
* SCK = clk_i / (2 (clk_div_i + 1)) measured for clk_div_i = 0, 255 and
  2^DIV_WIDTH - 1.
* MOSI hold after every sampling edge, including the last one of a
  CPHA = 1 frame: >= 1 T (the minimum, at clk_div_i = 0). SCK rests at
  CPOL from reset and while every CS_n is high; it never changes in the
  cycle CS_n falls.

Slave (asynchronous behavioural master with picosecond timing, random
phase, 0 / 3 % jitter, 16 configurations per point, W = 5, 8, 32):

| Quantity | Result |
|---|---|
| SCK period, no input skew | every period >= 3.1 T works (>= 3.0 T at W = 5); 69 periods from 8.0 T down to 1.6 T, 1104 three-word frames per sweep |
| SCK period, every SCK / MOSI / CS_n change delayed by 0..1 T independently (late synchronizer resolution) | every period >= 4.0 T works |
| MISO setup left to the master at those limits (W = 8) | 0.06 T at 3.1 T; 0.21 T at 4.0 T with the delays (zero board delay) |
| CS_n fall to first SCK edge | correct first word at every value swept, 0.3 T .. 3.5 T (44 configurations) |
| CS_n high between frames | correct at every value swept, 0.3 T .. 3.0 T (modes 0 and 3) |
| MOSI hold after the last sampling edge | failures below 1 T (CPHA = 1), none from 1 T on |
| TX word written before CS_n falls | sent intact at every distance swept, 0.5 T .. 6 T |

The simulated limits have no board delay and a master that samples MISO
exactly on its edge. In hardware the SCK period must also cover the
slave clock-to-output, the board round trip and the master's MISO
setup: SCK period >= 4 T + t_co + t_board + t_su is the budget the 4.0 T
result supports. The slave header lists the requirements.

### 3.5 Formal (`make formal`)

25/25 tasks pass. Proofs are unbounded (k-induction), with whitebox
invariants where needed for induction.

| Job | Tasks | Assertions / covers | Parameters |
|---|---|---|---|
| fifo | 15 proofs, 3 cover runs | 36 / 8 | DEPTH 1, 2, 3, 4, 5, 12, 16 in both read modes; WIDTH 1 at DEPTH 4 |
| master | 3 proofs, 90-step bounded check of the busy bound, 1 cover run | 31 / 7 | DATA_WIDTH 4 and 8, FIFO_DEPTH 2 and 4, DIV_WIDTH 1, 2, 4, CS_DLY_W 1, 2, 3, NUM_CS 1 and 4 |
| axi | 1 proof, 1 cover run | 40 / 12 | axi4lite_slave + axi4lite_regfile, 16 registers, all four attribute types |

Assertion counts include the whitebox invariants. All inputs other than
the reset sequence are unconstrained (configuration may change at any
time); the AXI master side is constrained by the AXI4 handshake rules.

Mutation check of the properties (each mutant re-introduces a v1.0 defect
or a plausible slip into a copy of the RTL):

| Mutant | Result |
|---|---|
| FIFO drops the write on full + read | proof fails (counterexample) |
| FWFT head not refilled on a read | proof fails |
| no pointer wrap for non-power-of-2 DEPTH | proof fails (DEPTH 3 and 5) |
| standard-mode read data not held | proof fails |
| master SCK not parked at CPOL while idle | proof fails |
| master launches while SCK still moves to a new CPOL | proof fails |
| MOSI released after the last sampling edge | proof inconclusive at depth 12; bounded check finds the counterexample |
| overrun flag = RX full and done | proof inconclusive at depth 12; bounded check finds the counterexample |

### 3.6 Mutation check of the C++ regressions

Of 10 mutants run through `make test`, 9 fail it: MOSI released after the
last sampling edge, slave TX FIFO double pop, irq_err_o = RX full and
done, miso_oe_o stuck high, AXI TX push of the previous value, AXI start
ignored, AXI err without loss, AXI write-1-to-clear ignored, and the
v1.0 RX_DATA behaviour (RX FIFO in registered-read mode, so a read
returns the previously popped word). The survivor pops the RX FIFO one
cycle after the RX_DATA address handshake instead of on it; the
AXI4-Lite slave accepts at most one read every two cycles, so the
returned data cannot differ.

### 3.7 Gate-level checks (`make gatelevel`)

`spi_master` mapped by Yosys to generic gates, each gate with a random
transport delay of 60..259 ps and 150 ps clock-to-output on every
flip-flop, 120 transfers over all modes, dividers 0..2, CS setup / hold,
inter-byte modes, burst lengths and the four CS lines:

| RTL | CS_n edges | CS_n pulses < 2 ns | MOSI pulses < 2 ns | Result |
|---|---|---|---|---|
| v1.1 | 360 | 0 | 0 | pass |
| v1.0 (same bench) | 1686 | 659 (479 on the selected line, 180 on idle lines) | 121 | fail |

The same target compares spi_fifo with its `synth_ice40` netlist
(Yosys iCE40 cell models): DEPTH 16 and 32 map to one SB_RAM40_4K each,
DEPTH 5 to logic, both read modes. 200000 random cycles per
configuration, 13775..18308 of them a read and a write on a full FIFO
(the same RAM address in the standard read mode): every output matches
the RTL on every cycle in all six configurations.

### 3.8 Parameter checks (`make params`)

32/32: 15 legal parameter sets elaborate in Yosys, Verilator and Icarus;
17 illegal sets stop all three tools for the intended reason.

### 3.9 Place and route (`make pnr`)

Default parameters, Yosys `synth_ice40` / `synth_ecp5 -abc9`, nextpnr
seeds 1, 2, 3. Resource counts are from synthesis of the core alone.
Fmax is from a wrapper that serializes every core port (the cores have
more ports than these packages have pins): it is the internal
register-to-register limit, with no pin constraints and no I/O timing.
The v1.0 column is v1.0.0 (ae8319d) through the same script and tools.

<!-- BEGIN GENERATED pnr-v10: make doc-numbers rewrites this section -->
Measured by `make pnr` (Yosys 0.70+286, nextpnr-0.11.1-85-ga2c64799,
OSS CAD Suite 2026-11-01), default parameters, nextpnr seeds 1, 2, 3.

| Design | Device | LUT4 | FF | RAM cells | Fmax (MHz) | v1.0, same flow | Fmax vs v1.0 |
|---|---|---|---|---|---|---|---|
| spi_top | iCE40 UP5K SG48 | 348 | 291 | 4 SB_RAM40_4K | 55.0 .. 57.6 (target 50 MHz: met) | 417 LUT4, 286 FF, 39.9 .. 42.7 | 35 .. 38 % faster |
| spi_top | ECP5 LFE5U-25F CABGA256 | 298 | 231 | 8 TRELLIS_DPR16X4 | 213.4 .. 227.0 (target 100 MHz: met) | 363 LUT4, 226 FF, 154.0 .. 163.5 | 39 % faster |
| spi_axi4lite | iCE40 UP5K SG48 | 622 | 586 | 2 SB_RAM40_4K | 51.2 .. 53.4 (target 50 MHz: met) | 1338 LUT4, 794 FF, 36.4 .. 38.1 | 40 .. 41 % faster |
| spi_axi4lite | ECP5 LFE5U-25F CABGA256 | 729 | 556 | 4 TRELLIS_DPR16X4 | 178.5 .. 184.8 (target 100 MHz: met) | 1296 LUT4, 764 FF, 133.6 .. 138.0 | 34 % faster |
<!-- END GENERATED pnr-v10 -->

The numbers move with netlist changes that do not touch the critical
paths. Yosys keeps the source path of every cell in its src attributes
and the path changes the mapping: with absolute paths of one checkout
the ECP5 spi_top netlist had 599 LUT4 instead of 506 through `rtl/...`,
so `scripts/pnr.py` reads the RTL through repository-relative paths
(two checkouts in different directories then gave identical results).
Changing only the VERSION constant from 0x0001_0000 to 0x0001_0100
changed spi_axi4lite on ECP5 from 1260 to 1430 LUT4 and from
109.0 .. 115.3 to 112.8 .. 124.2 MHz, and on iCE40 from 34.2 .. 36.4 to
34.8 .. 37.8 MHz. Compare seed ranges, not single values.

The v1.1 bug fixes first made the cores slower than v1.0 (ECP5 spi_top
about 25 %): the critical paths ran from the master delay counter
compare, the start / CPOL check and the TX FIFO empty compare through
the FSM next-state logic into the TX FIFO write enable, the
configuration latch and the counters (7 LUT levels on iCE40, 6 on
ECP5). The timing restructure (commit a108c41)
changes no port waveform, latency or register semantic, and
every check in sections 3.1 to 3.8 was rerun on it. It registers the
terminal flags of the master counters and decodes the master
transitions from the current state, latches the slave sampling edge
type and registers its bit-counter flags, gives each AXI register its
own decode (read-only words lose their storage), and keeps the FIFO
empty / full flags in flip-flops. An unbounded SymbiYosys equivalence
check (outside the repository targets) compared every output on every
cycle against the RTL before the restructure, for small parameter sets
of every module. The ABC mapping now has 4 LUT levels on iCE40 and 3 to
4 on ECP5. The remaining critical paths are the slave sampling edge
into the TX FIFO pop (iCE40 spi_top), the AXI write decode into the
register byte enables (iCE40 spi_axi4lite), the master CS hold end into
the TX FIFO (ECP5 spi_top) and the RX_DATA read decode into the RX FIFO
pop (ECP5 spi_axi4lite).

### 3.10 Waveform plots (`make waves`)

`tests/cocotb/results/waves/`: one plot per master mode (CS setup,
edge-to-data timing, MOSI hold after the last sampling edge), the master
SCK idle level after a reset with CPOL = 1, the slave at SCK = clk/4 with
3 % jitter and at clk/3.1 (minimum MISO setup annotated), and the
CPHA = 1 loopback around the final sampling edge.

### 3.11 VHDL wrapper (`make vhdl-test`)

Passes: Verilator lint of the SV core, GHDL analysis of the wrapper
(VHDL-2008), every spi_top port mapped by the wrapper, and an
elaboration of the wrapper with the SystemVerilog core in Yosys with the
ghdl plugin for six generic sets (defaults, NUM_CS = 1, 2, 8,
FIFO_DEPTH = 64, DATA_WIDTH = 16) without any port width adaptation.
GHDL warns that component `spi_top` is unbound when it elaborates the
wrapper alone; Yosys binds it to the SystemVerilog module. This is not a
simulation.

### 3.12 Synthesis (`make synth`)

Yosys `synth_ice40`, `synth_ecp5 -abc9` and `synth_xilinx` of spi_top
complete (3.5 s). During `synth_xilinx` ABC prints one internal message,
"Abc_FrameUpdateGia(): Transformation has failed", and Yosys continues;
its effect on the Xilinx mapping was not analysed. The iCE40 / ECP5
counts of the same synthesis scripts are in 3.9. `make synth_report`
writes SYNTH_REPORT.md: Yosys counts of spi_top and spi_axi4lite for
iCE40, ECP5 and Xilinx 7-series (Vivado and Quartus skipped, not
installed).

## 4. Defects fixed in v1.1 and the tests that cover them

| v1.0 defect | Fix | Covered by |
|---|---|---|
| CPHA = 1: MOSI released on the clock edge of the final sampling edge (a synchronizing receiver loses the last bit) | a59c75b | cocotb `test_m_cpha1_last_bit_hold`, `test_t_loopback_cpha1_lastbit`; `make test` test 9; formal `a_mosi_last_bit_hold`; plot `loopback_cpha1_last_bit_mode1.png` |
| Slave TX FIFO popped twice per word: queued words skipped | 534184a | cocotb `test_s_prefetch_skip`, slave random run with all words queued, loopback random run; `make test` test 10 |
| SCK not at CPOL after reset and while idle; it moved in the cycle CS_n fell | a59c75b | cocotb `test_m_idle_level`; formal `a_sck_*`, `a_cs_fall_sck_*`; SVA `p_sck_idle_to_cpol`, `p_sck_idle_polarity`, `p_sck_quiet_cs_fall`; plot `master_sck_idle_cpol1_after_reset.png` |
| `irq_err_o` / IRQ_STAT.err = RX full and done: pulse without loss, one pulse for several lost words | a59c75b | cocotb `test_t_irq`, `test_m_rx_overrun`, `t15_overrun_error_flag`; `make test` test 12 and AXI test 4; formal `a_overrun_strobe` |
| Slave re-armed in the middle of a frame after a reset with CS_n low | 534184a | cocotb `test_s_reset_mid_transfer` |
| FIFO dropped the write of a read + write on a full FIFO | b218f7d | cocotb `t04_simul_push_pop_at_full`, `t07_random_scoreboard`; formal fifo proofs |
| CS_n decoded combinationally from the FSM state: glitches | a59c75b | `make gatelevel` |
| No synchronizers on the slave SCK / CS_n / MOSI inputs | 534184a | cocotb `test_s_ratio_sweep`, `test_s_ratio_sweep_meta`, `test_s_matrix`, `test_s_min_cs_setup`, `test_s_mosi_hold`, `test_s_cs_gap`, `test_x_slave_vs_cocotbext_master` |
| AXI: CTRL.start taken from the previous CTRL value | 400d288 | cocotb `t11_start_bit_semantics`, `t10_e2e_documented_flow`, `t17_write_strobes_on_side_effects`; AXI test 2 |
| AXI: TX_DATA pushed the previous TX_DATA value | 400d288 | cocotb `t12_tx_data_reaches_mosi`, `t17_write_strobes_on_side_effects`; AXI test 2 |
| AXI: RX_DATA returned the word popped by the previous read | 400d288 | cocotb `t13_rx_data_read_returns_head`; AXI test 2 |
| AXI: IRQ_STAT not readable, write-1-to-clear without effect | 400d288 | cocotb `t14_irq_status_w1c_and_mask`; AXI test 3 |
| AXI: err set without data loss | a59c75b | cocotb `t15_overrun_error_flag`; AXI test 4 |
| FIFO wrong for DEPTH not a power of 2, broken for DEPTH = 1; illegal parameters accepted | b218f7d | cocotb FIFO DEPTH 1..17 and 32 in both read modes; formal fifo DEPTH 1, 2, 3, 5, 12; `make params` |
| AXI: register decode wrong for DATA_WIDTH = 64 | 400d288 | cocotb `p01_map_and_decode`, `p02_transfer_loopback` at DATA_WIDTH = 64 |
| NUM_CS = 1 did not elaborate (zero-width cs_sel_i); words above 32 bits truncated counters; master parameters unchecked | a59c75b | master suite at NUM_CS = 1 (ci and long), master / slave / loopback suites at W = 64 (long); AXI `p01` / `p02` at NUM_CS = 1; formal `prf_cs1`; `make params` |
| AXI: parameters the register map cannot hold accepted (CS_DLY_W > 8 put cs_hold bits above bit 31, DATA_WIDTH other than 32 / 64, ...) | 400d288 | `make params` |
| Slave configuration live during a frame | 534184a | cocotb `test_s_config_latched` |
| No MISO output enable for a shared MISO line | 534184a | cocotb `test_s_miso_oe`; `make test` per-cycle check |
| No slave underrun / overrun status | 534184a | cocotb `test_s_tx_empty`, `test_s_rx_overrun`; `make test` test 12 |
| VHDL wrapper missing the FIFO count ports, cs_sel width fixed at 2 | 7658b3c | `make vhdl-test` (port-mapping check) |
| Build and scripts: piped targets passed on failure, `make clean` removed the committed SYNTH_REPORT.md, synth_report parsed an old Yosys format, vhdl-test passed on a mismatch | 7658b3c | the targets themselves |
| Testbench did not build on Verilator 5.05x (generated header names, multi-driven coverage bins) | 7658b3c | `make lint`, `make test` |

## 5. Interface changes from v1.0

New ports are appended at the end of each port list; all existing port
names are unchanged.

| Module | Change |
|---|---|
| spi_fifo | new parameter `FWFT` (default 0 = v1.0 read timing); new outputs `overflow_o`, `underflow_o`; a write on full is accepted when a read frees a slot in the same cycle; any `DEPTH` >= 1 |
| spi_master | new parameter `RX_FWFT` (default 0); new output `rx_overrun_o`; `cs_sel_i` is 1 bit wide for NUM_CS = 1; SCK follows `cpol_i` while idle and a start waits (kept pending) until SCK rests at the new CPOL; MOSI keeps the last bit after a frame instead of returning to 0 |
| spi_slave | new outputs `miso_oe_o`, `tx_underrun_o`, `rx_overrun_o`; 2-FF synchronizers add 2..3 clk_i of latency; `cpol_i` / `cpha_i` / `lsb_first_i` are latched when CS_n assertion is detected |
| spi_top | new outputs `miso_oe_o`, `s_tx_underrun_o`, `s_rx_overrun_o`; `m_cs_sel_i` is 1 bit wide for NUM_CS = 1; `irq_err_o` is one pulse per RX word dropped by the master (was RX full and done) |
| axi4lite_slave | new outputs `wr_event_data_o`, `wr_event_strb_o` |
| spi_axi4lite | ports unchanged. CTRL.start acts on the written data and reads 0; TX_DATA pushes the written value; RX_DATA returns the RX FIFO head and pops it (0 when empty); IRQ_STAT is readable and write-1-to-clear; IRQ_STAT.err means a dropped RX word; registers sit at 8 n for DATA_WIDTH = 64; VERSION reads 0x0001_0100 ({major[15:0], minor[7:0], patch[7:0]}); parameters the register map cannot hold stop elaboration (DATA_WIDTH not 32 / 64, CS_DLY_W > 8, NUM_CS > 256, FIFO_DEPTH > 65535, ...) |
| spi_top_vhdl | added the four FIFO count ports and the three new outputs; `m_cs_sel_i` width follows NUM_CS; NUM_CS = 1 allowed |

## 6. Not verified

* Vendor synthesis and implementation (Vivado, Quartus, Diamond, Gowin):
  not run, no vendor tools available. `make synth_report` skips them.
* Real hardware: no FPGA board test, no logic-analyzer capture, no
  silicon or ASIC flow.
* Timing closure with real pads: the P&R flow has no pin constraints and
  no I/O timing; the iCE40 results miss the 50 MHz target.
* Metastability MTBF and CDC structure: synchronizer behaviour is
  emulated with 0..1 clk_i input delays only; no MTBF calculation, no
  CDC tool.
* Formal coverage of spi_slave and of the spi_axi4lite register side
  effects (simulation only), and of the master beyond the small
  parameters listed in 3.5.
* Gate-level timing with vendor cell models or SDF; the glitch check uses
  generic gates with random delays.
* Other simulators (Questa, Xcelium, VCS, Riviera) and other cocotb
  versions.
* The VHDL wrapper in a VHDL simulation (only analysis, elaboration and
  the port mapping are checked).
* Power: no measurement or estimate was made.

## 7. Documentation and CI

README.md, PORT_DESCRIPTION.md, SECURITY.md, RESOURCE_ESTIMATES.md,
SYNTH_REPORT.md, LICENSE.md and vhdl_wrapper/README.md describe v1.1;
CHANGELOG.md lists the fixes and interface changes. Every synthesis and
P&R number in them comes from `make doc-numbers`.
`.github/workflows/ci.yml` runs lint, test, params, vhdl-test, the cocotb
ci profile on both simulators, formal and gatelevel on GitHub Actions
with OSS CAD Suite 2026-11-01 and cocotb 2.1.0 from PyPI; its commands
were run locally on a copy of the same OSS CAD Suite build.
