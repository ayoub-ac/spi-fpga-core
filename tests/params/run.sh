#!/usr/bin/env bash
# Elaboration-time parameter checks.
#
# usage: tests/params/run.sh
#
# Legal parameter sets must elaborate in Yosys, Verilator and Icarus.
# Illegal sets must stop all three tools, and for the intended reason: the
# check's $error message (Yosys stops there) or the reference to the
# <module>_illegal_parameter_* module that the RTL instantiates when a
# check fails (Verilator, Icarus). Logs go to tests/params/build/.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RTL=${RTL:-$HERE/../../rtl}
OUT=${PARAMS_OUT:-$HERE/build}
YOSYS=${YOSYS:-yosys}
VERILATOR=${VERILATOR:-verilator}
IVERILOG=${IVERILOG:-iverilog}
REASON='illegal_parameter|ERROR: ?(spi_fifo|spi_master|spi_slave|spi_axi4lite): '

FIFO="$RTL/spi_fifo.sv"
MASTER="$RTL/spi_fifo.sv $RTL/spi_master.sv"
SLAVE="$RTL/spi_fifo.sv $RTL/spi_slave.sv"
TOP="$RTL/spi_fifo.sv $RTL/spi_master.sv $RTL/spi_slave.sv $RTL/spi_top.sv"
AXI="$RTL/spi_fifo.sv $RTL/spi_master.sv $RTL/axi4lite_regfile.sv $RTL/axi4lite_slave.sv $RTL/spi_axi4lite.sv"

rm -rf "$OUT"
mkdir -p "$OUT"
total=0
bad=0

# check ok|fail TOP "FILES" [PARAM=VALUE ...]
check() {
  local expect=$1 top=$2 files=$3
  shift 3
  local ch="" gv="" pv="" kv
  for kv in "$@"; do
    ch="$ch -chparam ${kv%%=*} ${kv#*=}"
    gv="$gv -G$kv"
    pv="$pv -P$top.$kv"
  done
  local tag
  tag="$top$(printf '_%s' "$@" | tr '=' '-')"
  local r=() tool log
  "$YOSYS" -q -p "read_verilog -sv $files; hierarchy -check -top $top $ch; proc" > "$OUT/$tag.yosys.log" 2>&1
  r+=($?)
  # shellcheck disable=SC2086
  "$VERILATOR" --lint-only -Wno-fatal $gv --top-module "$top" $files > "$OUT/$tag.verilator.log" 2>&1
  r+=($?)
  # shellcheck disable=SC2086
  "$IVERILOG" -g2012 -s "$top" $pv -o "$OUT/$tag.vvp" $files > "$OUT/$tag.iverilog.log" 2>&1
  r+=($?)
  local verdict=ok i=0
  for tool in yosys verilator iverilog; do
    log=$OUT/$tag.$tool.log
    if [ "$expect" = ok ]; then
      [ "${r[$i]}" -eq 0 ] || verdict="FAIL ($tool rejected a legal set, see $log)"
    else
      if [ "${r[$i]}" -eq 0 ]; then
        verdict="FAIL ($tool accepted an illegal set)"
      elif ! grep -qE "$REASON" "$log"; then
        verdict="FAIL ($tool failed for another reason, see $log)"
      fi
    fi
    i=$((i + 1))
  done
  total=$((total + 1))
  [ "$verdict" = ok ] || bad=$((bad + 1))
  printf '%-4s %-13s %-40s %s\n' "$expect" "$top" "${*:-defaults}" "$verdict"
}

check ok   spi_fifo     "$FIFO"   DEPTH=1
check ok   spi_fifo     "$FIFO"   DEPTH=3 FWFT=1
check ok   spi_fifo     "$FIFO"   WIDTH=1 DEPTH=16
check fail spi_fifo     "$FIFO"   DEPTH=0
check fail spi_fifo     "$FIFO"   WIDTH=0

check ok   spi_master   "$MASTER" DATA_WIDTH=2
check ok   spi_master   "$MASTER" DATA_WIDTH=64
check ok   spi_master   "$MASTER" NUM_CS=1 FIFO_DEPTH=1
check ok   spi_master   "$MASTER" DIV_WIDTH=1 CS_DLY_W=1
check fail spi_master   "$MASTER" DATA_WIDTH=1
check fail spi_master   "$MASTER" NUM_CS=0
check fail spi_master   "$MASTER" DIV_WIDTH=0
check fail spi_master   "$MASTER" CS_DLY_W=0
check fail spi_master   "$MASTER" FIFO_DEPTH=0

check ok   spi_slave    "$SLAVE"  DATA_WIDTH=2 FIFO_DEPTH=1
check ok   spi_slave    "$SLAVE"  DATA_WIDTH=64
check fail spi_slave    "$SLAVE"  DATA_WIDTH=1
check fail spi_slave    "$SLAVE"  FIFO_DEPTH=0

check ok   spi_top      "$TOP"    NUM_CS=1 FIFO_DEPTH=5

check ok   spi_axi4lite "$AXI"
check ok   spi_axi4lite "$AXI"    DATA_WIDTH=64
check ok   spi_axi4lite "$AXI"    NUM_CS=1 SPI_DATA_W=32 FIFO_DEPTH=1
check ok   spi_axi4lite "$AXI"    CS_DLY_W=1 DIV_WIDTH=32
check ok   spi_axi4lite "$AXI"    DATA_WIDTH=64 SPI_DATA_W=64 DIV_WIDTH=64
check fail spi_axi4lite "$AXI"    DATA_WIDTH=16
check fail spi_axi4lite "$AXI"    NUM_REGS=12
check fail spi_axi4lite "$AXI"    ADDR_WIDTH=5
check fail spi_axi4lite "$AXI"    SPI_DATA_W=40
check fail spi_axi4lite "$AXI"    DIV_WIDTH=33
check fail spi_axi4lite "$AXI"    CS_DLY_W=16
check fail spi_axi4lite "$AXI"    NUM_CS=300
check fail spi_axi4lite "$AXI"    FIFO_DEPTH=70000

echo "params: $((total - bad))/$total checks passed"
[ $bad -eq 0 ]
