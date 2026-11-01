// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Formal properties for rtl/spi_fifo.sv (both read modes).
//
// Included just before `endmodule` of a formal-only copy of the RTL that is
// otherwise byte-identical (Yosys has no `bind`). The first part uses ports
// only; FIFO_WHITEBOX adds internal invariants so that k-induction closes
// (unbounded proof). Derived from the independent SBY setup of the v1.0
// review.
`ifdef FORMAL
  reg f_past_valid = 1'b0;
  always @(posedge clk_i) f_past_valid <= 1'b1;
  always @(*) if (!f_past_valid) assume (!rst_ni);

  // Expected acceptance, from the header contract.
  wire f_do_rd = rd_en_i && !empty_o;
  wire f_do_wr = wr_en_i && (!full_o || f_do_rd);

  // ---------------- flags and strobes ----------------
  always @(*) if (f_past_valid) begin
    a_cnt_le_depth: assert (count_o <= DEPTH);
    a_full_flag:    assert (full_o == (count_o == DEPTH));
    a_ovf_strobe:   assert (overflow_o  == (wr_en_i && !f_do_wr));
    a_udf_strobe:   assert (underflow_o == (rd_en_i && !f_do_rd));
    if (FWFT) begin
      a_fwft_empty0: assert (count_o != 0 || empty_o);
      a_fwft_head:   assert (empty_o || count_o != 0);
    end else begin
      a_empty_flag:  assert (empty_o == (count_o == 0));
    end
  end

  // ---------------- transitions ----------------
  always @(posedge clk_i) if (f_past_valid) begin
    if (!$past(rst_ni)) begin
      a_rst_cnt:   assert (count_o == 0);
      a_rst_flags: assert (empty_o && !full_o);
      a_rst_rd:    assert (rd_data_o == '0);
    end else begin
      a_cnt_step: assert (count_o == $past(count_o) + $past(f_do_wr) - $past(f_do_rd));
      if (FWFT) begin
        // The head register holds until popped ...
        if ($past(!empty_o && !f_do_rd)) begin
          a_fwft_hold:  assert (!empty_o);
          a_fwft_data:  assert (rd_data_o == $past(rd_data_o));
        end
        // ... a word that is in the FIFO reaches the head within a clock ...
        if ($past(count_o != 0 && !f_do_rd)) a_fwft_latency: assert (!empty_o);
        // ... and a pop with more words behind it causes no bubble.
        if ($past(count_o >= 2 && f_do_rd))  a_fwft_nobubble: assert (!empty_o);
      end else begin
        if (!$past(f_do_rd)) a_rd_hold: assert (rd_data_o == $past(rd_data_o));
      end
    end
  end

  // ---------------- ordering: two consecutive tracked writes ----------------
  // The solver picks any accepted write (f_tag) as item 1; the next accepted
  // write is item 2. f_n1 / f_n2 = number of words ahead of each item.
  (* anyseq *) wire f_tag;
  localparam [2:0] F_IDLE = 3'd0, F_1 = 3'd1, F_12 = 3'd2, F_2 = 3'd3, F_DONE = 3'd4;
  localparam int unsigned F_CW = $clog2(DEPTH + 1);
  reg [2:0]       f_st;
  reg [WIDTH-1:0] f_v1, f_v2;
  reg [F_CW:0]    f_n1, f_n2;
  reg             f_chk1, f_chk2;
  reg             f_seen_full;

  wire f_pop1 = f_do_rd && (f_st == F_1 || f_st == F_12) && (f_n1 == 0);
  wire f_pop2 = f_do_rd && (f_st == F_2) && (f_n2 == 0);

  always @(posedge clk_i) begin
    f_chk1 <= 1'b0;
    f_chk2 <= 1'b0;
    if (!rst_ni) begin
      f_st        <= F_IDLE;
      f_seen_full <= 1'b0;
    end else begin
      if (full_o) f_seen_full <= 1'b1;
      case (f_st)
        F_IDLE: if (f_do_wr && f_tag) begin
                  f_st <= F_1;
                  f_v1 <= wr_data_i;
                  f_n1 <= count_o - f_do_rd;
                end
        F_1: begin
               if (f_pop1)       f_chk1 <= 1'b1;
               else if (f_do_rd) f_n1   <= f_n1 - 1'b1;
               if (f_do_wr) begin
                 f_v2 <= wr_data_i;
                 f_n2 <= count_o - f_do_rd;
                 f_st <= f_pop1 ? F_2 : F_12;
               end else if (f_pop1) begin
                 f_st <= F_DONE;
               end
             end
        F_12: begin
               if (f_pop1) begin
                 f_chk1 <= 1'b1;
                 f_st   <= F_2;
               end else if (f_do_rd) begin
                 f_n1 <= f_n1 - 1'b1;
               end
               if (f_do_rd) f_n2 <= f_n2 - 1'b1;
             end
        F_2: begin
               if (f_pop2) begin
                 f_chk2 <= 1'b1;
                 f_st   <= F_DONE;
               end else if (f_do_rd) begin
                 f_n2 <= f_n2 - 1'b1;
               end
             end
        default: ;
      endcase
    end
  end

  always @(*) if (f_past_valid) begin
    if (FWFT) begin
      // FWFT: the popped word is on rd_data_o in the cycle of the pop.
      if (f_pop1) a_order_item1_fwft: assert (rd_data_o == f_v1);
      if (f_pop2) a_order_item2_fwft: assert (rd_data_o == f_v2);
    end else begin
      // registered read: the popped word appears one cycle later.
      if (f_chk1) a_order_item1: assert (rd_data_o == f_v1);
      if (f_chk2) a_order_item2: assert (rd_data_o == f_v2);
    end
    if (f_st == F_1 || f_st == F_12) a_item1_inside: assert (f_n1 < count_o);
    if (f_st == F_12 || f_st == F_2) a_item2_inside: assert (f_n2 < count_o);
    if (f_st == F_12) a_item2_behind: assert (f_n2 == f_n1 + 1'b1);
    if (f_st == F_1)  a_item1_tail:   assert (f_n1 + 1'b1 == count_o);
    a_trk_state: assert (f_st <= F_DONE);
  end

`ifdef FIFO_WHITEBOX
  // Depth-agnostic invariants (pointers wrap at DEPTH-1).
  wire [31:0] f_wp = wr_ptr_q, f_rp = rd_ptr_q;
  wire [31:0] f_hv = FWFT ? head_valid_q : 1'b0;
  wire [31:0] f_mc = count_q - f_hv;                 // words in the memory array
  // Memory slot of the word with n words ahead of it (when not in the head).
  function automatic [31:0] f_slot(input [31:0] rp, input [31:0] hv, input [31:0] n);
    f_slot = (rp + n - hv) % DEPTH;
  endfunction
  always @(*) if (f_past_valid) begin
    w_ptr_rng: assert (f_wp < DEPTH && f_rp < DEPTH);
    w_ptr_cnt: assert ((f_wp + DEPTH - f_rp) % DEPTH == f_mc % DEPTH);
    if (FWFT) begin
      w_hv_cnt:    assert (!head_valid_q || count_q != 0);
      w_mem_small: assert (head_valid_q || f_mc <= 1);
      w_no_rw_collision: assert (!(mem_rd && do_wr && rd_ptr_q == wr_ptr_q));
    end else begin
      w_hv_zero:   assert (!head_valid_q);
    end
    if (f_st == F_1 || f_st == F_12) begin
      if (FWFT && head_valid_q && f_n1 == 0) w_head1: assert (rd_data_o == f_v1);
      else                                   w_mem1:  assert (mem_q[f_slot(f_rp, f_hv, f_n1)] == f_v1);
    end
    if (f_st == F_12 || f_st == F_2) begin
      if (FWFT && head_valid_q && f_n2 == 0) w_head2: assert (rd_data_o == f_v2);
      else                                   w_mem2:  assert (mem_q[f_slot(f_rp, f_hv, f_n2)] == f_v2);
    end
  end
`endif

  // ---------------- reachability ----------------
  always @(posedge clk_i) if (f_past_valid && rst_ni && $past(rst_ni)) begin
    c_full:             cover (full_o);
    c_empty_after_full: cover (f_seen_full && empty_o);
    c_overflow:         cover (overflow_o);
    c_underflow:        cover (underflow_o);
    c_simul_at_full:    cover (full_o && wr_en_i && rd_en_i && !overflow_o);
    c_simul_at_empty:   cover (empty_o && wr_en_i && rd_en_i);
    c_order_full:       cover (f_chk2 && f_seen_full && f_v1 != f_v2);
    c_pop_item2:        cover (f_pop2 && f_v1 != f_v2);
  end
`endif
