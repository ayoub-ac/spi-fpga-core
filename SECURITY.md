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
implemented exactly per the M68HC11 reference: the idle SCK polarity
matches CPOL, the sample edge is the first SCK edge for CPHA = 0 and
the second for CPHA = 1, and CS asserts before SCK starts toggling
(`p_cs_before_sck` SVA enforces this on every cycle of every test).
The 1000-byte random loopback regression cross-validates master and
slave under each mode.

### 2. CS isolation

The master drives at most one chip-select line low at any time
(`p_cs_one_hot_active`). A regression that breaks this (e.g. via a
bad cs_sel_i decode) fails the gate. This is a meaningful safety
property in a multi-slave bus where two devices on the same MOSI line
would short their drivers if both saw CS low simultaneously.

### 3. Reset hygiene

`rst_ni` is synchronous active-low and returns the FSM, FIFOs, shift
registers, and SCK to a clean idle state. Mid-transfer reset is
verified in `test_mid_reset` of the regression — the next transfer
launches successfully from the clean state.

### 4. FIFO overflow detection

`tx_full_o` and `rx_full_o` are exposed so the integrator can avoid
silently dropping bytes. The optional `irq_err_o` in `spi_top` /
`spi_axi4lite` pulses when an RX byte completes against an already
full FIFO. The shipped FIFO drops the new byte rather than corrupting
the existing queue (back-pressure on the SPI bus is not possible at
the protocol level — the master controls the clock).

## What is NOT covered (be honest)

This list is exhaustive so purchasers can evaluate fit:

* **Confidentiality.** SPI carries cleartext bytes. If you need to
  protect those bytes from a logic-analyser-equipped attacker, encrypt
  at the application layer. The sibling AES-128 / AES-XTS / ChaCha20
  cores are designed for exactly this.
* **Authenticity.** SPI does not authenticate either party. A man-in-
  the-middle on the PCB can impersonate either side. Use a MAC
  (sibling SHA / Poly1305 cores) at the application layer.
* **Side channels via timing.** The core is not constant-time across
  data values; the bit-clock generator and FIFO occupancy depend on
  the transfer length. SPI is rarely used for cryptographic key
  exchange where timing leakage matters; if you need a constant-time
  bus, this is not the right transport.
* **Power-analysis resistance.** No masking, no random delay
  insertion. The Premium roadmap includes a hardened variant for
  high-assurance use cases.
* **Glitch / fault-injection on the FSM.** A flipped flop in the
  bit-counter can desynchronise the master from the slave; recovery
  is via reset. No duplicated-FSM / parity-protected variant ships
  in the GPL tier.
* **CDC across a separate SCK domain.** The shipped slave uses 2-FF
  synchronisers on SCK and CS_n; this is the standard practice but
  cannot fully protect against deeply metastable inputs. For very
  fast bus speeds (close to the system clock), use a dedicated CDC
  primitive.

## Test methodology

The Verilator regression (`make test`) runs:

1. **Single-byte loopback in modes 0/1/2/3** — master sends byte X, slave responds with byte Y; verify both ends receive the expected counterpart.
2. **MSB-first / LSB-first** — endian-swapped transfer in mode 0.
3. **Clock-divisor sweep** — SCK at ~1, ~10, ~25 MHz from a 100 MHz system clock.
4. **CS setup/hold timing** — measure the cycle gap between cs_n falling and the first SCK edge; verify it meets the configured `cs_setup_cycles_i`.
5. **Multi-byte FIFO loopback** — 4-byte streaming via `cs_inter_byte_i = 1`.
6. **Mid-transfer reset recovery** — assert reset during a transfer, then run a fresh transfer and verify it succeeds.
7. **Alternate chip-select** — `cs_sel_i = 1` to verify the multi-CS decode.
8. **1000-byte random loopback** — round-trip 1000 random bytes through the master/slave loopback; verify byte-for-byte match.

Plus 7 SVA properties enforced on every cycle of every test
(see `tb/spi_assertions.sv`) and 11 functional-coverage bins
(see `tb/spi_cov.sv`).

## Reporting issues

Found a bug, a side channel we missed, or a discrepancy with the
Motorola SPI block guide? Open an issue or email the address listed
in the README. Coordinated disclosure welcome. We do not currently
offer a bug bounty.
