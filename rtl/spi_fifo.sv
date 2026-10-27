// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Parametrizable single-clock synchronous FIFO.
//
// Used by spi_master / spi_slave for the TX and RX queues. Generic enough
// to drop into any other peripheral that needs a small flop-backed FIFO
// (UART, I2C controller, etc.).
//
// Features:
//   * Any depth DEPTH >= 1 (default 16). When DEPTH is not a power of two
//     the pointers wrap explicitly at DEPTH-1. Illegal parameters stop
//     elaboration.
//   * count_o exposes occupancy so the controller can drive interrupts
//     (TX-empty / RX-full) without re-deriving it from full / empty.
//   * Two read modes, selected by FWFT. Both read the storage through one
//     registered read port (rd_data_o is the memory read register), so
//     block RAM can still be inferred.
//       FWFT = 0 (default): rd_data_o is registered, valid one cycle
//         after rd_en_i, and holds its value otherwise. This matches how
//         shift registers consume the TX FIFO (they only need the new
//         word once per transfer).
//       FWFT = 1 (first-word fall-through): the head word is loaded into
//         rd_data_o ahead of time and is valid while empty_o is low;
//         rd_en_i pops it and the next word follows on the next clock.
//         A word written into an empty FIFO reaches rd_data_o one clock
//         later than in FWFT = 0 mode, so count_o can read 1 for one
//         clock while empty_o is still high.
//   * Simultaneous read+write is legal. When the FIFO is full, the write
//     is accepted because the read frees a slot in the same cycle; when
//     it is empty, the read is ignored and the write is accepted.
//   * overflow_o / underflow_o flag, in the same cycle, a write that was
//     dropped (FIFO full and no accepted read) or a read that was
//     ignored (no word available).
//
// Reset is synchronous, active-low (rst_ni). After reset full=0, empty=1,
// count=0, rd_data_o=0. The storage array is not reset.

module spi_fifo #(
  parameter int unsigned WIDTH = 8,
  parameter int unsigned DEPTH = 16,
  parameter bit          FWFT  = 1'b0
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  input  logic                          wr_en_i,
  input  logic [WIDTH-1:0]              wr_data_i,
  input  logic                          rd_en_i,
  output logic [WIDTH-1:0]              rd_data_o,

  output logic                          empty_o,
  output logic                          full_o,
  output logic [$clog2(DEPTH+1)-1:0]    count_o,

  output logic                          overflow_o,
  output logic                          underflow_o
);

  // Elaboration-time parameter checks. $error gives the message; the
  // reference to a module that does not exist makes every tool stop,
  // including Verilator with -Wno-fatal.
  if (WIDTH < 1) begin : g_bad_width
    $error("spi_fifo: WIDTH must be >= 1");
    spi_fifo_illegal_parameter_WIDTH_must_be_at_least_1 u_param_error ();
  end
  if (DEPTH < 1) begin : g_bad_depth
    $error("spi_fifo: DEPTH must be >= 1");
    spi_fifo_illegal_parameter_DEPTH_must_be_at_least_1 u_param_error ();
  end

  // At least one pointer bit, so DEPTH = 1 does not produce a [-1:0] range.
  localparam int unsigned     PTR_W = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam int unsigned     CNT_W = (DEPTH >= 1) ? $clog2(DEPTH + 1) : 1;
  localparam bit              POW2  = (DEPTH > 1) && ((DEPTH & (DEPTH - 1)) == 0);
  localparam logic [PTR_W-1:0] LAST  = PTR_W'(DEPTH - 1);

  function automatic logic [PTR_W-1:0] ptr_inc(input logic [PTR_W-1:0] p);
    if (POW2) ptr_inc = p + 1'b1;
    else      ptr_inc = (p == LAST) ? '0 : p + 1'b1;
  endfunction

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [PTR_W-1:0] wr_ptr_q;
  logic [PTR_W-1:0] rd_ptr_q;
  logic [CNT_W-1:0] count_q;        // words held, including an FWFT head
  logic             head_valid_q;   // FWFT: rd_data_o holds the head word

  logic             avail;          // a word can be popped this cycle
  logic             do_wr;
  logic             do_rd;
  logic             mem_rd;         // read the memory into rd_data_o

  if (FWFT) begin : g_fwft
    logic [CNT_W-1:0] mem_count;    // words still in the memory array
    assign mem_count = count_q - CNT_W'(head_valid_q);
    assign avail     = head_valid_q;
    // Refill the head when it is empty or being popped. The word read is
    // at least one clock old, so the memory is never read and written at
    // the same address in one cycle.
    assign mem_rd    = (mem_count != '0) && (!head_valid_q || do_rd);
  end else begin : g_std
    assign avail     = (count_q != '0);
    assign mem_rd    = do_rd;
  end

  assign do_rd = rd_en_i && avail;
  assign do_wr = wr_en_i && ((count_q != CNT_W'(DEPTH)) || do_rd);

  always_ff @(posedge clk_i) begin
    if (rst_ni && do_wr) mem_q[wr_ptr_q] <= wr_data_i;
  end

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      wr_ptr_q     <= '0;
      rd_ptr_q     <= '0;
      count_q      <= '0;
      head_valid_q <= 1'b0;
    end else begin
      if (do_wr)  wr_ptr_q <= ptr_inc(wr_ptr_q);
      if (mem_rd) rd_ptr_q <= ptr_inc(rd_ptr_q);
      // Count update: an accepted write and read in the same cycle leave
      // the count unchanged (at full both are accepted, at empty only the
      // write).
      case ({do_wr, do_rd})
        2'b10:   count_q <= count_q + 1'b1;
        2'b01:   count_q <= count_q - 1'b1;
        default: count_q <= count_q;
      endcase
      if (FWFT) head_valid_q <= mem_rd || (head_valid_q && !do_rd);
    end
  end

  // Registered read data. FWFT = 0: sampled in the cycle of an accepted
  // rd_en_i, available the next cycle, held otherwise; when the FIFO is
  // full and a write lands on the head slot in the same cycle, the read
  // returns the old head (read-first). FWFT = 1: the head register.
  always_ff @(posedge clk_i) begin
    if (!rst_ni)     rd_data_o <= '0;
    else if (mem_rd) rd_data_o <= mem_q[rd_ptr_q];
  end

  assign empty_o     = !avail;
  assign full_o      = (count_q == CNT_W'(DEPTH));
  assign count_o     = count_q;
  assign overflow_o  = wr_en_i && !do_wr;
  assign underflow_o = rd_en_i && !do_rd;

endmodule
