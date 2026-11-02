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
  // Storage array.
  // ------------------------------------------------------------------
  logic [DATA_WIDTH-1:0] regs_q [NUM_REGS];

  // Unpack reset value for register `i`. Only used in the synchronous
  // reset block, where Yosys is happy to accept it as a function call
  // with an integer argument.
  function automatic logic [DATA_WIDTH-1:0] reg_reset_slice(input integer i);
    reg_reset_slice = REG_RESET[i*DATA_WIDTH +: DATA_WIDTH];
  endfunction

  // Apply byte strobes to a stored word.
  function automatic logic [DATA_WIDTH-1:0] apply_strb(
      input logic [DATA_WIDTH-1:0]   old_v,
      input logic [DATA_WIDTH-1:0]   new_v,
      input logic [DATA_WIDTH/8-1:0] strb);
    logic [DATA_WIDTH-1:0] r;
    integer b;
    r = old_v;
    for (b = 0; b < BYTES; b = b + 1) begin
      if (strb[b]) r[b*8 +: 8] = new_v[b*8 +: 8];
    end
    apply_strb = r;
  endfunction

  // ------------------------------------------------------------------
  // Storage update: write port + W1C clears + RC clears.
  // ------------------------------------------------------------------
  // Per-cycle attribute lookup. We pre-compute the integer index and
  // look up REG_ATTR with a 32-bit zero-extended index, which is what
  // Yosys / Vivado / older simulators expect from a packed-array slice.
  logic [1:0]      rd_attr_seq;
  logic [1:0]      wr_attr_seq;
  logic [31:0]     rd_idx32;
  logic [31:0]     wr_idx32;
  assign rd_idx32    = {{(32-IDX_W){1'b0}}, rd_idx};
  assign wr_idx32    = {{(32-IDX_W){1'b0}}, wr_idx};
  assign rd_attr_seq = REG_ATTR[2*rd_idx32 +: 2];
  assign wr_attr_seq = REG_ATTR[2*wr_idx32 +: 2];

  integer i;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      for (i = 0; i < NUM_REGS; i = i + 1)
        regs_q[i] <= reg_reset_slice(i);
    end else begin
      // Read-clear: if the read port is enabled and the addressed
      // register is RC and in range, clear it on this cycle. Note that
      // the rd_data_o output below presents the OLD value (before the
      // clear), which is the standard RC behaviour (read returns the
      // accumulated status, then clears).
      if (rd_en_i && !rd_out_of_range_o && rd_attr_seq == 2'b10) begin
        regs_q[rd_idx] <= '0;
      end

      // Write port. If both rd and wr fire on the same cycle to the
      // SAME register, and the register is RC, the RC clear takes
      // precedence (i.e. wr is dropped). This is the conventional
      // ordering for a status register that software RW-1-clears.
      if (wr_en_i && !wr_out_of_range_o) begin
        if (wr_attr_seq == 2'b00) begin
          // RW
          if (!(rd_en_i && !rd_out_of_range_o &&
                rd_idx == wr_idx && rd_attr_seq == 2'b10))
            regs_q[wr_idx] <= apply_strb(regs_q[wr_idx], wr_data_i, wr_strb_i);
        end else if (wr_attr_seq == 2'b11) begin
          // W1C: bits in wr_data_i that are 1 clear the corresponding
          // stored bits. Byte-strobed: a byte with strb=0 is untouched.
          for (i = 0; i < BYTES; i = i + 1) begin
            if (wr_strb_i[i])
              regs_q[wr_idx][i*8 +: 8] <=
                  regs_q[wr_idx][i*8 +: 8] & ~wr_data_i[i*8 +: 8];
          end
        end
        // RO (2'b01) and RC (2'b10) silently ignore writes.
      end
    end
  end

  // ------------------------------------------------------------------
  // Combinational read mux.
  // ------------------------------------------------------------------
  logic [DATA_WIDTH-1:0] rd_payload_word;
  assign rd_payload_word = rd_payload_i[rd_idx32 * DATA_WIDTH +: DATA_WIDTH];

  always_comb begin
    rd_data_o = '0;
    if (rd_en_i && !rd_out_of_range_o) begin
      if (rd_attr_seq == 2'b01) rd_data_o = rd_payload_word;
      else                      rd_data_o = regs_q[rd_idx];
    end
  end

  // ------------------------------------------------------------------
  // Pack storage out for user logic.
  // ------------------------------------------------------------------
  genvar gi;
  generate
    for (gi = 0; gi < NUM_REGS; gi = gi + 1) begin : g_pack
      assign reg_storage_o[gi*DATA_WIDTH +: DATA_WIDTH] = regs_q[gi];
    end
  endgenerate

endmodule
