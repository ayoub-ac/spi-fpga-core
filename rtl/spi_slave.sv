// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// SPI slave controller.
//
// Reciprocal counterpart to spi_master.sv: implements the four Motorola
// modes, parametrizable word width, MSB/LSB-first selection, and TX/RX
// FIFOs. Uses the master-driven SCK and CS_n directly — there is no
// internal clock generator. SCK is treated as a generic input and edges
// are detected against the previous-cycle sample (clk_i must be at least
// ~4x faster than SCK to avoid aliasing).
//
// Mode legend (same as master):
//   MODE  CPOL  CPHA  Idle SCK  Sample edge       Shift edge
//   ----  ----  ----  --------  -----------       ----------
//     0     0     0   low       rising  (1st)     falling (2nd)
//     1     0     1   low       falling (2nd)     rising  (1st)
//     2     1     0   high      falling (1st)     rising  (2nd)
//     3     1     1   high      rising  (2nd)     falling (1st)
//
// CPHA=0 quirk: the FIRST data bit must be presented on the MISO line
// when CS goes low (no SCK edge has happened yet). We honour this by
// loading the TX shift register the cycle after CS de-asserts -> asserts.
// The master is expected to wait at least 3 clk_i cycles between CS-fall
// and the first SCK edge (cs_setup_cycles_i >= 3) so the slave's loaded
// byte is visible on MISO.
//
// Reset is synchronous active-low. Mid-transfer reset returns to a
// clean state; the next CS_n falling edge starts a fresh transfer.

module spi_slave #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned FIFO_DEPTH = 16
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  // Configuration (live, but only consulted between transfers).
  input  logic                          cpol_i,
  input  logic                          cpha_i,
  input  logic                          lsb_first_i,

  // TX FIFO push (controller-side).
  input  logic                          tx_we_i,
  input  logic [DATA_WIDTH-1:0]         tx_wdata_i,
  output logic                          tx_full_o,
  output logic                          tx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] tx_count_o,

  // RX FIFO pop (controller-side).
  input  logic                          rx_re_i,
  output logic [DATA_WIDTH-1:0]         rx_rdata_o,
  output logic                          rx_full_o,
  output logic                          rx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] rx_count_o,

  // Status pulse.
  output logic                          busy_o,
  output logic                          xfer_done_o,

  // SPI bus.
  input  logic                          sck_i,
  input  logic                          mosi_i,
  output logic                          miso_o,
  input  logic                          cs_n_i
);

  // -------------------------------------------------------------
  // FIFOs
  // -------------------------------------------------------------
  logic                          tx_fifo_rd_en;
  logic [DATA_WIDTH-1:0]         tx_fifo_rdata;
  logic                          tx_fifo_empty;

  spi_fifo #(.WIDTH(DATA_WIDTH), .DEPTH(FIFO_DEPTH)) u_tx_fifo (
    .clk_i    (clk_i),
    .rst_ni   (rst_ni),
    .wr_en_i  (tx_we_i),
    .wr_data_i(tx_wdata_i),
    .rd_en_i  (tx_fifo_rd_en),
    .rd_data_o(tx_fifo_rdata),
    .empty_o  (tx_fifo_empty),
    .full_o   (tx_full_o),
    .count_o  (tx_count_o)
  );
  assign tx_empty_o = tx_fifo_empty;

  logic                          rx_fifo_wr_en;
  logic [DATA_WIDTH-1:0]         rx_fifo_wdata;

  spi_fifo #(.WIDTH(DATA_WIDTH), .DEPTH(FIFO_DEPTH)) u_rx_fifo (
    .clk_i    (clk_i),
    .rst_ni   (rst_ni),
    .wr_en_i  (rx_fifo_wr_en),
    .wr_data_i(rx_fifo_wdata),
    .rd_en_i  (rx_re_i),
    .rd_data_o(rx_rdata_o),
    .empty_o  (rx_empty_o),
    .full_o   (rx_full_o),
    .count_o  (rx_count_o)
  );

  // -------------------------------------------------------------
  // Edge detection on SCK and CS_n (1-FF sync; both signals are
  // assumed to come from the same clk domain in this design).
  // -------------------------------------------------------------
  logic sck_q;
  logic cs_q;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      sck_q <= 1'b0;
      cs_q  <= 1'b1;
    end else begin
      sck_q <= sck_i;
      cs_q  <= cs_n_i;
    end
  end
  logic sck_rise, sck_fall, cs_fall, cs_rise;
  assign sck_rise = !sck_q && sck_i;
  assign sck_fall =  sck_q && !sck_i;
  assign cs_fall  =  cs_q  && !cs_n_i;
  assign cs_rise  = !cs_q  &&  cs_n_i;

  logic cs_active;
  assign cs_active = !cs_q;

  // Sample / shift selection per mode:
  //   sample_on_rise = (CPOL ^ CPHA) == 0
  logic sample_on_rise;
  assign sample_on_rise = (cpol_i ^ cpha_i) == 1'b0;

  logic sample_now, shift_now;
  always_comb begin
    sample_now = 1'b0;
    shift_now  = 1'b0;
    if (cs_active) begin
      if (sample_on_rise) begin
        sample_now = sck_rise;
        shift_now  = sck_fall;
      end else begin
        sample_now = sck_fall;
        shift_now  = sck_rise;
      end
    end
  end

  // -------------------------------------------------------------
  // Pre-fetch byte from FIFO so it is registered on MISO BEFORE the
  // master's first SCK edge (CPHA=0 requirement).
  //
  // The FIFO has a registered output: when rd_en_i is asserted at
  // cycle N, rd_data_o is valid at cycle N+1. We fire rd_en_i on
  // cs_fall (combinational), so:
  //   * cycle N: cs_fall=1 -> tx_fifo_rd_en=1
  //   * cycle N+1: tx_fifo_rdata = next byte; we capture into tx_shift_q
  //   * cycle N+2 onwards: miso_o reflects tx_shift_q[head]
  // The master's cs_setup_cycles_i must be >= 3 for the slave to be
  // ready by the first SCK edge.
  // -------------------------------------------------------------
  logic [DATA_WIDTH-1:0] tx_shift_q;
  logic [DATA_WIDTH-1:0] rx_shift_q;
  logic [$clog2(DATA_WIDTH+1)-1:0] bit_cnt_q;
  logic                  fifo_loaded_q;
  logic                  first_shift_q;     // CPHA=1: drop the first shift edge
  logic                  shift_mask_q;      // mask the next shift edge after a byte boundary

  // Pulse FIFO read on CS-fall (first byte) AND on each completed
  // byte while CS stays low (back-to-back streaming). For CPHA=0, the
  // master toggles SCK one extra time after the last sample to release
  // the bus; the slave sees that as a SHIFT edge that would otherwise
  // corrupt the freshly loaded next byte. We mask shifts via
  // pending_load_q across that spurious edge.
  logic byte_complete_w;
  assign byte_complete_w = sample_now && bit_cnt_q == DATA_WIDTH-1;
  assign tx_fifo_rd_en = (cs_fall || byte_complete_w) && !tx_fifo_empty;
  assign miso_o = lsb_first_i ? tx_shift_q[0] : tx_shift_q[DATA_WIDTH-1];

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      tx_shift_q     <= '0;
      rx_shift_q     <= '0;
      bit_cnt_q      <= '0;
      fifo_loaded_q  <= 1'b0;
      first_shift_q  <= 1'b0;
      shift_mask_q   <= 1'b0;
      busy_o         <= 1'b0;
      xfer_done_o    <= 1'b0;
      rx_fifo_wr_en  <= 1'b0;
      rx_fifo_wdata  <= '0;
    end else begin
      xfer_done_o   <= 1'b0;
      rx_fifo_wr_en <= 1'b0;

      busy_o <= cs_active;

      // Capture next byte into shift register one cycle after the
      // FIFO read fires.
      if (fifo_loaded_q) begin
        tx_shift_q     <= tx_fifo_rdata;
        rx_shift_q     <= '0;
        bit_cnt_q      <= '0;
      end
      // Set fifo_loaded_q one cycle after cs_fall (or after a byte
      // boundary mid-CS for back-to-back streaming).
      fifo_loaded_q <= tx_fifo_rd_en;

      if (cs_fall) begin
        bit_cnt_q     <= '0;
        rx_shift_q    <= '0;
        first_shift_q <= 1'b0;
        shift_mask_q  <= 1'b0;
      end

      // Sample MISO->RX on the sample edge.
      if (sample_now) begin
        if (lsb_first_i)
          rx_shift_q <= {mosi_i, rx_shift_q[DATA_WIDTH-1:1]};
        else
          rx_shift_q <= {rx_shift_q[DATA_WIDTH-2:0], mosi_i};
        bit_cnt_q <= bit_cnt_q + 1'b1;
      end

      // Shift TX register on the shift edge. Two conditions suppress
      // the actual shift:
      //   * CPHA=1 first shift edge — the loaded byte already
      //     presents bit 7 on miso_o.
      //   * shift_mask_q — set on byte completion to mask the trailing
      //     CPHA=0 shift edge that the master emits before CS-rise
      //     (or before the next byte in inter-byte mode).
      if (shift_now) begin
        if (cpha_i && !first_shift_q) begin
          first_shift_q <= 1'b1;
        end else if (shift_mask_q) begin
          shift_mask_q <= 1'b0;
        end else begin
          if (lsb_first_i)
            tx_shift_q <= {1'b0, tx_shift_q[DATA_WIDTH-1:1]};
          else
            tx_shift_q <= {tx_shift_q[DATA_WIDTH-2:0], 1'b0};
        end
      end

      // Byte-complete: push received word, mark done, and pre-fetch
      // the next byte in case CS stays asserted for a multi-byte
      // burst.
      if (sample_now && bit_cnt_q == DATA_WIDTH-1) begin
        rx_fifo_wr_en <= 1'b1;
        if (lsb_first_i)
          rx_fifo_wdata <= {mosi_i, rx_shift_q[DATA_WIDTH-1:1]};
        else
          rx_fifo_wdata <= {rx_shift_q[DATA_WIDTH-2:0], mosi_i};
        xfer_done_o   <= 1'b1;
        bit_cnt_q     <= '0;
        first_shift_q <= 1'b0;
        // CPHA=0 only: master will emit one trailing shift edge after
        // this last sample. Mask it so it does not shift the freshly
        // loaded next byte.
        if (!cpha_i) shift_mask_q <= 1'b1;
      end

      if (cs_rise) begin
        bit_cnt_q     <= '0;
        fifo_loaded_q <= 1'b0;
      end
    end
  end

endmodule
