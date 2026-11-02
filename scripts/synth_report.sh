#!/usr/bin/env bash
# Cross-toolchain synthesis report. Runs every installed FPGA toolchain
# against spi_top (loopback: master + slave + 4 FIFOs) and spi_axi4lite
# (AXI4-Lite wrapper + master) and writes SYNTH_REPORT.md.
#
# Yosys numbers are post-synthesis, pre-place-and-route. Fmax is not
# reported here; it needs place-and-route (see RESOURCE_ESTIMATES.md).

set -uo pipefail
cd "$(dirname "$0")/.."

OUTDIR="synth_report"
mkdir -p "$OUTDIR"

REPORT="SYNTH_REPORT.md"
RTL_CORE="rtl/spi_fifo.sv rtl/spi_master.sv rtl/spi_slave.sv rtl/spi_top.sv"
RTL_AXI="$RTL_CORE rtl/axi4lite_regfile.sv rtl/axi4lite_slave.sv rtl/spi_axi4lite.sv"

have() { command -v "$1" >/dev/null 2>&1; }

yosys_run() {
    local top="$1" rtl="$2" yopts="$3" outfile="$4"
    have yosys || { echo "yosys not installed" > "$outfile"; return 1; }
    yosys -p "read_verilog -sv $rtl; hierarchy -check -top $top; $yopts; stat" \
          > "$outfile" 2>&1
}

# Sum the counts of every cell type matching the anchored regex $2 in the
# last stat block of log $1. Accepts both Yosys stat layouts
# ("SB_LUT4  417" and "417  SB_LUT4"). The design must be flattened so
# that each cell is counted once.
count_cells() {
    awk -v re="^($2)\$" '
        /Printing statistics/ { s = 0 }
        {
            hit = 0
            for (i = 1; i <= NF; i++) if ($i ~ re) hit = 1
            if (hit) for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+$/) { s += $i; break }
        }
        END { print s + 0 }
    ' "$1"
}

# $1 log, $2 LUT re, $3 FF re, $4 LUTRAM re, $5 BRAM re
summarize() {
    local f="$1"
    if [[ ! -s "$f" ]] || grep -q "yosys not installed" "$f"; then echo "(skipped)"; return; fi
    if ! grep -q "Printing statistics" "$f"; then echo "(failed - see $f)"; return; fi
    echo "$(count_cells "$f" "$2") LUT / $(count_cells "$f" "$3") FF / $(count_cells "$f" "$4") LUTRAM / $(count_cells "$f" "$5") BRAM"
}

ICE40_RE=("SB_LUT4" "SB_DFF[A-Z]*" "NONE" "SB_RAM40_4K")
ECP5_RE=("LUT4" "TRELLIS_FF" "TRELLIS_DPR16X4" "DP16KD")
XIL_RE=("LUT[1-6]|INV" "FD[CPRS]E" "RAM32M|RAM64M|RAM32X1D|RAM64X1D|RAM128X1D|SRL16E|SRLC32E" "RAMB18E1|RAMB36E1")

YOSYS_VER="(not installed)"
have yosys && YOSYS_VER=$(yosys -V | awk '{print $2}')
SUITE_FILE=""
have yosys && SUITE_FILE="$(dirname "$(readlink -f "$(command -v yosys)")")/../VERSION"
[[ -f $SUITE_FILE ]] && YOSYS_VER="$YOSYS_VER (OSS CAD Suite $(sed -E 's/^([0-9]{4})([0-9]{2})([0-9]{2})$/\1-\2-\3/' "$SUITE_FILE"))"

declare -A RES
for top in spi_top spi_axi4lite; do
    rtl="$RTL_CORE"; [[ $top == spi_axi4lite ]] && rtl="$RTL_AXI"
    echo "[synth_report] $top: Yosys ice40 / ecp5 / xilinx..."
    yosys_run "$top" "$rtl" "synth_ice40 -top $top"                "$OUTDIR/${top}_ice40.log"  || true
    yosys_run "$top" "$rtl" "synth_ecp5 -top $top -abc9"           "$OUTDIR/${top}_ecp5.log"   || true
    yosys_run "$top" "$rtl" "synth_xilinx -top $top -flatten"      "$OUTDIR/${top}_xilinx.log" || true
    RES[$top,ice40]=$(summarize  "$OUTDIR/${top}_ice40.log"  "${ICE40_RE[@]}")
    RES[$top,ecp5]=$(summarize   "$OUTDIR/${top}_ecp5.log"   "${ECP5_RE[@]}")
    RES[$top,xilinx]=$(summarize "$OUTDIR/${top}_xilinx.log" "${XIL_RE[@]}")
done

TOP="spi_top"
VIVADO_RESULT="(skipped: vivado not on PATH)"
if have vivado; then
    echo "[synth_report] Vivado synth_design Artix-7..."
    cat > "$OUTDIR/vivado_synth.tcl" <<EOF
set_part xc7a35tcsg324-1
read_verilog -sv {$RTL_CORE}
synth_design -top $TOP -part xc7a35tcsg324-1 -mode out_of_context
report_utilization -file $OUTDIR/vivado_util.rpt
EOF
    if vivado -mode batch -nojournal -nolog -source "$OUTDIR/vivado_synth.tcl" \
              > "$OUTDIR/vivado.log" 2>&1; then
        field() { awk -F'|' -v k="$1" '$2 ~ k {gsub(/ /,"",$3); print $3; exit}' "$OUTDIR/vivado_util.rpt"; }
        VIVADO_RESULT="$(field 'Slice LUTs') LUT / $(field 'Slice Registers') FF / $(field 'Block RAM Tile') BRAM tile"
    else
        VIVADO_RESULT="(failed - see $OUTDIR/vivado.log)"
    fi
fi

QUARTUS_RESULT="(skipped: quartus_sh not on PATH)"
if have quartus_sh; then
    echo "[synth_report] Quartus Prime (Standard/Lite) Cyclone V..."
    QPROJ="$OUTDIR/qproj"
    mkdir -p "$QPROJ"
    cat > "$QPROJ/${TOP}.qsf" <<EOF
set_global_assignment -name FAMILY "Cyclone V"
set_global_assignment -name DEVICE 5CSEMA5F31C6
set_global_assignment -name TOP_LEVEL_ENTITY $TOP
set_global_assignment -name SEARCH_PATH ../../rtl
set_global_assignment -name SYSTEMVERILOG_FILE ../../rtl/spi_fifo.sv
set_global_assignment -name SYSTEMVERILOG_FILE ../../rtl/spi_master.sv
set_global_assignment -name SYSTEMVERILOG_FILE ../../rtl/spi_slave.sv
set_global_assignment -name SYSTEMVERILOG_FILE ../../rtl/spi_top.sv
EOF
    echo "PROJECT_REVISION = \"$TOP\"" > "$QPROJ/${TOP}.qpf"
    if (cd "$QPROJ" && quartus_sh --flow compile $TOP > ../quartus.log 2>&1); then
        SUM="$QPROJ/output_files/${TOP}.fit.summary"
        QU_ALM=$(awk -F: '/Logic utilization/ {gsub(/^ +/,"",$2); print $2; exit}' "$SUM" 2>/dev/null)
        QU_FFS=$(awk -F: '/Total registers/   {gsub(/^ +/,"",$2); print $2; exit}' "$SUM" 2>/dev/null)
        QUARTUS_RESULT="${QU_ALM:-?} ALM / ${QU_FFS:-?} FF"
    else
        QUARTUS_RESULT="(failed - see $OUTDIR/quartus.log)"
    fi
fi

cat > "$REPORT" <<EOF
# Synthesis comparison report

Generated by \`make synth_report\` with Yosys ${YOSYS_VER}.
Numbers are post-synthesis, pre-place-and-route, default parameters
(DATA_WIDTH = 8, NUM_CS = 4, FIFO_DEPTH = 16, DIV_WIDTH = 16, CS_DLY_W = 8).
LUT counts exclude carry cells (SB_CARRY, CCU2C, CARRY4); the Xilinx LUT
count includes the INV cells (one LUT1 each). Place-and-route results
(iCE40, ECP5) are in RESOURCE_ESTIMATES.md (\`make pnr\`).

| Toolchain                  | Target                  | Design         | Result |
|----------------------------|-------------------------|----------------|--------|
| Yosys synth_ice40          | iCE40 (UP5K class)      | spi_top        | ${RES[spi_top,ice40]} |
| Yosys synth_ecp5 -abc9     | ECP5 (generic)          | spi_top        | ${RES[spi_top,ecp5]} |
| Yosys synth_xilinx         | Xilinx 7-series generic | spi_top        | ${RES[spi_top,xilinx]} |
| Yosys synth_ice40          | iCE40 (UP5K class)      | spi_axi4lite   | ${RES[spi_axi4lite,ice40]} |
| Yosys synth_ecp5 -abc9     | ECP5 (generic)          | spi_axi4lite   | ${RES[spi_axi4lite,ecp5]} |
| Yosys synth_xilinx         | Xilinx 7-series generic | spi_axi4lite   | ${RES[spi_axi4lite,xilinx]} |
| Vivado synth_design        | Artix-7 xc7a35tcsg324-1 | spi_top        | ${VIVADO_RESULT} |
| Quartus Prime Standard/Lite| Cyclone V 5CSEMA5F31C6  | spi_top        | ${QUARTUS_RESULT} |

## Notes

* \`spi_top\` is the verification loopback: master + slave + four
  16 x 8 FIFOs. \`spi_axi4lite\` contains the master only.
* Yosys infers the FIFO storage as memory: SB_RAM40_4K block RAM on
  iCE40, distributed RAM (TRELLIS_DPR16X4 / RAM32M) on ECP5 and Xilinx.
* The AXI4-Lite register file (16 x 32-bit, six scratch registers) is
  built from flip-flops and dominates the \`spi_axi4lite\` area.

## Reproduction

\`\`\`
make synth_report
\`\`\`

Raw logs land in \`synth_report/\`.
EOF

echo "[synth_report] Wrote $REPORT"
