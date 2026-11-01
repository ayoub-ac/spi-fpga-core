#!/usr/bin/env bash
# Gate-level glitch check of the spi_master outputs.
#
# usage: tests/gatelevel/run.sh
#
# Yosys maps spi_master (NUM_CS = 4) to generic gates; Icarus simulates the
# netlist with a random transport delay per gate (cells_delay.v) and
# tb_gl.v counts sub-2 ns pulses on every CS_n line and on MOSI. Outputs go
# to tests/gatelevel/build/. Exit status is non-zero on any glitch or hang.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RTL=${RTL:-$HERE/../../rtl}
OUT=${GL_OUT:-$HERE/build}
YOSYS=${YOSYS:-yosys}
IVERILOG=${IVERILOG:-iverilog}
VVP=${VVP:-vvp}

rm -rf "$OUT"
mkdir -p "$OUT"
"$YOSYS" -q -l "$OUT/yosys.log" -p "read_verilog -sv $RTL/spi_fifo.sv $RTL/spi_master.sv; \
    hierarchy -top spi_master -chparam NUM_CS 4; synth -flatten -top spi_master; \
    abc -g AND,NAND,OR,NOR,XOR,XNOR,ANDNOT,ORNOT,MUX; opt_clean -purge; stat; \
    write_verilog -noexpr -noattr $OUT/gl_master.v"
"$IVERILOG" -g2012 -o "$OUT/gl.vvp" -s tb_gl "$HERE/cells_delay.v" "$OUT/gl_master.v" "$HERE/tb_gl.v"
"$VVP" -n "$OUT/gl.vvp" | tee "$OUT/gl.log"
grep -q "GATE-LEVEL CHECK PASSED" "$OUT/gl.log"
