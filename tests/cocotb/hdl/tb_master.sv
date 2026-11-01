// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// cocotb wrapper: spi_master with cs_n_o[0] split out for cocotbext-spi and
// an optional VCD dump (+vcd=<file>).
module tb_master #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned NUM_CS     = 4,
  parameter int unsigned FIFO_DEPTH = 16,
  parameter int unsigned DIV_WIDTH  = 16,
  parameter int unsigned CS_DLY_W   = 8
) (
  input  logic                                           clk_i,
  input  logic                                           rst_ni,
  input  logic                                           cpol_i,
  input  logic                                           cpha_i,
  input  logic                                           lsb_first_i,
  input  logic [DIV_WIDTH-1:0]                           clk_div_i,
  input  logic [((NUM_CS > 1) ? $clog2(NUM_CS) : 1)-1:0] cs_sel_i,
  input  logic [CS_DLY_W-1:0]                            cs_setup_cycles_i,
  input  logic [CS_DLY_W-1:0]                            cs_hold_cycles_i,
  input  logic                                           cs_inter_byte_i,
  input  logic                                           tx_we_i,
  input  logic [DATA_WIDTH-1:0]                          tx_wdata_i,
  output logic                                           tx_full_o,
  output logic                                           tx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0]                tx_count_o,
  input  logic                                           rx_re_i,
  output logic [DATA_WIDTH-1:0]                          rx_rdata_o,
  output logic                                           rx_full_o,
  output logic                                           rx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0]                rx_count_o,
  output logic                                           rx_overrun_o,
  input  logic                                           start_i,
  output logic                                           busy_o,
  output logic                                           xfer_done_o,
  output logic                                           sck_o,
  output logic                                           mosi_o,
  input  logic                                           miso_i,
  output logic [NUM_CS-1:0]                              cs_n_o,
  output logic                                           cs0_n_o
);
  assign cs0_n_o = cs_n_o[0];
  spi_master #(.DATA_WIDTH(DATA_WIDTH), .NUM_CS(NUM_CS), .FIFO_DEPTH(FIFO_DEPTH),
               .DIV_WIDTH(DIV_WIDTH), .CS_DLY_W(CS_DLY_W)) dut (.*);
  initial begin
    string vcdfile;
    if ($value$plusargs("vcd=%s", vcdfile)) begin
      $dumpfile(vcdfile);
      $dumpvars(0, tb_master);
    end
  end
endmodule
