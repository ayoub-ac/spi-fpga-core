// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// Thin test wrapper: spi_axi4lite unchanged, MISO driven either by an
// internal MOSI->MISO loopback or by an external (Python) SPI slave model.
module tb_axi_top #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned NUM_CS     = 4,
  parameter int unsigned SPI_DATA_W = 8
) (
  input  logic                    s_axi_aclk,
  input  logic                    s_axi_aresetn,
  input  logic [7:0]              s_axi_awaddr,
  input  logic [2:0]              s_axi_awprot,
  input  logic                    s_axi_awvalid,
  output logic                    s_axi_awready,
  input  logic [DATA_WIDTH-1:0]   s_axi_wdata,
  input  logic [DATA_WIDTH/8-1:0] s_axi_wstrb,
  input  logic                    s_axi_wvalid,
  output logic                    s_axi_wready,
  output logic [1:0]              s_axi_bresp,
  output logic                    s_axi_bvalid,
  input  logic                    s_axi_bready,
  input  logic [7:0]              s_axi_araddr,
  input  logic [2:0]              s_axi_arprot,
  input  logic                    s_axi_arvalid,
  output logic                    s_axi_arready,
  output logic [DATA_WIDTH-1:0]   s_axi_rdata,
  output logic [1:0]              s_axi_rresp,
  output logic                    s_axi_rvalid,
  input  logic                    s_axi_rready,
  output logic                    sck_o,
  output logic                    mosi_o,
  output logic [NUM_CS-1:0]       cs_n_o,
  output logic                    irq_o,
  input  logic                    miso_ext_i,
  input  logic                    loopback_i
);
  logic miso;
  assign miso = loopback_i ? mosi_o : miso_ext_i;

  spi_axi4lite #(
    .DATA_WIDTH (DATA_WIDTH),
    .NUM_CS     (NUM_CS),
    .SPI_DATA_W (SPI_DATA_W)
  ) u_dut (
    .s_axi_aclk    (s_axi_aclk),
    .s_axi_aresetn (s_axi_aresetn),
    .s_axi_awaddr  (s_axi_awaddr),
    .s_axi_awprot  (s_axi_awprot),
    .s_axi_awvalid (s_axi_awvalid),
    .s_axi_awready (s_axi_awready),
    .s_axi_wdata   (s_axi_wdata),
    .s_axi_wstrb   (s_axi_wstrb),
    .s_axi_wvalid  (s_axi_wvalid),
    .s_axi_wready  (s_axi_wready),
    .s_axi_bresp   (s_axi_bresp),
    .s_axi_bvalid  (s_axi_bvalid),
    .s_axi_bready  (s_axi_bready),
    .s_axi_araddr  (s_axi_araddr),
    .s_axi_arprot  (s_axi_arprot),
    .s_axi_arvalid (s_axi_arvalid),
    .s_axi_arready (s_axi_arready),
    .s_axi_rdata   (s_axi_rdata),
    .s_axi_rresp   (s_axi_rresp),
    .s_axi_rvalid  (s_axi_rvalid),
    .s_axi_rready  (s_axi_rready),
    .sck_o         (sck_o),
    .mosi_o        (mosi_o),
    .miso_i        (miso),
    .cs_n_o        (cs_n_o),
    .irq_o         (irq_o)
  );

  initial begin
    string vcdfile;
    if ($value$plusargs("vcd=%s", vcdfile)) begin
      $dumpfile(vcdfile);
      $dumpvars(0, tb_axi_top);
    end
  end
endmodule
