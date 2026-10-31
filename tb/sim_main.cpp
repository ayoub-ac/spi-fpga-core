// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
// Verilator C++ harness for spi_top. Runs:
//   1. Single-byte loopback in each of the 4 modes (master <-> slave)
//   2. MSB-first and LSB-first variants in mode 0
//   3. Multi-byte FIFO loopback
//   4. Clock-divisor sweep at 1 MHz, 10 MHz, 25 MHz from 100 MHz clk
//   5. CS timing (setup/hold delays) sanity
//   6. Mid-transfer reset recovery
//   7. Back-to-back transfers (inter-byte CS hold)
//   8. 1000 random bytes loopback round-trip in mode 0
//   9. CPHA=1 words whose last bit is 1 (modes 1/3, MSB/LSB first)
//  10. One word per CS frame (inter_byte=0) with several words queued on
//      both sides: the slave sends each queued word exactly once
//  11. Random loopback in all modes, MSB/LSB first, dividers, CS timing,
//      single words and bursts
//  12. Full FIFOs, RX overrun on both sides (irq_err_o, s_rx_overrun_o),
//      slave TX underrun (s_tx_underrun_o)
// Every cycle: miso_oe_o must equal !cs_n_o[0].
//
// Output: prints "+PASS" or "+FAIL" with a coverage report; all 12
// coverage bins must be hit.

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <random>
#include <vector>

#include "verilated.h"
#include "Vspi_top_tb.h"

// ---------------------------------------------------------------------------
// Sim primitives
// ---------------------------------------------------------------------------
static int g_failures = 0;
static vluint64_t g_time = 0;

// Strobe counts (one count per cycle high) and per-cycle checks.
struct Monitor {
    int irq_err = 0;
    int s_rx_overrun = 0;
    int s_tx_underrun = 0;
    int miso_oe_mismatch = 0;
};
static Monitor g_mon;

static void tick(Vspi_top_tb* dut) {
    dut->clk_i = 0; dut->eval(); g_time++;
    dut->clk_i = 1; dut->eval(); g_time++;
    g_mon.irq_err       += dut->irq_err_o;
    g_mon.s_rx_overrun  += dut->s_rx_overrun_o;
    g_mon.s_tx_underrun += dut->s_tx_underrun_o;
    if (dut->miso_oe_o != !(dut->cs_n_o & 1)) g_mon.miso_oe_mismatch++;
}

static void reset(Vspi_top_tb* dut) {
    dut->rst_ni = 0;
    dut->m_cpol_i = 0; dut->m_cpha_i = 0;
    dut->m_lsb_first_i = 0;
    dut->m_clk_div_i = 3;
    dut->m_cs_sel_i = 0;
    dut->m_cs_setup_i = 4;
    dut->m_cs_hold_i = 4;
    dut->m_cs_inter_byte_i = 0;
    dut->m_start_i = 0;
    dut->m_tx_we_i = 0; dut->m_tx_wdata_i = 0;
    dut->m_rx_re_i = 0;
    dut->s_cpol_i = 0; dut->s_cpha_i = 0;
    dut->s_lsb_first_i = 0;
    dut->s_tx_we_i = 0; dut->s_tx_wdata_i = 0;
    dut->s_rx_re_i = 0;
    for (int i = 0; i < 8; i++) tick(dut);
    dut->rst_ni = 1;
    tick(dut);
}

static void cfg_master(Vspi_top_tb* dut, int cpol, int cpha,
                       int lsb_first, int div, int cs_sel,
                       int cs_setup, int cs_hold, int inter_byte) {
    dut->m_cpol_i = cpol; dut->m_cpha_i = cpha;
    dut->m_lsb_first_i = lsb_first;
    dut->m_clk_div_i = div;
    dut->m_cs_sel_i = cs_sel;
    dut->m_cs_setup_i = cs_setup;
    dut->m_cs_hold_i = cs_hold;
    dut->m_cs_inter_byte_i = inter_byte;
}
static void cfg_slave(Vspi_top_tb* dut, int cpol, int cpha, int lsb_first) {
    dut->s_cpol_i = cpol; dut->s_cpha_i = cpha;
    dut->s_lsb_first_i = lsb_first;
}

static void push_tx(Vspi_top_tb* dut, uint8_t v, bool master = true) {
    if (master) { dut->m_tx_we_i = 1; dut->m_tx_wdata_i = v; }
    else        { dut->s_tx_we_i = 1; dut->s_tx_wdata_i = v; }
    tick(dut);
    if (master) dut->m_tx_we_i = 0;
    else        dut->s_tx_we_i = 0;
    tick(dut);
}

static uint8_t pop_rx(Vspi_top_tb* dut, bool master = true) {
    uint8_t v;
    if (master) { dut->m_rx_re_i = 1; tick(dut); v = dut->m_rx_rdata_o;
                  dut->m_rx_re_i = 0; tick(dut); }
    else        { dut->s_rx_re_i = 1; tick(dut); v = dut->s_rx_rdata_o;
                  dut->s_rx_re_i = 0; tick(dut); }
    return v;
}

static void start_xfer(Vspi_top_tb* dut) {
    dut->m_start_i = 1;
    tick(dut);
    dut->m_start_i = 0;
}

static bool wait_idle(Vspi_top_tb* dut, int max_cycles = 200000) {
    int n = 0;
    while ((dut->m_busy_o || !(dut->cs_n_o & 1)) && n < max_cycles) {
        tick(dut); n++;
    }
    // a few more for the FSM to settle
    for (int i = 0; i < 8; i++) tick(dut);
    return n < max_cycles;
}

// ---------------------------------------------------------------------------
// Test 1: single-byte loopback in each of the 4 modes.
// Master sends `m2s`, slave responds with `s2m`. After the transfer,
// master.RX should hold s2m, slave.RX should hold m2s.
// ---------------------------------------------------------------------------
static void test_one_byte(Vspi_top_tb* dut, int cpol, int cpha,
                          uint8_t m2s, uint8_t s2m, const char* tag) {
    reset(dut);
    cfg_master(dut, cpol, cpha, 0, 3, 0, 4, 4, 0);
    cfg_slave (dut, cpol, cpha, 0);

    // Pre-load slave TX with the response byte.
    push_tx(dut, s2m, /*master=*/false);
    push_tx(dut, m2s, /*master=*/true);

    start_xfer(dut);
    if (!wait_idle(dut)) {
        std::printf("  [FAIL] %s timed out\n", tag);
        g_failures++; return;
    }

    uint8_t m_got = pop_rx(dut, /*master=*/true);
    uint8_t s_got = pop_rx(dut, /*master=*/false);

    bool ok = (m_got == s2m) && (s_got == m2s);
    if (ok)
        std::printf("  [PASS] %s m_rx=0x%02x s_rx=0x%02x\n", tag, m_got, s_got);
    else {
        std::printf("  [FAIL] %s m_rx=0x%02x exp=0x%02x  s_rx=0x%02x exp=0x%02x\n",
                    tag, m_got, s2m, s_got, m2s);
        g_failures++;
    }
}

static void test_modes(Vspi_top_tb* dut) {
    std::printf("---- Test 1: single-byte loopback in modes 0/1/2/3 ----\n");
    test_one_byte(dut, 0, 0, 0xA5, 0x5A, "mode0");
    test_one_byte(dut, 0, 1, 0x3C, 0xC3, "mode1");
    test_one_byte(dut, 1, 0, 0x55, 0xAA, "mode2");
    test_one_byte(dut, 1, 1, 0xF0, 0x0F, "mode3");
}

// ---------------------------------------------------------------------------
// Test 2: MSB-first vs LSB-first (mode 0).
// ---------------------------------------------------------------------------
static void test_endian(Vspi_top_tb* dut) {
    std::printf("---- Test 2: MSB-first / LSB-first (mode 0) ----\n");
    // MSB-first done in test 1 implicitly. Here run LSB-first.
    reset(dut);
    cfg_master(dut, 0, 0, /*lsb_first=*/1, 3, 0, 4, 4, 0);
    cfg_slave (dut, 0, 0, /*lsb_first=*/1);
    push_tx(dut, 0xC3, /*master=*/false);
    push_tx(dut, 0x96, /*master=*/true);
    start_xfer(dut);
    if (!wait_idle(dut)) { std::printf("  [FAIL] LSB-first timeout\n"); g_failures++; return; }
    uint8_t m = pop_rx(dut, true);
    uint8_t s = pop_rx(dut, false);
    bool ok = (m == 0xC3) && (s == 0x96);
    if (ok) std::printf("  [PASS] LSB-first m_rx=0x%02x s_rx=0x%02x\n", m, s);
    else    { std::printf("  [FAIL] LSB-first m=0x%02x s=0x%02x\n", m, s); g_failures++; }
}

// ---------------------------------------------------------------------------
// Test 3: clock divisor sweep (slow / mid / fast SCK).
// ---------------------------------------------------------------------------
static void test_clk_div(Vspi_top_tb* dut) {
    std::printf("---- Test 3: SCK divisor sweep ----\n");
    int divs[] = {1, 4, 49};   // ~25/10/1 MHz from 100MHz clk
    const char* names[] = {"~25MHz", "~10MHz", "~1MHz"};
    for (int i = 0; i < 3; i++) {
        reset(dut);
        cfg_master(dut, 0, 0, 0, divs[i], 0, 4, 4, 0);
        cfg_slave (dut, 0, 0, 0);
        push_tx(dut, 0x12, false);
        push_tx(dut, 0x34, true);
        start_xfer(dut);
        if (!wait_idle(dut, 500000)) {
            std::printf("  [FAIL] div=%d (%s) timeout\n", divs[i], names[i]);
            g_failures++; continue;
        }
        uint8_t m = pop_rx(dut, true);
        uint8_t s = pop_rx(dut, false);
        bool ok = (m == 0x12) && (s == 0x34);
        if (ok) std::printf("  [PASS] div=%d (%s) m=0x%02x s=0x%02x\n",
                            divs[i], names[i], m, s);
        else    { std::printf("  [FAIL] div=%d m=0x%02x s=0x%02x\n",
                              divs[i], m, s); g_failures++; }
    }
}

// ---------------------------------------------------------------------------
// Test 4: CS setup/hold timing — verify CS asserts before the first SCK
// edge by measuring cycles between cs_n[0] going low and the first SCK
// transition.
// ---------------------------------------------------------------------------
static void test_cs_timing(Vspi_top_tb* dut) {
    std::printf("---- Test 4: CS setup/hold timing ----\n");
    int setup = 6, hold = 6;
    reset(dut);
    cfg_master(dut, 0, 0, 0, 3, 0, setup, hold, 0);
    cfg_slave (dut, 0, 0, 0);
    push_tx(dut, 0x55, false);
    push_tx(dut, 0xAA, true);
    start_xfer(dut);

    // Watch for cs falling, then count cycles until first SCK edge.
    int cycles_until_cs_low = 0;
    while ((dut->cs_n_o & 1) && cycles_until_cs_low < 200) {
        tick(dut); cycles_until_cs_low++;
    }
    if (dut->cs_n_o & 1) { std::printf("  [FAIL] CS never asserted\n"); g_failures++; return; }
    int cs_low_time = (int)g_time;
    int cycles_until_sck_edge = 0;
    int prev_sck = dut->sck_o;
    while (dut->sck_o == prev_sck && cycles_until_sck_edge < 500) {
        tick(dut); cycles_until_sck_edge++;
    }
    int setup_observed = cycles_until_sck_edge;

    if (!wait_idle(dut)) { std::printf("  [FAIL] xfer never completed\n"); g_failures++; return; }
    uint8_t m = pop_rx(dut, true);
    uint8_t s = pop_rx(dut, false);
    bool ok = (m == 0x55) && (s == 0xAA);

    if (ok && setup_observed >= setup)
        std::printf("  [PASS] setup observed %d >= %d, hold programmed %d, m=0x%02x s=0x%02x\n",
                    setup_observed, setup, hold, m, s);
    else {
        std::printf("  [FAIL] setup obs=%d (need>=%d), m=0x%02x s=0x%02x\n",
                    setup_observed, setup, m, s);
        g_failures++;
    }
    (void)cs_low_time;
}

// ---------------------------------------------------------------------------
// Test 5: multi-byte loopback (mode 0).
// ---------------------------------------------------------------------------
static void test_multi_byte(Vspi_top_tb* dut) {
    std::printf("---- Test 5: multi-byte loopback (4 bytes, mode 0) ----\n");
    reset(dut);
    cfg_master(dut, 0, 0, 0, 3, 0, 4, 4, /*inter_byte=*/1);
    cfg_slave (dut, 0, 0, 0);
    uint8_t m_in[4] = {0x11, 0x22, 0x33, 0x44};
    uint8_t s_in[4] = {0xAA, 0xBB, 0xCC, 0xDD};
    for (int i = 0; i < 4; i++) push_tx(dut, s_in[i], false);
    for (int i = 0; i < 4; i++) push_tx(dut, m_in[i], true);
    start_xfer(dut);
    if (!wait_idle(dut, 500000)) {
        std::printf("  [FAIL] multi-byte timeout\n"); g_failures++; return;
    }
    int m_match = 0, s_match = 0;
    for (int i = 0; i < 4; i++) {
        uint8_t m = pop_rx(dut, true);
        uint8_t s = pop_rx(dut, false);
        if (m == s_in[i]) m_match++;
        if (s == m_in[i]) s_match++;
    }
    bool ok = (m_match == 4) && (s_match == 4);
    if (ok) std::printf("  [PASS] multi-byte 4/4 match each side\n");
    else    { std::printf("  [FAIL] multi-byte m_match=%d s_match=%d\n", m_match, s_match); g_failures++; }
}

// ---------------------------------------------------------------------------
// Test 6: mid-transfer reset recovery.
// ---------------------------------------------------------------------------
static void test_mid_reset(Vspi_top_tb* dut) {
    std::printf("---- Test 6: mid-transfer reset recovery ----\n");
    reset(dut);
    cfg_master(dut, 0, 0, 0, 5, 0, 4, 4, 1);
    cfg_slave (dut, 0, 0, 0);
    push_tx(dut, 0x42, false);
    push_tx(dut, 0x84, false);
    push_tx(dut, 0xAB, true);
    push_tx(dut, 0xCD, true);
    start_xfer(dut);
    // Run ~30 cycles, then assert reset.
    for (int i = 0; i < 30; i++) tick(dut);
    dut->rst_ni = 0;
    for (int i = 0; i < 8; i++) tick(dut);
    dut->rst_ni = 1;
    tick(dut);

    // Now run a fresh transfer.
    cfg_master(dut, 0, 0, 0, 3, 0, 4, 4, 0);
    cfg_slave (dut, 0, 0, 0);
    push_tx(dut, 0x99, false);
    push_tx(dut, 0x66, true);
    start_xfer(dut);
    if (!wait_idle(dut, 500000)) {
        std::printf("  [FAIL] post-reset xfer timeout\n"); g_failures++; return;
    }
    uint8_t m = pop_rx(dut, true);
    uint8_t s = pop_rx(dut, false);
    bool ok = (m == 0x99) && (s == 0x66);
    if (ok) std::printf("  [PASS] post-reset m=0x%02x s=0x%02x\n", m, s);
    else    { std::printf("  [FAIL] post-reset m=0x%02x s=0x%02x\n", m, s); g_failures++; }
}

// ---------------------------------------------------------------------------
// Test 7: 1000 random bytes loopback round-trip in mode 0.
// ---------------------------------------------------------------------------
static void test_random_1000(Vspi_top_tb* dut) {
    std::printf("---- Test 7: 1000 random bytes loopback (mode 0) ----\n");
    reset(dut);
    cfg_master(dut, 0, 0, 0, 1, 0, 2, 2, 1);
    cfg_slave (dut, 0, 0, 0);
    std::mt19937 rng(0xC0DEFEEDu);
    std::uniform_int_distribution<int> u(0, 255);

    int passes = 0;
    int fails = 0;
    // 1000 bytes / 16-FIFO = 63 chunks of <= 16
    int total = 1000;
    int chunk = 12;
    int sent = 0;
    while (sent < total) {
        int n = std::min(chunk, total - sent);
        std::vector<uint8_t> m_data(n), s_data(n);
        for (int i = 0; i < n; i++) { m_data[i] = u(rng); s_data[i] = u(rng); }
        for (int i = 0; i < n; i++) push_tx(dut, s_data[i], false);
        for (int i = 0; i < n; i++) push_tx(dut, m_data[i], true);
        start_xfer(dut);
        if (!wait_idle(dut, 500000)) {
            std::printf("  [FAIL] random chunk timeout at sent=%d\n", sent);
            g_failures++; return;
        }
        for (int i = 0; i < n; i++) {
            uint8_t m = pop_rx(dut, true);
            uint8_t s = pop_rx(dut, false);
            if (m == s_data[i]) passes++;
            else { if (fails < 5)
                     std::printf("  [FAIL] m@%d got 0x%02x exp 0x%02x\n", sent+i, m, s_data[i]);
                   fails++; }
            if (s == m_data[i]) passes++;
            else { if (fails < 5)
                     std::printf("  [FAIL] s@%d got 0x%02x exp 0x%02x\n", sent+i, s, m_data[i]);
                   fails++; }
        }
        sent += n;
    }
    std::printf("  random: %d/%d byte-comparisons pass, %d fail\n",
                passes, total*2, fails);
    if (fails > 0) g_failures += fails;
}

// ---------------------------------------------------------------------------
// Coverage report
// ---------------------------------------------------------------------------
static void report_coverage(const Vspi_top_tb* dut) {
    // Bins come through the cov_bins_o port (bit order in tb/spi_cov.sv), so
    // the harness does not depend on Verilator's generated class names.
    const uint32_t cov = dut->cov_bins_o;
    auto bin = [cov](int bit) { return static_cast<uint8_t>((cov >> bit) & 1u); };
    struct Bin { const char* name; uint8_t hit; };
    Bin bins[] = {
        {"mode0",            bin(0)},
        {"mode1",            bin(1)},
        {"mode2",            bin(2)},
        {"mode3",            bin(3)},
        {"msb_first",        bin(4)},
        {"lsb_first",        bin(5)},
        {"tx_full",          bin(6)},
        {"tx_empty",         bin(7)},
        {"rx_full",          bin(8)},
        {"rx_empty",         bin(9)},
        {"slave_responded",  bin(10)},
        {"cs1_used",         bin(11)},
    };
    int total = sizeof(bins) / sizeof(bins[0]);
    int hit = 0;
    std::printf("\n---- Functional coverage ----\n");
    for (int i = 0; i < total; i++) {
        std::printf("  [%s] %-18s\n", bins[i].hit ? "HIT " : "MISS",
                    bins[i].name);
        if (bins[i].hit) hit++;
    }
    std::printf("Coverage: %d/%d bins (%.1f%%)\n",
                hit, total, 100.0 * hit / total);
    if (hit < total) g_failures++;
}

// ---------------------------------------------------------------------------
// Test 8: CS1 (alt chip-select line) usage.
// ---------------------------------------------------------------------------
static void test_cs1(Vspi_top_tb* dut) {
    std::printf("---- Test 8: alt chip-select (cs_sel=1) ----\n");
    reset(dut);
    cfg_master(dut, 0, 0, 0, 3, /*cs_sel=*/1, 4, 4, 0);
    // Slave is wired to cs_n[0] only — but we still want to exercise
    // the master driving cs_n[1] low. Verify by inspection: after
    // start, cs_n[1] goes low for the duration.
    push_tx(dut, 0x77, /*master=*/true);
    start_xfer(dut);
    bool cs1_low_seen = false;
    for (int i = 0; i < 5000; i++) {
        if (!(dut->cs_n_o & 2)) cs1_low_seen = true;   // cs_n[1] is bit 1
        tick(dut);
        if (!dut->m_busy_o && (dut->cs_n_o & 0xF) == 0xF) break;
    }
    if (cs1_low_seen) std::printf("  [PASS] cs_n[1] asserted during transfer\n");
    else              { std::printf("  [FAIL] cs_n[1] never asserted\n"); g_failures++; }
}

static bool check(bool ok, const char* what) {
    if (!ok) { std::printf("  [FAIL] %s\n", what); g_failures++; }
    return ok;
}

// ---------------------------------------------------------------------------
// Test 9: CPHA=1, words whose last bit on the wire is 1 or 0. The master
// must hold MOSI after its final sampling edge; the slave must answer
// with every bit of its own word.
// ---------------------------------------------------------------------------
static void test_cpha1_last_bit(Vspi_top_tb* dut) {
    std::printf("---- Test 9: CPHA=1 last bit (modes 1/3, MSB/LSB first, div 1/3) ----\n");
    const uint8_t words[] = {0x01, 0x3D, 0xFF, 0x81, 0x80, 0xF1, 0x7F, 0x3C};
    int checked = 0, fails = 0;
    for (int cpol = 0; cpol < 2; cpol++)
        for (int lsb = 0; lsb < 2; lsb++)
            for (int div : {1, 3})
                for (uint8_t w : words) {
                    reset(dut);
                    cfg_master(dut, cpol, 1, lsb, div, 0, 4, 4, 0);
                    cfg_slave (dut, cpol, 1, lsb);
                    const uint8_t resp = static_cast<uint8_t>(~w);
                    push_tx(dut, resp, false);
                    push_tx(dut, w, true);
                    start_xfer(dut);
                    if (!check(wait_idle(dut), "CPHA=1 transfer timed out")) return;
                    const uint8_t m = pop_rx(dut, true);
                    const uint8_t s = pop_rx(dut, false);
                    checked++;
                    if (m != resp || s != w) {
                        if (fails < 5)
                            std::printf("  [FAIL] mode%d lsb%d div%d sent 0x%02x/0x%02x got m=0x%02x s=0x%02x\n",
                                        2 * cpol + 1, lsb, div, w, resp, m, s);
                        fails++;
                    }
                }
    if (fails == 0) std::printf("  [PASS] %d CPHA=1 words, both directions\n", checked);
    g_failures += fails;
}

// ---------------------------------------------------------------------------
// Test 10: inter_byte=0, six words queued on both sides before the first
// start: one word per CS frame, and the slave TX count drops by exactly
// one per frame.
// ---------------------------------------------------------------------------
static void test_word_per_frame(Vspi_top_tb* dut) {
    std::printf("---- Test 10: one word per CS frame, 6 words queued on both sides ----\n");
    const int n = 6;
    const int f0 = g_failures;
    for (int mode = 0; mode < 4; mode++) {
        const int lsb = mode & 1;
        reset(dut);
        cfg_master(dut, mode >> 1, mode & 1, lsb, 2, 0, 3, 3, /*inter_byte=*/0);
        cfg_slave (dut, mode >> 1, mode & 1, lsb);
        uint8_t m_in[n], s_in[n];
        for (int i = 0; i < n; i++) {
            m_in[i] = static_cast<uint8_t>(0x11 * (i + 1) + mode);
            s_in[i] = static_cast<uint8_t>(0xA0 + 0x0B * i + mode);
        }
        for (int i = 0; i < n; i++) push_tx(dut, s_in[i], false);
        for (int i = 0; i < n; i++) push_tx(dut, m_in[i], true);
        for (int k = 0; k < n; k++) {
            start_xfer(dut);
            if (!check(wait_idle(dut), "frame timed out")) return;
            if (dut->s_tx_count_o != static_cast<unsigned>(n - 1 - k)) {
                std::printf("  [FAIL] mode%d: slave TX count %d after frame %d (expected %d)\n",
                            mode, dut->s_tx_count_o, k, n - 1 - k);
                g_failures++;
            }
        }
        for (int i = 0; i < n; i++) {
            const uint8_t m = pop_rx(dut, true);
            const uint8_t s = pop_rx(dut, false);
            if (m != s_in[i] || s != m_in[i]) {
                std::printf("  [FAIL] mode%d word %d: m=0x%02x exp 0x%02x  s=0x%02x exp 0x%02x\n",
                            mode, i, m, s_in[i], s, m_in[i]);
                g_failures++;
            }
        }
    }
    if (g_failures == f0) std::printf("  [PASS] 4 modes x %d frames, every queued word sent once\n", n);
}

// ---------------------------------------------------------------------------
// Test 11: random loopback over all modes, bit orders, dividers, CS
// setup/hold, single-word frames and bursts.
// ---------------------------------------------------------------------------
static void test_random_all_modes(Vspi_top_tb* dut, int total) {
    std::printf("---- Test 11: %d random words, all modes, MSB/LSB first, div 1..7 ----\n", total);
    reset(dut);
    std::mt19937 rng(0x5EED0011u);
    auto pick = [&rng](int lo, int hi) { return std::uniform_int_distribution<int>(lo, hi)(rng); };
    const int divs[] = {1, 1, 2, 3, 5, 7};
    int sent = 0, fails = 0, frames = 0;
    while (sent < total && fails < 20) {
        const int mode = pick(0, 3), lsb = pick(0, 1), div = divs[pick(0, 5)];
        const int setup = pick(0, 7), hold = pick(0, 7), inter = pick(0, 1);
        const int n = inter ? pick(1, 8) : pick(1, 3);
        cfg_master(dut, mode >> 1, mode & 1, lsb, div, 0, setup, hold, inter);
        cfg_slave (dut, mode >> 1, mode & 1, lsb);
        tick(dut); tick(dut);
        std::vector<uint8_t> m_in(n), s_in(n);
        for (int i = 0; i < n; i++) { m_in[i] = pick(0, 255); s_in[i] = pick(0, 255); }
        for (int i = 0; i < n; i++) push_tx(dut, s_in[i], false);
        for (int i = 0; i < n; i++) push_tx(dut, m_in[i], true);
        for (int k = 0; k < (inter ? 1 : n); k++) {
            start_xfer(dut);
            if (!check(wait_idle(dut), "random transfer timed out")) return;
            frames++;
        }
        for (int i = 0; i < n; i++) {
            const uint8_t m = pop_rx(dut, true);
            const uint8_t s = pop_rx(dut, false);
            if (m != s_in[i] || s != m_in[i]) {
                if (fails < 5)
                    std::printf("  [FAIL] word %d mode%d lsb%d div%d setup%d hold%d inter%d: "
                                "m=0x%02x exp 0x%02x  s=0x%02x exp 0x%02x\n",
                                sent + i, mode, lsb, div, setup, hold, inter, m, s_in[i], s, m_in[i]);
                fails++;
            }
        }
        sent += n;
    }
    std::printf("  random: %d words in %d CS frames, %d mismatches\n", sent, frames, fails);
    g_failures += fails;
}

// ---------------------------------------------------------------------------
// Test 12: fill both TX FIFOs, receive until both RX FIFOs are full (no
// loss, no overrun), then one more word each way (dropped on both sides,
// one overrun strobe each), then a frame with the slave TX FIFO empty
// (zeros on MISO, one underrun strobe).
// ---------------------------------------------------------------------------
static void test_full_and_overrun(Vspi_top_tb* dut) {
    std::printf("---- Test 12: full FIFOs, RX overrun, slave TX underrun ----\n");
    const int depth = 16;
    const int f0 = g_failures;
    reset(dut);
    cfg_master(dut, 0, 0, 0, 1, 0, 2, 2, /*inter_byte=*/1);
    cfg_slave (dut, 0, 0, 0);
    for (int i = 0; i < depth; i++) push_tx(dut, static_cast<uint8_t>(0x40 + i), false);
    check(dut->s_tx_full_o, "slave TX FIFO full after 16 writes");
    for (int i = 0; i < depth; i++) push_tx(dut, static_cast<uint8_t>(0x80 + i), true);
    check(dut->m_tx_full_o, "master TX FIFO full after 16 writes");
    g_mon.irq_err = g_mon.s_rx_overrun = g_mon.s_tx_underrun = 0;
    start_xfer(dut);
    check(wait_idle(dut), "16-word burst completes");
    check(dut->m_rx_full_o && dut->m_rx_count_o == depth, "master RX FIFO full");
    check(dut->s_rx_full_o && dut->s_rx_count_o == depth, "slave RX FIFO full");
    check(g_mon.irq_err == 0, "no irq_err_o while nothing is lost");
    check(g_mon.s_rx_overrun == 0, "no s_rx_overrun_o while nothing is lost");
    check(g_mon.s_tx_underrun == 0, "no s_tx_underrun_o with 16 queued words");

    push_tx(dut, 0xEE, false);
    push_tx(dut, 0x77, true);
    start_xfer(dut);
    check(wait_idle(dut), "transfer into full RX FIFOs completes");
    check(g_mon.irq_err == 1, "one irq_err_o strobe for the word dropped by the master");
    check(g_mon.s_rx_overrun == 1, "one s_rx_overrun_o strobe for the word dropped by the slave");
    check(dut->m_rx_count_o == depth && dut->s_rx_count_o == depth, "RX counts stay at the depth");
    bool kept = true;
    for (int i = 0; i < depth; i++) {
        kept = kept && pop_rx(dut, true) == static_cast<uint8_t>(0x40 + i);
        kept = kept && pop_rx(dut, false) == static_cast<uint8_t>(0x80 + i);
    }
    check(kept, "both RX FIFOs keep the first 16 words");

    cfg_master(dut, 0, 0, 0, 1, 0, 2, 2, 0);
    push_tx(dut, 0x5A, true);
    start_xfer(dut);
    check(wait_idle(dut), "transfer with an empty slave TX FIFO completes");
    check(g_mon.s_tx_underrun == 1, "one s_tx_underrun_o strobe");
    check(pop_rx(dut, true) == 0x00, "master receives zeros from an empty slave TX FIFO");
    check(pop_rx(dut, false) == 0x5A, "slave still receives the word");
    if (g_failures == f0) std::printf("  [PASS] full flags, overrun and underrun strobes\n");
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    setvbuf(stdout, nullptr, _IOLBF, 0);
    Vspi_top_tb* dut = new Vspi_top_tb();

    test_modes(dut);
    test_endian(dut);
    test_clk_div(dut);
    test_cs_timing(dut);
    test_multi_byte(dut);
    test_mid_reset(dut);
    test_cs1(dut);
    test_random_1000(dut);
    test_cpha1_last_bit(dut);
    test_word_per_frame(dut);
    test_random_all_modes(dut, 3000);
    test_full_and_overrun(dut);

    std::printf("\n---- Per-cycle checks ----\n");
    if (g_mon.miso_oe_mismatch == 0) std::printf("  [PASS] miso_oe_o == !cs_n_o[0] on every cycle\n");
    else { std::printf("  [FAIL] miso_oe_o != !cs_n_o[0] on %d cycles\n", g_mon.miso_oe_mismatch); g_failures++; }

    report_coverage(dut);

    if (g_failures == 0) {
        std::printf("+PASS all tests passed\n");
        delete dut;
        return 0;
    } else {
        std::printf("+FAIL %d failures\n", g_failures);
        delete dut;
        return 1;
    }
}
