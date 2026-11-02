// SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
//
// Verilator C++ harness for spi_axi4lite (tb/spi_axi4lite_tb.sv, MISO looped
// back to MOSI). An AXI4-Lite master drives the register map cycle by cycle
// and a bus monitor decodes every CS_n frame from SCK and MOSI. Runs:
//   1. Reset values, ID / VERSION, DECERR on unaligned and unmapped
//      addresses, scratch registers with byte strobes
//   2. TX_DATA pushes the written value; CTRL.start launches one transfer
//      with the configuration of the same CTRL write and reads back as 0;
//      RX_DATA returns the received words in order and 0 when empty
//   3. IRQ_STAT sticky, readable, write-1-to-clear; irq_o follows IRQ_MASK
//   4. IRQ_STAT.err only when a received word is dropped (RX FIFO full)
//   5. Random loopback: all modes, MSB/LSB first, dividers, CS lines,
//      CS timing, single words and bursts
//
// Output: prints "+PASS" or "+FAIL".

#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

#include "verilated.h"
#include "Vspi_axi4lite_tb.h"

namespace {

// Register byte offsets for DATA_WIDTH = 32.
constexpr uint32_t kCtrl = 0x00, kStatus = 0x04, kIrqStat = 0x08, kIrqMask = 0x0C,
                   kClkDiv = 0x10, kTxData = 0x14, kRxData = 0x18, kFifoLvl = 0x1C,
                   kId = 0x20, kVersion = 0x24, kScratch = 0x28;
constexpr uint32_t kOkay = 0, kDecErr = 3;
constexpr uint32_t kIrqTxEmpty = 1, kIrqRxFull = 2, kIrqDone = 4, kIrqErr = 8;
constexpr uint32_t kBusy = 1, kTxEmpty = 2, kTxFull = 4, kRxEmpty = 8, kRxFull = 16;
constexpr int kDepth = 16, kNumCs = 4, kWordBits = 8;

int g_failures = 0;

void expect(const char* what, bool ok) {
    if (ok) return;
    std::printf("  [FAIL] %s\n", what);
    ++g_failures;
}

void expect_eq(const char* what, uint64_t got, uint64_t exp) {
    if (got == exp) return;
    std::printf("  [FAIL] %s: got 0x%llx expected 0x%llx\n", what,
                static_cast<unsigned long long>(got), static_cast<unsigned long long>(exp));
    ++g_failures;
}

void report(const char* name, int failures_before) {
    if (g_failures == failures_before) std::printf("  [PASS] %s\n", name);
}

struct SpiCfg {
    uint32_t cpol = 0, cpha = 0, lsb = 0, inter = 0, cs_sel = 0, setup = 0, hold = 0;
    uint32_t ctrl(bool start) const {
        return (start ? 1u : 0u) | lsb << 1 | cpol << 2 | cpha << 3 | inter << 4 |
               cs_sel << 8 | setup << 16 | hold << 24;
    }
};

struct Frame {
    int line = -1;            // the one CS line driven low, -1 if not exactly one
    bool sck_ok = true;       // SCK at CPOL and quiet when CS_n fell
    uint32_t ctrl = 0;        // CTRL value in force (decodes the bits)
    std::vector<int> bits;    // MOSI at each sampling edge
};

class Bench {
  public:
    explicit Bench(Vspi_axi4lite_tb* dut) : d_(dut) {}

    std::vector<Frame> frames;

    void tick() {
        d_->s_axi_aclk = 0;
        d_->eval();
        d_->s_axi_aclk = 1;
        d_->eval();
        monitor();
    }

    void idle(int n) {
        for (int i = 0; i < n; ++i) tick();
    }

    void reset() {
        d_->s_axi_aresetn = 0;
        d_->s_axi_awvalid = 0;
        d_->s_axi_wvalid = 0;
        d_->s_axi_bready = 0;
        d_->s_axi_arvalid = 0;
        d_->s_axi_rready = 0;
        d_->s_axi_awaddr = 0;
        d_->s_axi_awprot = 0;
        d_->s_axi_wdata = 0;
        d_->s_axi_wstrb = 0;
        d_->s_axi_araddr = 0;
        d_->s_axi_arprot = 0;
        idle(8);
        d_->s_axi_aresetn = 1;
        idle(2);
        ctrl_ = 0;
        frames.clear();
        in_frame_ = false;
    }

    // One AXI4-Lite write, AW and W presented together. Returns BRESP.
    uint32_t write(uint32_t addr, uint32_t data, uint32_t strb = 0xF) {
        d_->s_axi_awaddr = addr;
        d_->s_axi_awvalid = 1;
        d_->s_axi_wdata = data;
        d_->s_axi_wstrb = strb;
        d_->s_axi_wvalid = 1;
        d_->s_axi_bready = 1;
        uint32_t resp = 0;
        bool done = false;
        for (int n = 0; n < 64 && !done; ++n) {
            d_->eval();
            const bool aw = d_->s_axi_awvalid && d_->s_axi_awready;
            const bool w = d_->s_axi_wvalid && d_->s_axi_wready;
            const bool b = d_->s_axi_bvalid && d_->s_axi_bready;
            resp = d_->s_axi_bresp;
            tick();
            if (aw) d_->s_axi_awvalid = 0;
            if (w) d_->s_axi_wvalid = 0;
            done = b;
        }
        d_->s_axi_awvalid = 0;
        d_->s_axi_wvalid = 0;
        d_->s_axi_bready = 0;
        expect("AXI write completes", done);
        if (done && resp == kOkay && addr == kCtrl)
            for (int b = 0; b < 4; ++b)
                if ((strb >> b) & 1u) ctrl_ = (ctrl_ & ~(0xFFu << 8 * b)) | (data & (0xFFu << 8 * b));
        return resp;
    }

    // One AXI4-Lite read. Returns RDATA, RRESP through resp.
    uint32_t read(uint32_t addr, uint32_t* resp = nullptr) {
        d_->s_axi_araddr = addr;
        d_->s_axi_arvalid = 1;
        d_->s_axi_rready = 1;
        uint32_t data = 0, rr = 0;
        bool done = false;
        for (int n = 0; n < 64 && !done; ++n) {
            d_->eval();
            const bool ar = d_->s_axi_arvalid && d_->s_axi_arready;
            const bool r = d_->s_axi_rvalid && d_->s_axi_rready;
            data = d_->s_axi_rdata;
            rr = d_->s_axi_rresp;
            tick();
            if (ar) d_->s_axi_arvalid = 0;
            done = r;
        }
        d_->s_axi_arvalid = 0;
        d_->s_axi_rready = 0;
        expect("AXI read completes", done);
        if (resp) *resp = rr;
        return data;
    }

    void ctrl(const SpiCfg& c, bool start) { write(kCtrl, c.ctrl(start)); }

    // Wait until `target` frames have ended on the bus and STATUS.busy is 0.
    bool wait_frames(size_t target) {
        for (int n = 0; frames.size() < target; ++n) {
            if (n == 200000) return false;
            tick();
        }
        for (int n = 0; n < 1000; ++n)
            if ((read(kStatus) & kBusy) == 0) return true;
        return false;
    }

    bool irq() const { return d_->irq_o != 0; }

    static std::vector<uint32_t> words(const Frame& f) {
        std::vector<uint32_t> out;
        const bool lsb = (f.ctrl >> 1) & 1u;
        for (size_t i = 0; i + kWordBits <= f.bits.size(); i += kWordBits) {
            uint32_t v = 0;
            for (int b = 0; b < kWordBits; ++b) {
                const uint32_t bit = static_cast<uint32_t>(f.bits[i + b]);
                v = lsb ? (v | bit << b) : ((v << 1) | bit);
            }
            out.push_back(v);
        }
        return out;
    }

  private:
    void monitor() {
        const int cs = d_->cs_n_o & ((1 << kNumCs) - 1);
        const int sck = d_->sck_o & 1;
        const int idle_cs = (1 << kNumCs) - 1;
        if (!in_frame_ && cs != idle_cs) {
            in_frame_ = true;
            cur_ = Frame();
            cur_.ctrl = ctrl_;
            const int low = ~cs & idle_cs;
            for (int i = 0; i < kNumCs; ++i)
                if (low == (1 << i)) cur_.line = i;
            const int cpol = (ctrl_ >> 2) & 1;
            cur_.sck_ok = (sck == cpol) && (sck == prev_sck_);
        } else if (in_frame_ && cs == idle_cs) {
            in_frame_ = false;
            frames.push_back(cur_);
        } else if (in_frame_) {
            if (cs != prev_cs_) cur_.line = -1;
            if (sck != prev_sck_) {
                const bool sample_on_rise = ((cur_.ctrl >> 2) & 1) == ((cur_.ctrl >> 3) & 1);
                if ((sck != 0) == sample_on_rise) cur_.bits.push_back(d_->mosi_o & 1);
            }
        }
        prev_cs_ = cs;
        prev_sck_ = sck;
    }

    Vspi_axi4lite_tb* d_;
    uint32_t ctrl_ = 0;       // CTRL as last written (start bit included)
    bool in_frame_ = false;
    int prev_cs_ = (1 << kNumCs) - 1;
    int prev_sck_ = 0;
    Frame cur_;
};

// Frames [first, end) must all be on `line`, start with SCK at CPOL and
// carry whole words; returns the words.
std::vector<uint32_t> bus_words(const Bench& b, size_t first, uint32_t line) {
    std::vector<uint32_t> out;
    for (size_t i = first; i < b.frames.size(); ++i) {
        const Frame& f = b.frames[i];
        expect_eq("CS line of the frame", static_cast<uint32_t>(f.line), line);
        expect("SCK at CPOL and quiet when CS_n falls", f.sck_ok);
        expect_eq("bits in the frame modulo the word width", f.bits.size() % kWordBits, 0);
        for (uint32_t w : Bench::words(f)) out.push_back(w);
    }
    return out;
}

void test_map(Bench& b) {
    std::printf("---- Test 1: reset values, decode errors, scratch registers ----\n");
    const int f0 = g_failures;
    b.reset();
    struct Reg { uint32_t addr, value; const char* name; };
    const Reg regs[] = {
        {kCtrl, 0, "CTRL"},         {kStatus, kTxEmpty | kRxEmpty, "STATUS"},
        {kIrqStat, kIrqTxEmpty, "IRQ_STAT"}, {kIrqMask, 0, "IRQ_MASK"},
        {kClkDiv, 0, "CLK_DIV"},    {kTxData, 0, "TX_DATA"},
        {kRxData, 0, "RX_DATA"},    {kFifoLvl, 0, "FIFO_LVL"},
        {kId, 0x5910017E, "ID"},    {kVersion, 0x00010100, "VERSION"},
    };
    uint32_t resp = 0;
    for (const Reg& r : regs) {
        expect_eq(r.name, b.read(r.addr, &resp), r.value);
        expect_eq("RRESP of a mapped register", resp, kOkay);
    }
    b.read(0x02, &resp);
    expect_eq("RRESP of an unaligned read", resp, kDecErr);
    b.read(0x40, &resp);
    expect_eq("RRESP of an unmapped read", resp, kDecErr);
    expect_eq("BRESP of an unaligned write", b.write(kTxData + 1, 0x11), kDecErr);
    expect_eq("BRESP of an unmapped write", b.write(0x80, 0x11), kDecErr);
    expect_eq("FIFO_LVL after rejected writes", b.read(kFifoLvl), 0);
    for (uint32_t a = kScratch; a < 0x40; a += 4) b.write(a, 0xA5A50000u | a);
    for (uint32_t a = kScratch; a < 0x40; a += 4) expect_eq("scratch register", b.read(a), 0xA5A50000u | a);
    b.write(kScratch, 0x11223344);
    b.write(kScratch, 0xAABBCCDD, 0x5);
    expect_eq("scratch register after a write to byte lanes 0 and 2", b.read(kScratch), 0x11BB33DD);
    report("reset values, DECERR, scratch and byte strobes", f0);
}

void test_tx_start_rx(Bench& b) {
    std::printf("---- Test 2: TX_DATA, CTRL.start, RX_DATA ----\n");
    const int f0 = g_failures;
    b.reset();
    b.write(kClkDiv, 1);
    const std::vector<uint32_t> words = {0x5A, 0xC3, 0x01};
    for (uint32_t w : words) b.write(kTxData, w);
    expect_eq("tx_count after 3 TX_DATA writes", b.read(kFifoLvl) & 0xFFFF, 3);
    b.write(kTxData, 0x77, 0x2);   // byte lane 1 only: outside the 8-bit SPI word
    expect_eq("tx_count after a TX_DATA write without lane 0", b.read(kFifoLvl) & 0xFFFF, 3);
    b.idle(100);
    expect("no frame before CTRL.start", b.frames.empty());

    SpiCfg c;
    c.cpol = 1; c.cpha = 1; c.inter = 1; c.cs_sel = 2; c.setup = 2; c.hold = 2;
    b.ctrl(c, true);   // configuration and start in the same write
    expect("burst completes", b.wait_frames(1));
    expect_eq("frames after one CTRL.start", b.frames.size(), 1);
    expect("MOSI carries the TX_DATA words (mode 3, CS line 2)", bus_words(b, 0, 2) == words);
    expect_eq("CTRL reads back with start = 0", b.read(kCtrl), c.ctrl(false));

    b.write(kTxData, 0x96);
    b.ctrl(c, false);
    b.idle(400);
    expect_eq("frames after a CTRL write without start", b.frames.size(), 1);
    expect_eq("tx_count after a CTRL write without start", b.read(kFifoLvl) & 0xFFFF, 1);
    b.ctrl(c, true);
    expect("second transfer completes", b.wait_frames(2));
    expect("MOSI carries the queued word", bus_words(b, 1, 2) == std::vector<uint32_t>{0x96});

    expect_eq("rx_count after 4 words", b.read(kFifoLvl) >> 16, 4);
    expect_eq("STATUS with RX not empty", b.read(kStatus), kTxEmpty);
    for (uint32_t w : {0x5Au, 0xC3u, 0x01u, 0x96u}) expect_eq("RX_DATA", b.read(kRxData), w);
    expect_eq("RX_DATA on an empty RX FIFO", b.read(kRxData), 0);
    expect_eq("rx_count after reading every word", b.read(kFifoLvl) >> 16, 0);

    b.write(kCtrl, 0x00070000, 0x4);   // byte lane 2 only: cs_setup
    expect_eq("CTRL after a write to lane 2", b.read(kCtrl), (c.ctrl(false) & ~0x00FF0000u) | 0x00070000u);
    expect_eq("no transfer from a CTRL write without lane 0", b.frames.size(), 2);
    report("TX_DATA push, CTRL.start from the written data, RX_DATA head and pop", f0);
}

void test_irq(Bench& b) {
    std::printf("---- Test 3: IRQ_STAT and irq_o ----\n");
    const int f0 = g_failures;
    b.reset();
    expect("irq_o low after reset (IRQ_MASK = 0)", !b.irq());
    b.write(kIrqMask, kIrqDone);
    expect("irq_o low before a transfer", !b.irq());
    b.write(kTxData, 0x3C);
    SpiCfg c;
    b.ctrl(c, true);
    expect("transfer completes", b.wait_frames(1));
    expect("IRQ_STAT.done set after the transfer", (b.read(kIrqStat) & kIrqDone) != 0);
    expect("irq_o high with done enabled", b.irq());
    b.write(kIrqStat, kIrqDone, 0x2);   // lane 0 not strobed: clears nothing
    expect("IRQ_STAT.done kept by a write without lane 0", (b.read(kIrqStat) & kIrqDone) != 0);
    b.write(kIrqStat, 0);
    expect("IRQ_STAT.done kept by writing 0", (b.read(kIrqStat) & kIrqDone) != 0);
    b.write(kIrqStat, kIrqDone);
    expect_eq("IRQ_STAT.done after write-1-to-clear", b.read(kIrqStat) & kIrqDone, 0);
    expect("irq_o low after the clear", !b.irq());
    b.write(kIrqStat, kIrqTxEmpty);
    expect("IRQ_STAT.tx_empty set again while TX stays empty", (b.read(kIrqStat) & kIrqTxEmpty) != 0);
    b.write(kIrqMask, kIrqTxEmpty);
    expect("irq_o follows IRQ_MASK", b.irq());
    expect_eq("RX_DATA", b.read(kRxData), 0x3C);
    report("sticky bits, write-1-to-clear, irq_o masking", f0);
}

void test_overrun(Bench& b) {
    std::printf("---- Test 4: RX FIFO full and IRQ_STAT.err ----\n");
    const int f0 = g_failures;
    b.reset();
    SpiCfg c;
    c.inter = 1; c.setup = 1; c.hold = 1;
    std::vector<uint32_t> sent;
    for (int i = 0; i < kDepth; ++i) {
        sent.push_back((37u * i + 11u) & 0xFFu);
        b.write(kTxData, sent.back());
    }
    expect_eq("tx_count with a full TX FIFO", b.read(kFifoLvl) & 0xFFFF, kDepth);
    expect("STATUS.tx_full", (b.read(kStatus) & kTxFull) != 0);
    b.ctrl(c, true);
    expect("burst completes", b.wait_frames(1));
    expect_eq("rx_count after a full burst", b.read(kFifoLvl) >> 16, kDepth);
    expect("STATUS.rx_full", (b.read(kStatus) & kRxFull) != 0);
    uint32_t st = b.read(kIrqStat);
    expect("IRQ_STAT.rx_full set", (st & kIrqRxFull) != 0);
    expect("IRQ_STAT.err clear: the RX FIFO is full but nothing was lost", (st & kIrqErr) == 0);

    b.write(kTxData, 0xEE);
    b.ctrl(c, true);
    expect("transfer into a full RX FIFO completes", b.wait_frames(2));
    expect("IRQ_STAT.err set: a received word was dropped", (b.read(kIrqStat) & kIrqErr) != 0);
    expect_eq("rx_count stays at the depth", b.read(kFifoLvl) >> 16, kDepth);
    bool same = true;
    for (int i = 0; i < kDepth; ++i) same = same && (b.read(kRxData) == sent[i]);
    expect("RX FIFO keeps the first words, the newest word is dropped", same);
    expect("STATUS.rx_empty after reading every word", (b.read(kStatus) & kRxEmpty) != 0);
    b.write(kIrqMask, kIrqErr);
    expect("irq_o high with err enabled", b.irq());
    b.write(kIrqStat, kIrqErr);
    expect("irq_o low after clearing err", !b.irq());
    report("err only on a dropped word", f0);
}

void test_random(Bench& b, int iterations) {
    std::printf("---- Test 5: random loopback, %d transfers ----\n", iterations);
    const int f0 = g_failures;
    b.reset();
    std::mt19937 rng(0xA0B1C2D3u);
    auto pick = [&rng](uint32_t lo, uint32_t hi) {
        return std::uniform_int_distribution<uint32_t>(lo, hi)(rng);
    };
    int words_checked = 0, mismatches = 0;
    for (int it = 0; it < iterations && g_failures - f0 < 10; ++it) {
        SpiCfg c;
        c.cpol = pick(0, 1); c.cpha = pick(0, 1); c.lsb = pick(0, 1); c.inter = pick(0, 1);
        c.cs_sel = pick(0, kNumCs - 1); c.setup = pick(0, 3); c.hold = pick(0, 3);
        const uint32_t n = c.inter ? pick(1, 6) : pick(1, 3);
        b.write(kClkDiv, pick(0, 3));
        std::vector<uint32_t> tx(n);
        for (uint32_t& w : tx) {
            w = pick(0, 255);
            b.write(kTxData, w);
        }
        const size_t first = b.frames.size();
        b.ctrl(c, false);
        for (uint32_t k = 0; k < (c.inter ? 1 : n); ++k) {
            b.ctrl(c, true);
            if (!b.wait_frames(b.frames.size() + 1)) {
                expect("transfer completes", false);
                break;
            }
        }
        const std::vector<uint32_t> bus = bus_words(b, first, c.cs_sel);
        if (bus != tx) ++mismatches;
        for (uint32_t i = 0; i < n; ++i) {
            if (b.read(kRxData) != tx[i]) ++mismatches;
            ++words_checked;
        }
    }
    std::printf("  random: %d words, %d mismatches (bus or RX_DATA)\n", words_checked, mismatches);
    expect("random loopback without mismatches", mismatches == 0);
    report("random loopback", f0);
}

}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    setvbuf(stdout, nullptr, _IOLBF, 0);
    Vspi_axi4lite_tb* dut = new Vspi_axi4lite_tb();
    Bench bench(dut);

    test_map(bench);
    test_tx_start_rx(bench);
    test_irq(bench);
    test_overrun(bench);
    test_random(bench, 400);

    dut->final();
    delete dut;
    if (g_failures == 0) {
        std::printf("+PASS all AXI4-Lite tests passed\n");
        return 0;
    }
    std::printf("+FAIL %d failures\n", g_failures);
    return 1;
}
