# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-Commercial
# Out-of-context Vivado compile of one top level (synthesis, placement,
# routing), as used for RESOURCE_ESTIMATES.md.
#
# Run from an empty directory:
#   vivado -mode batch -source <repo>/scripts/vivado_compile.tcl \
#          -tclargs <repo>/rtl <top> [part] [period_ns] [clock_port]
# The clock port defaults to clk_i; spi_axi4lite uses s_axi_aclk.
# Reports: <top>_impl_util.rpt and <top>_timing.rpt; the last line printed
# is "RESULT ... wns=<ns> fmax_mhz=<MHz>", with Fmax = 1000 / (period - WNS).
lassign $argv rtl top part period clk
if {$part eq ""} { set part xc7a35tcsg324-1 }
if {$period eq ""} { set period 3.0 }
if {$clk eq ""} { set clk clk_i }
read_verilog -sv [glob $rtl/*.sv]
set fh [open clk.xdc w]
puts $fh "create_clock -period $period -name clk \[get_ports $clk\]"
close $fh
read_xdc -mode out_of_context clk.xdc
synth_design -top $top -part $part -mode out_of_context
opt_design
place_design
route_design
report_utilization -file ${top}_impl_util.rpt
report_timing_summary -max_paths 3 -file ${top}_timing.rpt
set wns [get_property SLACK [get_timing_paths -max_paths 1 -setup]]
puts "RESULT top=$top part=$part period=$period wns=$wns fmax_mhz=[format %.2f [expr {1000.0 / ($period - $wns)}]]"
