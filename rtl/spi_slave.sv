// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// SPI slave controller.
//
// Reciprocal counterpart to spi_master.sv: implements the four Motorola
// modes, parametrizable word width, MSB/LSB-first selection, and TX/RX
// FIFOs. There is no internal clock generator: SCK, CS_n and MOSI are
// sampled with clk_i through 2-FF synchronizers and SCK edges are detected
// on the synchronized signals, so the bus may be fully asynchronous to
// clk_i.
//
// Mode legend (same as master):
//   MODE  CPOL  CPHA  Idle SCK  Sample edge       Shift edge
//   ----  ----  ----  --------  -----------       ----------
//     0     0     0   low       rising  (1st)     falling (2nd)
//     1     0     1   low       falling (2nd)     rising  (1st)
//     2     1     0   high      falling (1st)     rising  (2nd)
//     3     1     1   high      rising  (2nd)     falling (1st)
//
// Configuration (cpol_i, cpha_i, lsb_first_i) is latched when the CS_n
// fall is detected and held until CS_n rises.
//
// Data path:
//   * MOSI is sampled on the sampling edge. It passes through the same
//     2-FF synchronizer as SCK, so the bit taken is the one present at the
//     clk_i edge that first saw the SCK edge.
//   * MISO is the MSB of the TX shift register (LSB-first words are loaded
//     bit-reversed). The next bit is presented right after each sampling
//     edge is detected, without waiting for the shift edge, so the master
//     sees it a full SCK period before its next sampling edge. Bit 0 of a
//     frame is loaded while CS_n is high and bit 0 of the next word of a
//     burst at the last sampling edge of the current word, so it is on
//     MISO before the first SCK edge (CPHA = 0).
//   * A TX word is popped from the TX FIFO (first-word fall-through) when
//     its first bit is sampled, so a CS_n pulse without SCK edges consumes
//     nothing. A word that starts while the TX FIFO is empty is sent as
//     zeros and tx_underrun_o pulses.
//   * An RX word is pushed when its last bit is sampled. If the RX FIFO is
//     full the word is dropped and rx_overrun_o pulses. The bits of a word
//     cut short by CS_n rising are discarded.
//
// Timing requirements (T = clk_i period). An SCK edge is detected 2..3 T
// after it reaches sck_i (one T more if the first synchronizer stage goes
// metastable):
//   * SCK high time and SCK low time >= 2 T.
//   * MOSI stable from the sampling edge until 2 T after it, including the
//     last bit of a frame.
//   * MISO changes 2..4 T + clock-to-output after a sampling edge; the
//     master needs SCK period >= that delay plus its own MISO setup and
//     the board round trip (about 4.5 T in simulation without delays).
//   * CS_n fall to first SCK edge >= 1 T; CS_n high between frames >= 2 T.
//   * TX data for the first word of a frame written >= 3 T before CS_n
//     falls; a later write may be sent from bit 1 on (data race).
//
// miso_oe_o = !cs_n_i, combinational from the pin (no clock), for a
// tri-state or shared MISO line: drive the pad with miso_o when miso_oe_o
// is high. miso_o itself is always driven.
//
// Reset is synchronous active-low. A frame is only accepted on a CS_n
// falling edge seen after reset: a reset while CS_n is low ignores the rest
// of that frame instead of re-arming in the middle of it.

module spi_slave #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned FIFO_DEPTH = 16
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  // Configuration, latched when CS_n assertion is detected.
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

  // Status: busy while a frame is active, one-cycle pulse per word.
  output logic                          busy_o,
  output logic                          xfer_done_o,

  // SPI bus.
  input  logic                          sck_i,
  input  logic                          mosi_i,
  output logic                          miso_o,
  input  logic                          cs_n_i,

  // MISO output enable for a tri-state pad (= !cs_n_i).
  output logic                          miso_oe_o,
  // One-cycle pulses: a word started with the TX FIFO empty (zeros sent),
  // a received word was dropped because the RX FIFO was full.
  output logic                          tx_underrun_o,
  output logic                          rx_overrun_o
);

  // -------------------------------------------------------------
  // Parameter checks (stop elaboration in Yosys, Verilator, Icarus)
  // -------------------------------------------------------------
  if (DATA_WIDTH < 2) begin : g_bad_data_width
    $error("spi_slave: DATA_WIDTH must be >= 2");
    spi_slave_illegal_parameter_DATA_WIDTH_must_be_at_least_2 u_param_error ();
  end

  localparam int unsigned BIT_W = (DATA_WIDTH > 1) ? $clog2(DATA_WIDTH) : 1;

  function automatic logic [DATA_WIDTH-1:0] bit_reverse(input logic [DATA_WIDTH-1:0] v);
    for (int i = 0; i < DATA_WIDTH; i++) bit_reverse[i] = v[DATA_WIDTH-1-i];
  endfunction

  // -------------------------------------------------------------
  // FIFOs. The TX FIFO is first-word fall-through: its head is visible
  // before it is popped, so it can wait on MISO for the next frame.
  // -------------------------------------------------------------
  logic                          tx_fifo_rd_en;
  logic [DATA_WIDTH-1:0]         tx_fifo_head;
  logic                          tx_fifo_empty;
  logic                          tx_fifo_overflow;   // unused: the user honours tx_full_o
  logic                          tx_fifo_underflow;  // unused: pops only a valid head

  spi_fifo #(.WIDTH(DATA_WIDTH), .DEPTH(FIFO_DEPTH), .FWFT(1'b1)) u_tx_fifo (
    .clk_i      (clk_i),
    .rst_ni     (rst_ni),
    .wr_en_i    (tx_we_i),
    .wr_data_i  (tx_wdata_i),
    .rd_en_i    (tx_fifo_rd_en),
    .rd_data_o  (tx_fifo_head),
    .empty_o    (tx_fifo_empty),
    .full_o     (tx_full_o),
    .count_o    (tx_count_o),
    .overflow_o (tx_fifo_overflow),
    .underflow_o(tx_fifo_underflow)
  );
  assign tx_empty_o = tx_fifo_empty;

  logic                          rx_fifo_wr_en;
  logic [DATA_WIDTH-1:0]         rx_fifo_wdata;
  logic                          rx_fifo_overflow;
  logic                          rx_fifo_underflow;  // unused

  spi_fifo #(.WIDTH(DATA_WIDTH), .DEPTH(FIFO_DEPTH)) u_rx_fifo (
    .clk_i      (clk_i),
    .rst_ni     (rst_ni),
    .wr_en_i    (rx_fifo_wr_en),
    .wr_data_i  (rx_fifo_wdata),
    .rd_en_i    (rx_re_i),
    .rd_data_o  (rx_rdata_o),
    .empty_o    (rx_empty_o),
    .full_o     (rx_full_o),
    .count_o    (rx_count_o),
    .overflow_o (rx_fifo_overflow),
    .underflow_o(rx_fifo_underflow)
  );

  // -------------------------------------------------------------
  // 2-FF synchronizers on the asynchronous SPI inputs. All three have the
  // same depth, so MOSI stays aligned with the SCK edge that samples it.
  // CS_n resets to "low" so that a CS_n high must be observed after reset
  // before a falling edge (and a frame) can be detected.
  // -------------------------------------------------------------
  (* async_reg = "true" *) logic [1:0] sck_sync_q;
  (* async_reg = "true" *) logic [1:0] cs_sync_q;
  (* async_reg = "true" *) logic [1:0] mosi_sync_q;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      sck_sync_q  <= 2'b00;
      cs_sync_q   <= 2'b00;
      mosi_sync_q <= 2'b00;
    end else begin
      sck_sync_q  <= {sck_sync_q[0],  sck_i};
      cs_sync_q   <= {cs_sync_q[0],   cs_n_i};
      mosi_sync_q <= {mosi_sync_q[0], mosi_i};
    end
  end

  logic sck_s, cs_n_s, mosi_s;
  assign sck_s  = sck_sync_q[1];
  assign cs_n_s = cs_sync_q[1];
  assign mosi_s = mosi_sync_q[1];

  logic sck_prev_q;
  logic cs_n_prev_q;
  logic active_q;       // inside a frame that started after reset

  logic sck_rise, sck_fall, cs_fall, in_frame;
  assign sck_rise = sck_s && !sck_prev_q;
  assign sck_fall = !sck_s && sck_prev_q;
  assign cs_fall  = cs_n_prev_q && !cs_n_s;
  assign in_frame = !cs_n_s && (active_q || cs_fall);

  // Configuration in force: live while no frame is active, latched during
  // a frame (the latch is taken on the cycle CS_n assertion is detected).
  logic cpol_q, cpha_q, lsb_q;
  logic cpol_f, cpha_f, lsb_f;
  assign cpol_f = active_q ? cpol_q : cpol_i;
  assign cpha_f = active_q ? cpha_q : cpha_i;
  assign lsb_f  = active_q ? lsb_q  : lsb_first_i;

  // Sampling edge: rising for modes 0 and 3, falling for modes 1 and 2.
  logic sample_edge;
  assign sample_edge = in_frame && ((cpol_f == cpha_f) ? sck_rise : sck_fall);

  // -------------------------------------------------------------
  // Shift registers
  // -------------------------------------------------------------
  logic [BIT_W-1:0]      bit_cnt_q;        // bits of the current word sampled
  logic [DATA_WIDTH-1:0] tx_shift_q;       // MSB is on MISO
  logic [DATA_WIDTH-1:0] rx_shift_q;
  logic                  tx_from_fifo_q;   // tx_shift_q holds the unpopped FIFO head

  logic                  word_first, word_last;
  logic [DATA_WIDTH-1:0] rx_word;
  logic [DATA_WIDTH-1:0] tx_next;
  assign word_first = sample_edge && (bit_cnt_q == '0);
  assign word_last  = sample_edge && (bit_cnt_q == BIT_W'(DATA_WIDTH - 1));
  assign rx_word    = {rx_shift_q[DATA_WIDTH-2:0], mosi_s};
  assign tx_next    = tx_fifo_empty ? '0
                    : (lsb_f ? bit_reverse(tx_fifo_head) : tx_fifo_head);

  assign tx_fifo_rd_en = word_first && tx_from_fifo_q;
  assign rx_fifo_wr_en = word_last;
  assign rx_fifo_wdata = lsb_f ? bit_reverse(rx_word) : rx_word;

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      sck_prev_q     <= 1'b0;
      cs_n_prev_q    <= 1'b0;
      active_q       <= 1'b0;
      cpol_q         <= 1'b0;
      cpha_q         <= 1'b0;
      lsb_q          <= 1'b0;
      bit_cnt_q      <= '0;
      tx_shift_q     <= '0;
      rx_shift_q     <= '0;
      tx_from_fifo_q <= 1'b0;
      xfer_done_o    <= 1'b0;
      tx_underrun_o  <= 1'b0;
      rx_overrun_o   <= 1'b0;
    end else begin
      sck_prev_q    <= sck_s;
      cs_n_prev_q   <= cs_n_s;
      active_q      <= in_frame;
      xfer_done_o   <= word_last;
      tx_underrun_o <= word_first && !tx_from_fifo_q;
      rx_overrun_o  <= rx_fifo_overflow;

      if (!active_q) begin
        cpol_q <= cpol_i;
        cpha_q <= cpha_i;
        lsb_q  <= lsb_first_i;
      end

      if (!in_frame) begin
        // Between frames: keep the next word on MISO.
        bit_cnt_q      <= '0;
        tx_shift_q     <= tx_next;
        tx_from_fifo_q <= !tx_fifo_empty;
      end else if (sample_edge) begin
        rx_shift_q <= rx_word;
        if (word_last) begin
          // Next word of a burst: its bit 0 goes on MISO now.
          bit_cnt_q      <= '0;
          tx_shift_q     <= tx_next;
          tx_from_fifo_q <= !tx_fifo_empty;
        end else begin
          bit_cnt_q  <= bit_cnt_q + 1'b1;
          tx_shift_q <= {tx_shift_q[DATA_WIDTH-2:0], 1'b0};
          if (word_first) tx_from_fifo_q <= 1'b0;   // popped this cycle
        end
      end
    end
  end

  assign busy_o    = active_q;
  assign miso_o    = tx_shift_q[DATA_WIDTH-1];
  assign miso_oe_o = !cs_n_i;

endmodule
