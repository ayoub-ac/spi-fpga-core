// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
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
  input  logic [NUM_CS-1:0]     cs_n
);

  logic c_mode0           /*verilator public*/;
  logic c_mode1           /*verilator public*/;
  logic c_mode2           /*verilator public*/;
  logic c_mode3           /*verilator public*/;
  logic c_msb_first       /*verilator public*/;
  logic c_lsb_first       /*verilator public*/;
  logic c_tx_full         /*verilator public*/;
  logic c_tx_empty        /*verilator public*/;
  logic c_rx_full         /*verilator public*/;
  logic c_rx_empty        /*verilator public*/;
  logic c_slave_responded /*verilator public*/;
  logic c_cs1_used        /*verilator public*/;

  // Coverage bins are sticky across reset by design: the harness
  // resets the DUT between tests, but the coverage report aggregates
  // every test in the regression.
  always_ff @(posedge clk_i) begin
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

  logic _unused;
  assign _unused = &{1'b0, m_busy, s_busy};

endmodule
