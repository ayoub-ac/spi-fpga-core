# Changelog

## 1.1.0 (unreleased)

An independent check of v1.0.0 found the defects below.
[`VERIFICATION.md`](VERIFICATION.md) §4 maps each defect to its commit
and the tests that cover it.

### Fixed

- Master, CPHA = 1: MOSI was released on the clock edge of the final
  sampling edge, so a synchronizing receiver lost the last bit.
- Master: SCK was not at CPOL after reset or while idle, and moved in
  the cycle CS_n fell.
- Master: CS_n was decoded combinationally from the FSM state and
  glitched at gate level; it is now driven from flip-flops.
- Master: NUM_CS = 1 did not elaborate, and word widths above 32 bits
  truncated internal counters.
- Slave: each queued TX word was popped twice, so words were skipped.
- Slave: SCK, CS_n and MOSI had no synchronizers; they now pass through
  2-FF synchronizers.
- Slave: a reset with CS_n low re-armed the slave in the middle of a
  frame.
- Slave: the mode configuration was live during a frame.
- `irq_err_o` / IRQ_STAT.err were "RX full and done": they pulsed
  without data loss and gave one pulse for several lost words.
- FIFO: a write on a full FIFO was dropped even when a read freed a slot
  in the same cycle; depths that are not a power of two corrupted data;
  DEPTH = 1 did not work; illegal parameters were accepted.
- AXI4-Lite: CTRL.start was taken from the previous CTRL value; TX_DATA
  pushed the previous TX_DATA value; RX_DATA returned the word popped by
  the previous read; IRQ_STAT was not readable and write-1-to-clear had
  no effect; the register decode was wrong for DATA_WIDTH = 64;
  parameters the register map cannot hold were accepted.
- VHDL wrapper: the four FIFO count ports were missing and `m_cs_sel_i`
  was fixed at 2 bits.
- Build and scripts: piped make targets passed on failure, `make clean`
  removed the committed SYNTH_REPORT.md, synth_report parsed an old
  Yosys stat format and left out the Xilinx INV cells, `make vhdl-test`
  passed on a port mismatch, and the testbench did not build on
  Verilator 5.05x.
- Documentation: the static "tests passing" / "lint clean" badges, the
  invented Fmax, LUT and power figures, the latency tables, the SPDX
  expression and the pricing page that did not exist.

### Interface changes

New ports are appended at the end of each port list; no existing port
was renamed or removed. Behaviour that changed:

- `irq_err_o` (`spi_top`) and IRQ_STAT.err (`spi_axi4lite`) pulse / set
  once per received word dropped because the RX FIFO was full.
- SCK follows `cpol_i` while idle (after reset and between transfers); a
  start that arrives while SCK moves to a new CPOL stays pending until
  SCK rests there.
- MOSI keeps the last bit of a frame after the frame instead of
  returning to 0.
- The slave adds 2..3 clk_i cycles of latency (synchronizers) and has
  new timing requirements: SCK high / low >= 2 clk_i, SCK period >= 4
  clk_i plus clock-to-output, board and MISO setup delays, the first TX
  word written before CS_n falls (see PORT_DESCRIPTION.md). `cpol_i`,
  `cpha_i` and `lsb_first_i` are latched when CS_n falls.
- AXI4-Lite: CTRL.start acts on the data of the same write and
  self-clears (reads 0); TX_DATA pushes the written value; RX_DATA
  returns the RX FIFO head and pops it (0 when empty); IRQ_STAT is
  readable and write-1-to-clear.
- AXI4-Lite with DATA_WIDTH = 64: registers sit at a stride of 8 bytes.
- AXI4-Lite: VERSION reads 0x0001_0100, encoded as {major[15:0],
  minor[7:0], patch[7:0]} (v1.0.0 read 0x0001_0000).
- AXI4-Lite: elaboration stops for CS_DLY_W > 8, DATA_WIDTH other than
  32 / 64, NUM_CS > 256, FIFO_DEPTH > 65535, SPI_DATA_W or DIV_WIDTH
  above DATA_WIDTH, or an ADDR_WIDTH too small for 16 registers.
- `cs_sel_i` / `m_cs_sel_i` is 1 bit wide (and ignored) for NUM_CS = 1.

New ports and parameters:

| Module | Added |
|---|---|
| `spi_fifo` | parameter `FWFT` (default 0, the v1.0 read timing); outputs `overflow_o`, `underflow_o`; any DEPTH >= 1 |
| `spi_master` | parameter `RX_FWFT` (default 0); output `rx_overrun_o` |
| `spi_slave` | outputs `miso_oe_o`, `tx_underrun_o`, `rx_overrun_o` |
| `spi_top` | outputs `miso_oe_o`, `s_tx_underrun_o`, `s_rx_overrun_o` |
| `axi4lite_slave` | outputs `wr_event_data_o`, `wr_event_strb_o` |
| `spi_top_vhdl` | the four FIFO count ports and the three new `spi_top` outputs |

### Added

- cocotb regression on Verilator and Icarus (`make cocotb`), SymbiYosys
  proofs (`make formal`), gate-level checks (`make gatelevel`),
  parameter checks (`make params`), open-source place-and-route
  (`make pnr`), timing plots (`make waves`), and `make doc-numbers`,
  which regenerates every synthesis and P&R number in the docs.
- GitHub Actions CI and VERIFICATION.md.
- A commercial license request form (GitHub issue template).

## 1.0.0 (2026-05-09)

Initial release.
