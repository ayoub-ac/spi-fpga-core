// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Parametrizable single-clock synchronous FIFO.
//
// Used by spi_master / spi_slave for the TX and RX queues. Generic enough
// to drop into any other peripheral that needs a small flop-backed FIFO
// (UART, I2C controller, etc.).
//
// Features:
//   * Power-of-two depth via DEPTH parameter (default 16). Asserts on
//     non-power-of-two at elaboration.
//   * count_o exposes occupancy so the controller can drive interrupts
//     (TX-empty / RX-full) without re-deriving it from full / empty.
//   * No first-word-fall-through: rd_data_o is registered, valid one
//     cycle after rd_en_i. This matches how shift registers consume the
//     TX FIFO (they only need the new word once per transfer).
//   * Simultaneous read+write is legal even when the FIFO is full or
//     empty; the controller is responsible for honouring full / empty.
//
// Reset is synchronous, active-low (rst_ni). After reset full=0, empty=1,
// count=0.

module spi_fifo #(
  parameter int unsigned WIDTH = 8,
  parameter int unsigned DEPTH = 16
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  input  logic                          wr_en_i,
  input  logic [WIDTH-1:0]              wr_data_i,
  input  logic                          rd_en_i,
  output logic [WIDTH-1:0]              rd_data_o,

  output logic                          empty_o,
  output logic                          full_o,
  output logic [$clog2(DEPTH+1)-1:0]    count_o
);

  localparam int unsigned PTR_W = $clog2(DEPTH);

  // synthesis-time sanity check: DEPTH must be power of two.
  initial begin
    if ((DEPTH & (DEPTH - 1)) != 0)
      $error("spi_fifo: DEPTH=%0d is not a power of two", DEPTH);
  end

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [PTR_W-1:0] wr_ptr_q;
  logic [PTR_W-1:0] rd_ptr_q;
  logic [$clog2(DEPTH+1)-1:0] count_q;

  logic do_wr;
  logic do_rd;
  assign do_wr = wr_en_i && !full_o;
  assign do_rd = rd_en_i && !empty_o;

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      wr_ptr_q <= '0;
      rd_ptr_q <= '0;
      count_q  <= '0;
    end else begin
      if (do_wr) begin
        mem_q[wr_ptr_q] <= wr_data_i;
        wr_ptr_q        <= wr_ptr_q + 1'b1;
      end
      if (do_rd) begin
        rd_ptr_q <= rd_ptr_q + 1'b1;
      end
      // Count update: handle simultaneous wr+rd correctly (count
      // unchanged, even at full/empty boundaries).
      unique case ({do_wr, do_rd})
        2'b10:   count_q <= count_q + 1'b1;
        2'b01:   count_q <= count_q - 1'b1;
        default: count_q <= count_q;
      endcase
    end
  end

  // Registered read data — sampled the cycle of rd_en_i, available next
  // cycle. We expose the head of the queue combinationally so that
  // single-cycle peeks work; a registered read keeps timing clean.
  always_ff @(posedge clk_i) begin
    if (!rst_ni) rd_data_o <= '0;
    else if (do_rd) rd_data_o <= mem_q[rd_ptr_q];
  end

  assign empty_o = (count_q == 0);
  assign full_o  = (count_q == DEPTH);
  assign count_o = count_q;

endmodule
