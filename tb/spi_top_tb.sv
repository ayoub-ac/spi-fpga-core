// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Top-level testbench wrapper for spi_top. Stimulus from tb/sim_main.cpp
// drives the master and slave configuration / FIFO ports directly.

module spi_top_tb #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned NUM_CS     = 4,
  parameter int unsigned FIFO_DEPTH = 16,
  parameter int unsigned DIV_WIDTH  = 16,
  parameter int unsigned CS_DLY_W   = 8
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  input  logic                          m_cpol_i,
  input  logic                          m_cpha_i,
  input  logic                          m_lsb_first_i,
  input  logic [DIV_WIDTH-1:0]          m_clk_div_i,
  input  logic [((NUM_CS > 1) ? $clog2(NUM_CS) : 1)-1:0] m_cs_sel_i,
  input  logic [CS_DLY_W-1:0]           m_cs_setup_i,
  input  logic [CS_DLY_W-1:0]           m_cs_hold_i,
  input  logic                          m_cs_inter_byte_i,
  input  logic                          m_start_i,

  input  logic                          m_tx_we_i,
  input  logic [DATA_WIDTH-1:0]         m_tx_wdata_i,
  output logic                          m_tx_full_o,
  output logic                          m_tx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] m_tx_count_o,
  input  logic                          m_rx_re_i,
  output logic [DATA_WIDTH-1:0]         m_rx_rdata_o,
  output logic                          m_rx_full_o,
  output logic                          m_rx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] m_rx_count_o,
  output logic                          m_busy_o,
  output logic                          m_xfer_done_o,

  input  logic                          s_cpol_i,
  input  logic                          s_cpha_i,
  input  logic                          s_lsb_first_i,
  input  logic                          s_tx_we_i,
  input  logic [DATA_WIDTH-1:0]         s_tx_wdata_i,
  output logic                          s_tx_full_o,
  output logic                          s_tx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] s_tx_count_o,
  input  logic                          s_rx_re_i,
  output logic [DATA_WIDTH-1:0]         s_rx_rdata_o,
  output logic                          s_rx_full_o,
  output logic                          s_rx_empty_o,
  output logic [$clog2(FIFO_DEPTH+1)-1:0] s_rx_count_o,
  output logic                          s_busy_o,
  output logic                          s_xfer_done_o,

  output logic                          sck_o,
  output logic                          mosi_o,
  output logic                          miso_o,
  output logic [NUM_CS-1:0]             cs_n_o,

  output logic                          irq_tx_empty_o,
  output logic                          irq_rx_full_o,
  output logic                          irq_done_o,
  output logic                          irq_err_o,

  // Functional coverage bins (see tb/spi_cov.sv).
  output logic [11:0]                   cov_bins_o
);

  spi_top #(
    .DATA_WIDTH(DATA_WIDTH),
    .NUM_CS    (NUM_CS),
    .FIFO_DEPTH(FIFO_DEPTH),
    .DIV_WIDTH (DIV_WIDTH),
    .CS_DLY_W  (CS_DLY_W)
  ) u_dut (
    .clk_i             (clk_i),
    .rst_ni            (rst_ni),
    .m_cpol_i          (m_cpol_i),
    .m_cpha_i          (m_cpha_i),
    .m_lsb_first_i     (m_lsb_first_i),
    .m_clk_div_i       (m_clk_div_i),
    .m_cs_sel_i        (m_cs_sel_i),
    .m_cs_setup_i      (m_cs_setup_i),
    .m_cs_hold_i       (m_cs_hold_i),
    .m_cs_inter_byte_i (m_cs_inter_byte_i),
    .m_start_i         (m_start_i),
    .m_tx_we_i         (m_tx_we_i),
    .m_tx_wdata_i      (m_tx_wdata_i),
    .m_tx_full_o       (m_tx_full_o),
    .m_tx_empty_o      (m_tx_empty_o),
    .m_tx_count_o      (m_tx_count_o),
    .m_rx_re_i         (m_rx_re_i),
    .m_rx_rdata_o      (m_rx_rdata_o),
    .m_rx_full_o       (m_rx_full_o),
    .m_rx_empty_o      (m_rx_empty_o),
    .m_rx_count_o      (m_rx_count_o),
    .m_busy_o          (m_busy_o),
    .m_xfer_done_o     (m_xfer_done_o),
    .s_cpol_i          (s_cpol_i),
    .s_cpha_i          (s_cpha_i),
    .s_lsb_first_i     (s_lsb_first_i),
    .s_tx_we_i         (s_tx_we_i),
    .s_tx_wdata_i      (s_tx_wdata_i),
    .s_tx_full_o       (s_tx_full_o),
    .s_tx_empty_o      (s_tx_empty_o),
    .s_tx_count_o      (s_tx_count_o),
    .s_rx_re_i         (s_rx_re_i),
    .s_rx_rdata_o      (s_rx_rdata_o),
    .s_rx_full_o       (s_rx_full_o),
    .s_rx_empty_o      (s_rx_empty_o),
    .s_rx_count_o      (s_rx_count_o),
    .s_busy_o          (s_busy_o),
    .s_xfer_done_o     (s_xfer_done_o),
    .sck_o             (sck_o),
    .mosi_o            (mosi_o),
    .miso_o            (miso_o),
    .cs_n_o            (cs_n_o),
    .irq_tx_empty_o    (irq_tx_empty_o),
    .irq_rx_full_o     (irq_rx_full_o),
    .irq_done_o        (irq_done_o),
    .irq_err_o         (irq_err_o)
  );

  // ------------------------------------------------------------------
  // Bind SVA + coverage
  // ------------------------------------------------------------------
  spi_assertions #(
    .NUM_CS(NUM_CS)
  ) u_assert (
    .clk_i  (clk_i),
    .rst_ni (rst_ni),
    .sck    (sck_o),
    .mosi   (mosi_o),
    .miso   (miso_o),
    .cs_n   (cs_n_o),
    .m_busy (m_busy_o),
    .m_done (m_xfer_done_o),
    .cpol   (m_cpol_i)
  );

  spi_cov #(
    .DATA_WIDTH(DATA_WIDTH),
    .NUM_CS    (NUM_CS)
  ) u_cov (
    .clk_i        (clk_i),
    .rst_ni       (rst_ni),
    .m_cpol       (m_cpol_i),
    .m_cpha       (m_cpha_i),
    .m_lsb_first  (m_lsb_first_i),
    .m_busy       (m_busy_o),
    .m_done       (m_xfer_done_o),
    .s_busy       (s_busy_o),
    .s_done       (s_xfer_done_o),
    .m_tx_full    (m_tx_full_o),
    .m_tx_empty   (m_tx_empty_o),
    .m_rx_full    (m_rx_full_o),
    .m_rx_empty   (m_rx_empty_o),
    .cs_n         (cs_n_o),
    .bins_o       (cov_bins_o)
  );

endmodule
