// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Formal properties for rtl/spi_master.sv (control path and bus timing).
//
// Included just before `endmodule` of a formal-only copy of the RTL that is
// otherwise byte-identical (Yosys has no `bind`). All inputs are
// unconstrained after an initial reset: configuration may change at any
// time, as software can. Derived from the independent SBY setup of the
// v1.0 review, updated for the v1.1 SCK idle rule.
`ifdef FORMAL
  reg f_past_valid = 1'b0;
  always @(posedge clk_i) f_past_valid <= 1'b1;
  always @(*) if (!f_past_valid) assume (!rst_ni);

  wire              f_cs_idle = &cs_n_o;
  wire [NUM_CS-1:0] f_cs_act  = ~cs_n_o;

  // P1: at most one chip select low; FSM encoding always legal
  always @(*) if (f_past_valid) begin
    a_cs_onehot0:  assert ((f_cs_act & (f_cs_act - 1'b1)) == '0);
    a_state_legal: assert (state_q <= S_DONE);
  end

  // P2: SCK idle level
  always @(posedge clk_i) if (f_past_valid && $past(rst_ni) && rst_ni) begin
    // a CS line never asserts on an SCK change, and SCK is at CPOL then
    if ($past(f_cs_idle) && !f_cs_idle) begin
      a_cs_fall_sck_quiet: assert (sck_o == $past(sck_o));
      a_cs_fall_sck_cpol:  assert (sck_o == cpol_q);
    end
    // while idle, SCK only moves to the configured CPOL ...
    if (f_cs_idle && $past(f_cs_idle) && sck_o != $past(sck_o))
      a_sck_idle_to_cpol: assert (sck_o == $past(cpol_i));
    // ... and sits there after two idle cycles
    if (f_cs_idle && $past(f_cs_idle) && $past(f_cs_idle, 2) && $past(rst_ni, 2))
      a_sck_idle_cpol: assert (sck_o == $past(cpol_i));
    // while a CS line is low and no bit is being shifted, SCK rests at CPOL
    if (!f_cs_idle && state_q != S_SHIFT)
      a_sck_rest_cpol: assert (sck_o == cpol_q);
  end
  // SCK right after reset equals cpol_i sampled in reset
  always @(posedge clk_i) if (f_past_valid && !$past(rst_ni))
    a_sck_reset_cpol: assert (sck_o == $past(cpol_i));

  // P3: MOSI is quiet while idle and keeps the last bit after the final edge
  always @(posedge clk_i) if (f_past_valid && $past(rst_ni) && rst_ni) begin
    if (f_cs_idle && $past(f_cs_idle))
      a_mosi_idle_quiet: assert (mosi_o == $past(mosi_o));
    if ($past(state_q) == S_SHIFT && state_q == S_CS_HOLD)
      a_mosi_last_bit_hold: assert (mosi_o == $past(mosi_o));
    if ($past(state_q) == S_CS_HOLD && state_q == S_CS_HOLD)
      a_mosi_cs_hold_quiet: assert (mosi_o == $past(mosi_o));
  end

  // P4: busy consistent with CS; busy cannot stay high without new TX writes
  always @(*) if (f_past_valid && !f_cs_idle)
    a_busy_while_cs: assert (busy_o);
`ifdef F_BUSY_MAX
  reg [7:0] f_busy_cnt;
  always @(posedge clk_i)
    if (!rst_ni || !busy_o || tx_we_i) f_busy_cnt <= 8'd0;
    else if (f_busy_cnt != 8'hFF)      f_busy_cnt <= f_busy_cnt + 1'b1;
  always @(*) if (f_past_valid) a_busy_bounded: assert (f_busy_cnt <= `F_BUSY_MAX);
`endif

  // P5: xfer_done_o is a single-cycle pulse that follows a busy period
  always @(posedge clk_i) if (f_past_valid && $past(rst_ni)) begin
    if ($past(xfer_done_o)) a_done_one_cycle: assert (!xfer_done_o);
    if (xfer_done_o)        a_done_after_busy: assert ($past(busy_o));
  end

  // P6: rx_overrun_o is the RX FIFO drop strobe, one cycle later
  always @(posedge clk_i) if (f_past_valid && $past(rst_ni))
    a_overrun_strobe: assert (rx_overrun_o == $past(rx_fifo_overflow));

`ifdef MASTER_WHITEBOX
  always @(*) if (f_past_valid) begin
    w_busy_state: assert (busy_o == (state_q != S_IDLE));
    w_done_idle:  assert (!xfer_done_o || state_q == S_IDLE);
    w_pend_idle:  assert (!start_pend_q || state_q == S_IDLE);
    w_cs_state:   assert (f_cs_idle == (state_q == S_IDLE || state_q == S_DONE));
    if (state_q == S_SHIFT) begin
      w_sck_shift: assert (sck_q == (cpol_q ^ edge_cnt_q[0]));
      w_edge_rng:  assert (edge_cnt_q < 2*DATA_WIDTH);
    end else begin
      w_edge_zero: assert (edge_cnt_q == '0);
      if (state_q != S_IDLE) w_sck_rest: assert (sck_q == cpol_q);
    end
  end
`endif

  // reachability
  always @(posedge clk_i) if (f_past_valid && rst_ni && $past(rst_ni)) begin
    c_done:          cover (xfer_done_o);
    c_done_cpol1:    cover (xfer_done_o && cpol_q && cpha_q);
    c_cs_last:       cover (cs_n_o == {1'b0, {(NUM_CS-1){1'b1}}});
    c_inter_byte:    cover (state_q == S_LOAD_WAIT && $past(state_q) == S_CS_HOLD);
    c_idle_cpol_chg: cover (f_cs_idle && sck_o != $past(sck_o));
    c_start_pend:    cover (start_pend_q);
    c_overrun:       cover (rx_overrun_o);
  end
`endif
