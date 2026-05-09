-- SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
--
-- VHDL-2008 wrapper for the SystemVerilog spi_top module. Use this when
-- your top-level project is VHDL but the SPI core is delivered in SV.

library ieee;
  use ieee.std_logic_1164.all;
  use ieee.numeric_std.all;

entity spi_top_vhdl is
  generic (
    DATA_WIDTH : integer := 8;
    NUM_CS     : integer := 4;
    FIFO_DEPTH : integer := 16;
    DIV_WIDTH  : integer := 16;
    CS_DLY_W   : integer := 8
  );
  port (
    clk_i             : in  std_logic;
    rst_ni            : in  std_logic;

    m_cpol_i          : in  std_logic;
    m_cpha_i          : in  std_logic;
    m_lsb_first_i     : in  std_logic;
    m_clk_div_i       : in  std_logic_vector(DIV_WIDTH-1 downto 0);
    m_cs_sel_i        : in  std_logic_vector(1 downto 0);
    m_cs_setup_i      : in  std_logic_vector(CS_DLY_W-1 downto 0);
    m_cs_hold_i       : in  std_logic_vector(CS_DLY_W-1 downto 0);
    m_cs_inter_byte_i : in  std_logic;
    m_start_i         : in  std_logic;

    m_tx_we_i         : in  std_logic;
    m_tx_wdata_i      : in  std_logic_vector(DATA_WIDTH-1 downto 0);
    m_tx_full_o       : out std_logic;
    m_tx_empty_o      : out std_logic;
    m_rx_re_i         : in  std_logic;
    m_rx_rdata_o      : out std_logic_vector(DATA_WIDTH-1 downto 0);
    m_rx_full_o       : out std_logic;
    m_rx_empty_o      : out std_logic;
    m_busy_o          : out std_logic;
    m_xfer_done_o     : out std_logic;

    s_cpol_i          : in  std_logic;
    s_cpha_i          : in  std_logic;
    s_lsb_first_i     : in  std_logic;
    s_tx_we_i         : in  std_logic;
    s_tx_wdata_i      : in  std_logic_vector(DATA_WIDTH-1 downto 0);
    s_tx_full_o       : out std_logic;
    s_tx_empty_o      : out std_logic;
    s_rx_re_i         : in  std_logic;
    s_rx_rdata_o      : out std_logic_vector(DATA_WIDTH-1 downto 0);
    s_rx_full_o       : out std_logic;
    s_rx_empty_o      : out std_logic;
    s_busy_o          : out std_logic;
    s_xfer_done_o     : out std_logic;

    sck_o             : out std_logic;
    mosi_o            : out std_logic;
    miso_o            : out std_logic;
    cs_n_o            : out std_logic_vector(NUM_CS-1 downto 0);

    irq_tx_empty_o    : out std_logic;
    irq_rx_full_o     : out std_logic;
    irq_done_o        : out std_logic;
    irq_err_o         : out std_logic
  );
end entity spi_top_vhdl;

architecture rtl of spi_top_vhdl is

  component spi_top
    generic (
      DATA_WIDTH : integer := 8;
      NUM_CS     : integer := 4;
      FIFO_DEPTH : integer := 16;
      DIV_WIDTH  : integer := 16;
      CS_DLY_W   : integer := 8
    );
    port (
      clk_i             : in  std_logic;
      rst_ni            : in  std_logic;
      m_cpol_i          : in  std_logic;
      m_cpha_i          : in  std_logic;
      m_lsb_first_i     : in  std_logic;
      m_clk_div_i       : in  std_logic_vector(DIV_WIDTH-1 downto 0);
      m_cs_sel_i        : in  std_logic_vector(1 downto 0);
      m_cs_setup_i      : in  std_logic_vector(CS_DLY_W-1 downto 0);
      m_cs_hold_i       : in  std_logic_vector(CS_DLY_W-1 downto 0);
      m_cs_inter_byte_i : in  std_logic;
      m_start_i         : in  std_logic;
      m_tx_we_i         : in  std_logic;
      m_tx_wdata_i      : in  std_logic_vector(DATA_WIDTH-1 downto 0);
      m_tx_full_o       : out std_logic;
      m_tx_empty_o      : out std_logic;
      m_rx_re_i         : in  std_logic;
      m_rx_rdata_o      : out std_logic_vector(DATA_WIDTH-1 downto 0);
      m_rx_full_o       : out std_logic;
      m_rx_empty_o      : out std_logic;
      m_busy_o          : out std_logic;
      m_xfer_done_o     : out std_logic;
      s_cpol_i          : in  std_logic;
      s_cpha_i          : in  std_logic;
      s_lsb_first_i     : in  std_logic;
      s_tx_we_i         : in  std_logic;
      s_tx_wdata_i      : in  std_logic_vector(DATA_WIDTH-1 downto 0);
      s_tx_full_o       : out std_logic;
      s_tx_empty_o      : out std_logic;
      s_rx_re_i         : in  std_logic;
      s_rx_rdata_o      : out std_logic_vector(DATA_WIDTH-1 downto 0);
      s_rx_full_o       : out std_logic;
      s_rx_empty_o      : out std_logic;
      s_busy_o          : out std_logic;
      s_xfer_done_o     : out std_logic;
      sck_o             : out std_logic;
      mosi_o            : out std_logic;
      miso_o            : out std_logic;
      cs_n_o            : out std_logic_vector(NUM_CS-1 downto 0);
      irq_tx_empty_o    : out std_logic;
      irq_rx_full_o     : out std_logic;
      irq_done_o        : out std_logic;
      irq_err_o         : out std_logic
    );
  end component;

begin

  u_top : spi_top
    generic map (
      DATA_WIDTH => DATA_WIDTH,
      NUM_CS     => NUM_CS,
      FIFO_DEPTH => FIFO_DEPTH,
      DIV_WIDTH  => DIV_WIDTH,
      CS_DLY_W   => CS_DLY_W
    )
    port map (
      clk_i             => clk_i,
      rst_ni            => rst_ni,
      m_cpol_i          => m_cpol_i,
      m_cpha_i          => m_cpha_i,
      m_lsb_first_i     => m_lsb_first_i,
      m_clk_div_i       => m_clk_div_i,
      m_cs_sel_i        => m_cs_sel_i,
      m_cs_setup_i      => m_cs_setup_i,
      m_cs_hold_i       => m_cs_hold_i,
      m_cs_inter_byte_i => m_cs_inter_byte_i,
      m_start_i         => m_start_i,
      m_tx_we_i         => m_tx_we_i,
      m_tx_wdata_i      => m_tx_wdata_i,
      m_tx_full_o       => m_tx_full_o,
      m_tx_empty_o      => m_tx_empty_o,
      m_rx_re_i         => m_rx_re_i,
      m_rx_rdata_o      => m_rx_rdata_o,
      m_rx_full_o       => m_rx_full_o,
      m_rx_empty_o      => m_rx_empty_o,
      m_busy_o          => m_busy_o,
      m_xfer_done_o     => m_xfer_done_o,
      s_cpol_i          => s_cpol_i,
      s_cpha_i          => s_cpha_i,
      s_lsb_first_i     => s_lsb_first_i,
      s_tx_we_i         => s_tx_we_i,
      s_tx_wdata_i      => s_tx_wdata_i,
      s_tx_full_o       => s_tx_full_o,
      s_tx_empty_o      => s_tx_empty_o,
      s_rx_re_i         => s_rx_re_i,
      s_rx_rdata_o      => s_rx_rdata_o,
      s_rx_full_o       => s_rx_full_o,
      s_rx_empty_o      => s_rx_empty_o,
      s_busy_o          => s_busy_o,
      s_xfer_done_o     => s_xfer_done_o,
      sck_o             => sck_o,
      mosi_o            => mosi_o,
      miso_o            => miso_o,
      cs_n_o            => cs_n_o,
      irq_tx_empty_o    => irq_tx_empty_o,
      irq_rx_full_o     => irq_rx_full_o,
      irq_done_o        => irq_done_o,
      irq_err_o         => irq_err_o
    );

end architecture rtl;
