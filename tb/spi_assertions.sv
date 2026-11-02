// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// SPI protocol assertions (Motorola SPI block guide §8.5).
//
// Properties (9 total):
//   p_sck_idle_to_cpol   While CS is high (idle), SCK only changes to move to
//                        the configured CPOL; it never pulses.
//   p_sck_idle_polarity  After two idle cycles, SCK equals CPOL.
//   p_sck_quiet_cs_fall  SCK does not change on the clock edge where a CS
//                        line asserts.
//   p_no_data_when_idle  MOSI does not toggle while all CS lines are high.
//   p_cs_one_hot_active  At most one CS line is asserted (low) at a time.
//   p_busy_during_xfer   m_busy is high in the cycle after any cycle with a
//                        CS line asserted.
//   p_no_done_when_idle  m_done does not pulse while the bus is idle and
//                        m_busy was low in the previous cycle.
//   p_done_pulse_oneshot m_done is never high in two consecutive cycles.
//   p_no_busy_deadlock   m_busy never stays high for 100000 cycles.

module spi_assertions #(
  parameter int unsigned NUM_CS = 4
) (
  input logic               clk_i,
  input logic               rst_ni,
  input logic               sck,
  input logic               mosi,
  input logic               miso,
  input logic [NUM_CS-1:0]  cs_n,
  input logic               m_busy,
  input logic               m_done,
  input logic               cpol        // master cpol_i
);
`ifndef SYNTHESIS

  // CS_n all high == bus idle.
  logic cs_idle;
  assign cs_idle = &cs_n;

  // SCK previous sample for edge detection. Tracked once the FSM has
  // settled (both rst_ni high AND CS has been observed at least once).
  logic sck_q;
  logic post_reset_q;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin sck_q <= 1'b0; post_reset_q <= 1'b0; end
    else         begin sck_q <= sck;  post_reset_q <= 1'b1; end
  end
  logic sck_edge;
  assign sck_edge = post_reset_q && (sck ^ sck_q);

  // ---- p_sck_idle_to_cpol ----
  // While the bus is idle (all CS high) SCK may change only to follow a
  // CPOL change (the master parks SCK at cpol_i while idle, so it is at
  // the idle level before the next CS falls). Any other idle SCK edge,
  // and any SCK pulse, is a bug.
  property p_sck_idle_to_cpol;
    @(posedge clk_i) disable iff (!rst_ni)
      cs_idle && sck_edge |-> (sck == $past(cpol));
  endproperty
  a_sck_idle_to_cpol: assert property (p_sck_idle_to_cpol)
    else $error("spi: SCK toggled while CS idle without a CPOL change");

  // ---- p_sck_idle_polarity ----
  // After two idle cycles SCK sits at CPOL (one cycle to leave the last
  // transfer, one to follow a CPOL change).
  property p_sck_idle_polarity;
    @(posedge clk_i) disable iff (!rst_ni)
      post_reset_q && cs_idle && $past(cs_idle) && $past(cs_idle, 2)
        |-> (sck == $past(cpol));
  endproperty
  a_sck_idle_polarity: assert property (p_sck_idle_polarity)
    else $error("spi: SCK not at CPOL while idle");

  // ---- p_sck_quiet_cs_fall ----
  // A CS line never asserts on the same clock edge as an SCK change, so a
  // slave cannot see a spurious first edge.
  property p_sck_quiet_cs_fall;
    @(posedge clk_i) disable iff (!rst_ni)
      post_reset_q && $past(cs_idle) && !cs_idle |-> !sck_edge;
  endproperty
  a_sck_quiet_cs_fall: assert property (p_sck_quiet_cs_fall)
    else $error("spi: SCK changed on the CS-fall clock edge");

  // ---- p_cs_one_hot_active ----
  // At any cycle, no more than one CS line is low.
  function automatic int popcount_low(input logic [NUM_CS-1:0] v);
    int n; n = 0;
    foreach (v[i]) if (!v[i]) n++;
    return n;
  endfunction
  property p_cs_one_hot_active;
    @(posedge clk_i) disable iff (!rst_ni)
      (popcount_low(cs_n) <= 1);
  endproperty
  a_cs_one_hot_active: assert property (p_cs_one_hot_active)
    else $error("spi: more than one CS asserted");

  // ---- p_busy_during_xfer ----
  // While ANY CS is asserted (low) during a master-driven transfer,
  // m_busy must be high. (We allow a 1-cycle slack on entry/exit so
  // the busy_o registered output can update.)
  property p_busy_during_xfer;
    @(posedge clk_i) disable iff (!rst_ni)
      (!cs_idle) |=> m_busy;
  endproperty
  a_busy_during_xfer: assert property (p_busy_during_xfer)
    else $error("spi: master CS active but m_busy low");

  // ---- p_no_done_when_idle ----
  // m_done is a 1-cycle pulse following the end of a real transfer; it
  // must never assert during prolonged idle.
  property p_no_done_when_idle;
    @(posedge clk_i) disable iff (!rst_ni)
      (cs_idle && !$past(m_busy)) |-> !m_done;
  endproperty
  a_no_done_when_idle: assert property (p_no_done_when_idle)
    else $error("spi: m_done pulsed during idle");

  // ---- p_done_pulse_oneshot ----
  // m_done is at most 1 cycle wide.
  property p_done_pulse_oneshot;
    @(posedge clk_i) disable iff (!rst_ni)
      m_done |=> !m_done;
  endproperty
  a_done_pulse_oneshot: assert property (p_done_pulse_oneshot)
    else $error("spi: m_done was high for two consecutive cycles");

  // ---- p_no_data_when_idle ----
  // MOSI is held quiet (we don't enforce a level, just that it doesn't
  // toggle) while the bus is idle.
  logic mosi_q;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) mosi_q <= 1'b0;
    else         mosi_q <= mosi;
  end
  property p_no_data_when_idle;
    @(posedge clk_i) disable iff (!rst_ni)
      cs_idle |-> (mosi == mosi_q);
  endproperty
  a_no_data_when_idle: assert property (p_no_data_when_idle)
    else $error("spi: MOSI toggled while bus idle");

  // ---- liveness: a busy that never ends within LAT_MAX cycles is a bug ----
  localparam int unsigned LAT_MAX = 100000;
  int unsigned busy_cycles_q;
  always_ff @(posedge clk_i) begin
    if (!rst_ni)         busy_cycles_q <= 0;
    else if (m_busy)     busy_cycles_q <= busy_cycles_q + 1;
    else                 busy_cycles_q <= 0;
  end
  property p_no_busy_deadlock;
    @(posedge clk_i) disable iff (!rst_ni)
      (busy_cycles_q < LAT_MAX);
  endproperty
  a_no_busy_deadlock: assert property (p_no_busy_deadlock)
    else $error("spi: m_busy stuck high >%0d cycles", LAT_MAX);

  // ---- miso silence (sanity, not bound) ----
  logic _unused;
  assign _unused = &{1'b0, miso};

`endif
endmodule
