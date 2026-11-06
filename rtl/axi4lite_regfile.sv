// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// Parameterizable register file for an AXI4-Lite slave (ARM IHI 0022).
//
// The register file holds NUM_REGS storage words of DATA_WIDTH bits each
// and exposes:
//   * a write port the AXI slave drives on a successful AW + W handshake,
//     with byte-strobe (WSTRB) granularity;
//   * a read port the AXI slave drives on a successful AR handshake;
//   * an out-of-range flag so the slave can answer DECERR cleanly;
//   * a per-register attribute table that selects RW / RO / RC / W1C
//     semantics on a per-register basis.
//
// Per-register attributes (REG_ATTR_BITS):
//   2'b00  RW   read returns the stored value; write replaces masked bytes
//   2'b01  RO   read returns rd_payload_i (driven by the user logic);
//               write is silently ignored (BRESP still OKAY)
//   2'b10  RC   read returns the stored value AND clears it to zero on the
//               read handshake (read-clear, common for status / flag regs)
//   2'b11  W1C  write of '1' to a bit clears that bit; write of '0' is a
//               no-op for that bit (common for interrupt-status regs)
//
// Attributes are passed in as a packed bit-vector REG_ATTR with two bits
// per register, register 0 occupying the LOW two bits. The default value
// (all zeros) makes every register RW, which is what most users want.
//
// Address decode is byte-addressable but the protocol requires aligned
// accesses (AXI4-Lite §A8.1.1: only DATA_WIDTH/8-byte aligned addresses
// are legal). The decoded register index is `addr[ADDR_WIDTH-1 : ALIGN]`,
// where ALIGN = $clog2(DATA_WIDTH/8) (e.g. 2 for 32-bit, 3 for 64-bit).
// Anything that maps past the last valid register raises out_of_range_o
// so the slave can respond DECERR.
//
// Live read-only data: rd_payload_i is sampled combinationally and routed
// to the read port for any register whose attribute is RO. This lets the
// integrator surface real status (FIFO levels, interrupt vectors, hardware
// counters) without making the regfile itself stateful for those words.
// The mux uses the SAME register index as the storage path so software
// sees a consistent view.

module axi4lite_regfile #(
  parameter int unsigned ADDR_WIDTH = 8,
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned NUM_REGS   = 16,
  // Per-register attribute table, 2 bits per register, register 0 in LSBs.
  // Default = all RW.
  parameter logic [2*NUM_REGS-1:0] REG_ATTR = {2*NUM_REGS{1'b0}},
  // Reset values for each register (NUM_REGS x DATA_WIDTH packed). Register
  // 0 in the low DATA_WIDTH bits. Default zero.
  parameter logic [NUM_REGS*DATA_WIDTH-1:0] REG_RESET = {NUM_REGS*DATA_WIDTH{1'b0}}
) (
  input  logic                    clk_i,
  input  logic                    rst_ni,

  // Write port (driven by the slave on AW + W handshake).
  input  logic                    wr_en_i,
  input  logic [ADDR_WIDTH-1:0]   wr_addr_i,
  input  logic [DATA_WIDTH-1:0]   wr_data_i,
  input  logic [DATA_WIDTH/8-1:0] wr_strb_i,

  // Read port (driven by the slave on AR handshake).
  input  logic                    rd_en_i,
  input  logic [ADDR_WIDTH-1:0]   rd_addr_i,
  output logic [DATA_WIDTH-1:0]   rd_data_o,

  // Live read-only data (one DATA_WIDTH word per register; the regfile
  // uses it for any register whose attribute is RO). Wire the unused
  // slots to '0.
  input  logic [NUM_REGS*DATA_WIDTH-1:0] rd_payload_i,

  // User-visible storage values (so user logic can read whatever software
  // wrote without going back through the bus). Same packing as REG_RESET.
  output logic [NUM_REGS*DATA_WIDTH-1:0] reg_storage_o,

  // Out-of-range flags — high when the corresponding *_addr lands past
  // NUM_REGS or is mis-aligned. The slave uses these to drive BRESP /
  // RRESP = DECERR.
  output logic                    wr_out_of_range_o,
  output logic                    rd_out_of_range_o
);

  localparam int unsigned BYTES = DATA_WIDTH / 8;
  localparam int unsigned ALIGN = (BYTES <= 1) ? 0 : $clog2(BYTES);
  localparam int unsigned IDX_W = (NUM_REGS <= 1) ? 1 : $clog2(NUM_REGS);

  // ------------------------------------------------------------------
  // Address decode: register index, alignment + range check.
  // ------------------------------------------------------------------
  logic [ADDR_WIDTH-1:0] wr_align_mask;
  logic [ADDR_WIDTH-1:0] rd_align_mask;
  assign wr_align_mask = (BYTES > 1) ? (ADDR_WIDTH'(BYTES) - ADDR_WIDTH'(1))
                                     : ADDR_WIDTH'(0);
  assign rd_align_mask = wr_align_mask;

  logic [ADDR_WIDTH-1:0] wr_idx_full;
  logic [ADDR_WIDTH-1:0] rd_idx_full;
  assign wr_idx_full = wr_addr_i >> ALIGN;
  assign rd_idx_full = rd_addr_i >> ALIGN;

  logic                wr_align_err;
  logic                rd_align_err;
  logic                wr_range_err;
  logic                rd_range_err;
  assign wr_align_err = (wr_addr_i & wr_align_mask) != '0;
  assign rd_align_err = (rd_addr_i & rd_align_mask) != '0;
  assign wr_range_err = wr_idx_full >= ADDR_WIDTH'(NUM_REGS);
  assign rd_range_err = rd_idx_full >= ADDR_WIDTH'(NUM_REGS);

  assign wr_out_of_range_o = wr_align_err || wr_range_err;
  assign rd_out_of_range_o = rd_align_err || rd_range_err;

  logic [IDX_W-1:0] wr_idx;
  logic [IDX_W-1:0] rd_idx;
  assign wr_idx = wr_idx_full[IDX_W-1:0];
  assign rd_idx = rd_idx_full[IDX_W-1:0];

  // ------------------------------------------------------------------
  // Storage: one word per register, written through its own decoded byte
  // enables (no read-modify-write of the addressed word). The attribute
  // of each register is a constant, so an RO register has no storage
  // (it shows its reset value on reg_storage_o) and needs no write logic.
  //   RW   a write replaces the strobed bytes
  //   RO   writes are silently ignored (BRESP still OKAY)
  //   RC   writes are ignored; a read returns the stored value and clears
  //        it on the read handshake (read-clear)
  //   W1C  a written 1 clears that bit in the strobed bytes
  // ------------------------------------------------------------------
  logic rd_ok;
  assign rd_ok = rd_en_i && !rd_out_of_range_o;

  // What a read of each register returns: rd_payload_i for RO, the stored
  // value otherwise (an RC read returns the value before the clear).
  logic [NUM_REGS*DATA_WIDTH-1:0] rd_words;

  genvar gi;
  generate
    for (gi = 0; gi < NUM_REGS; gi = gi + 1) begin : g_reg
      localparam logic [1:0] ATTR = REG_ATTR[2*gi +: 2];
      localparam logic [DATA_WIDTH-1:0] RESET = REG_RESET[gi*DATA_WIDTH +: DATA_WIDTH];
      // The one aligned address of register gi (none if ADDR_WIDTH cannot
      // reach it): a hit is an in-range access to this register.
      localparam bit REACH = ((gi << ALIGN) >> ADDR_WIDTH) == 0;
      logic                  wr_hit, rd_hit;
      logic [DATA_WIDTH-1:0] q;
      assign wr_hit = REACH && wr_en_i && (wr_addr_i == ADDR_WIDTH'(gi << ALIGN));
      assign rd_hit = REACH && rd_en_i && (rd_addr_i == ADDR_WIDTH'(gi << ALIGN));
      if (ATTR == 2'b01) begin : g_ro
        assign q = RESET;
        assign rd_words[gi*DATA_WIDTH +: DATA_WIDTH] = rd_payload_i[gi*DATA_WIDTH +: DATA_WIDTH];
      end else begin : g_stored
        always_ff @(posedge clk_i) begin
          if (!rst_ni) begin
            q <= RESET;
          end else if (ATTR == 2'b10) begin
            if (rd_hit) q <= '0;
          end else begin
            for (int b = 0; b < BYTES; b++)
              if (wr_hit && wr_strb_i[b])
                q[b*8 +: 8] <= (ATTR == 2'b11) ? (q[b*8 +: 8] & ~wr_data_i[b*8 +: 8])
                                               : wr_data_i[b*8 +: 8];
          end
        end
        assign rd_words[gi*DATA_WIDTH +: DATA_WIDTH] = q;
      end
      assign reg_storage_o[gi*DATA_WIDTH +: DATA_WIDTH] = q;
    end
  endgenerate

  // ------------------------------------------------------------------
  // Combinational read mux. The index is zero-extended to 32 bits, which
  // is what Yosys / Vivado / older simulators expect from a packed-array
  // slice.
  // ------------------------------------------------------------------
  logic [31:0] rd_idx32;
  assign rd_idx32 = {{(32-IDX_W){1'b0}}, rd_idx};

  always_comb begin
    rd_data_o = '0;
    if (rd_ok) rd_data_o = rd_words[rd_idx32 * DATA_WIDTH +: DATA_WIDTH];
  end

endmodule
