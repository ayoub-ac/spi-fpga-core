// SPDX-License-Identifier: GPL-3.0-or-later OR Commercial
//
`timescale 1ps/1ps
// Generic yosys gate models with a random transport delay per instance (60..260 ps) and
// 150 ps clk-to-q, used only to look for output glitches of the synthesized netlist.
`define DLY integer d; initial d = 60 + ($urandom % 200);
module \$_NOT_    (input A, output reg Y);           `DLY always @* Y <= #(d) ~A;            endmodule
module \$_AND_    (input A, B, output reg Y);        `DLY always @* Y <= #(d) A & B;         endmodule
module \$_NAND_   (input A, B, output reg Y);        `DLY always @* Y <= #(d) ~(A & B);      endmodule
module \$_OR_     (input A, B, output reg Y);        `DLY always @* Y <= #(d) A | B;         endmodule
module \$_NOR_    (input A, B, output reg Y);        `DLY always @* Y <= #(d) ~(A | B);      endmodule
module \$_XOR_    (input A, B, output reg Y);        `DLY always @* Y <= #(d) A ^ B;         endmodule
module \$_XNOR_   (input A, B, output reg Y);        `DLY always @* Y <= #(d) ~(A ^ B);      endmodule
module \$_ANDNOT_ (input A, B, output reg Y);        `DLY always @* Y <= #(d) A & ~B;        endmodule
module \$_ORNOT_  (input A, B, output reg Y);        `DLY always @* Y <= #(d) A | ~B;        endmodule
module \$_MUX_    (input A, B, S, output reg Y);     `DLY always @* Y <= #(d) S ? B : A;     endmodule
module \$_DFF_P_      (input C, D, output reg Q);         always @(posedge C) Q <= #150 D; endmodule
module \$_DFFE_PP_    (input C, D, E, output reg Q);      always @(posedge C) if (E) Q <= #150 D; endmodule
module \$_DFFE_PN_    (input C, D, E, output reg Q);      always @(posedge C) if (!E) Q <= #150 D; endmodule
module \$_SDFF_PN0_   (input C, D, R, output reg Q);      always @(posedge C) Q <= #150 (!R ? 1'b0 : D); endmodule
module \$_SDFF_PN1_   (input C, D, R, output reg Q);      always @(posedge C) Q <= #150 (!R ? 1'b1 : D); endmodule
module \$_SDFF_PP0_   (input C, D, R, output reg Q);      always @(posedge C) Q <= #150 ( R ? 1'b0 : D); endmodule
module \$_SDFF_PP1_   (input C, D, R, output reg Q);      always @(posedge C) Q <= #150 ( R ? 1'b1 : D); endmodule
module \$_SDFFE_PN0P_ (input C, D, R, E, output reg Q);   always @(posedge C) if (!R) Q <= #150 1'b0; else if (E) Q <= #150 D; endmodule
module \$_SDFFE_PN1P_ (input C, D, R, E, output reg Q);   always @(posedge C) if (!R) Q <= #150 1'b1; else if (E) Q <= #150 D; endmodule
module \$_SDFFE_PP0P_ (input C, D, R, E, output reg Q);   always @(posedge C) if ( R) Q <= #150 1'b0; else if (E) Q <= #150 D; endmodule
module \$_SDFFE_PN0N_ (input C, D, R, E, output reg Q);   always @(posedge C) if (!R) Q <= #150 1'b0; else if (!E) Q <= #150 D; endmodule
module \$_SDFFCE_PN0P_ (input C, D, R, E, output reg Q);  always @(posedge C) if (E) Q <= #150 (!R ? 1'b0 : D); endmodule
module \$_SDFFCE_PN1P_ (input C, D, R, E, output reg Q);  always @(posedge C) if (E) Q <= #150 (!R ? 1'b1 : D); endmodule
module \$_SDFFCE_PP0P_ (input C, D, R, E, output reg Q);  always @(posedge C) if (E) Q <= #150 ( R ? 1'b0 : D); endmodule
