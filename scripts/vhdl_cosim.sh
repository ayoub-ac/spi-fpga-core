#!/usr/bin/env bash
# Static check of the VHDL wrapper: Verilator lint of the SV core, GHDL
# analysis of the wrapper and, when Yosys has the ghdl plugin, a
# mixed-language elaboration that fails on any port width mismatch.
# This is not a simulation. Mixed-language simulation requires Vivado
# xsim, ModelSim or Questa.

set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v ghdl >/dev/null 2>&1; then
    echo "[vhdl-test] ghdl not installed - skipping"
    exit 0
fi
if ! command -v verilator >/dev/null 2>&1; then
    echo "[vhdl-test] verilator not installed"
    exit 1
fi

RTL="rtl/spi_fifo.sv rtl/spi_master.sv rtl/spi_slave.sv rtl/spi_top.sv"
WORK="build/ghdl"
mkdir -p "$WORK"

echo "[vhdl-test] Verilator lint of SV core..."
verilator --lint-only -Wall -Wno-UNUSEDPARAM -Wno-UNUSEDSIGNAL \
    -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC --top-module spi_top $RTL

echo "[vhdl-test] GHDL analyse of VHDL wrapper..."
ghdl -a --std=08 --workdir="$WORK" vhdl_wrapper/spi_top_vhdl.vhd

if command -v yosys >/dev/null 2>&1; then
    echo "[vhdl-test] Every spi_top port must appear in the wrapper..."
    yosys -q -p "read_verilog -sv $RTL; hierarchy -top spi_top; tee -q -o $WORK/spi_top_ports.txt select -list -module spi_top i:* o:*" >/dev/null
    missing=0
    for port in $(sed -n 's|^spi_top/||p' "$WORK/spi_top_ports.txt"); do
        if ! grep -qiE "^[[:space:]]*$port[[:space:]]*=>" vhdl_wrapper/spi_top_vhdl.vhd; then
            echo "[vhdl-test] FAIL: spi_top port $port is not mapped by the VHDL wrapper"
            missing=1
        fi
    done
    [ "$missing" -eq 0 ] || exit 1
fi

if command -v yosys >/dev/null 2>&1 && yosys -m ghdl -p "help ghdl" >/dev/null 2>&1; then
    for g in "" "-gNUM_CS=1" "-gNUM_CS=2" "-gNUM_CS=8" "-gFIFO_DEPTH=64" "-gDATA_WIDTH=16"; do
        echo "[vhdl-test] Mixed-language elaboration ${g:-(defaults)}..."
        log="$WORK/mixed${g//[^A-Za-z0-9_]/_}.log"
        yosys -m ghdl -l "$log" -p "ghdl --std=08 $g vhdl_wrapper/spi_top_vhdl.vhd -e spi_top_vhdl; \
            read_verilog -sv $RTL; hierarchy -check -top spi_top_vhdl" >/dev/null
        if grep -q "Resizing cell port" "$log"; then
            grep "Resizing cell port" "$log"
            echo "[vhdl-test] FAIL: VHDL wrapper ports do not match spi_top"
            exit 1
        fi
    done
else
    echo "[vhdl-test] yosys ghdl plugin not available - skipping mixed-language elaboration"
fi

echo "[vhdl-test] OK"
