// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// cocotb wrapper: spi_slave plus an optional VCD dump (+vcd=<file>).
module tb_slave #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned FIFO_DEPTH = 16
) (
  input  logic                            clk_i,
  input  logic                            rst_ni,
  input  logic                            cpol_i,
  input  logic                            cpha_i,
  input  logic                            lsb_first_i,
  input  logic                            tx_we_i,
  input  logic [DATA_WIDTH-1:0]           tx_wdata_i,
  output logic                            tx_full_o,
  output logic                            tx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] tx_count_o,
  input  logic                            rx_re_i,
  output logic [DATA_WIDTH-1:0]           rx_rdata_o,
  output logic                            rx_full_o,
  output logic                            rx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] rx_count_o,
  output logic                            busy_o,
  output logic                            xfer_done_o,
  input  logic                            sck_i,
  input  logic                            mosi_i,
  output logic                            miso_o,
  input  logic                            cs_n_i,
  output logic                            miso_oe_o,
  output logic                            tx_underrun_o,
  output logic                            rx_overrun_o
);
  spi_slave #(.DATA_WIDTH(DATA_WIDTH), .FIFO_DEPTH(FIFO_DEPTH)) dut (.*);
  initial begin
    string vcdfile;
    if ($value$plusargs("vcd=%s", vcdfile)) begin
      $dumpfile(vcdfile);
      $dumpvars(0, tb_slave);
    end
  end
endmodule
