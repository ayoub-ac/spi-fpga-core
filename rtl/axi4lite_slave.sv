// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// AXI4-Lite slave protocol engine (ARM IHI 0022, AXI4 §B.1).
//
// Five independent channels:
//   * AW (write address)   AWADDR / AWVALID / AWREADY / AWPROT
//   * W  (write data)      WDATA  / WSTRB  / WVALID  / WREADY
//   * B  (write response)  BVALID / BREADY / BRESP
//   * AR (read address)    ARADDR / ARVALID / ARREADY / ARPROT
//   * R  (read data)       RDATA  / RVALID  / RREADY  / RRESP
//
// The slave accepts AW and W in either order or together (full-handshake
// freedom required by §A3.2.1) and only emits B once BOTH have been
// captured. On the read side it accepts an AR, presents R in the next
// cycle, and waits for RREADY before accepting the next AR. RVALID is
// kept high until RREADY (no de-asserting after assertion, §A3.1.2).
//
// Response codes (§A3.4.4):
//   2'b00 OKAY     normal completion
//   2'b01 EXOKAY   N/A on AXI4-Lite (exclusive accesses are AXI-only)
//   2'b10 SLVERR   slave-side error (the integrator marks this through
//                  the optional slv_err_i hint, e.g. when a write to a
//                  side-effect register would violate a precondition)
//   2'b11 DECERR   address decode error, raised when wr_out_of_range_o
//                  or rd_out_of_range_o asserts (alignment or
//                  out-of-range reg index).
//
// Performance:
//   * Best case: B-cycle latency 1 after AW + W; RVALID one cycle after
//     ARVALID & ARREADY. Both channels can be back-to-back with no idle
//     gap (the FSM accepts a new AW or AR on the cycle the previous B
//     or R is consumed).
//   * The slave is purely combinational on the slave side — there is no
//     deep pipeline. WSTRB-merged writes happen in one cycle inside the
//     register file; SLVERR and DECERR latencies match OKAY.
//
// Reset: synchronous, active-low (rst_ni). A glitched ARVALID or AWVALID
// during reset is masked by the FSM (xVALID is observable only after
// rst_ni is high for one cycle).

module axi4lite_slave #(
  parameter int unsigned ADDR_WIDTH = 8,
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned NUM_REGS   = 16,
  parameter logic [2*NUM_REGS-1:0] REG_ATTR = {2*NUM_REGS{1'b0}},
  parameter logic [NUM_REGS*DATA_WIDTH-1:0] REG_RESET = {NUM_REGS*DATA_WIDTH{1'b0}}
) (
  input  logic                    s_axi_aclk,
  input  logic                    s_axi_aresetn,

  // AW channel
  input  logic [ADDR_WIDTH-1:0]   s_axi_awaddr,
  input  logic [2:0]              s_axi_awprot,
  input  logic                    s_axi_awvalid,
  output logic                    s_axi_awready,

  // W channel
  input  logic [DATA_WIDTH-1:0]   s_axi_wdata,
  input  logic [DATA_WIDTH/8-1:0] s_axi_wstrb,
  input  logic                    s_axi_wvalid,
  output logic                    s_axi_wready,

  // B channel
  output logic [1:0]              s_axi_bresp,
  output logic                    s_axi_bvalid,
  input  logic                    s_axi_bready,

  // AR channel
  input  logic [ADDR_WIDTH-1:0]   s_axi_araddr,
  input  logic [2:0]              s_axi_arprot,
  input  logic                    s_axi_arvalid,
  output logic                    s_axi_arready,

  // R channel
  output logic [DATA_WIDTH-1:0]   s_axi_rdata,
  output logic [1:0]              s_axi_rresp,
  output logic                    s_axi_rvalid,
  input  logic                    s_axi_rready,

  // User-side hooks
  input  logic [NUM_REGS*DATA_WIDTH-1:0] rd_payload_i,
  input  logic                    slv_err_i,
  output logic [NUM_REGS*DATA_WIDTH-1:0] reg_storage_o,
  output logic                    wr_event_o,
  output logic [ADDR_WIDTH-1:0]   wr_event_addr_o,
  output logic                    rd_event_o,
  output logic [ADDR_WIDTH-1:0]   rd_event_addr_o
);

  localparam logic [1:0] RESP_OKAY   = 2'b00;
  localparam logic [1:0] RESP_SLVERR = 2'b10;
  localparam logic [1:0] RESP_DECERR = 2'b11;

  // ------------------------------------------------------------------
  // Write channel buffering.
  //
  // We accept AW and W independently. Each side has a one-deep skid of
  // "captured" address/data so the master can hand us one before the
  // other. Once BOTH have been captured AND the previous B has been
  // consumed, we issue the regfile write and assert BVALID.
  // ------------------------------------------------------------------
  logic                    aw_captured_q;
  logic [ADDR_WIDTH-1:0]   aw_addr_q;
  logic                    w_captured_q;
  logic [DATA_WIDTH-1:0]   w_data_q;
  logic [DATA_WIDTH/8-1:0] w_strb_q;
  logic                    bvalid_q;
  logic [1:0]              bresp_q;

  logic write_fire;
  assign write_fire = aw_captured_q && w_captured_q && !bvalid_q;

  // Default ready handshake: accept whenever we have not yet captured.
  assign s_axi_awready = !aw_captured_q;
  assign s_axi_wready  = !w_captured_q;

  assign s_axi_bvalid  = bvalid_q;
  assign s_axi_bresp   = bresp_q;

  // Write port to the regfile (one-cycle pulse).
  logic                    rf_wr_en;
  logic [ADDR_WIDTH-1:0]   rf_wr_addr;
  logic [DATA_WIDTH-1:0]   rf_wr_data;
  logic [DATA_WIDTH/8-1:0] rf_wr_strb;
  logic                    rf_wr_out_of_range;

  assign rf_wr_en   = write_fire;
  assign rf_wr_addr = aw_addr_q;
  assign rf_wr_data = w_data_q;
  assign rf_wr_strb = w_strb_q;

  always_ff @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn) begin
      aw_captured_q <= 1'b0;
      aw_addr_q     <= '0;
      w_captured_q  <= 1'b0;
      w_data_q      <= '0;
      w_strb_q      <= '0;
      bvalid_q      <= 1'b0;
      bresp_q       <= RESP_OKAY;
    end else begin
      // AW capture
      if (s_axi_awvalid && s_axi_awready) begin
        aw_captured_q <= 1'b1;
        aw_addr_q     <= s_axi_awaddr;
      end
      // W capture
      if (s_axi_wvalid && s_axi_wready) begin
        w_captured_q <= 1'b1;
        w_data_q     <= s_axi_wdata;
        w_strb_q     <= s_axi_wstrb;
      end

      // Issue B response when both halves are captured and B is free.
      if (write_fire) begin
        bvalid_q <= 1'b1;
        if (rf_wr_out_of_range)      bresp_q <= RESP_DECERR;
        else if (slv_err_i)          bresp_q <= RESP_SLVERR;
        else                         bresp_q <= RESP_OKAY;
      end

      // B handshake: clear once master accepts.
      if (bvalid_q && s_axi_bready) begin
        bvalid_q      <= 1'b0;
        aw_captured_q <= 1'b0;
        w_captured_q  <= 1'b0;
        bresp_q       <= RESP_OKAY;
      end
    end
  end

  // ------------------------------------------------------------------
  // Read channel.
  //
  // We accept AR, present R in the next cycle. RVALID stays high until
  // RREADY consumes it (§A3.2.1: a master should never delay RREADY
  // forever, but the slave must hold). After RREADY we are immediately
  // ready to accept the next AR.
  // ------------------------------------------------------------------
  logic                    rd_in_flight_q;
  logic [ADDR_WIDTH-1:0]   ar_addr_q;
  logic [DATA_WIDTH-1:0]   rdata_q;
  logic [1:0]              rresp_q;
  logic                    rvalid_q;

  assign s_axi_arready = !rd_in_flight_q && !rvalid_q;
  assign s_axi_rdata   = rdata_q;
  assign s_axi_rresp   = rresp_q;
  assign s_axi_rvalid  = rvalid_q;

  // Read port to regfile. We pulse `rf_rd_en` on the AR-accept cycle
  // and use `s_axi_araddr` directly so the regfile produces a
  // combinational `rf_rd_data` we sample into `rdata_q` on the same
  // posedge that captures the AR. This gives 1-cycle RDATA latency
  // (RVALID rises the cycle after AR-accept).
  logic                  rf_rd_en;
  logic [ADDR_WIDTH-1:0] rf_rd_addr;
  logic [DATA_WIDTH-1:0] rf_rd_data;
  logic                  rf_rd_out_of_range;

  assign rf_rd_en   = s_axi_arvalid && s_axi_arready;
  assign rf_rd_addr = s_axi_araddr;

  always_ff @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn) begin
      rd_in_flight_q <= 1'b0;
      ar_addr_q      <= '0;
      rdata_q        <= '0;
      rresp_q        <= RESP_OKAY;
      rvalid_q       <= 1'b0;
    end else begin
      // AR-accept: sample regfile combinational read directly into
      // rdata_q, set RRESP from the alignment / range / slv_err state,
      // and arm RVALID for the next cycle.
      if (s_axi_arvalid && s_axi_arready) begin
        rd_in_flight_q <= 1'b1;
        ar_addr_q      <= s_axi_araddr;
        rdata_q        <= rf_rd_data;
        if (rf_rd_out_of_range)      rresp_q <= RESP_DECERR;
        else if (slv_err_i)          rresp_q <= RESP_SLVERR;
        else                         rresp_q <= RESP_OKAY;
        rvalid_q       <= 1'b1;
      end

      // R handshake: clear once master accepts.
      if (rvalid_q && s_axi_rready) begin
        rvalid_q       <= 1'b0;
        rd_in_flight_q <= 1'b0;
        rresp_q        <= RESP_OKAY;
      end
    end
  end

  // ------------------------------------------------------------------
  // Register file instance.
  // ------------------------------------------------------------------
  axi4lite_regfile #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH),
    .NUM_REGS  (NUM_REGS),
    .REG_ATTR  (REG_ATTR),
    .REG_RESET (REG_RESET)
  ) u_rf (
    .clk_i             (s_axi_aclk),
    .rst_ni            (s_axi_aresetn),
    .wr_en_i           (rf_wr_en),
    .wr_addr_i         (rf_wr_addr),
    .wr_data_i         (rf_wr_data),
    .wr_strb_i         (rf_wr_strb),
    .rd_en_i           (rf_rd_en),
    .rd_addr_i         (rf_rd_addr),
    .rd_data_o         (rf_rd_data),
    .rd_payload_i      (rd_payload_i),
    .reg_storage_o     (reg_storage_o),
    .wr_out_of_range_o (rf_wr_out_of_range),
    .rd_out_of_range_o (rf_rd_out_of_range)
  );

  // Side-band events for user logic that wants a write/read pulse
  // (e.g. to start a state machine when software writes a control reg).
  assign wr_event_o      = rf_wr_en && !rf_wr_out_of_range;
  assign wr_event_addr_o = rf_wr_addr;
  assign rd_event_o      = rf_rd_en && !rf_rd_out_of_range;
  assign rd_event_addr_o = rf_rd_addr;

endmodule
