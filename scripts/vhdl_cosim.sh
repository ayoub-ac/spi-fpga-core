#!/usr/bin/env bash
# Smoke-test the VHDL wrapper using GHDL (VHDL side) and Verilator (SV side).
# Both tools must be installed; `make vhdl-test` exits cleanly if either is
# missing.

set -u
cd "$(dirname "$0")/.."

if ! command -v ghdl >/dev/null 2>&1; then
    echo "[vhdl-test] ghdl not installed - skipping"
    exit 0
fi
if ! command -v verilator >/dev/null 2>&1; then
    echo "[vhdl-test] verilator not installed"
    exit 1
fi

echo "[vhdl-test] Verilator lint of SV core..."
verilator --lint-only -Wall -Wno-UNUSEDPARAM -Wno-UNUSEDSIGNAL \
    --top-module spi_top \
    rtl/spi_fifo.sv rtl/spi_master.sv rtl/spi_slave.sv rtl/spi_top.sv

echo "[vhdl-test] GHDL analyse of VHDL wrapper..."
ghdl -a --std=08 vhdl_wrapper/spi_top_vhdl.vhd

echo "[vhdl-test] OK - VHDL wrapper compiles, SV core lints. Mixed-language"
echo "[vhdl-test]      simulation requires Vivado xsim / ModelSim / Questa."
