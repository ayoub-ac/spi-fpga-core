// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
`timescale 1ns/1ps
// RTL spi_fifo against its synth_ice40 netlist (block RAM for larger depths),
// same random stimulus, biased towards a full FIFO so that read + write on a
// full FIFO (a read and a write of the same address in the standard read
// mode) happens often. Every output is compared on every cycle.
module tb_fifo_ram;
  parameter int WIDTH = 8;
  parameter int DEPTH = 16;
  parameter int FWFT  = 0;
  parameter int CYCLES = 200000;
  localparam int CW = $clog2(DEPTH + 1);
  logic clk = 0, rst_n = 0, wr_en = 0, rd_en = 0;
  logic [WIDTH-1:0] wr_data = '0;
  logic [WIDTH-1:0] rd_r, rd_g;
  logic e_r, e_g, f_r, f_g, o_r, o_g, u_r, u_g;
  logic [CW-1:0] c_r, c_g;
  always #5 clk = ~clk;
  spi_fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH), .FWFT(FWFT)) u_rtl (
    .clk_i(clk), .rst_ni(rst_n), .wr_en_i(wr_en), .wr_data_i(wr_data), .rd_en_i(rd_en),
    .rd_data_o(rd_r), .empty_o(e_r), .full_o(f_r), .count_o(c_r), .overflow_o(o_r), .underflow_o(u_r));
  spi_fifo_gl u_gl (
    .clk_i(clk), .rst_ni(rst_n), .wr_en_i(wr_en), .wr_data_i(wr_data), .rd_en_i(rd_en),
    .rd_data_o(rd_g), .empty_o(e_g), .full_o(f_g), .count_o(c_g), .overflow_o(o_g), .underflow_o(u_g));
  int mism = 0, collisions = 0, i, phase;
  // compare after every rising edge (outputs settled)
  always @(negedge clk) if (rst_n) begin
    if ({rd_r, e_r, f_r, c_r, o_r, u_r} !== {rd_g, e_g, f_g, c_g, o_g, u_g}) begin
      mism++;
      if (mism <= 5) $display("MISMATCH t=%0t rtl rd=%h e%b f%b c%0d o%b u%b  gl rd=%h e%b f%b c%0d o%b u%b",
                              $time, rd_r, e_r, f_r, c_r, o_r, u_r, rd_g, e_g, f_g, c_g, o_g, u_g);
    end
  end
  initial begin
    repeat (4) @(posedge clk);
    #1 rst_n = 1;
    for (i = 0; i < CYCLES; i++) begin
      @(posedge clk); #1;
      phase = (i / 500) % 4;     // fill-biased, balanced, drain-biased, balanced
      wr_en   = ($urandom % 100) < (phase == 0 ? 85 : phase == 2 ? 15 : 50);
      rd_en   = ($urandom % 100) < (phase == 0 ? 35 : phase == 2 ? 85 : 50);
      wr_data = $urandom;
      if (f_r && wr_en && rd_en) collisions++;
      if (($urandom % 20000) == 0) begin rst_n = 0; @(posedge clk); #1 rst_n = 1; end
    end
    $display("fifo DEPTH=%0d FWFT=%0d: %0d cycles, %0d read+write on full, %0d mismatches", DEPTH, FWFT,
             CYCLES, collisions, mism);
    if (mism != 0) $fatal(1, "RTL and netlist differ");
    $finish;
  end
endmodule
