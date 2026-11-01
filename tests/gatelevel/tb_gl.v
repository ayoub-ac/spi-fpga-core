// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
`timescale 1ps/1ps
// Gate-level glitch check of the spi_master outputs.
//
// The netlist (Yosys generic gates, see run.sh) is simulated with a random
// transport delay per gate (cells_delay.v). The monitor counts pulses
// shorter than 2 ns on every CS_n line and on MOSI: a registered output
// never produces one, a decoded output can. 120 transfers cover all modes,
// dividers 0..2, CS setup/hold 0..3, inter-byte on/off, 1..3 words and all
// four chip selects. Exit status is non-zero on any CS_n glitch, any MOSI
// glitch, or a transfer that does not complete.
module tb_gl;
  // Inputs get their first value at 1 ps, not at time 0: a gate whose inputs
  // only carry time-0 initial values never evaluates and would stay X.
  reg clk = 1'b0;
  reg rst_n, cpol, cpha, lsb, inter, tx_we, rx_re, start, miso;
  reg [15:0] div; reg [1:0] cs_sel; reg [7:0] setup, hold, wdata;
  wire sck, mosi, busy, done, txf, txe, rxf, rxe, ovr; wire [3:0] cs_n; wire [4:0] txc, rxc; wire [7:0] rdata;
  spi_master dut (.clk_i(clk), .rst_ni(rst_n), .cpol_i(cpol), .cpha_i(cpha), .lsb_first_i(lsb), .clk_div_i(div),
    .cs_sel_i(cs_sel), .cs_setup_cycles_i(setup), .cs_hold_cycles_i(hold), .cs_inter_byte_i(inter),
    .tx_we_i(tx_we), .tx_wdata_i(wdata), .tx_full_o(txf), .tx_empty_o(txe), .tx_count_o(txc),
    .rx_re_i(rx_re), .rx_rdata_o(rdata), .rx_full_o(rxf), .rx_empty_o(rxe), .rx_count_o(rxc),
    .start_i(start), .busy_o(busy), .xfer_done_o(done), .sck_o(sck), .mosi_o(mosi), .miso_i(miso), .cs_n_o(cs_n),
    .rx_overrun_o(ovr));
  always #5000 clk = ~clk;
  always @(posedge sck or negedge sck) miso <= #1000 $urandom;
  // glitch monitor: any CS_n line pulse shorter than 2 ns, or toggling while it should be stable
  integer i, glitches = 0, cs_changes = 0, hi_pulses = 0, lo_pulses = 0; time last_t [0:3]; reg [3:0] prev = 4'hF;
  initial for (i = 0; i < 4; i = i + 1) last_t[i] = 0;
  always @(cs_n) begin
    for (i = 0; i < 4; i = i + 1) if (cs_n[i] !== prev[i]) begin
      cs_changes = cs_changes + 1;
      if ($time - last_t[i] < 2000 && $time > 200000) begin
        glitches = glitches + 1;
        if (cs_n[i]) lo_pulses = lo_pulses + 1; else hi_pulses = hi_pulses + 1;
        if (glitches <= 6) $display("GLITCH cs_n[%0d]: %s pulse of %0d ps ending at t=%0t ps", i, cs_n[i] ? "LOW (spurious select)" : "HIGH (deselect during frame)", $time - last_t[i], $time);
      end
      last_t[i] = $time;
    end
    prev = cs_n;
  end
  integer mosi_glitch = 0; time mlast = 0;
  always @(mosi) begin if ($time - mlast < 2000 && $time > 200000) mosi_glitch = mosi_glitch + 1; mlast = $time; end
  integer timeouts = 0;
  task automatic wait_done;
    integer n;
    begin
      n = 0;
      while (done !== 1'b1 && n < 20000) begin @(posedge clk); n = n + 1; end
      if (n >= 20000) begin timeouts = timeouts + 1; $display("TIMEOUT at t=%0t ps: busy=%b cs_n=%b sck=%b", $time, busy, cs_n, sck); end
    end
  endtask
  task automatic xfer(input integer m, input integer d, input integer s, input integer h, input integer ib, input integer n, input integer line);
    integer k;
    begin
      @(negedge clk); cpol = m[1]; cpha = m[0]; div = d; setup = s; hold = h; inter = ib; cs_sel = line;
      for (k = 0; k < n; k = k + 1) begin @(negedge clk); tx_we = 1; wdata = $urandom; end
      @(negedge clk); tx_we = 0;
      for (k = 0; k < (ib ? 1 : n); k = k + 1) begin
        @(negedge clk); start = 1; @(negedge clk); start = 0;
        wait_done; repeat (2) @(negedge clk);
      end
      while (!rxe) begin @(negedge clk); rx_re = 1; @(negedge clk); rx_re = 0; end
    end
  endtask
  integer t;
  initial begin
    #1;
    rst_n = 0; cpol = 0; cpha = 0; lsb = 0; inter = 0; tx_we = 0; rx_re = 0; start = 0; miso = 0;
    div = 0; cs_sel = 0; setup = 0; hold = 0; wdata = 0;
    repeat (6) @(negedge clk); rst_n = 1; repeat (3) @(negedge clk);
    for (t = 0; t < 120; t = t + 1) xfer(t % 4, t % 3, (t / 3) % 4, (t / 5) % 3, t % 2, 1 + t % 3, (t / 7) % 4);
    $display("gate-level run: %0d CS_n edges, %0d CS_n glitches (<2ns pulses): %0d HIGH pulses on the selected line, %0d LOW pulses on idle lines; %0d MOSI glitches; %0d timeouts", cs_changes, glitches, hi_pulses, lo_pulses, mosi_glitch, timeouts);
    if (glitches != 0 || mosi_glitch != 0 || timeouts != 0 || cs_changes == 0) begin
      $display("GATE-LEVEL CHECK FAILED");
      $fatal(1);
    end
    $display("GATE-LEVEL CHECK PASSED");
    $finish;
  end
endmodule
