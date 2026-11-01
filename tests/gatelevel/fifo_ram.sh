#!/usr/bin/env bash
# spi_fifo against its iCE40 netlist.
#
# usage: tests/gatelevel/fifo_ram.sh
#
# Yosys maps spi_fifo with synth_ice40 (DEPTH 16 and 32 use SB_RAM40_4K
# block RAM, DEPTH 5 logic) in both read modes; Icarus simulates the RTL and
# the netlist side by side with the Yosys iCE40 cell models and compares
# every output on every cycle (tb_fifo_ram.sv), so a read and a write of
# the same RAM address on a full FIFO must behave as in the RTL. Outputs go
# to tests/gatelevel/build/fifo_ram/. Exit status is non-zero on any
# mismatch.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RTL=${RTL:-$HERE/../../rtl}
OUT=${GL_OUT:-$HERE/build}/fifo_ram
YOSYS=${YOSYS:-yosys}
IVERILOG=${IVERILOG:-iverilog}
VVP=${VVP:-vvp}
CELLS=$("${YOSYS}-config" --datdir)/ice40/cells_sim.v

rm -rf "$OUT"
mkdir -p "$OUT"
fails=0
for cfg in "16 0" "16 1" "32 0" "32 1" "5 0" "5 1"; do
  set -- $cfg
  tag=depth$1_fwft$2
  if ! "$YOSYS" -q -l "$OUT/yosys_$tag.log" -p "read_verilog -sv $RTL/spi_fifo.sv; \
      chparam -set WIDTH 8 -set DEPTH $1 -set FWFT $2 spi_fifo; synth_ice40 -top spi_fifo; \
      rename spi_fifo spi_fifo_gl; tee -q -o $OUT/stat_$tag.txt stat; write_verilog -noattr $OUT/gl_$tag.v"; then
    echo "FAIL $tag: synthesis"; fails=$((fails + 1)); continue
  fi
  ram=$(awk '/SB_RAM40_4K/ {print $1; exit}' "$OUT/stat_$tag.txt"); ram=${ram:-0}
  if ! "$IVERILOG" -g2012 -o "$OUT/sim_$tag.vvp" -s tb_fifo_ram -Ptb_fifo_ram.DEPTH="$1" -Ptb_fifo_ram.FWFT="$2" \
      -DNO_ICE40_DEFAULT_ASSIGNMENTS "$CELLS" "$OUT/gl_$tag.v" "$RTL/spi_fifo.sv" "$HERE/tb_fifo_ram.sv" \
      > "$OUT/iverilog_$tag.log" 2>&1; then
    echo "FAIL $tag: compile, see $OUT/iverilog_$tag.log"; fails=$((fails + 1)); continue
  fi
  "$VVP" -n "$OUT/sim_$tag.vvp" > "$OUT/sim_$tag.log" 2>&1
  if grep -q " 0 mismatches" "$OUT/sim_$tag.log"; then st=PASS; else st=FAIL; fails=$((fails + 1)); fi
  echo "$st $tag ($ram SB_RAM40_4K instances): $(grep -E 'fifo DEPTH' "$OUT/sim_$tag.log")"
done
[ $fails -eq 0 ]
