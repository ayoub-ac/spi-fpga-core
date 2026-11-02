# SPI FPGA core - build / lint / sim / synth
#
# Targets:
#   make lint            verilator --lint-only on RTL (fifo + master + slave +
#                        top + axi4lite_slave + axi4lite_regfile + spi_axi4lite)
#   make sim             build the spi_top and spi_axi4lite simulators
#   make test            build + run both simulators, check for "+PASS"
#   make synth           Yosys: ice40 + ecp5 + xilinx generic for spi_top (no P&R)
#   make synth_report    run all available toolchains and emit SYNTH_REPORT.md
#   make vhdl-test       GHDL analysis + mixed-language elaboration check of the
#                        VHDL wrapper (if installed; not a simulation)
#   make cocotb          cocotb regression on Verilator and Icarus (CI profile;
#                        COCOTB_PROFILE=long for the long random runs,
#                        COCOTB_SIMS to pick the simulators)
#   make waves           timing plots (PNG) of the master modes, the slave at
#                        its SCK/clk limit and the CPHA=1 loopback last bit,
#                        in tests/cocotb/results/waves/
#   make formal          SymbiYosys proofs, bounded checks and covers of the
#                        FIFO, master and AXI4-Lite slave (formal/; FORMAL_JOBS
#                        selects jobs, e.g. FORMAL_JOBS="fifo master:prf_w4")
#   make gatelevel       glitch check of CS_n / MOSI on a Yosys gate netlist of
#                        spi_master with random gate delays, and spi_fifo
#                        against its iCE40 block-RAM netlist (Icarus)
#   make params          legal parameter sets elaborate and illegal ones stop
#                        Yosys, Verilator and Icarus
#   make pnr             Yosys + nextpnr place-and-route of spi_top and
#                        spi_axi4lite for iCE40 UP5K and ECP5-25 (build/pnr/)
#   make doc-numbers     pnr + synth_report, then copy the measured numbers
#                        into README.md, RESOURCE_ESTIMATES.md, VERIFICATION.md
#   make clean           remove build artifacts

# Recipes such as `sim | tee log` must fail when the first command fails.
SHELL       := bash
.SHELLFLAGS := -o pipefail -c

VERILATOR ?= verilator
YOSYS     ?= yosys
GHDL      ?= ghdl
VIVADO    ?= vivado
QUARTUS   ?= quartus_sh
PYTHON    ?= python3

COCOTB_PROFILE ?= ci
COCOTB_SIMS    ?= verilator icarus

RTL_CORE := \
    rtl/spi_fifo.sv \
    rtl/spi_master.sv \
    rtl/spi_slave.sv \
    rtl/spi_top.sv

RTL_AXI := \
    $(RTL_CORE) \
    rtl/axi4lite_regfile.sv \
    rtl/axi4lite_slave.sv \
    rtl/spi_axi4lite.sv

TB_TOP := tb/spi_top_tb.sv
TB_AUX := tb/spi_assertions.sv tb/spi_cov.sv
TB_CPP := tb/sim_main.cpp
TB_AXI     := tb/spi_axi4lite_tb.sv
TB_AXI_CPP := tb/sim_axi.cpp

VFLAGS := -Wall -Wno-UNUSEDPARAM -Wno-UNUSEDSIGNAL -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC

.PHONY: lint sim test synth synth_report vhdl-test cocotb waves formal gatelevel params pnr doc-numbers clean

lint:
	$(VERILATOR) --lint-only $(VFLAGS) --top-module spi_top_tb \
	    $(RTL_CORE) $(TB_TOP) $(TB_AUX)
	$(VERILATOR) --lint-only $(VFLAGS) --top-module spi_axi4lite \
	    $(RTL_AXI)
	$(VERILATOR) --lint-only $(VFLAGS) --top-module spi_axi4lite_tb \
	    $(RTL_AXI) $(TB_AXI)

sim: obj_dir/Vspi_top_tb obj_dir_axi/Vspi_axi4lite_tb

obj_dir/Vspi_top_tb: $(RTL_CORE) $(TB_TOP) $(TB_AUX) $(TB_CPP)
	$(VERILATOR) --cc --exe --build $(VFLAGS) --assert \
	    --top-module spi_top_tb \
	    $(RTL_CORE) $(TB_TOP) $(TB_AUX) $(TB_CPP) \
	    -o Vspi_top_tb

obj_dir_axi/Vspi_axi4lite_tb: $(RTL_AXI) $(TB_AXI) $(TB_AXI_CPP)
	$(VERILATOR) --cc --exe --build $(VFLAGS) --assert --Mdir obj_dir_axi \
	    --top-module spi_axi4lite_tb \
	    $(RTL_AXI) $(TB_AXI) $(TB_AXI_CPP) \
	    -o Vspi_axi4lite_tb

test: sim
	./obj_dir/Vspi_top_tb | tee test.log
	./obj_dir_axi/Vspi_axi4lite_tb | tee test_axi.log
	@grep -q "+PASS" test.log && grep -q "+PASS" test_axi.log && echo "TESTS PASSED" \
	    || (echo "TESTS FAILED" && exit 1)

# ---------- Open synthesis (Yosys) ------------------------------------------
synth:
	$(YOSYS) -p "read_verilog -sv $(RTL_CORE); hierarchy -top spi_top; synth_ice40 -top spi_top; stat" \
	    | tee synth_ice40.log
	$(YOSYS) -p "read_verilog -sv $(RTL_CORE); hierarchy -top spi_top; synth_ecp5 -top spi_top -abc9; stat" \
	    | tee synth_ecp5.log
	$(YOSYS) -p "read_verilog -sv $(RTL_CORE); hierarchy -top spi_top; synth_xilinx -top spi_top -flatten; stat" \
	    | tee synth_xilinx.log

# ---------- Cross-toolchain synthesis report --------------------------------
synth_report:
	@bash scripts/synth_report.sh

# ---------- VHDL wrapper check (optional) ------------------------------------
vhdl-test:
	@which $(GHDL) >/dev/null 2>&1 || { echo "ghdl not installed - skipping vhdl-test"; exit 0; }
	@which $(VERILATOR) >/dev/null 2>&1 || { echo "verilator not installed"; exit 1; }
	@bash scripts/vhdl_cosim.sh

# ---------- cocotb regression (tests/cocotb) ---------------------------------
# Every simulator in COCOTB_SIMS runs; the target fails if any of them fails.
cocotb:
	@rc=0; for sim in $(COCOTB_SIMS); do \
	    $(PYTHON) tests/cocotb/regress.py --sim $$sim --profile $(COCOTB_PROFILE) || rc=1; \
	done; exit $$rc

waves:
	$(PYTHON) tests/cocotb/waves.py

# ---------- Formal verification (SymbiYosys) ----------------------------------
FORMAL_JOBS ?=

formal:
	bash formal/run.sh $(FORMAL_JOBS)

# ---------- Gate-level CS_n / MOSI glitch check -------------------------------
gatelevel:
	bash tests/gatelevel/run.sh
	bash tests/gatelevel/fifo_ram.sh

# ---------- Elaboration-time parameter checks ---------------------------------
params:
	bash tests/params/run.sh

# ---------- Open place-and-route (Yosys + nextpnr) ----------------------------
pnr:
	$(PYTHON) scripts/pnr.py

# Every synthesis / P&R number in the docs comes from here (SYNTH_REPORT.md
# from synth_report, the generated sections of the other docs from build/pnr).
doc-numbers: pnr synth_report
	$(PYTHON) scripts/update_numbers.py

clean:
	rm -rf obj_dir obj_dir_axi \
	    test.log test_axi.log \
	    synth_ice40.log synth_ecp5.log synth_xilinx.log \
	    synth_report/ build/ \
	    tests/cocotb/build/ tests/cocotb/results/ formal/work/ \
	    tests/gatelevel/build/ tests/params/build/
