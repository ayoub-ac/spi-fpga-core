// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Top-level integrating spi_master and spi_slave with the SPI bus wired
// in loopback (master.MOSI -> slave.MOSI, slave.MISO -> master.MISO,
// master.SCK -> slave.SCK, master.CS_n[0] -> slave.CS_n).
//
// This is the verification top: it lets the testbench push bytes to the
// master TX FIFO and the slave TX FIFO, run a transfer, and read back
// what each side received. The same master and slave modules are also
// usable independently — `spi_master` exposes its bus pins directly,
// likewise `spi_slave` for an external bus.
//
// Interrupts:
//   irq_tx_empty    TX FIFO empty (controller can refill)
//   irq_rx_full     RX FIFO full  (controller must drain or risk overrun)
//   irq_done        one-cycle pulse on each completed transfer
//   irq_err         FIFO push to a full RX FIFO (overrun)

module spi_top #(
  parameter int unsigned DATA_WIDTH = 8,
  parameter int unsigned NUM_CS     = 4,
  parameter int unsigned FIFO_DEPTH = 16,
  parameter int unsigned DIV_WIDTH  = 16,
  parameter int unsigned CS_DLY_W   = 8
) (
  input  logic                          clk_i,
  input  logic                          rst_ni,

  // Master configuration / control
  input  logic                          m_cpol_i,
  input  logic                          m_cpha_i,
  input  logic                          m_lsb_first_i,
  input  logic [DIV_WIDTH-1:0]          m_clk_div_i,
  input  logic [$clog2(NUM_CS)-1:0]     m_cs_sel_i,
  input  logic [CS_DLY_W-1:0]           m_cs_setup_i,
  input  logic [CS_DLY_W-1:0]           m_cs_hold_i,
  input  logic                          m_cs_inter_byte_i,
  input  logic                          m_start_i,

  // Master TX/RX
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

  // Slave configuration
  input  logic                          s_cpol_i,
  input  logic                          s_cpha_i,
  input  logic                          s_lsb_first_i,

  // Slave TX/RX
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

  // SPI bus (exposed for external test observation; loopback is wired
  // internally between master and slave).
  output logic                          sck_o,
  output logic                          mosi_o,
  output logic                          miso_o,
  output logic [NUM_CS-1:0]             cs_n_o,

  // Interrupts (master side).
  output logic                          irq_tx_empty_o,
  output logic                          irq_rx_full_o,
  output logic                          irq_done_o,
  output logic                          irq_err_o
);

  logic              sck_w, mosi_w, miso_w;
  logic [NUM_CS-1:0] cs_n_w;

  spi_master #(
    .DATA_WIDTH(DATA_WIDTH),
    .NUM_CS    (NUM_CS),
    .FIFO_DEPTH(FIFO_DEPTH),
    .DIV_WIDTH (DIV_WIDTH),
    .CS_DLY_W  (CS_DLY_W)
  ) u_master (
    .clk_i             (clk_i),
    .rst_ni            (rst_ni),
    .cpol_i            (m_cpol_i),
    .cpha_i            (m_cpha_i),
    .lsb_first_i       (m_lsb_first_i),
    .clk_div_i         (m_clk_div_i),
    .cs_sel_i          (m_cs_sel_i),
    .cs_setup_cycles_i (m_cs_setup_i),
    .cs_hold_cycles_i  (m_cs_hold_i),
    .cs_inter_byte_i   (m_cs_inter_byte_i),
    .tx_we_i           (m_tx_we_i),
    .tx_wdata_i        (m_tx_wdata_i),
    .tx_full_o         (m_tx_full_o),
    .tx_empty_o        (m_tx_empty_o),
    .tx_count_o        (m_tx_count_o),
    .rx_re_i           (m_rx_re_i),
    .rx_rdata_o        (m_rx_rdata_o),
    .rx_full_o         (m_rx_full_o),
    .rx_empty_o        (m_rx_empty_o),
    .rx_count_o        (m_rx_count_o),
    .start_i           (m_start_i),
    .busy_o            (m_busy_o),
    .xfer_done_o       (m_xfer_done_o),
    .sck_o             (sck_w),
    .mosi_o            (mosi_w),
    .miso_i            (miso_w),
    .cs_n_o            (cs_n_w)
  );

  spi_slave #(
    .DATA_WIDTH(DATA_WIDTH),
    .FIFO_DEPTH(FIFO_DEPTH)
  ) u_slave (
    .clk_i        (clk_i),
    .rst_ni       (rst_ni),
    .cpol_i       (s_cpol_i),
    .cpha_i       (s_cpha_i),
    .lsb_first_i  (s_lsb_first_i),
    .tx_we_i      (s_tx_we_i),
    .tx_wdata_i   (s_tx_wdata_i),
    .tx_full_o    (s_tx_full_o),
    .tx_empty_o   (s_tx_empty_o),
    .tx_count_o   (s_tx_count_o),
    .rx_re_i      (s_rx_re_i),
    .rx_rdata_o   (s_rx_rdata_o),
    .rx_full_o    (s_rx_full_o),
    .rx_empty_o   (s_rx_empty_o),
    .rx_count_o   (s_rx_count_o),
    .busy_o       (s_busy_o),
    .xfer_done_o  (s_xfer_done_o),
    .sck_i        (sck_w),
    .mosi_i       (mosi_w),
    .miso_o       (miso_w),
    .cs_n_i       (cs_n_w[0])
  );

  assign sck_o  = sck_w;
  assign mosi_o = mosi_w;
  assign miso_o = miso_w;
  assign cs_n_o = cs_n_w;

  // Interrupts (master perspective; slave wires are available through
  // the per-side outputs).
  assign irq_tx_empty_o = m_tx_empty_o;
  assign irq_rx_full_o  = m_rx_full_o;
  assign irq_done_o     = m_xfer_done_o;
  assign irq_err_o      = m_rx_full_o && m_xfer_done_o;

endmodule
