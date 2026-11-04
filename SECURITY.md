# Security model

This IP core implements the Motorola Serial Peripheral Interface (SPI)
master and slave. SPI is a transport, not a security primitive — it
carries whatever bytes the higher layer puts on it, in cleartext, and
imposes no authentication or confidentiality. This document describes
what the core does protect, what it does not, and how to evaluate the
claims.

## What is protected

### 1. Protocol-level correctness

The four Motorola SPI modes (CPOL x CPHA = {0, 1, 2, 3}) are each
implemented per the M68HC11 reference: the idle SCK polarity matches
CPOL, the sample edge is the first SCK edge for CPHA = 0 and the second
for CPHA = 1, and SCK rests at CPOL while every CS is high and never
changes on the clock edge where a CS asserts. Three SVA properties check
this on every cycle of `make test` (`p_sck_idle_to_cpol`,
`p_sck_idle_polarity`, `p_sck_quiet_cs_fall`), and the SymbiYosys master
proof covers it (`a_sck_*`, `a_cs_fall_sck_*`). The random loopback
regressions (3000 words in `make test`, more in the cocotb suites)
cross-validate master and slave in every mode.

### 2. CS isolation

The master drives at most one chip-select line low at any time
(`p_cs_one_hot_active`). A regression that breaks this (e.g. via a
bad cs_sel_i decode) fails the gate. This is a meaningful safety
property in a multi-slave bus where two devices on the same MOSI line
would short their drivers if both saw CS low simultaneously. CS_n is
driven from flip-flops, and `make gatelevel` checks a gate netlist with
random gate delays for glitches on every CS_n line. A `cs_sel_i` value
>= NUM_CS asserts no CS line.

### 3. Reset hygiene

`rst_ni` is synchronous active-low and returns the FSM, FIFOs, shift
registers, and SCK to a clean idle state. Mid-transfer reset is
verified in `test_mid_reset` of the regression — the next transfer
launches successfully from the clean state. After a reset with CS_n low,
the slave ignores the rest of that frame instead of re-arming in the
middle of it (cocotb `test_s_reset_mid_transfer`).

### 4. FIFO overflow detection

`tx_full_o` and `rx_full_o` are exposed so the integrator can avoid
silently dropping words. A word the master receives while its RX FIFO
is full is dropped, without corrupting the existing queue, and
`irq_err_o` (`spi_top`) / IRQ_STAT.err (`spi_axi4lite`) records it: one
pulse per dropped word, none without loss. The slave reports the same
case on `rx_overrun_o`, and a word sent with an empty TX FIFO (zeros) on
`tx_underrun_o`. Back-pressure on the SPI bus is not possible at the
protocol level — the master controls the clock.

## What is NOT covered (be honest)

This list is exhaustive so purchasers can evaluate fit:

* **Confidentiality.** SPI carries cleartext bytes. If you need to
  protect those bytes from a logic-analyser-equipped attacker, encrypt
  at the application layer with a vetted cipher implementation.
* **Authenticity.** SPI does not authenticate either party. A man-in-
  the-middle on the PCB can impersonate either side. Use a vetted MAC
  implementation at the application layer.
* **Side channels via timing.** The core is not constant-time across
  data values; the bit-clock generator and FIFO occupancy depend on
  the transfer length. SPI is rarely used for cryptographic key
  exchange where timing leakage matters; if you need a constant-time
  bus, this is not the right transport.
* **Power-analysis resistance.** No masking, no random delay
  insertion.
* **Glitch / fault-injection on the FSM.** A flipped flop in the
  bit-counter can desynchronise the master from the slave; recovery
  is via reset. There is no duplicated-FSM or parity-protected variant.
* **CDC across a separate SCK domain.** The slave passes SCK, CS_n and
  MOSI through 2-FF synchronizers; this is the standard practice but
  cannot fully protect against deeply metastable inputs. No MTBF figure
  was calculated and no CDC tool was run; the synchronizer behaviour was
  only emulated in simulation with input delays of 0..1 clk_i. The SCK
  period must be at least 4 clk_i periods plus the slave clock-to-output,
  the board round trip and the master's MISO setup (see
  [`PORT_DESCRIPTION.md`](PORT_DESCRIPTION.md)); for faster buses use a
  dedicated CDC primitive.

## Test methodology

The Verilator regression (`make test`) runs, on `spi_top`:

1. **Single-byte loopback in modes 0/1/2/3** — master sends byte X, slave responds with byte Y; verify both ends receive the expected counterpart.
2. **MSB-first / LSB-first** — endian-swapped transfer in mode 0.
3. **Clock-divisor sweep** — SCK at ~1, ~10, ~25 MHz from a 100 MHz system clock.
4. **CS setup/hold timing** — measure the cycle gap between cs_n falling and the first SCK edge; verify it meets the configured `cs_setup_cycles_i`.
5. **Multi-byte FIFO loopback** — 4-byte streaming via `cs_inter_byte_i = 1`.
6. **Mid-transfer reset recovery** — assert reset during a transfer, then run a fresh transfer and verify it succeeds.
7. **Alternate chip-select** — `cs_sel_i = 1` to verify the multi-CS decode.
8. **1000-byte random loopback** — round-trip 1000 random bytes in mode 0; verify byte-for-byte match.
9. **CPHA = 1 last bit** — 64 words whose last bit is 1 or 0, modes 1 and 3, MSB / LSB first.
10. **One word per CS frame** — several words queued on both sides; the slave sends each queued word exactly once.
11. **Random loopback, all modes** — 3000 random words over all modes, bit orders, dividers, CS setup / hold, single words and bursts.
12. **Full FIFOs** — one `irq_err_o` and one `s_rx_overrun_o` strobe per dropped word, none without loss; one `s_tx_underrun_o` strobe for a frame with an empty slave TX FIFO.

On every cycle it checks `miso_oe_o == !cs_n_o[0]`, the 9 SVA
properties of `tb/spi_assertions.sv`, and collects the 12 functional
coverage bins of `tb/spi_cov.sv` (all must be hit). A second harness
runs five tests on `spi_axi4lite`. The cocotb regression, the formal
proofs, the gate-level and parameter checks, and what was not verified
are described in [`VERIFICATION.md`](VERIFICATION.md).

## Reporting issues

Found a bug, a side channel we missed, or a discrepancy with the
Motorola SPI block guide? Open an issue on GitHub. For a report you do
not want to make public, open an issue that asks for a private contact
without the details. Coordinated disclosure welcome. We do not currently
offer a bug bounty.
