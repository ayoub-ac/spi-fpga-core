// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// AXI4-Lite wrapper around spi_master. Provides a software-visible
// register-mapped control / status interface so a CPU can drive the SPI
// master without bit-banging.
//
// Register map (16 x 32-bit, byte address space 0x00..0x3C; with
// DATA_WIDTH = 64 the registers are 64-bit and register n is at 8*n):
//
//   0x00  CTRL       RW    bit0=start, bit1=lsb_first, bit2=cpol, bit3=cpha,
//                          bit4=cs_inter_byte, bits[7:5]=reserved,
//                          bits[15:8]=cs_sel, bits[23:16]=cs_setup,
//                          bits[31:24]=cs_hold
//                          start: every write with WDATA[0]=1 (byte lane 0
//                          strobed) launches one transfer; the bit is not
//                          stored and reads as 0, so a read-modify-write of
//                          CTRL does not start another transfer.
//   0x04  STATUS     RO    bit0=busy, bit1=tx_empty, bit2=tx_full,
//                          bit3=rx_empty, bit4=rx_full
//   0x08  IRQ_STAT   W1C   bit0=tx_empty, bit1=rx_full, bit2=done, bit3=err
//                          sticky, readable, write 1 clears (lane 0 strobed);
//                          tx_empty / rx_full set again while the condition
//                          lasts; err = a received word was dropped
//   0x0C  IRQ_MASK   RW    per-IRQ enable
//   0x10  CLK_DIV    RW    SCK divider: SCK = clk_i / (2*(CLK_DIV+1))
//   0x14  TX_DATA    RW    a write that strobes a byte lane of the SPI word
//                          pushes the written (strobe-merged) value
//                          [SPI_DATA_W-1:0] into the TX FIFO
//   0x18  RX_DATA    RO    read returns the RX FIFO head and pops it
//                          (reads 0 and pops nothing when RX is empty)
//   0x1C  FIFO_LVL   RO    bits[15:0]=tx_count, bits[31:16]=rx_count
//   0x20  ID         RO    32'h5910_017E
//   0x24  VERSION    RO    32'h0001_0100 = v1.1.0, encoded as
//                          {major[15:0], minor[7:0], patch[7:0]}
//   0x28..0x3C       RW    user scratch
//
// REG_ATTR encoding (2 bits per register, register 0 in LSBs):
//   00 RW  / 01 RO / 10 RC / 11 W1C

module spi_axi4lite #(
  parameter int unsigned ADDR_WIDTH = 8,
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned NUM_REGS   = 16,
  parameter int unsigned SPI_DATA_W = 8,
  parameter int unsigned NUM_CS     = 4,
  parameter int unsigned FIFO_DEPTH = 16,
  parameter int unsigned DIV_WIDTH  = 16,
  parameter int unsigned CS_DLY_W   = 8
) (
  input  logic                    s_axi_aclk,
  input  logic                    s_axi_aresetn,

  input  logic [ADDR_WIDTH-1:0]   s_axi_awaddr,
  input  logic [2:0]              s_axi_awprot,
  input  logic                    s_axi_awvalid,
  output logic                    s_axi_awready,

  input  logic [DATA_WIDTH-1:0]   s_axi_wdata,
  input  logic [DATA_WIDTH/8-1:0] s_axi_wstrb,
  input  logic                    s_axi_wvalid,
  output logic                    s_axi_wready,

  output logic [1:0]              s_axi_bresp,
  output logic                    s_axi_bvalid,
  input  logic                    s_axi_bready,

  input  logic [ADDR_WIDTH-1:0]   s_axi_araddr,
  input  logic [2:0]              s_axi_arprot,
  input  logic                    s_axi_arvalid,
  output logic                    s_axi_arready,

  output logic [DATA_WIDTH-1:0]   s_axi_rdata,
  output logic [1:0]              s_axi_rresp,
  output logic                    s_axi_rvalid,
  input  logic                    s_axi_rready,

  // SPI bus
  output logic                    sck_o,
  output logic                    mosi_o,
  input  logic                    miso_i,
  output logic [NUM_CS-1:0]       cs_n_o,

  output logic                    irq_o
);

  // ----------------------------------------------------------------
  // Parameter checks: reject what the fixed register map cannot hold
  // (stops elaboration in Yosys, Verilator and Icarus).
  // ----------------------------------------------------------------
  if (DATA_WIDTH != 32 && DATA_WIDTH != 64) begin : g_bad_data_width
    $error("spi_axi4lite: DATA_WIDTH must be 32 or 64");
    spi_axi4lite_illegal_parameter_DATA_WIDTH_must_be_32_or_64 u_param_error ();
  end
  if (NUM_REGS != 16) begin : g_bad_num_regs
    $error("spi_axi4lite: NUM_REGS must be 16 (fixed register map)");
    spi_axi4lite_illegal_parameter_NUM_REGS_must_be_16 u_param_error ();
  end
  if (ADDR_WIDTH < $clog2(DATA_WIDTH / 8) + 4) begin : g_bad_addr_width
    $error("spi_axi4lite: ADDR_WIDTH too small to address 16 registers");
    spi_axi4lite_illegal_parameter_ADDR_WIDTH_too_small u_param_error ();
  end
  if (SPI_DATA_W > DATA_WIDTH) begin : g_bad_spi_data_w
    $error("spi_axi4lite: SPI_DATA_W must be <= DATA_WIDTH (TX_DATA / RX_DATA)");
    spi_axi4lite_illegal_parameter_SPI_DATA_W_above_DATA_WIDTH u_param_error ();
  end
  if (DIV_WIDTH > DATA_WIDTH) begin : g_bad_div_width
    $error("spi_axi4lite: DIV_WIDTH must be <= DATA_WIDTH (CLK_DIV)");
    spi_axi4lite_illegal_parameter_DIV_WIDTH_above_DATA_WIDTH u_param_error ();
  end
  if (CS_DLY_W > 8) begin : g_bad_cs_dly_w
    $error("spi_axi4lite: CS_DLY_W must be <= 8 (CTRL cs_setup / cs_hold are 8-bit fields)");
    spi_axi4lite_illegal_parameter_CS_DLY_W_above_8 u_param_error ();
  end
  if (NUM_CS > 256) begin : g_bad_num_cs
    $error("spi_axi4lite: NUM_CS must be <= 256 (CTRL cs_sel is an 8-bit field)");
    spi_axi4lite_illegal_parameter_NUM_CS_above_256 u_param_error ();
  end
  if (FIFO_DEPTH > 65535) begin : g_bad_fifo_depth
    $error("spi_axi4lite: FIFO_DEPTH must be <= 65535 (FIFO_LVL fields are 16 bits)");
    spi_axi4lite_illegal_parameter_FIFO_DEPTH_above_65535 u_param_error ();
  end

  // Reg attributes (16 registers, LSB pair = reg 0):
  //   reg0=RO(CTRL, stored below), 1=RO(STATUS), 2=RO(IRQ_STAT, sticky
  //   bits and W1C below), 3=RW(IRQ_MASK), 4=RW(CLK_DIV), 5=RW(TX_DATA),
  //   6=RO(RX_DATA), 7=RO(FIFO_LVL), 8=RO(ID), 9=RO(VERSION), 10..15=RW
  localparam logic [2*NUM_REGS-1:0] EX_REG_ATTR = {
      2'b00, 2'b00, 2'b00, 2'b00, 2'b00, 2'b00,    // 15..10 RW
      2'b01, 2'b01,                                // 9..8   RO
      2'b01, 2'b01,                                // 7..6   RO
      2'b00, 2'b00,                                // 5..4   RW
      2'b00, 2'b01,                                // 3 RW, 2 RO (IRQ_STAT)
      2'b01, 2'b01                                 // 1 RO, 0 RO (CTRL)
  };

  localparam logic [DATA_WIDTH-1:0] ID_VAL      = 32'h5910_017E;
  localparam logic [DATA_WIDTH-1:0] VERSION_VAL = 32'h0001_0100;   // v1.1.0

  localparam logic [NUM_REGS*DATA_WIDTH-1:0] EX_REG_RESET =
        ({{(NUM_REGS*DATA_WIDTH-DATA_WIDTH){1'b0}}, ID_VAL}      << (8*DATA_WIDTH))
      | ({{(NUM_REGS*DATA_WIDTH-DATA_WIDTH){1'b0}}, VERSION_VAL} << (9*DATA_WIDTH));

  // ----------------------------------------------------------------
  // SPI master instance
  // ----------------------------------------------------------------
  logic                              spi_start;
  logic                              spi_busy;
  logic                              spi_done;
  logic                              spi_rx_overrun;
  logic                              tx_we;
  logic [SPI_DATA_W-1:0]             tx_wdata;
  logic                              tx_full;
  logic                              tx_empty;
  logic [$clog2(FIFO_DEPTH+1)-1:0]   tx_count;
  logic                              rx_re;
  logic [SPI_DATA_W-1:0]             rx_rdata;
  logic                              rx_full;
  logic                              rx_empty;
  logic [$clog2(FIFO_DEPTH+1)-1:0]   rx_count;

  logic                              cfg_cpol;
  logic                              cfg_cpha;
  logic                              cfg_lsb_first;
  logic                              cfg_inter_byte;
  logic [((NUM_CS > 1) ? $clog2(NUM_CS) : 1)-1:0] cfg_cs_sel;
  logic [CS_DLY_W-1:0]               cfg_cs_setup;
  logic [CS_DLY_W-1:0]               cfg_cs_hold;
  logic [DIV_WIDTH-1:0]              cfg_clk_div;

  spi_master #(
    .DATA_WIDTH(SPI_DATA_W),
    .NUM_CS    (NUM_CS),
    .FIFO_DEPTH(FIFO_DEPTH),
    .DIV_WIDTH (DIV_WIDTH),
    .CS_DLY_W  (CS_DLY_W),
    .RX_FWFT   (1'b1)       // RX_DATA reads the head and pops it in one cycle
  ) u_master (
    .clk_i             (s_axi_aclk),
    .rst_ni            (s_axi_aresetn),
    .cpol_i            (cfg_cpol),
    .cpha_i            (cfg_cpha),
    .lsb_first_i       (cfg_lsb_first),
    .clk_div_i         (cfg_clk_div),
    .cs_sel_i          (cfg_cs_sel),
    .cs_setup_cycles_i (cfg_cs_setup),
    .cs_hold_cycles_i  (cfg_cs_hold),
    .cs_inter_byte_i   (cfg_inter_byte),
    .tx_we_i           (tx_we),
    .tx_wdata_i        (tx_wdata),
    .tx_full_o         (tx_full),
    .tx_empty_o        (tx_empty),
    .tx_count_o        (tx_count),
    .rx_re_i           (rx_re),
    .rx_rdata_o        (rx_rdata),
    .rx_full_o         (rx_full),
    .rx_empty_o        (rx_empty),
    .rx_count_o        (rx_count),
    .start_i           (spi_start),
    .busy_o            (spi_busy),
    .xfer_done_o       (spi_done),
    .sck_o             (sck_o),
    .mosi_o            (mosi_o),
    .miso_i            (miso_i),
    .cs_n_o            (cs_n_o),
    .rx_overrun_o      (spi_rx_overrun)
  );

  // ----------------------------------------------------------------
  // Live RO read payload
  // ----------------------------------------------------------------
  logic [NUM_REGS*DATA_WIDTH-1:0] rd_payload;
  logic [DATA_WIDTH-1:0]          status_word;
  logic [DATA_WIDTH-1:0]          rx_data_word;
  logic [DATA_WIDTH-1:0]          fifo_lvl_word;
  logic [DATA_WIDTH-1:0]          ctrl_q;
  logic [3:0]                     irq_stat_q;
  assign status_word = {{(DATA_WIDTH-5){1'b0}},
                        rx_full, rx_empty, tx_full, tx_empty, spi_busy};
  assign rx_data_word = rx_empty ? '0 : {{(DATA_WIDTH-SPI_DATA_W){1'b0}}, rx_rdata};
  assign fifo_lvl_word = {{(16-$clog2(FIFO_DEPTH+1)){1'b0}}, rx_count,
                          {(16-$clog2(FIFO_DEPTH+1)){1'b0}}, tx_count};

  always_comb begin
    rd_payload = '0;
    rd_payload[0*DATA_WIDTH +: DATA_WIDTH] = ctrl_q;          // CTRL
    rd_payload[1*DATA_WIDTH +: DATA_WIDTH] = status_word;     // STATUS
    rd_payload[2*DATA_WIDTH +: DATA_WIDTH] = DATA_WIDTH'(irq_stat_q); // IRQ_STAT
    rd_payload[6*DATA_WIDTH +: DATA_WIDTH] = rx_data_word;    // RX_DATA
    rd_payload[7*DATA_WIDTH +: DATA_WIDTH] = fifo_lvl_word;   // FIFO_LVL
    rd_payload[8*DATA_WIDTH +: DATA_WIDTH] = ID_VAL;          // ID
    rd_payload[9*DATA_WIDTH +: DATA_WIDTH] = VERSION_VAL;     // VERSION
  end

  // ----------------------------------------------------------------
  // AXI slave
  // ----------------------------------------------------------------
  logic [NUM_REGS*DATA_WIDTH-1:0] reg_storage;
  logic                           wr_event;
  logic [ADDR_WIDTH-1:0]          wr_event_addr;
  logic [DATA_WIDTH-1:0]          wr_event_data;
  logic [DATA_WIDTH/8-1:0]        wr_event_strb;
  logic                           rd_event;
  logic [ADDR_WIDTH-1:0]          rd_event_addr;

  axi4lite_slave #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH),
    .NUM_REGS  (NUM_REGS),
    .REG_ATTR  (EX_REG_ATTR),
    .REG_RESET (EX_REG_RESET)
  ) u_slave (
    .s_axi_aclk     (s_axi_aclk),
    .s_axi_aresetn  (s_axi_aresetn),
    .s_axi_awaddr   (s_axi_awaddr),
    .s_axi_awprot   (s_axi_awprot),
    .s_axi_awvalid  (s_axi_awvalid),
    .s_axi_awready  (s_axi_awready),
    .s_axi_wdata    (s_axi_wdata),
    .s_axi_wstrb    (s_axi_wstrb),
    .s_axi_wvalid   (s_axi_wvalid),
    .s_axi_wready   (s_axi_wready),
    .s_axi_bresp    (s_axi_bresp),
    .s_axi_bvalid   (s_axi_bvalid),
    .s_axi_bready   (s_axi_bready),
    .s_axi_araddr   (s_axi_araddr),
    .s_axi_arprot   (s_axi_arprot),
    .s_axi_arvalid  (s_axi_arvalid),
    .s_axi_arready  (s_axi_arready),
    .s_axi_rdata    (s_axi_rdata),
    .s_axi_rresp    (s_axi_rresp),
    .s_axi_rvalid   (s_axi_rvalid),
    .s_axi_rready   (s_axi_rready),
    .rd_payload_i   (rd_payload),
    .slv_err_i      (1'b0),
    .reg_storage_o  (reg_storage),
    .wr_event_o     (wr_event),
    .wr_event_addr_o(wr_event_addr),
    .wr_event_data_o(wr_event_data),
    .wr_event_strb_o(wr_event_strb),
    .rd_event_o     (rd_event),
    .rd_event_addr_o(rd_event_addr)
  );

  // ----------------------------------------------------------------
  // Decode CTRL, CLK_DIV, TX_DATA; pulse on writes. Register n sits at
  // byte address n * DATA_WIDTH/8, so the index starts at bit ALIGN.
  // ----------------------------------------------------------------
  localparam int unsigned ALIGN = $clog2(DATA_WIDTH / 8);

  logic [DATA_WIDTH-1:0] ctrl_word;
  logic [DATA_WIDTH-1:0] clk_div_word;
  logic [DATA_WIDTH-1:0] tx_data_word;
  assign ctrl_word    = ctrl_q;
  assign clk_div_word = reg_storage[4*DATA_WIDTH +: DATA_WIDTH];
  assign tx_data_word = reg_storage[5*DATA_WIDTH +: DATA_WIDTH];

  assign cfg_lsb_first  = ctrl_word[1];
  assign cfg_cpol       = ctrl_word[2];
  assign cfg_cpha       = ctrl_word[3];
  assign cfg_inter_byte = ctrl_word[4];
  assign cfg_cs_sel     = ctrl_word[8 +: ((NUM_CS > 1) ? $clog2(NUM_CS) : 1)];
  assign cfg_cs_setup   = ctrl_word[16 +: CS_DLY_W];
  assign cfg_cs_hold    = ctrl_word[24 +: CS_DLY_W];
  assign cfg_clk_div    = clk_div_word[DIV_WIDTH-1:0];

  // CTRL is stored here rather than in the register file: the start pulse
  // must come from the data being written (the register file updates its
  // storage on the same clock edge, so it only holds the previous value),
  // and START must read back as 0. Byte strobes apply as for RW registers.
  logic ctrl_write_now;
  assign ctrl_write_now = wr_event && (wr_event_addr[ADDR_WIDTH-1:ALIGN] == '0);

  always_ff @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn) begin
      ctrl_q    <= '0;
      spi_start <= 1'b0;
    end else begin
      spi_start <= ctrl_write_now && wr_event_strb[0] && wr_event_data[0];
      if (ctrl_write_now) begin
        for (int b = 0; b < DATA_WIDTH/8; b++)
          if (wr_event_strb[b]) ctrl_q[b*8 +: 8] <= wr_event_data[b*8 +: 8];
        ctrl_q[0] <= 1'b0;
      end
    end
  end

  // TX_DATA write -> push into the master's TX FIFO. tx_we pulses in the
  // cycle after the write is committed, when the register file already
  // holds the new, strobe-merged value, and only if the write touched a
  // byte lane of the SPI word.
  localparam int unsigned SPI_LANES = (SPI_DATA_W + 7) / 8;
  logic tx_data_write_now;
  assign tx_data_write_now = wr_event && (wr_event_addr[ADDR_WIDTH-1:ALIGN] == 'd5) &&
                             (|wr_event_strb[SPI_LANES-1:0]);
  always_ff @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn) tx_we <= 1'b0;
    else                tx_we <= tx_data_write_now;
  end
  assign tx_wdata = tx_data_word[SPI_DATA_W-1:0];

  // RX_DATA read -> the AXI slave samples RDATA (the RX FIFO head, the
  // master runs its RX FIFO in first-word fall-through mode) on the AR
  // handshake; the head is popped on the same clock edge. The previous code
  // popped one cycle later and returned the word popped by the previous
  // read.
  logic rx_data_read_now;
  assign rx_data_read_now = rd_event && (rd_event_addr[ADDR_WIDTH-1:ALIGN] == 'd6);
  assign rx_re = rx_data_read_now;

  // IRQ_STAT (reg 2): sticky bits set by hardware, cleared by writing 1
  // (from the committed write data, byte lane 0 strobed), and served as
  // live read data. The register file cannot be set by hardware, so the
  // bits live here and reg 2 is RO in the register file. Sources are
  // applied after the clear, so an event in the cycle of the acknowledge
  // is not lost.
  logic irq_ack_now;
  assign irq_ack_now = wr_event && (wr_event_addr[ADDR_WIDTH-1:ALIGN] == 'd2) && wr_event_strb[0];
  always_ff @(posedge s_axi_aclk) begin
    if (!s_axi_aresetn) irq_stat_q <= 4'b0;
    else begin
      if (irq_ack_now)    irq_stat_q    <= irq_stat_q & ~wr_event_data[3:0];
      if (tx_empty)       irq_stat_q[0] <= 1'b1;
      if (rx_full)        irq_stat_q[1] <= 1'b1;
      if (spi_done)       irq_stat_q[2] <= 1'b1;
      // err = overrun: a received word was dropped (RX FIFO full)
      if (spi_rx_overrun) irq_stat_q[3] <= 1'b1;
    end
  end

  logic [DATA_WIDTH-1:0] irq_mask_word;
  assign irq_mask_word = reg_storage[3*DATA_WIDTH +: DATA_WIDTH];
  assign irq_o = |(irq_stat_q & irq_mask_word[3:0]);

  // unused
  logic _unused;
  assign _unused = &{1'b0, rd_event, rd_event_addr, wr_event_addr,
                     tx_full, rx_empty, ctrl_word, reg_storage, wr_event_data,
                     wr_event_strb};

endmodule
