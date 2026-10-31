# Port description

Detailed handshake protocol and timing for the SPI master, slave, and
loopback top.

## Reset

`rst_ni` is **synchronous active-low**. Hold it low for at least 4
clock cycles before the first transaction. After de-assertion the
master is idle (CS_n all high, SCK at CPOL) and the slave is idle
(MISO undriven by the slave; the bus is owned by whatever pulls).

## Master configuration

Configuration is sampled on the `start_i` pulse and latched for the
full duration of one transfer. Changing config mid-transfer is unsafe;
either wait for `xfer_done_o` or hold reset across a config change.

| Signal              | Width      | Description                                                                |
|---------------------|------------|----------------------------------------------------------------------------|
| `cpol_i`            | 1          | 0 = SCK idles low, 1 = idles high.                                         |
| `cpha_i`            | 1          | 0 = sample on first SCK edge, 1 = sample on second.                        |
| `lsb_first_i`       | 1          | 0 = MSB first (default), 1 = LSB first.                                    |
| `clk_div_i`         | DIV_WIDTH  | SCK = clk_i / (2*(div_i + 1)). div=0 -> clk_i/2; div=255 -> clk_i/512.    |
| `cs_sel_i`          | max(1, log2(NUM_CS)) | Which CS line drives low for this transfer.                     |
| `cs_setup_cycles_i` | CS_DLY_W   | Cycles between CS-fall and the first SCK edge.                             |
| `cs_hold_cycles_i`  | CS_DLY_W   | Cycles between the last shift edge and CS-rise (or the next byte).         |
| `cs_inter_byte_i`   | 1          | 0 = de-assert CS between bytes, 1 = hold CS through the whole TX FIFO.    |

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
time it loads a new shift register on a fresh byte.

## Master RX FIFO

| Signal        | Width           | Description                                       |
|---------------|-----------------|---------------------------------------------------|
| `rx_re_i`     | 1               | Read enable (one cycle).                          |
| `rx_rdata_o`  | DATA_WIDTH      | Word at FIFO head; valid the cycle after `rx_re_i`. |
| `rx_full_o`   | 1               | RX FIFO full.                                     |
| `rx_empty_o`  | 1               | RX FIFO empty.                                    |
| `rx_count_o`  | log2(DEPTH+1)   | Current occupancy.                                |

## Master control / status

| Signal        | Description                                                              |
|---------------|--------------------------------------------------------------------------|
| `start_i`     | One-cycle pulse to launch a transfer using the FIFO contents.            |
| `busy_o`      | High while the FSM is not in IDLE.                                       |
| `xfer_done_o` | One-cycle pulse on the cycle the FSM returns to IDLE after a transfer.   |

## Master SPI bus

| Signal | Dir | Description                                                 |
|--------|-----|-------------------------------------------------------------|
| `sck_o`  | out | Serial clock; idles at `cpol_i`.                          |
| `mosi_o` | out | Master-out, slave-in data.                                |
| `miso_i` | in  | Master-in, slave-out data.                                |
| `cs_n_o` | out | Active-low chip-select; only `cs_sel_i` is asserted low.  |

## Slave SPI bus

The slave consumes the master's SCK directly (no internal generator).
`clk_i` should be at least 4x faster than the maximum SCK to avoid
aliasing in the edge detector.

| Signal   | Dir | Description                                                 |
|----------|-----|-------------------------------------------------------------|
| `sck_i`  | in  | Serial clock from the master.                               |
| `mosi_i` | in  | Master-out, slave-in data.                                  |
| `miso_o` | out | Master-in, slave-out data; head bit of slave's TX shift.    |
| `cs_n_i` | in  | Active-low chip-select; transfer runs while this is low.    |

## Mode timing (CPOL x CPHA)

```
mode 0 (CPOL=0, CPHA=0): SCK idles low; sample on rising; shift on falling
mode 1 (CPOL=0, CPHA=1): SCK idles low; shift on rising; sample on falling
mode 2 (CPOL=1, CPHA=0): SCK idles high; sample on falling; shift on rising
mode 3 (CPOL=1, CPHA=1): SCK idles high; shift on falling; sample on rising
```

CPHA=0 quirk: the FIRST output bit is presented on MOSI/MISO when CS
goes low (no SCK edge has happened yet). Both the master and the
slave honour this by loading the shift register on the CS-fall (slave)
or CS_SETUP-to-SHIFT transition (master).

## Latency summary

| Phase                                        | Cycles                       |
|----------------------------------------------|------------------------------|
| Reset assert -> ready to accept              | 1 (after rst_ni high)        |
| `start_i` -> first SCK edge                  | `cs_setup_cycles_i` + 1      |
| Last sample -> `xfer_done_o`                 | `cs_hold_cycles_i` + 2       |
| Back-to-back bytes (`cs_inter_byte_i=1`)     | `cs_hold_cycles_i` + load    |
| AR -> R (AXI4-Lite wrapper read)             | 1                            |

## Interrupts (loopback top + AXI wrapper)

| Signal           | Active when                                            |
|------------------|--------------------------------------------------------|
| `irq_tx_empty_o` | TX FIFO is empty (controller can refill).              |
| `irq_rx_full_o`  | RX FIFO is full (controller must drain).               |
| `irq_done_o`     | One-cycle pulse on each completed transfer.            |
| `irq_err_o`      | One-cycle pulse per received word dropped because the RX FIFO was full. |
