# VHDL wrapper

`spi_top_vhdl.vhd` is a VHDL-2008 entity wrapping the SystemVerilog
`spi_top` module. Use it from a VHDL top in mixed-language projects;
it has only been checked with GHDL and Yosys (below), not with a vendor
tool (Vivado, Quartus, Diamond, ModelSim, Questa). The datapath remains in
SystemVerilog (`rtl/*.sv`); the wrapper only re-declares the port list
in VHDL syntax.

```vhdl
library ieee;
  use ieee.std_logic_1164.all;
  use ieee.numeric_std.all;

u_spi : entity work.spi_top_vhdl
  generic map ( DATA_WIDTH => 8, NUM_CS => 4, FIFO_DEPTH => 16,
                DIV_WIDTH => 16, CS_DLY_W => 8 )
  port map (
    clk_i  => clk,
    rst_ni => rst_n,
    -- ... see spi_top_vhdl.vhd for the full port list
  );
```

The wrapper maps every `spi_top` port, including the four FIFO count
ports and the v1.1 outputs `miso_oe_o`, `s_tx_underrun_o` and
`s_rx_overrun_o`. The width of `m_cs_sel_i` follows NUM_CS
(`$clog2(NUM_CS)`, 1 bit for NUM_CS = 1).

`make vhdl-test`, run from the repository root, checks the wrapper
statically; it is not a simulation:

1. Verilator lint of the SystemVerilog core.
2. GHDL analysis of the wrapper (VHDL-2008).
3. Every `spi_top` port (listed by Yosys) must appear in the wrapper's
   port map.
4. Mixed-language elaboration in Yosys with the ghdl plugin: GHDL
   elaborates the wrapper, Yosys binds the `spi_top` component to the
   SystemVerilog module, and any port width adaptation fails the check.
   Six generic sets: defaults, NUM_CS = 1, 2 and 8, FIFO_DEPTH = 64,
   DATA_WIDTH = 16.

GHDL warns that component `spi_top` is unbound when it elaborates the
wrapper on its own; Yosys binds it in step 4. Steps 3 and 4 are skipped
when Yosys or its ghdl plugin is missing. The wrapper has not been
simulated; a mixed-language simulation needs a simulator that supports
both languages (for example Vivado xsim or Questa).
