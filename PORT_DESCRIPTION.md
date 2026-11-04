# Port description

Detailed handshake protocol and timing for the SPI master, slave,
loopback top and AXI4-Lite wrapper. T is one `clk_i` period. The
latencies below were measured in simulation (see
[`VERIFICATION.md`](VERIFICATION.md) §3.4).

## Reset

`rst_ni` is **synchronous active-low**. Hold it low for at least 4
clock cycles before the first transaction. After de-assertion the
master is idle (CS_n all high, SCK at `cpol_i`, which should be stable
while `rst_ni` is low) and the slave is idle. The slave always drives
`miso_o`: 0 after reset, then, while CS_n is high, the first bit of its
TX FIFO head (0 while the TX FIFO is empty). Use `miso_oe_o` to drive a
tri-state or shared MISO line. A reset while the slave's CS_n is low
ignores the rest of that frame; the slave only starts on a CS_n fall
seen after reset.

## Master parameters

| Parameter    | Default | Legal values | Description                                      |
|--------------|---------|--------------|--------------------------------------------------|
| `DATA_WIDTH` | 8       | >= 2         | Word width (2..64 bits simulated).               |
| `NUM_CS`     | 4       | >= 1         | Number of chip-select lines.                     |
| `FIFO_DEPTH` | 16      | >= 1         | TX and RX FIFO depth.                            |
| `DIV_WIDTH`  | 16      | >= 1         | Width of `clk_div_i`.                            |
| `CS_DLY_W`   | 8       | >= 1         | Width of `cs_setup_cycles_i` / `cs_hold_cycles_i`. |
| `RX_FWFT`    | 0       | 0, 1         | RX FIFO read mode, see below.                    |

Illegal values stop elaboration in Yosys, Verilator and Icarus.

## Master configuration

Configuration is latched when a start is accepted (the clock edge on
which CS_n falls) and holds for the whole transfer, including every
word of a `cs_inter_byte_i = 1` burst; changing the inputs during a
transfer does not affect it. While idle, SCK follows `cpol_i`. A start
that arrives while SCK is still moving to a new `cpol_i` stays pending
and launches as soon as SCK rests at the new level, so CS_n never falls
on the same clock edge as an SCK change.

| Signal              | Width      | Description                                                                |
|---------------------|------------|----------------------------------------------------------------------------|
| `cpol_i`            | 1          | 0 = SCK idles low, 1 = idles high.                                         |
| `cpha_i`            | 1          | 0 = sample on first SCK edge, 1 = sample on second.                        |
| `lsb_first_i`       | 1          | 0 = MSB first (default), 1 = LSB first.                                    |
| `clk_div_i`         | DIV_WIDTH  | SCK = clk_i / (2*(div_i + 1)). div=0 -> clk_i/2; div=255 -> clk_i/512.    |
| `cs_sel_i`          | $clog2(NUM_CS), 1 for NUM_CS = 1 | Which CS line drives low for this transfer; ignored for NUM_CS = 1. A value >= NUM_CS asserts no CS line (the transfer still runs). |
| `cs_setup_cycles_i` | CS_DLY_W   | Extra cycles between CS-fall and the first SCK edge (see Latency summary). |
| `cs_hold_cycles_i`  | CS_DLY_W   | Extra cycles between the last SCK edge and CS-rise (or the next word).     |
| `cs_inter_byte_i`   | 1          | 0 = one word per start, CS de-asserted after it; 1 = keep CS low and send the next word while the TX FIFO is not empty at the end of a word (it may be refilled during the burst). |

## Master TX FIFO

Push interface is the standard sync-FIFO write port:

| Signal        | Width           | Description                                       |
|---------------|-----------------|---------------------------------------------------|
| `tx_we_i`     | 1               | Write enable (one cycle).                         |
| `tx_wdata_i`  | DATA_WIDTH      | Word to push.                                     |
| `tx_full_o`   | 1               | High when FIFO cannot accept another word.        |
| `tx_empty_o`  | 1               | High when FIFO is empty.                          |
| `tx_count_o`  | log2(DEPTH+1)   | Current occupancy.                                |

Pop is internal — the master FSM consumes the head of the FIFO each
time it loads a new word into the shift register. A write while the
FIFO is full is dropped, unless the master pops a word in the same
cycle.

## Master RX FIFO

| Signal        | Width           | Description                                       |
|---------------|-----------------|---------------------------------------------------|
| `rx_re_i`     | 1               | Read enable (one cycle); ignored while empty.     |
| `rx_rdata_o`  | DATA_WIDTH      | `RX_FWFT = 0`: the popped word, valid the cycle after `rx_re_i` and held. `RX_FWFT = 1`: the FIFO head, valid while `rx_empty_o` is low; `rx_re_i` pops it. |
| `rx_full_o`   | 1               | RX FIFO full.                                     |
| `rx_empty_o`  | 1               | RX FIFO empty.                                    |
| `rx_count_o`  | log2(DEPTH+1)   | Current occupancy.                                |

A word received while the RX FIFO is full (and not read in the same
cycle) is dropped and `rx_overrun_o` pulses.

## Master control / status

| Signal         | Description                                                              |
|----------------|--------------------------------------------------------------------------|
| `start_i`      | Launch a transfer of the TX FIFO contents. Sampled every cycle while idle; ignored (not kept pending) while the TX FIFO is empty, including in the cycle of the first TX write. |
| `busy_o`       | High from the clock edge on which CS_n falls until `xfer_done_o`.       |
| `xfer_done_o`  | One-cycle pulse one cycle after CS_n rises; `busy_o` falls on the same edge. |
| `rx_overrun_o` | One-cycle pulse, one clock after a received word was dropped (RX FIFO full). |

## Master SPI bus

| Signal | Dir | Description                                                 |
|--------|-----|-------------------------------------------------------------|
| `sck_o`  | out | Serial clock; rests at `cpol_i` from reset and while every CS_n is high. |
| `mosi_o` | out | Master-out, slave-in data; keeps the last bit of a frame afterwards. |
| `miso_i` | in  | Master-in, slave-out data.                                |
| `cs_n_o` | out | Active-low chip-selects, driven from flip-flops; only `cs_sel_i` is asserted low. |

## Slave configuration

| Parameter    | Default | Legal values | Description          |
|--------------|---------|--------------|----------------------|
| `DATA_WIDTH` | 8       | >= 2         | Word width.          |
| `FIFO_DEPTH` | 16      | >= 1         | TX and RX FIFO depth. |

`cpol_i`, `cpha_i` and `lsb_first_i` are latched when the slave detects
CS_n falling and held until CS_n rises. The TX and RX FIFO ports have
the same names and widths as on the master; `rx_rdata_o` is valid the
cycle after `rx_re_i`.

| Signal          | Dir | Description                                                 |
|-----------------|-----|-------------------------------------------------------------|
| `busy_o`        | out | High while a frame is active (after the synchronizer).      |
| `xfer_done_o`   | out | One-cycle pulse per received word.                          |
| `tx_underrun_o` | out | One-cycle pulse: a word started with the TX FIFO empty; zeros are sent. |
| `rx_overrun_o`  | out | One-cycle pulse: a received word was dropped (RX FIFO full). |

## Slave SPI bus

The slave consumes the master's SCK directly (no internal generator).
SCK, CS_n and MOSI pass through 2-FF synchronizers clocked by `clk_i`,
so the bus may be asynchronous to `clk_i`; this adds 2..3 T of latency
(one T more if the first stage goes metastable).

| Signal      | Dir | Description                                                 |
|-------------|-----|-------------------------------------------------------------|
| `sck_i`     | in  | Serial clock from the master.                               |
| `mosi_i`    | in  | Master-out, slave-in data.                                  |
| `miso_o`    | out | Master-in, slave-out data; head bit of slave's TX shift. Always driven. |
| `cs_n_i`    | in  | Active-low chip-select; transfer runs while this is low.    |
| `miso_oe_o` | out | MISO output enable, `!cs_n_i`, combinational from the pin.  |

Timing requirements:

* SCK high time and SCK low time >= 2 T.
* SCK period >= 4 T + t_co + t_board + t_su in hardware (slave
  clock-to-output, board round trip, master MISO setup). Simulated
  without board delay and with a master sampling exactly on its edge,
  every period >= 3.1 T works, and >= 4.0 T with every input change
  delayed by up to 1 T (late synchronizer resolution).
* MOSI stable from the sampling edge until 2 T after it, including the
  last bit of a frame.
* MISO changes 2..4 T + clock-to-output after a sampling edge.
* CS_n fall to first SCK edge >= 1 T; CS_n high between frames >= 2 T.
* The first TX word of a frame must be written (`tx_we_i` sampled) on a
  `clk_i` edge before CS_n falls. A word written later may miss that word
  slot, which then goes out as zeros with `tx_underrun_o`; the word is
  never sent in part and stays queued for the next word.

## Mode timing (CPOL x CPHA)

```
mode 0 (CPOL=0, CPHA=0): SCK idles low; sample on rising; shift on falling
mode 1 (CPOL=0, CPHA=1): SCK idles low; shift on rising; sample on falling
mode 2 (CPOL=1, CPHA=0): SCK idles high; sample on falling; shift on rising
mode 3 (CPOL=1, CPHA=1): SCK idles high; shift on falling; sample on rising
```

CPHA=0 quirk: the FIRST output bit must be on MOSI/MISO before the
first SCK edge, because that edge samples it. The master loads its
shift register one `clk_i` cycle after CS_n falls, at least
`cs_setup_cycles_i + clk_div_i + 2` cycles before the first SCK edge.
The slave keeps the first bit of its TX FIFO head on MISO while CS_n is
high, and puts the first bit of the next word of a burst on MISO at the
last sampling edge of the current word.

## Latency summary

| Phase                                                        | Cycles (T)                    |
|--------------------------------------------------------------|-------------------------------|
| `start_i` accepted -> CS_n low, `busy_o` high                | 0 (same clock edge)           |
| CS_n fall -> first SCK edge                                  | `cs_setup_cycles_i + clk_div_i + 3` |
| SCK half period                                              | `clk_div_i + 1`               |
| Last SCK edge -> CS_n rise                                   | `cs_hold_cycles_i + 1`        |
| CS_n rise -> `xfer_done_o` pulse, `busy_o` low               | 1                             |
| Burst (`cs_inter_byte_i=1`): last SCK edge of a word -> first SCK edge of the next (CS_n stays low) | `cs_hold_cycles_i + cs_setup_cycles_i + clk_div_i + 4` |
| Slave: SPI input change -> seen by the slave logic           | 2..3 (2-FF synchronizer)      |
| AXI4-Lite: later of the AW / W handshakes -> BVALID          | 2                             |
| AXI4-Lite: AR handshake -> RVALID                            | 1                             |
| AXI4-Lite throughput (BREADY / RREADY high)                  | 3 cycles per write, 2 per read |

## Interrupts (loopback top)

| Signal           | Active when                                            |
|------------------|--------------------------------------------------------|
| `irq_tx_empty_o` | TX FIFO is empty (controller can refill).              |
| `irq_rx_full_o`  | RX FIFO is full (controller must drain).               |
| `irq_done_o`     | One-cycle pulse on each completed transfer.            |
| `irq_err_o`      | One-cycle pulse per received word dropped because the RX FIFO was full. |

`spi_top` also exposes the slave's `miso_oe_o` and its status pulses as
`s_tx_underrun_o` / `s_rx_overrun_o`. The AXI4-Lite wrapper has a single
`irq_o`, the OR of `IRQ_STAT & IRQ_MASK`.

## AXI4-Lite wrapper (`spi_axi4lite`)

`spi_axi4lite` contains one `spi_master` (RX FIFO in first-word
fall-through mode) behind a 16-register map. With `DATA_WIDTH = 32`
register n sits at byte address 4 n; with `DATA_WIDTH = 64` every
register is 64 bits wide and sits at 8 n. Unaligned and unmapped
addresses return DECERR.

| Offset (DW=32) | Register | Access | Contents                                          |
|----------------|----------|--------|---------------------------------------------------|
| 0x00 | CTRL     | RW  | bit0 start, bit1 lsb_first, bit2 cpol, bit3 cpha, bit4 cs_inter_byte, [15:8] cs_sel, [23:16] cs_setup, [31:24] cs_hold. A write with bit0 = 1 (byte lane 0 strobed) launches one transfer with the configuration of that same write; start is not stored and reads 0. |
| 0x04 | STATUS   | RO  | bit0 busy, bit1 tx_empty, bit2 tx_full, bit3 rx_empty, bit4 rx_full. |
| 0x08 | IRQ_STAT | W1C | bit0 tx_empty, bit1 rx_full, bit2 done, bit3 err (a received word was dropped). Sticky and readable; writing 1 clears (lane 0 strobed); tx_empty / rx_full set again while the condition lasts. |
| 0x0C | IRQ_MASK | RW  | Per-bit enable of IRQ_STAT into `irq_o`.          |
| 0x10 | CLK_DIV  | RW  | SCK = clk / (2 (CLK_DIV + 1)).                    |
| 0x14 | TX_DATA  | RW  | A write that strobes a byte lane of the SPI word pushes the written (strobe-merged) value into the TX FIFO. |
| 0x18 | RX_DATA  | RO  | A read returns the RX FIFO head and pops it; 0 (and no pop) when empty. |
| 0x1C | FIFO_LVL | RO  | [15:0] tx_count, [31:16] rx_count.                |
| 0x20 | ID       | RO  | 0x5910_017E.                                      |
| 0x24 | VERSION  | RO  | {major[15:0], minor[7:0], patch[7:0]}: 0x0001_0100 for v1.1.0. |
| 0x28..0x3C | scratch | RW |                                              |

Only the low `$clog2(NUM_CS)` bits of CTRL.cs_sel are used (none for
`NUM_CS = 1`); within those bits a value >= NUM_CS selects no chip
select. Elaboration stops for parameters the register map cannot hold:
`DATA_WIDTH` other than 32 or 64, `NUM_REGS` other than 16, `ADDR_WIDTH`
too small for 16 registers, `SPI_DATA_W > DATA_WIDTH`,
`DIV_WIDTH > DATA_WIDTH`, `CS_DLY_W > 8` (cs_setup / cs_hold are 8-bit
CTRL fields), `NUM_CS > 256`, `FIFO_DEPTH > 65535`.
