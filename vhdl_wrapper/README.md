# VHDL wrapper

`spi_top_vhdl.vhd` is a VHDL-2008 entity wrapping the SystemVerilog
`spi_top` module. Use it from a VHDL top in mixed-language projects
(Vivado, Quartus, Diamond, ModelSim, Questa). The datapath remains in
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

For pure-VHDL simulation, run `make vhdl-test` from the repository
root. It analyses the wrapper with GHDL and lints the SV core with
Verilator. End-to-end mixed-language elaboration requires Vivado
xsim, ModelSim, or Questa.
