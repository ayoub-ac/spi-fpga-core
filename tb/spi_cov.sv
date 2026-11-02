// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// Functional coverage for spi_top. 12 sticky bins reported at end-of-test.
//
// Bins:
//   c_mode0           CPOL=0 CPHA=0 transfer observed
//   c_mode1           CPOL=0 CPHA=1 transfer observed
//   c_mode2           CPOL=1 CPHA=0 transfer observed
//   c_mode3           CPOL=1 CPHA=1 transfer observed
//   c_msb_first       LSB-first=0 transfer observed
//   c_lsb_first       LSB-first=1 transfer observed
//   c_tx_full         master TX FIFO observed full at some point
//   c_tx_empty        master TX FIFO observed empty
//   c_rx_full         master RX FIFO observed full at some point
//   c_rx_empty        master RX FIFO observed empty (idle case)
//   c_slave_responded slave xfer_done pulsed (slave drove MISO data)
//   c_cs1_used        CS line 1 (non-default) was asserted

module spi_cov #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned NUM_CS     = 4
) (
  input  logic                  clk_i,
  input  logic                  rst_ni,
  input  logic                  m_cpol,
  input  logic                  m_cpha,
  input  logic                  m_lsb_first,
  input  logic                  m_busy,
  input  logic                  m_done,
  input  logic                  s_busy,
  input  logic                  s_done,
  input  logic                  m_tx_full,
  input  logic                  m_tx_empty,
  input  logic                  m_rx_full,
  input  logic                  m_rx_empty,
  input  logic [NUM_CS-1:0]     cs_n,

  // Bins packed for the C++ harness, bit order as listed in the header.
  output logic [11:0]           bins_o
);

  logic c_mode0;
  logic c_mode1;
  logic c_mode2;
  logic c_mode3;
  logic c_msb_first;
  logic c_lsb_first;
  logic c_tx_full;
  logic c_tx_empty;
  logic c_rx_full;
  logic c_rx_empty;
  logic c_slave_responded;
  logic c_cs1_used;

  // Coverage bins are sticky across reset by design: the harness
  // resets the DUT between tests, but the coverage report aggregates
  // every test in the regression. The bins start at 0 from the initial
  // block below, so they are written by two processes; a plain `always`
  // (not `always_ff`, which requires a single driver, IEEE 1800 9.2.2.4)
  // is the matching construct for this simulation-only state.
  always @(posedge clk_i) begin
    begin
      // Mode bins: tagged on a completed transfer.
      if (m_done) begin
        case ({m_cpol, m_cpha})
          2'b00: c_mode0 <= 1'b1;
          2'b01: c_mode1 <= 1'b1;
          2'b10: c_mode2 <= 1'b1;
          2'b11: c_mode3 <= 1'b1;
          default: ;
        endcase
        if (m_lsb_first) c_lsb_first <= 1'b1;
        else             c_msb_first <= 1'b1;
      end

      if (m_tx_full)  c_tx_full  <= 1'b1;
      if (m_tx_empty) c_tx_empty <= 1'b1;
      if (m_rx_full)  c_rx_full  <= 1'b1;
      if (m_rx_empty) c_rx_empty <= 1'b1;

      if (s_done)     c_slave_responded <= 1'b1;
      // CS1 (any non-zero CS) used.
      if (NUM_CS > 1 && !cs_n[1]) c_cs1_used <= 1'b1;
    end
  end

  initial begin
    c_mode0 = 1'b0; c_mode1 = 1'b0; c_mode2 = 1'b0; c_mode3 = 1'b0;
    c_msb_first = 1'b0; c_lsb_first = 1'b0;
    c_tx_full = 1'b0; c_tx_empty = 1'b0;
    c_rx_full = 1'b0; c_rx_empty = 1'b0;
    c_slave_responded = 1'b0; c_cs1_used = 1'b0;
  end

  assign bins_o = {c_cs1_used, c_slave_responded, c_rx_empty, c_rx_full,
                   c_tx_empty, c_tx_full, c_lsb_first, c_msb_first,
                   c_mode3, c_mode2, c_mode1, c_mode0};

  logic _unused;
  assign _unused = &{1'b0, m_busy, s_busy};

endmodule
