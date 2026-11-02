// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// Formal top: axi4lite_slave with the spi_axi4lite attribute map, except
// reg10 made RC so all four attribute types are exercised. Non-zero reset
// values on W1C (reg2) and RC (reg10) make clears observable.
module axi_fv_top (
  input  logic        clk,
  input  logic        rstn,
  input  logic [7:0]  awaddr,  input logic [2:0] awprot, input logic awvalid,
  input  logic [31:0] wdata,   input logic [3:0] wstrb,  input logic wvalid,
  input  logic        bready,
  input  logic [7:0]  araddr,  input logic [2:0] arprot, input logic arvalid,
  input  logic        rready,
  input  logic [16*32-1:0] rd_payload,
  input  logic        slv_err,
  output logic        awready, wready, bvalid, arready, rvalid,
  output logic [1:0]  bresp, rresp,
  output logic [31:0] rdata
);
  localparam logic [31:0] ATTR = 32'h0025_5034;   // reg1,6..9 RO; reg2 W1C; reg10 RC; rest RW
  localparam logic [16*32-1:0] RST =
      (512'h0000_000F << (2*32)) | (512'h5910_017E << (8*32)) |
      (512'h0001_0100 << (9*32)) | (512'h1234_5678 << (10*32));
  axi4lite_slave #(.ADDR_WIDTH(8), .DATA_WIDTH(32), .NUM_REGS(16),
                   .REG_ATTR(ATTR), .REG_RESET(RST)) u_s (
    .s_axi_aclk(clk), .s_axi_aresetn(rstn),
    .s_axi_awaddr(awaddr), .s_axi_awprot(awprot), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
    .s_axi_araddr(araddr), .s_axi_arprot(arprot), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
    .rd_payload_i(rd_payload), .slv_err_i(slv_err),
    .reg_storage_o(), .wr_event_o(), .wr_event_addr_o(), .wr_event_data_o(), .wr_event_strb_o(),
    .rd_event_o(), .rd_event_addr_o()
  );
endmodule
