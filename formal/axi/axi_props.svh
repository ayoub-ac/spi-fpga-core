// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// Formal properties for axi4lite_slave (+ its axi4lite_regfile), included
// just before `endmodule` of a formal-only copy of rtl/axi4lite_slave.sv.
// Master side is modelled with assumptions; everything else is asserted.
// Derived from the independent SBY setup of the v1.0 review; v1.1 adds the
// write-event data/strobe outputs used by spi_axi4lite.
`ifdef FORMAL
  localparam int unsigned F_ALIGN = $clog2(DATA_WIDTH/8);
  localparam [1:0] F_OKAY = 2'b00, F_SLVERR = 2'b10, F_DECERR = 2'b11;
  localparam [1:0] F_RW = 2'b00, F_RO = 2'b01, F_RC = 2'b10, F_W1C = 2'b11;

  wire clk = s_axi_aclk;
  wire rstn = s_axi_aresetn;

  reg f_past_valid = 1'b0;
  always @(posedge clk) f_past_valid <= 1'b1;
  always @(*) if (!f_past_valid) assume (!rstn);

  // ------------------------------------------------------------------
  // Master (environment) assumptions, AXI4 A3.1/A3.2
  // ------------------------------------------------------------------
  always @(*) if (!rstn) begin
    assume (!s_axi_awvalid);
    assume (!s_axi_wvalid);
    assume (!s_axi_arvalid);
  end
  always @(posedge clk) if (f_past_valid && $past(rstn) && rstn) begin
    if ($past(s_axi_awvalid && !s_axi_awready)) begin
      assume (s_axi_awvalid);
      assume (s_axi_awaddr == $past(s_axi_awaddr));
      assume (s_axi_awprot == $past(s_axi_awprot));
    end
    if ($past(s_axi_wvalid && !s_axi_wready)) begin
      assume (s_axi_wvalid);
      assume (s_axi_wdata == $past(s_axi_wdata));
      assume (s_axi_wstrb == $past(s_axi_wstrb));
    end
    if ($past(s_axi_arvalid && !s_axi_arready)) begin
      assume (s_axi_arvalid);
      assume (s_axi_araddr == $past(s_axi_araddr));
      assume (s_axi_arprot == $past(s_axi_arprot));
    end
  end
  // BREADY / RREADY / slv_err_i / rd_payload_i are unconstrained.

  // ------------------------------------------------------------------
  // Handshake bookkeeping (black-box)
  // ------------------------------------------------------------------
  wire f_aw_hs = s_axi_awvalid && s_axi_awready;
  wire f_w_hs  = s_axi_wvalid  && s_axi_wready;
  wire f_b_hs  = s_axi_bvalid  && s_axi_bready;
  wire f_ar_hs = s_axi_arvalid && s_axi_arready;
  wire f_r_hs  = s_axi_rvalid  && s_axi_rready;

  reg [2:0] f_aw_out, f_w_out, f_ar_out;      // accepted requests without response
  reg [ADDR_WIDTH-1:0] f_awaddr;
  reg [DATA_WIDTH-1:0] f_wdata;
  reg [DATA_WIDTH/8-1:0] f_wstrb;
  reg [2:0] f_b_wait;
  always @(posedge clk) begin
    if (!rstn) begin
      f_aw_out <= 0; f_w_out <= 0; f_ar_out <= 0; f_b_wait <= 0;
    end else begin
      f_aw_out <= f_aw_out + f_aw_hs - f_b_hs;
      f_w_out  <= f_w_out  + f_w_hs  - f_b_hs;
      f_ar_out <= f_ar_out + f_ar_hs - f_r_hs;
      f_b_wait <= (f_aw_out != 0 && f_w_out != 0 && !s_axi_bvalid) ? f_b_wait + 1'b1 : 3'd0;
    end
    if (f_aw_hs) f_awaddr <= s_axi_awaddr;
    if (f_w_hs) begin f_wdata <= s_axi_wdata; f_wstrb <= s_axi_wstrb; end
  end

  function automatic logic f_bad_addr(input logic [ADDR_WIDTH-1:0] a);
    f_bad_addr = (a[F_ALIGN-1:0] != '0) || ((a >> F_ALIGN) >= NUM_REGS);
  endfunction

  // ------------------------------------------------------------------
  // Slave assertions: reset, VALID hold, payload stability
  // ------------------------------------------------------------------
  always @(posedge clk) if (f_past_valid) begin
    if (!$past(rstn)) begin
      a_rst_bvalid: assert (!s_axi_bvalid);
      a_rst_rvalid: assert (!s_axi_rvalid);
    end else begin
      if ($past(s_axi_bvalid && !s_axi_bready)) begin
        a_bvalid_hold:  assert (s_axi_bvalid);
        a_bresp_stable: assert (s_axi_bresp == $past(s_axi_bresp));
      end
      if ($past(s_axi_rvalid && !s_axi_rready)) begin
        a_rvalid_hold:  assert (s_axi_rvalid);
        a_rdata_stable: assert (s_axi_rdata == $past(s_axi_rdata));
        a_rresp_stable: assert (s_axi_rresp == $past(s_axi_rresp));
      end
    end
  end

  // ------------------------------------------------------------------
  // Exactly one response per request
  // ------------------------------------------------------------------
  always @(*) if (f_past_valid && rstn) begin
    a_no_b_without_aw_w: assert (!s_axi_bvalid || (f_aw_out != 0 && f_w_out != 0));
    a_no_r_without_ar:   assert (!s_axi_rvalid || f_ar_out != 0);
    a_aw_le1:            assert (f_aw_out <= 1);
    a_w_le1:             assert (f_w_out  <= 1);
    a_ar_le1:            assert (f_ar_out <= 1);
    a_b_bounded:         assert (f_b_wait <= 1);       // B within 1 cycle of AW+W
    a_r_follows_ar:      assert (s_axi_rvalid == (f_ar_out != 0));
    a_aw_ready_idle:     assert (f_aw_out != 0 || s_axi_awready);   // no deadlock
    a_w_ready_idle:      assert (f_w_out  != 0 || s_axi_wready);
    a_ar_ready_idle:     assert (f_ar_out != 0 || s_axi_arready);
  end

  // ------------------------------------------------------------------
  // Response codes and data: shadow model of one arbitrary register
  // ------------------------------------------------------------------
  (* anyconst *) reg [$clog2(NUM_REGS)-1:0] f_idx;
  wire [1:0] f_attr = REG_ATTR[2*f_idx +: 2];
  reg [DATA_WIDTH-1:0] f_shadow;
  reg f_slverr_w;

  function automatic logic [DATA_WIDTH-1:0] f_merge(input logic [DATA_WIDTH-1:0] o,
      input logic [DATA_WIDTH-1:0] d, input logic [DATA_WIDTH/8-1:0] s);
    integer b;
    for (b = 0; b < DATA_WIDTH/8; b = b + 1) if (s[b]) o[b*8 +: 8] = d[b*8 +: 8];
    f_merge = o;
  endfunction
  function automatic logic [DATA_WIDTH-1:0] f_w1c(input logic [DATA_WIDTH-1:0] o,
      input logic [DATA_WIDTH-1:0] d, input logic [DATA_WIDTH/8-1:0] s);
    integer b;
    for (b = 0; b < DATA_WIDTH/8; b = b + 1) if (s[b]) o[b*8 +: 8] = o[b*8 +: 8] & ~d[b*8 +: 8];
    f_w1c = o;
  endfunction

  wire f_wr_hit = rf_wr_en && !f_bad_addr(f_awaddr) && ((f_awaddr >> F_ALIGN) == f_idx);
  wire f_rd_hit = f_ar_hs && !f_bad_addr(s_axi_araddr) && ((s_axi_araddr >> F_ALIGN) == f_idx);

  always @(posedge clk) begin
    if (!rstn) begin
      f_shadow <= REG_RESET[f_idx*DATA_WIDTH +: DATA_WIDTH];
    end else begin
      if (f_rd_hit && f_attr == F_RC) f_shadow <= '0;
      else if (f_wr_hit && f_attr == F_RW)  f_shadow <= f_merge(f_shadow, f_wdata, f_wstrb);
      else if (f_wr_hit && f_attr == F_W1C) f_shadow <= f_w1c(f_shadow, f_wdata, f_wstrb);
    end
    if (rf_wr_en) f_slverr_w <= slv_err_i;
  end

  // regfile write happens once, between AW/W acceptance and B
  reg f_wr_done;
  always @(posedge clk)
    if (!rstn || f_b_hs) f_wr_done <= 1'b0;
    else if (rf_wr_en)   f_wr_done <= 1'b1;
  always @(*) if (f_past_valid && rstn) begin
    a_wr_once:      assert (!(rf_wr_en && f_wr_done));
    a_wr_after_req: assert (!rf_wr_en || (f_aw_out != 0 && f_w_out != 0));
    a_b_after_wr:   assert (!s_axi_bvalid || f_wr_done);
    if (rf_wr_en) begin
      a_wr_addr: assert (rf_wr_addr == f_awaddr);
      a_wr_data: assert (rf_wr_data == f_wdata);
      a_wr_strb: assert (rf_wr_strb == f_wstrb);
    end
    if (s_axi_bvalid)
      a_bresp: assert (s_axi_bresp == (f_bad_addr(f_awaddr) ? F_DECERR :
                                      f_slverr_w ? F_SLVERR : F_OKAY));
    a_storage: assert (reg_storage_o[f_idx*DATA_WIDTH +: DATA_WIDTH] == f_shadow);
    // the side-band write event carries the committed address, data, strobes
    if (wr_event_o) begin
      a_wr_event_addr: assert (wr_event_addr_o == f_awaddr && !f_bad_addr(f_awaddr));
      a_wr_event_data: assert (wr_event_data_o == f_wdata);
      a_wr_event_strb: assert (wr_event_strb_o == f_wstrb);
      a_wr_event_once: assert (rf_wr_en);
    end
    if (rf_wr_en && !f_bad_addr(f_awaddr)) a_wr_event_fires: assert (wr_event_o);
  end

  // expected read response captured at AR acceptance
  reg [DATA_WIDTH-1:0] f_rexp;
  reg [1:0]            f_rresp_exp;
  reg                  f_rchk_data;
  always @(posedge clk) if (f_ar_hs) begin
    f_rresp_exp <= f_bad_addr(s_axi_araddr) ? F_DECERR : (slv_err_i ? F_SLVERR : F_OKAY);
    f_rchk_data <= f_bad_addr(s_axi_araddr) || f_rd_hit;
    f_rexp      <= f_bad_addr(s_axi_araddr) ? '0 :
                   (f_attr == F_RO) ? rd_payload_i[f_idx*DATA_WIDTH +: DATA_WIDTH] : f_shadow;
  end
  always @(*) if (f_past_valid && rstn && s_axi_rvalid) begin
    a_rresp: assert (s_axi_rresp == f_rresp_exp);
    if (f_rchk_data) a_rdata: assert (s_axi_rdata == f_rexp);
  end

`ifdef AXI_WHITEBOX
  always @(*) if (f_past_valid && rstn) begin
    w_awcap: assert (aw_captured_q == (f_aw_out != 0));
    w_wcap:  assert (w_captured_q  == (f_w_out  != 0));
    w_bv:    assert (!bvalid_q || (aw_captured_q && w_captured_q));
    w_rv:    assert (rvalid_q == rd_in_flight_q);
    if (aw_captured_q) w_awaddr: assert (aw_addr_q == f_awaddr);
    if (w_captured_q) begin
      w_wdata: assert (w_data_q == f_wdata);
      w_wstrb: assert (w_strb_q == f_wstrb);
    end
    w_fire_done: assert (f_wr_done == bvalid_q);
  end
`endif

  // ------------------------------------------------------------------
  // Reachability
  // ------------------------------------------------------------------
  reg f_aw_first, f_w_first, f_saw_bp_b, f_saw_bp_r;
  reg [1:0] f_bp_b, f_bp_r;
  always @(posedge clk) begin
    if (!rstn) begin
      f_aw_first <= 0; f_w_first <= 0; f_bp_b <= 0; f_bp_r <= 0;
    end else begin
      if (f_aw_hs && !f_w_hs && f_w_out == 0) f_aw_first <= 1;
      if (f_w_hs && !f_aw_hs && f_aw_out == 0) f_w_first <= 1;
      if (f_b_hs) begin f_aw_first <= 0; f_w_first <= 0; end
      f_bp_b <= (s_axi_bvalid && !s_axi_bready) ? (f_bp_b == 3 ? 3 : f_bp_b + 1) : 0;
      f_bp_r <= (s_axi_rvalid && !s_axi_rready) ? (f_bp_r == 3 ? 3 : f_bp_r + 1) : 0;
    end
  end
  always @(posedge clk) if (f_past_valid && rstn && $past(rstn)) begin
    c_aw_before_w:  cover (f_b_hs && f_aw_first);
    c_w_before_aw:  cover (f_b_hs && f_w_first);
    c_simul_aw_w:   cover (f_aw_hs && f_w_hs);
    c_b_backpress:  cover (f_b_hs && f_bp_b == 3);
    c_r_backpress:  cover (f_r_hs && f_bp_r == 3);
    c_decerr_w:     cover (f_b_hs && s_axi_bresp == F_DECERR);
    c_decerr_r:     cover (f_r_hs && s_axi_rresp == F_DECERR);
    c_slverr_w:     cover (f_b_hs && s_axi_bresp == F_SLVERR);
    c_rw_readback:  cover (f_r_hs && f_rchk_data && f_attr == F_RW && s_axi_rdata == 32'hA5A5_5A5A);
    c_w1c_clear:    cover (f_wr_hit && f_attr == F_W1C && f_shadow != 0);
    c_rc_clear:     cover (f_rd_hit && f_attr == F_RC && f_shadow != 0);
    c_wr_and_rd:    cover (rf_wr_en && f_ar_hs);
  end
`endif
